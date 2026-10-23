"""Helpers for the kb-ocr / run_batch tests (WI-04b, matrix 04b-*, 00s-16 run_batch).

Engine boundary (plan section 0.4): the four routes are replaced by recorded per-page output through the test
seam ``KLODE_KIT_ENGINE_REPLAY=<dir>`` (honoured only when the test seams are active). Layout of a replay dir:

- ``<dir>/<engine>/<page key>.jsonl``: the ``ocr_line`` rows that route returns for that page;
- ``<dir>/<engine>/<page key>.fail``: every call for that page fails; if the file holds an integer ``n``,
  only the first ``n`` calls of the process fail;
- neither file: the route returns no page (``route_missing_page``).

Every replayed call appends ``{"tool": <engine>, "page": <key>, "der_sha256", "pid"}`` to ``$KLODE_KIT_FAKE_LOG``
(the same per-test log the fake claude/codex use).

Page keys are ``p001, p002, ...``: the position of each distinct image in the inbox, sorted by file name.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping

from _support.flows import REPO, base_env

FIXTURES = REPO / "tests" / "fixtures"
BIN = REPO / "bin" / "klode-kit"
ROUTES = ("vision", "mlx", "claude", "codex")
VOLATILE = ("started", "ended")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kb_ocr_env(replay: Path, log: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = base_env({"KLODE_KIT_ENGINE_REPLAY": str(replay), "KLODE_KIT_FAKE_LOG": str(log)})
    env["KLODE_KIT_TEST_SEAMS"] = "1"
    env.pop("KLODE_KIT_REAL", None)
    if extra:
        env.update(extra)
    return env


def run_kb_ocr(project: Path, inbox: Path, env: Mapping[str, str], *args: str, timeout: float = 120,
               launcher: bool = False) -> subprocess.CompletedProcess:
    argv = ["kb-ocr", "--batch", str(inbox), "--project", str(project), *args]
    cmd = [str(BIN), *argv] if launcher else [sys.executable, "-m", "klode_kit", *argv]
    return subprocess.run(cmd, env=dict(env), capture_output=True, timeout=timeout, cwd=str(project))


def batch_dirs(project: Path) -> list[Path]:
    work = project / "work"
    if not work.is_dir():
        return []
    return sorted(p for p in work.iterdir() if p.is_dir())


def only_batch(project: Path) -> Path:
    dirs = batch_dirs(project)
    assert len(dirs) == 1, dirs
    return dirs[0]


def receipt(project: Path) -> dict:
    return json.loads((only_batch(project) / "receipt.json").read_text(encoding="utf-8"))


def attempt_dirs(batch: Path, engine: str) -> list[Path]:
    root = batch / "ocr" / engine
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def jsonl(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n"), f"{path} does not end with a newline"
    return [json.loads(line) for line in raw.splitlines()]


def log_calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` (relative posix path -> bytes); directories as ``<path>/`` -> b""."""
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            out[Path(dirpath, d).relative_to(root).as_posix() + "/"] = b""
        for f in filenames:
            p = Path(dirpath, f)
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for rel, data in sorted(tree(root).items()):
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def normalised(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """Drop the wall-clock fields of JSON receipts so two runs can be compared byte for byte."""
    out = {}
    for rel, data in files.items():
        if rel.endswith(".json") and data:
            obj = json.loads(data)
            if isinstance(obj, dict) and any(k in obj for k in VOLATILE):
                obj = {k: v for k, v in obj.items() if k not in VOLATILE}
                data = json.dumps(obj, sort_keys=True).encode("utf-8")
        out[rel] = data
    return out


def make_inbox(dest: Path, images: Iterable[Path], names: Iterable[str] | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    images = list(images)
    names = list(names) if names is not None else [p.name for p in images]
    for src, name in zip(images, names):
        shutil.copyfile(src, dest / name)
    return dest


def make_replay(dest: Path, plan: Mapping[tuple[str, str], tuple[str, Path | None]]) -> Path:
    """``plan[(engine, key)] = (outcome, source jsonl)``; outcome is ``ok``, ``missing``, ``fail`` or
    ``flaky<n>`` (the first n calls fail, then the source rows are returned)."""
    for (engine, key), (outcome, src) in plan.items():
        d = dest / engine
        d.mkdir(parents=True, exist_ok=True)
        if outcome in ("ok",) or outcome.startswith("flaky"):
            assert src is not None
            shutil.copyfile(src, d / f"{key}.jsonl")
        if outcome == "fail":
            (d / f"{key}.fail").write_text("", encoding="utf-8")
        elif outcome.startswith("flaky"):
            (d / f"{key}.fail").write_text(outcome[len("flaky"):], encoding="utf-8")
    dest.mkdir(parents=True, exist_ok=True)
    return dest
