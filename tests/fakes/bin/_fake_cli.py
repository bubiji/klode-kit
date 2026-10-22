"""Replay fake behind tests/fakes/bin/{claude,codex} (tests only; never a real model call).

Usage (via the sh wrappers): ``_fake_cli.py <tool> <args...>``.

- Every invocation appends one JSON line to ``$KLODE_KIT_FAKE_LOG`` (when set):
  ``{"tool", "argv", "argv_sha", "cwd", "pid"}``.
- The scenario is ``$KLODE_KIT_REPLAY_DIR/<tool>/<argv_sha>.json`` (default replay dir:
  ``tests/fixtures/replay``), with ``argv_sha`` = sha256 of the compact UTF-8 JSON array of
  the arguments. It holds ``{"stdout": str, "stderr": str, "exit_code": int}``; stdout and
  stderr are replayed byte for byte.
- No scenario -> exit 97, ``no replay scenario`` on stderr, nothing on stdout.
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


def main() -> int:
    tool, args = sys.argv[1], sys.argv[2:]
    sha = argv_sha(args)
    log = os.environ.get("KLODE_KIT_FAKE_LOG")
    if log:
        entry = {"tool": tool, "argv": args, "argv_sha": sha, "cwd": os.getcwd(), "pid": os.getpid()}
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    default_dir = Path(__file__).resolve().parents[2] / "fixtures" / "replay"
    replay_dir = Path(os.environ.get("KLODE_KIT_REPLAY_DIR") or default_dir)
    scenario = replay_dir / tool / f"{sha}.json"
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
