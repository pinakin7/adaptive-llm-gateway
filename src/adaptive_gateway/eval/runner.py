"""Run the fixture through the HTTP gateway, Docker worker, and independent verifier."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from adaptive_gateway.agent.controller import run_task
from adaptive_gateway.agent.worker import DockerWorker, WorkerError
from adaptive_gateway.contracts import parse_gateway_config
from adaptive_gateway.gateway.api import create_app

REPO = Path(__file__).resolve().parents[3]


async def run(output: Path) -> dict:
    config = parse_gateway_config(json.loads((REPO / "configs/offline.json").read_text()))
    fixture = REPO / "examples/bugfix/repo"
    verifier = REPO / "examples/bugfix/verifier"
    task = json.loads((REPO / "examples/bugfix/task.json").read_text())
    key = os.environ.get("GATEWAY_LOCAL_KEY", "offline-demo-key")
    app = create_app(config, keys={"GATEWAY_LOCAL_KEY": key})
    output.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.local") as client:
        with DockerWorker(fixture, verifier, task["test_command"]) as worker:
            episode = await run_task(client, worker, config, key=key, prompt=task["prompt"],
                                     max_steps=task["max_steps"], deadline_seconds=task["deadline_seconds"])
            try:
                patch = worker.tool("diff", {})["patch"]
                verification = worker.verify(task["verifier_command"])
            except WorkerError as exc:
                patch = ""
                verification = {"exit_code": None, "output": str(exc)}
            if episode["terminal_state"] == "resolved" and verification["exit_code"] != 0:
                episode["terminal_state"] = "unresolved"
                episode["reason"] = "independent_verification_failed"
            if episode["terminal_state"] == "resolved" and not patch:
                episode["terminal_state"] = "unresolved"
                episode["reason"] = "empty_patch"
            episode["verification"] = verification
            episode["patch_file"] = "patch.diff"
            (output / "patch.diff").write_text(patch)
            (output / "episode.json").write_text(json.dumps(episode, indent=2) + "\n")
            return episode


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/bugfix"))
    arguments = parser.parse_args()
    episode = asyncio.run(run(arguments.output))
    print(json.dumps({"terminal_state": episode["terminal_state"], "reason": episode["reason"],
                      "output": str(arguments.output)}))
    raise SystemExit(0 if episode["terminal_state"] == "resolved" else 1)


if __name__ == "__main__":
    main()
