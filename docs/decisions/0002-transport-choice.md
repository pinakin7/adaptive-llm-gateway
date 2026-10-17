# 0002: Direct transport for v0.1

Date: 2026-10-17. Status: accepted for local M0; revisit after M2 live contract tests.

## Decision

Use the small `httpx`-based OpenAI-compatible transport in `src/adaptive_gateway/providers/direct.py` as the v0.1 starting path. Keep LiteLLM as an optional comparison dependency, not a production transport. The gateway will continue to own routing, deadlines, retries, and accounting. The transport makes one HTTP attempt and reports missing usage as unknown.

This choice is limited to the documented chat-completions subset. It does not establish compatibility with any commercial provider yet. M2 must test a chosen provider/model and decide whether a direct adapter still covers that provider's billable token categories and protocol behavior.

## Offline comparison

Environment: Python 3.12.13, `httpx==0.28.1`, `litellm==1.103.0`, and its pinned `openai==2.54.0` dependency from `uv.lock`. Tests used an injected `httpx.MockTransport` and an `AsyncOpenAI` client; no provider API or socket was called. `LITELLM_LOCAL_MODEL_COST_MAP=True` prevented LiteLLM's optional remote cost-map fetch. The same response builders and SSE fragments were supplied to both paths in `tests/test_direct_transport.py` and `tests/test_litellm_spike.py`.

| Case | Direct | LiteLLM SDK 1.103.0 |
|---|---|---|
| Text response and reported usage | Preserved | Preserved |
| Tool call ID and arguments | Preserved | Preserved |
| Fragmented streamed tool arguments | Preserved as fragments | Preserved as fragments |
| Explicit stream close | Closed injected upstream stream | Closed injected upstream stream |
| Injected failure after content | Raised; not retryable by adapter | Raised; one upstream call observed |
| HTTP 503 before content with retries disabled | One upstream call; classified retryable for gateway | One upstream call |
| Missing nonstreaming usage | `None` | `Usage(0, 0, 0)` in the SDK result |

The missing-usage result is decisive for this project's accounting contract: a zero count cannot represent an unknown charge. A LiteLLM integration would need separate raw-response evidence or an adapter fix before it could settle such attempts. The direct path also keeps one-attempt behavior visible without coordinating the [Router retry layer](https://docs.litellm.ai/docs/routing) and provider SDK retries.

## Verification

```sh
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline python -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --group transport-spike --offline python -m unittest discover -s tests -p 'test_*.py'
```

The second command requires the optional pinned dependency group to have been downloaded once. The default suite skips LiteLLM spike tests when the group is absent. The direct transport's `httpx` dependency is in the default lock. These tests exercise in-memory protocol behavior, not latency, real provider compatibility, billing accuracy, or worker isolation.
