"""One-attempt OpenAI-compatible chat transport for the M0 contract spike.

Retries, routing, accounting, and caller deadlines belong above this adapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping

import httpx

from adaptive_gateway.contracts import ChatRequest, ModelAlias


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class Completion:
    model: str
    message: Mapping[str, Any]
    finish_reason: str
    usage: Usage | None


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    call_id: str | None
    name: str | None
    arguments: str


@dataclass(frozen=True)
class StreamEnd:
    finish_reason: str
    usage: Usage | None


StreamEvent = TextDelta | ToolCallDelta | StreamEnd


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


def _usage(value: Any) -> Usage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProviderError("invalid_response", "Usage is not an object", retryable=False)
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    counts = []
    for field in fields:
        count = value.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ProviderError("invalid_response", f"Invalid usage field: {field}", retryable=False)
        counts.append(count)
    return Usage(*counts, dict(value))


def _choice(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("choices"), list) or len(value["choices"]) != 1:
        raise ProviderError("invalid_response", "Expected exactly one choice", retryable=False)
    choice = value["choices"][0]
    if not isinstance(choice, dict):
        raise ProviderError("invalid_response", "Choice is not an object", retryable=False)
    return choice


def _request_body(request: ChatRequest, model: ModelAlias) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model.model_id, "messages": list(request.messages), "stream": request.stream}
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    if request.tools:
        body["tools"] = list(request.tools)
    if request.tool_choice is not None:
        body["tool_choice"] = request.tool_choice
    if request.stream:
        body["stream_options"] = {"include_usage": True}
    return body


class DirectTransport:
    """An injected client allows every M0 case to run without sockets or keys."""

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        if not api_key:
            raise ValueError("Provider key is required")
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def complete(self, request: ChatRequest, model: ModelAlias) -> Completion:
        if request.stream:
            raise ValueError("Use stream() for streaming requests")
        try:
            response = await self._client.post(model.endpoint, json=_request_body(request, model), headers=self._headers)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", str(exc), retryable=True, outcome_unknown=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("transport_error", str(exc), retryable=True, outcome_unknown=True) from exc
        _check_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_response", "Provider returned invalid JSON", retryable=False) from exc
        choice = _choice(data)
        message = choice.get("message")
        reason = choice.get("finish_reason")
        if not isinstance(message, dict) or reason not in ("stop", "length", "tool_calls"):
            raise ProviderError("invalid_response", "Invalid message or finish reason", retryable=False)
        _validate_message(message)
        provider_model = data.get("model")
        if not isinstance(provider_model, str) or not provider_model:
            raise ProviderError("invalid_response", "Missing provider model", retryable=False)
        return Completion(provider_model, message, reason, _usage(data.get("usage")))

    async def stream(self, request: ChatRequest, model: ModelAlias) -> AsyncIterator[StreamEvent]:
        if not request.stream:
            raise ValueError("Use complete() for nonstreaming requests")
        emitted = False
        try:
            async with self._client.stream("POST", model.endpoint, json=_request_body(request, model),
                                           headers=self._headers) as response:
                _check_status(response)
                finish_reason: str | None = None
                usage: Usage | None = None
                done = False
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data: "):
                        raise ProviderError("invalid_stream", "Unexpected SSE line", retryable=False)
                    data = line[6:]
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError as exc:
                        raise ProviderError("invalid_stream", "Invalid SSE JSON", retryable=False) from exc
                    if not isinstance(chunk, dict):
                        raise ProviderError("invalid_stream", "SSE event is not an object", retryable=False)
                    if chunk.get("usage") is not None:
                        usage = _usage(chunk["usage"])
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or len(choices) > 1:
                        raise ProviderError("invalid_stream", "Invalid choices array", retryable=False)
                    if not choices:
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                        raise ProviderError("invalid_stream", "Invalid choice delta", retryable=False)
                    delta = choice["delta"]
                    content = delta.get("content")
                    if content is not None:
                        if not isinstance(content, str):
                            raise ProviderError("invalid_stream", "Nontext delta", retryable=False)
                        if content:
                            emitted = True
                            yield TextDelta(content)
                    calls = delta.get("tool_calls", [])
                    if not isinstance(calls, list):
                        raise ProviderError("invalid_stream", "Invalid tool call delta", retryable=False)
                    for call in calls:
                        if (not isinstance(call, dict) or isinstance(call.get("index"), bool)
                                or not isinstance(call.get("index"), int) or call["index"] < 0):
                            raise ProviderError("invalid_stream", "Invalid tool call index", retryable=False)
                        function = call.get("function") or {}
                        if not isinstance(function, dict):
                            raise ProviderError("invalid_stream", "Invalid function delta", retryable=False)
                        call_id, name, arguments = call.get("id"), function.get("name"), function.get("arguments", "")
                        if (call_id is not None and not isinstance(call_id, str)) or (name is not None and not isinstance(name, str)) or not isinstance(arguments, str):
                            raise ProviderError("invalid_stream", "Invalid tool call fragment", retryable=False)
                        emitted = True
                        yield ToolCallDelta(call["index"], call_id, name, arguments)
                    reason = choice.get("finish_reason")
                    if reason is not None:
                        if reason not in ("stop", "length", "tool_calls"):
                            raise ProviderError("invalid_stream", "Invalid finish reason", retryable=False)
                        finish_reason = reason
                if not done or finish_reason is None:
                    raise ProviderError("incomplete_stream", "Stream ended before completion", retryable=False,
                                        outcome_unknown=True)
                yield StreamEnd(finish_reason, usage)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", str(exc), retryable=not emitted, outcome_unknown=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("transport_error", str(exc), retryable=not emitted, outcome_unknown=True) from exc


def _check_status(response: httpx.Response) -> None:
    if 200 <= response.status_code < 300:
        return
    code = "rate_limited" if response.status_code == 429 else "provider_http_error"
    retryable = response.status_code in (408, 409, 429) or response.status_code >= 500
    raise ProviderError(code, f"Provider HTTP {response.status_code}", retryable=retryable)


def _validate_message(message: Mapping[str, Any]) -> None:
    if message.get("role") != "assistant":
        raise ProviderError("invalid_response", "Expected assistant message", retryable=False)
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderError("invalid_response", "Nontext response", retryable=False)
    calls = message.get("tool_calls")
    if calls is None:
        if content is None:
            raise ProviderError("invalid_response", "No content or tool calls", retryable=False)
        return
    if not isinstance(calls, list) or not calls:
        raise ProviderError("invalid_response", "Invalid tool calls", retryable=False)
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("id"), str) or call.get("type") != "function":
            raise ProviderError("invalid_response", "Invalid tool call", retryable=False)
        function = call.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
            raise ProviderError("invalid_response", "Invalid function call", retryable=False)
