"""Small tool process executed only inside the disposable Docker worker."""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/workspace")
BASELINE = Path("/baseline")
MAX_OUTPUT = 16_384


def file_path(raw: str, *, existing: bool = True) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\x00" in raw:
        raise ValueError("invalid_path")
    relative = Path(raw)
    if ".." in relative.parts or relative.parts[0].startswith("."):
        raise ValueError("invalid_path")
    path = ROOT / relative
    if path.is_symlink():
        raise ValueError("protected_path")
    try:
        resolved = path.resolve(strict=existing)
    except OSError as exc:
        raise ValueError("invalid_path") from exc
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("protected_path")
    if existing and not path.is_file():
        raise ValueError("not_a_file")
    return path


def run(command: list[str], *, cwd: Path = ROOT, extra_env: dict[str, str] | None = None) -> dict:
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("invalid_test_command")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                timeout=20, check=False)
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "output": "Test command timed out"}
    return {"exit_code": result.returncode, "output": (result.stdout + result.stderr)[:MAX_OUTPUT]}


def dispatch(action: dict) -> dict:
    name = action.get("name")
    args = action.get("arguments", {})
    if not isinstance(args, dict):
        raise ValueError("invalid_arguments")
    if name == "list_files":
        files = []
        for path in sorted(ROOT.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files.append(str(path.relative_to(ROOT)))
        return {"files": files[:200]}
    if name == "read_file":
        return {"content": file_path(args["path"]).read_text()[:MAX_OUTPUT]}
    if name == "search":
        needle = args["query"]
        if not isinstance(needle, str) or not needle or len(needle) > 200:
            raise ValueError("invalid_query")
        matches = []
        for path in sorted(ROOT.rglob("*")):
            if path.is_file() and not path.is_symlink():
                for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if needle in line:
                        matches.append({"path": str(path.relative_to(ROOT)), "line": number,
                                        "text": line[:300]})
                    if len(matches) >= 100:
                        return {"matches": matches}
        return {"matches": matches}
    if name == "apply_patch":
        path = file_path(args["path"])
        old, new = args["old"], args["new"]
        if not isinstance(old, str) or not old or not isinstance(new, str) or len(new) > MAX_OUTPUT:
            raise ValueError("invalid_patch")
        original = path.read_text()
        if original.count(old) != 1:
            raise ValueError("patch_context_mismatch")
        path.write_text(original.replace(old, new, 1))
        return {"applied": True}
    if name == "run_tests":
        return run(action["command"])
    if name == "verify":
        return run(action["command"], extra_env={"PYTHONPATH": "/workspace"})
    if name == "diff":
        patches = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(ROOT)
            before = BASELINE / relative
            old = before.read_text().splitlines(keepends=True) if before.is_file() else []
            new = path.read_text().splitlines(keepends=True)
            patches.extend(difflib.unified_diff(old, new, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        return {"patch": "".join(patches)[:MAX_OUTPUT]}
    raise ValueError("unknown_tool")


def main() -> None:
    try:
        result = dispatch(json.load(sys.stdin))
        print(json.dumps({"ok": True, "result": result}))
    except (KeyError, OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))


if __name__ == "__main__":
    main()
