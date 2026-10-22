"""Replay-scenario helpers for tests/fakes/bin/{claude,codex}.

Contract: a scenario for ``<tool> <args...>`` lives at
``$KLODE_KIT_REPLAY_DIR/<tool>/<argv_sha>.json`` where ``argv_sha`` is the sha256 of the
compact UTF-8 JSON array of ``args`` (program name excluded). The file holds
``{"stdout": str, "stderr": str, "exit_code": int}``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def argv_sha(args: list[str]) -> str:
    blob = json.dumps(list(args), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def write_scenario(replay_dir: Path, tool: str, args: list[str], stdout: str, *, stderr: str = "", exit_code: int = 0) -> Path:
    target = replay_dir / tool / f"{argv_sha(args)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"stdout": stdout, "stderr": stderr, "exit_code": exit_code}, ensure_ascii=False))
    return target


def log_entries(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
