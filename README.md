# Adaptive LLM Gateway

An experimental LLM gateway and coding-task harness for comparing model-routing policies on cost, task success, and reliability.

## Current status

**M1 offline slice implemented.** A deterministic mock provider serves the documented `/v1/chat/completions` subset. A bounded controller requests a scripted patch through the gateway, executes tools in a disposable Docker workspace, and runs independent verifier tests. The episode records a patch, trace, token usage, and fictional fixture cost. The [transport comparison](docs/decisions/0002-transport-choice.md) selected the direct path for later live-provider work. There is no live provider, budget ledger, retry policy, or routing evaluation yet.

## Local setup

Requires Python 3.12 and `uv`. The full M1 demo also requires a running Docker daemon (Colima works on macOS). The worker uses a pinned Python 3.12 Alpine image and does not receive a provider key or network access.

GitHub Actions runs the offline tests and Docker integration on pull requests and pushes to `main`. The Docker workflow uploads the generated episode and patch as a workflow artifact. Neither workflow requires a provider key.

```sh
uv python install 3.12
UV_CACHE_DIR=.uv-cache uv sync --locked
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline python -m unittest discover -s tests -p 'test_*.py'
```

Run the offline bug-fix episode after Docker is available and the worker image has been pulled once:

```sh
colima start  # macOS, if using Colima
docker pull python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline python -m adaptive_gateway.eval.runner --output artifacts/bugfix
RUN_DOCKER_INTEGRATION=1 PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline python -m unittest discover -s tests -p 'test_m1_docker_integration.py' -q
```

Inspect `artifacts/bugfix/episode.json` and `artifacts/bugfix/patch.diff`. These generated files are ignored by Git. The run uses an in-process HTTP ASGI client, so no listening port or gateway key setup is needed. To serve the same mock endpoint locally, set `GATEWAY_LOCAL_KEY` to a nonempty local secret and run:

```sh
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline uvicorn adaptive_gateway.gateway.api:app_from_environment --factory --host 127.0.0.1 --port 8000
```

The fixture command arrays and time/step limits are in `examples/bugfix/task.json`. The independent verifier lives outside the editable fixture repository and is mounted read-only. The worker uses no network, a read-only container root, one limited workspace mount, no added capabilities, and CPU/memory/process/time limits. This is a local research harness, not a hardened public sandbox. M1 estimates cost from fictional configured prices and reported usage; it does not enforce a monetary cap.

The optional LiteLLM comparison uses a pinned dependency group and in-memory responses. It makes no provider calls:

```sh
UV_CACHE_DIR=.uv-cache uv sync --group transport-spike
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --group transport-spike --offline python -m unittest discover -s tests -p 'test_*.py'
```

The contract tests also run without `uv`:

```sh
PYTHONPATH=src python3.12 -m unittest discover -s tests -p 'test_*.py'
```

See [API contract](docs/api.md), [M0 decisions](docs/decisions/0001-m0-contract.md), and [fixture manifest](evals/fixtures/manifest.json).

