"""Opt-in Docker boundary checks; the default suite remains daemon-free."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from adaptive_gateway.agent.worker import DockerWorker, WorkerError
from adaptive_gateway.eval.runner import REPO, run


@unittest.skipUnless(os.environ.get("RUN_DOCKER_INTEGRATION") == "1", "Docker integration is opt-in")
class DockerIntegrationTests(unittest.TestCase):
    def test_full_episode_and_independent_verifier(self):
        with tempfile.TemporaryDirectory(dir=REPO / "examples/bugfix") as output:
            episode = asyncio.run(run(Path(output)))
            self.assertEqual(episode["terminal_state"], "resolved")
            self.assertEqual(episode["verification"]["exit_code"], 0)
            self.assertIn("+    return a + b", (Path(output) / "patch.diff").read_text())
            self.assertEqual(len([x for x in episode["trace"] if "request_id" in x]), 5)

    def test_worker_rejects_escape_and_verifier_catches_bad_patch(self):
        fixture = REPO / "examples/bugfix/repo"
        verifier = REPO / "examples/bugfix/verifier"
        with DockerWorker(fixture, verifier, ["python", "-m", "unittest", "discover", "-s", "tests", "-q"]) as worker:
            with self.assertRaises(WorkerError):
                worker.tool("read_file", {"path": "../task.json"})
            worker.tool("apply_patch", {"path": "calculator.py", "old": "return a - b", "new": "return a * b"})
            result = worker.verify(["python", "-m", "unittest", "discover", "-s", "/verifier", "-q"])
            self.assertNotEqual(result["exit_code"], 0)
