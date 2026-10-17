"""Authenticated, localhost-oriented chat-completion subset for offline M1."""

from __future__ import annotations

import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from adaptive_gateway.contracts import ContractError, GatewayConfig, PolicyError, parse_chat_request, parse_gateway_config
from adaptive_gateway.providers.direct import ProviderError, StreamEnd, TextDelta, ToolCallDelta
from adaptive_gateway.providers.mock import MockProvider
from adaptive_gateway.routing.policy import POLICY_VERSION, route

_CORRELATION = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _error(status: int, code: str, message: str, request_id: str, param: str | None = None) -> JSONResponse:
    body = {"error": {"type": "invalid_request_error" if status == 400 else "gateway_error",
                      "code": code, "message": message, "request_id": request_id}}
    if param is not None:
        body["error"]["param"] = param
    return JSONResponse(body, status_code=status, headers={"X-Request-ID": request_id})


def _usage(value: object) -> dict | None:
    if value is None:
        return None
    return dict(value.raw)


def create_app(config: GatewayConfig, provider: MockProvider | None = None, *, keys: dict[str, str] | None = None) -> FastAPI:
    app = FastAPI(title="Adaptive LLM Gateway", version="0.1.0")
    mock = provider or MockProvider()
    app.state.provider = mock
    key_values = keys if keys is not None else {p.key_env: os.environ.get(p.key_env, "") for p in config.principals}
    if any(not key_values.get(p.key_env) for p in config.principals):
        raise ValueError("Every configured gateway key must be set")

    @app.post("/v1/chat/completions")
    async def completions(http_request: Request):
        request_id = f"req-{uuid.uuid4().hex}"
        auth = http_request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        principal = next((p for p in config.principals if hmac.compare_digest(token, key_values[p.key_env])), None)
        if principal is None:
            return _error(401, "invalid_api_key", "Invalid gateway key", request_id)
        for header in ("X-Task-ID", "X-Step-ID"):
            value = http_request.headers.get(header)
            if value is not None and not _CORRELATION.fullmatch(value):
                return _error(400, "invalid_correlation_id", "Invalid correlation ID", request_id, header)
        try:
            body = await http_request.json()
        except ValueError:
            return _error(400, "invalid_json", "Request body must be JSON", request_id)
        try:
            parsed = parse_chat_request(body)
            model, reason = route(parsed, config, principal)
        except ContractError as exc:
            return _error(400, exc.code, str(exc), request_id, exc.param)
        except PolicyError as exc:
            return _error(400 if exc.code == "output_limit_exceeded" else 403, exc.code, str(exc), request_id)
        if not model.endpoint.startswith("mock://"):
            return _error(503, "offline_only", "M1 only dispatches mock endpoints", request_id)
        metadata = {"request_id": request_id, "alias": parsed.model, "policy_version": POLICY_VERSION,
                    "route_reason": reason}
        created = int(time.time())
        if not parsed.stream:
            try:
                result = await mock.complete(parsed, model)
            except ProviderError as exc:
                return _error(502, exc.code, str(exc), request_id)
            return JSONResponse({"id": request_id, "object": "chat.completion", "created": created,
                                 "model": result.model, "choices": [{"index": 0, "message": result.message,
                                                                       "finish_reason": result.finish_reason}],
                                 "usage": _usage(result.usage), "adaptive_gateway": metadata},
                                headers={"X-Request-ID": request_id})

        async def events():
            iterator = mock.stream(parsed, model)
            try:
                async for event in iterator:
                    if await http_request.is_disconnected():
                        break
                    choice = {"index": 0, "delta": {}, "finish_reason": None}
                    usage = None
                    if isinstance(event, TextDelta):
                        choice["delta"] = {"content": event.text}
                    elif isinstance(event, ToolCallDelta):
                        function = {"arguments": event.arguments}
                        if event.name is not None:
                            function["name"] = event.name
                        call = {"index": event.index, "function": function}
                        if event.call_id is not None:
                            call["id"] = event.call_id
                        choice["delta"] = {"tool_calls": [call]}
                    elif isinstance(event, StreamEnd):
                        choice["finish_reason"] = event.finish_reason
                        usage = _usage(event.usage)
                    chunk = {"id": request_id, "object": "chat.completion.chunk", "created": created,
                             "model": model.model_id, "choices": [choice], "usage": usage,
                             "adaptive_gateway": metadata}
                    yield f"data: {json.dumps(chunk)}\n\n"
                    if isinstance(event, StreamEnd):
                        yield "data: [DONE]\n\n"
            finally:
                await iterator.aclose()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Request-ID": request_id})

    return app


def app_from_environment() -> FastAPI:
    path = Path(os.environ.get("GATEWAY_CONFIG", "configs/offline.json"))
    return create_app(parse_gateway_config(json.loads(path.read_text())))


