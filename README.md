# Adaptive LLM Gateway

An experimental LLM gateway and coding-task harness for comparing model-routing policies on cost, task success, and reliability.

## Current status

**M0 contract increment.** This repository currently contains a typed request/config contract, a documented `/v1/chat/completions` subset, an offline config, and a fixture manifest. The M0 transport comparison is next. It does not yet serve HTTP requests, call a model, execute agent tools, or measure routing performance.

## Local setup

Requires Python 3.12 and `uv`. Docker is planned for disposable workers in M1; it is not needed to inspect M0.

```sh
uv python install 3.12
UV_CACHE_DIR=.uv-cache uv sync --offline
PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run --offline python -m unittest discover -s tests -p 'test_*.py'
```

The contract tests also run without `uv`:

```sh
PYTHONPATH=src python3.12 -m unittest discover -s tests -p 'test_*.py'
```

See [API contract](docs/api.md), [M0 decision](docs/decisions/0001-m0-contract.md), and [fixture manifest](evals/fixtures/manifest.json).

