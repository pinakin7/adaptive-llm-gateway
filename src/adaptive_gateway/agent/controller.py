"""Bounded reference controller; tool names and arguments are checked before Docker."""

from __future__ import annotations

from decimal import Decimal
import json
import time
from typing import Any
import uuid

import httpx

from adaptive_gateway.agent.worker import DockerWorker, WorkerError
from adaptive_gateway.contracts import GatewayConfig

TOOL_PARAMETERS = {
    "list_files": set(), "read_file": {"path"}, "search": {"query"},
    "apply_patch": {"path", "old", "new"}, "run_tests": set(), "diff": set(),
}
TOOLS = [{"type": "function", "function": {"name": name, "parameters": {
    "type": "object", "properties": {field: {"type": "string"} for field in fields},
    "required": sorted(fields), "additionalProperties": False}}}
         for name, fields in TOOL_PARAMETERS.items()]


def _arguments(name: str, raw: str) -> dict:
    if name not in TOOL_PARAMETERS:
        raise ValueError("Unknown tool")
    arguments = json.loads(raw)
    if not isinstance(arguments, dict) or set(arguments) != TOOL_PARAMETERS[name]:
        raise ValueError("Tool arguments do not match the declared schema")
    if not all(isinstance(value, str) for value in arguments.values()):
        raise ValueError("Tool arguments must be strings")
    return arguments


async def run_task(client: httpx.AsyncClient, worker: DockerWorker, config: GatewayConfig,
                   *, key: str, alias: str = "economy", max_steps: int = 6,
                   deadline_seconds: float = 90, prompt: str = "Fix the addition bug in calculator.py and run the tests.") -> dict[str, Any]:
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    messages: list[dict] = [{"role": "user", "content": prompt}]
    trace: list[dict] = []
    started = time.monotonic()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    usage_complete = True
    terminal = "unresolved"
    reason = "step_limit"
    test_failed = False
    for step in range(max_steps):
        if time.monotonic() - started >= deadline_seconds:
            terminal, reason = "deadline_exceeded", "task_deadline"
            break
        step_id = f"step-{step + 1}"
        try:
            response = await client.post("/v1/chat/completions", json={"model": alias, "messages": messages,
                                         "tools": TOOLS, "tool_choice": "auto", "max_tokens": 256},
                                         headers={"Authorization": f"Bearer {key}", "X-Task-ID": task_id,
                                                  "X-Step-ID": step_id}, timeout=max(1, deadline_seconds - (time.monotonic() - started)))
        except httpx.HTTPError as exc:
            terminal, reason = "infrastructure_error", type(exc).__name__
            break
        if response.status_code != 200:
            error = response.json()["error"]
            terminal = "policy_denied" if response.status_code in (401, 403) else "infrastructure_error"
            reason = error["code"]
            break
        data = response.json()
        choice = data["choices"][0]
        reported = data.get("usage")
        if reported is not None:
            usage["prompt_tokens"] += reported["prompt_tokens"]
            usage["completion_tokens"] += reported["completion_tokens"]
        else:
            usage_complete = False
        trace.append({"task_id": task_id, "step_id": step_id, "request_id": data["id"],
                      "alias": alias, "model": data["model"],
                      "route_reason": data["adaptive_gateway"]["route_reason"],
                      "policy_version": data["adaptive_gateway"]["policy_version"],
                      "finish_reason": choice["finish_reason"], "usage": reported})
        message = choice["message"]
        messages.append(message)
        if choice["finish_reason"] == "stop":
            terminal, reason = "resolved", "model_finished"
            break
        if choice["finish_reason"] != "tool_calls":
            reason = "incomplete_response"
            break
        try:
            for call in message["tool_calls"]:
                name = call["function"]["name"]
                arguments = _arguments(name, call["function"]["arguments"])
                result = worker.tool(name, arguments)
                if name == "run_tests" and result["exit_code"] != 0:
                    test_failed = True
                trace.append({"step_id": step_id, "tool": name, "outcome": result.get("exit_code", "ok")})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result)[:16_384]})
        except (KeyError, ValueError, WorkerError) as exc:
            terminal = "infrastructure_error" if isinstance(exc, WorkerError) else "policy_denied"
            reason = str(exc)
            break
    if terminal == "resolved" and test_failed:
        terminal, reason = "unresolved", "agent_tests_failed"
    price = config.aliases[alias]
    estimated = ((Decimal(usage["prompt_tokens"]) * price.input_per_million +
                  Decimal(usage["completion_tokens"]) * price.output_per_million) / Decimal(1_000_000)) if usage_complete else None
    return {"task_id": task_id, "terminal_state": terminal, "reason": reason, "trace": trace,
            "usage": usage if usage_complete else None,
            "estimated_fixture_cost": str(estimated) if estimated is not None else None, "currency": price.currency}
