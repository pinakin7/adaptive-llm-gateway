# Gateway API contract, M0 revision 1

This is the supported **subset** of `POST /v1/chat/completions`, not a claim of full OpenAI API compatibility. M1 serves it with a deterministic mock provider. The Python parser in `src/adaptive_gateway/contracts.py` validates its JSON request body and the offline config.

## Request

The JSON object requires `model` (an alias) and a nonempty `messages` array. Optional fields are `stream` (boolean, default `false`), `max_tokens` (positive integer), `tools` (array, default empty), and `tool_choice` (`auto`, `none`, or `required`). `required` needs at least one tool. Unknown fields fail with `unsupported_parameter`; no argument is silently ignored. In particular, image/audio parts, `response_format`, parallel-tool controls, `n`, `logprobs`, and arbitrary provider options are outside revision 1. Sampling fields are omitted until a selected transport can enforce and test them.

Messages support roles `system`, `developer`, `user`, `assistant`, and `tool`. Content is a string; an assistant message with tool calls may use `null`. A tool message requires `tool_call_id`. An assistant tool call has `id`, `type: "function"`, and `function: {"name": "...", "arguments": "{...}"}`. Arguments are a JSON **string**, as in common chat-completion transports. Tool declarations use `type: "function"` and a function object with `name`, optional `description`, and object `parameters`. The gateway validates only the outer shape of that JSON Schema in M0; the controller must validate arguments against the tool schema before execution.

The alias is selected from server config. `Authorization: Bearer <gateway-key>` binds a server-side principal to its allowed aliases. `X-Task-ID` and `X-Step-ID` are optional opaque correlation headers, limited and sanitized by the gateway; they confer no identity, model, budget, or tool rights. The gateway never accepts client-supplied principal or budget fields.

Before dispatch, the gateway rejects a denied alias, unsupported capability, or output limit beyond the alias limit. The documented local server command binds to `127.0.0.1`. M1 accepts mock endpoints only. No secret is supplied by the offline config or sent to a worker.

## Response and stream

A successful nonstreaming response uses `id`, `object: "chat.completion"`, `created`, `model` (the chosen provider model ID), `choices[0].message`, and `choices[0].finish_reason` (`stop`, `length`, or `tool_calls`). `usage` may be `null` if unavailable; it must never be fabricated as zero. An `adaptive_gateway` object carries `request_id`, `alias`, `policy_version`, and `route_reason`. Attempt timing and accounting are stored separately from this client response.

Streaming responses use server-sent events with `chat.completion.chunk` objects. Each chunk has the same response ID and chosen model. Text and tool-call deltas preserve call IDs and ordering. A terminating chunk carries `finish_reason`; the final `[DONE]` marker follows only after a complete success. Usage is supplied in a final chunk when the provider reports it; otherwise it is `null`. The controller buffers fragmented tool arguments and executes only a complete call. Once any content is emitted, an upstream failure ends this stream; a different model is never spliced into it. Client disconnect cancels upstream work, while accounting keeps its attempt unresolved until usage can be reconciled.

## Errors

Validation failures use HTTP 400 and `{"error":{"type":"invalid_request_error","code":"...","param":"...","message":"..."}}`. Authentication failure uses 401; alias/capability policy denial uses 403; a request whose output cap exceeds model limits uses 400; exhausted budget uses 429 with a distinct `budget_exhausted` code. Provider failures map to a non-2xx error with a stable gateway code and request ID. Exact transport status mapping is an M2 contract test. No provider request is sent after validation, auth, capability, or budget denial.

## Configuration contract

`configs/offline.json` is schema version 1. It requires exactly the `economy` and `capable` aliases for the M0 fixture setup. Every alias names a provider model ID, an HTTPS or mock endpoint, declared capabilities, context and output token limits, and versioned USD-style decimal-string prices for input/output tokens per million. These numbers are **fictional fixture metadata**, not commercial pricing. A missing or unknown price is invalid; zero is accepted only when explicitly configured as a known price. Later adapters must declare any additional billable categories before a strict cap can be claimed.

Each principal has a server-defined identity, a unique `key_env` naming the environment variable containing its gateway key, and allowed aliases. The config file contains no key values. M1 compares keys with `hmac.compare_digest` and enforces alias/output limits. A transactional budget ledger is planned for M3.

