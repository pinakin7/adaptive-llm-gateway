# 0001: M0 contract and offline boundary

Date: 2026-10-15. Status: implemented; transport choice recorded in [0002](0002-transport-choice.md).

## Decisions

- Start in one local Python 3.12 repository, using `uv`; use Docker for the later disposable worker.
- Freeze a narrow chat-completions request subset and explicitly reject unknown semantics.
- Keep two fixture aliases and one scoped local principal. No real provider or spend is selected.
- Use decimal-string, versioned fixture prices; treat missing provider usage as unknown.
- Keep identity, cost reservation, provider credentials, and tool execution on distinct boundaries.
- Use a mock provider for the M1 vertical slice. Compare transport approaches before M1, then validate the chosen live transport in M2.

## Transport decision gate for the next M0 increment

Compare a direct adapter and a LiteLLM-backed adapter against the **same** offline contract cases: text, tool IDs, fragmented streamed calls, cancellation, missing usage, unsupported parameters, deadline/retry ownership, and error classification. Record exact package versions and fixture outputs. Choose one production transport only after that comparison. This contract increment does not claim either is already compatible. Disable or explicitly account for library-managed retries so that every provider attempt can be reserved and recorded by the gateway.

## Phase exits

The contract increment exits when request/config validation tests pass offline, the documented API subset matches the parser, and the fixture manifest covers the planned negative and failure cases. Full M0 also needs the offline transport comparison and a recorded choice. M1 implements the mock transport, HTTP gateway, disposable worker, controller, and independent verifier. M2 validates the chosen live transport and runs one explicitly initiated smoke test. M3 adds transactional accounting and reliability behavior. M4 evaluates routing with a frozen protocol. M5 prepares a contributor-ready release.

No public repository, license, provider choice, or paid budget is assumed by this decision.
