"""Replay fake behind tests/fakes/bin/{claude,codex} (tests only; never a real model call).

Usage (via the sh wrappers): ``_fake_cli.py <tool> <args...>``.

- Every invocation appends one JSON line to ``$KLODE_KIT_FAKE_LOG`` (when set):
  ``{"tool", "argv", "argv_sha", "cwd", "pid"}``.
- The scenario is ``$KLODE_KIT_REPLAY_DIR/<tool>/<argv_sha>.json`` (default replay dir:
  ``tests/fixtures/replay``), with ``argv_sha`` = sha256 of the compact UTF-8 JSON array of
  the arguments. It holds ``{"stdout": str, "stderr": str, "exit_code": int}``; stdout and
  stderr are replayed byte for byte.
- No scenario -> exit 97, ``no replay scenario`` on stderr, nothing on stdout.
- A process started with the scrubbed cloud environment (only ``PATH HOME TMPDIR LANG ANTHROPIC_API_KEY USER``,
  r5) never sees ``KLODE_KIT_FAKE_LOG`` / ``KLODE_KIT_REPLAY_DIR``. For that case a test points ``HOME`` at a
  synthetic directory holding ``.klode-kit-fake.json`` = ``{"log": <path>, "replay_dir": <path>}``; the
  environment variables, when present, win (WI-00c3).
- No scenario -> live recordings (WI-00c3): ``$KLODE_KIT_RECORDINGS_DIR/<tool>/*.json`` (default
  ``tests/recordings``; ``recordings_dir`` in the HOME settings) whose ``argv_sha`` equals the sha of the compact
  UTF-8 JSON array ``[tool, *args]`` (``isolation_check.argv_sha``: program name included) are replayed the same
  way. Only then exit 97.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

NO_SCENARIO_EXIT = 97


def argv_sha(args: list[str]) -> str:
    return hashlib.sha256(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _home_settings() -> dict:
    cfg = Path(os.environ.get("HOME") or "/nonexistent") / ".klode-kit-fake.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _recording(tool: str, args: list[str], home: dict) -> Path | None:
    default_dir = Path(__file__).resolve().parents[2] / "recordings"
    root = Path(os.environ.get("KLODE_KIT_RECORDINGS_DIR") or home.get("recordings_dir") or default_dir) / tool
    key = argv_sha([tool, *args])
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("argv_sha") == key and "stdout" in data:
            return path
    return None


def main() -> int:
    tool, args = sys.argv[1], sys.argv[2:]
    sha = argv_sha(args)
    home = _home_settings()
    log = os.environ.get("KLODE_KIT_FAKE_LOG") or home.get("log")
    if log:
        entry = {"tool": tool, "argv": args, "argv_sha": sha, "cwd": os.getcwd(), "pid": os.getpid()}
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    default_dir = Path(__file__).resolve().parents[2] / "fixtures" / "replay"
    replay_dir = Path(os.environ.get("KLODE_KIT_REPLAY_DIR") or home.get("replay_dir") or default_dir)
    scenario = replay_dir / tool / f"{sha}.json"
    if not scenario.is_file():
        scenario = _recording(tool, args, home) or scenario
    if not scenario.is_file():
        sys.stderr.write(f"fake {tool}: no replay scenario for argv sha {sha} (looked in {replay_dir / tool})\n")
        return NO_SCENARIO_EXIT

    data = json.loads(scenario.read_text(encoding="utf-8"))
    sys.stdout.buffer.write(data.get("stdout", "").encode("utf-8"))
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(data.get("stderr", "").encode("utf-8"))
    sys.stderr.buffer.flush()
    return int(data.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
