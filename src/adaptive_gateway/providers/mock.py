"""Deterministic offline responses for the M1 bug-fix fixture."""

from __future__ import annotations

import json
from typing import AsyncIterator

from adaptive_gateway.contracts import ChatRequest, ModelAlias
from adaptive_gateway.providers.direct import Completion, ProviderError, StreamEnd, TextDelta, ToolCallDelta, Usage


class MockProvider:
    def __init__(self, scenario: str = "bugfix") -> None:
        if scenario not in {"bugfix", "text", "missing_usage", "error", "partial_stream"}:
            raise ValueError("Unknown mock scenario")
        self.scenario = scenario
        self.dispatches = 0
        self.stream_closed = False

    async def complete(self, request: ChatRequest, model: ModelAlias) -> Completion:
        self.dispatches += 1
        if self.scenario == "error":
            raise ProviderError("mock_failure", "Scripted provider failure", retryable=False)
        if self.scenario in {"text", "missing_usage", "partial_stream"} or not request.tools or request.tool_choice == "none":
            usage = None if self.scenario == "missing_usage" else Usage(
                2, 3, 5, {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
            return Completion(model.model_id, {"role": "assistant", "content": "offline reply"}, "stop", usage)
        prior = [message for message in request.messages if message["role"] == "tool"]
        step = len(prior)
        if step == 0:
            name, arguments = "read_file", {"path": "calculator.py"}
        elif step == 1:
            name, arguments = "apply_patch", {
                "path": "calculator.py", "old": "return a - b", "new": "return a + b",
            }
        elif step == 2:
            name, arguments = "run_tests", {}
        elif step == 3:
            name, arguments = "diff", {}
        else:
            return Completion(model.model_id, {"role": "assistant", "content": "Patch ready for independent verification."},
                              "stop", Usage(12, 8, 20, {"prompt_tokens": 12, "completion_tokens": 8,
                                                        "total_tokens": 20}))
        call = {"id": f"mock-call-{step + 1}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}
        return Completion(model.model_id, {"role": "assistant", "content": None, "tool_calls": [call]},
                          "tool_calls", Usage(12, 8, 20, {"prompt_tokens": 12, "completion_tokens": 8,
                                                              "total_tokens": 20}))

    async def stream(self, request: ChatRequest, model: ModelAlias) -> AsyncIterator[TextDelta | ToolCallDelta | StreamEnd]:
        finished = False
        try:
            result = await self.complete(request, model)
            if result.finish_reason == "tool_calls":
                call = result.message["tool_calls"][0]
                arguments = call["function"]["arguments"]
                midpoint = max(1, len(arguments) // 2)
                yield ToolCallDelta(0, call["id"], call["function"]["name"], arguments[:midpoint])
                yield ToolCallDelta(0, None, None, arguments[midpoint:])
            else:
                yield TextDelta(result.message["content"])
            if self.scenario == "partial_stream":
                raise ProviderError("mock_stream_failure", "Scripted failure after content", retryable=False)
            yield StreamEnd(result.finish_reason, result.usage)
            finished = True
        finally:
            self.stream_closed = not finished
