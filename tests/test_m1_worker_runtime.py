import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from adaptive_gateway.agent import worker_runtime


class WorkerRuntimeTests(unittest.TestCase):
    def test_path_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            (root / "safe.txt").write_text("original")
            (root / "escape").symlink_to(Path(directory) / "outside.txt")
            with patch.object(worker_runtime, "ROOT", root):
                for path in ("../outside.txt", "/etc/passwd", "escape"):
                    with self.assertRaises(ValueError):
                        worker_runtime.file_path(path)
                with self.assertRaises(ValueError):
                    worker_runtime.dispatch({"name": "apply_patch", "arguments": {
                        "path": "safe.txt", "old": "missing", "new": "bad"}})
                self.assertEqual((root / "safe.txt").read_text(), "original")
