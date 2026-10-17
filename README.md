# Adaptive LLM Gateway

An experimental LLM gateway and coding-task harness for comparing model-routing policies on cost, task success, and reliability.

## Current status

**M0 complete.** This repository contains a typed request/config contract, a documented `/v1/chat/completions` subset, an offline config, a fixture manifest, and a direct transport tested against in-memory provider responses. The [transport comparison](docs/decisions/0002-transport-choice.md) selects the direct path for v0.1. It does not yet serve HTTP requests, call a real model, execute agent tools, or measure routing performance.

## Local setup

Requires Python 3.12 and `uv`. Docker is planned for disposable workers in M1; it is not needed to inspect M0.

```sh
uv python install 3.12
UV_CACHE_DIR=.uv-cache uv sync --offline
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline python -m unittest discover -s tests -p 'test_*.py'
```

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

