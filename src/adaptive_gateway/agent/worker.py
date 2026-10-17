"""Docker-only tool boundary for one disposable coding workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUNTIME = Path(__file__).with_name("worker_runtime.py")
IMAGE = "python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"
TOOLS = {"list_files", "read_file", "search", "apply_patch", "run_tests", "diff"}


class WorkerError(RuntimeError):
    pass


class DockerWorker:
    def __init__(self, fixture: Path, verifier: Path, test_command: list[str]) -> None:
        self.fixture = fixture.resolve()
        self.verifier = verifier.resolve()
        self.test_command = test_command
        self._temporary: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> "DockerWorker":
        shared_tmp = self.fixture.parents[2] / ".worker-runs"
        shared_tmp.mkdir(exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="gateway-worker-", dir=shared_tmp)
        self.root = Path(self._temporary.name)
        self.workspace = self.root / "workspace"
        self.baseline = self.root / "baseline"
        shutil.copytree(self.fixture, self.workspace, symlinks=True)
        shutil.copytree(self.fixture, self.baseline, symlinks=True)
        if any(p.is_symlink() for p in self.workspace.rglob("*")):
            self._temporary.cleanup()
            raise WorkerError("Fixture contains a symlink")
        return self

    def __exit__(self, *_: object) -> None:
        if self._temporary:
            self._temporary.cleanup()

    def _invoke(self, name: str, arguments: dict, command: list[str] | None = None) -> dict:
        payload = {"name": name, "arguments": arguments}
        if command is not None:
            payload["command"] = command
        docker = ["docker", "run", "--rm", "--interactive", "--network", "none", "--read-only",
                  "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
                  "--memory", "256m", "--cpus", "1", "--user", f"{os.getuid()}:{os.getgid()}",
                  "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                  "--mount", f"type=bind,src={self.workspace},dst=/workspace",
                  "--mount", f"type=bind,src={self.baseline},dst=/baseline,readonly",
                  "--mount", f"type=bind,src={self.verifier},dst=/verifier,readonly",
                  "--mount", f"type=bind,src={RUNTIME},dst=/worker_runtime.py,readonly",
                  IMAGE, "python", "/worker_runtime.py"]
        try:
            process = subprocess.run(docker, input=json.dumps(payload), text=True, capture_output=True,
                                     timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkerError(f"Docker worker unavailable: {exc}") from exc
        if process.returncode:
            raise WorkerError(f"Docker worker failed: {process.stderr[:1000]}")
        try:
            response = json.loads(process.stdout)
        except ValueError as exc:
            raise WorkerError("Docker worker returned invalid JSON") from exc
        if not response.get("ok"):
            raise WorkerError(response.get("error", "Tool failed"))
        return response["result"]

    def tool(self, name: str, arguments: dict) -> dict:
        if name not in TOOLS:
            raise WorkerError("Tool is not allowed")
        if name == "run_tests":
            return self._invoke(name, arguments, self.test_command)
        return self._invoke(name, arguments)

    def verify(self, command: list[str]) -> dict:
        return self._invoke("verify", {}, command)
