"""Helpers for the WI-08e tests (``kb-audit --reopen``; spec ``.claude/tdd-guardian/wi-08e-spec.md``).

The base project is the one the public stages leave for ``fx-u1-3labs``: kb-ocr batch -> ``kb-audit`` -> the writeback
ingest. Its record ``AUTO_REC`` is ``verified(auto)`` at r1 (four routes agree on every field), the other two records
wait for a human (``pending_confirm``). Nothing here writes ``records/``: that is the writeback's alone.

``template`` builds the base project once per module, ``base`` copies it for each test.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from _support import writeback_fx as W
from _support.flows import base_env
from klode_kit.audit import confirm_page as CP

AUTO_REC = "r-f88e621f4921"            # verified(auto) at r1: all four routes agree
PENDING_REC = "r-0d58f9bc1980"         # pending_confirm at r1: waits for a human
HB = f"{AUTO_REC}/r005-number-0"       # haemoglobin value: every route reads 119
PLT = f"{AUTO_REC}/r008-number-0"      # platelet value: every route reads 210
OLD_HB, NEW_HB = "119", "11.9"
ROUTES = ("vision", "mlx", "claude", "codex")


@dataclass
class Base:
    project: Path
    batch: str
    adir: Path


def build(root: Path) -> Base:
    root.mkdir(parents=True, exist_ok=True)
    project = root / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.LABS3)
    code, _, err = W.submit(project, None, root, batch_id=b)
    assert code == 10, err
    return Base(project, b, adir)


def copy(tpl: Base, dest: Path) -> Base:
    dest.mkdir(parents=True, exist_ok=True)
    project = dest / "proj"
    shutil.copytree(tpl.project, project, symlinks=True)
    return Base(project, tpl.batch, project / tpl.adir.relative_to(tpl.project))


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Base:
    return build(tmp_path_factory.mktemp("reopen-template"))


@pytest.fixture()
def base(template: Base, tmp_path: Path) -> Base:
    return copy(template, tmp_path / "base")


# --------------------------------------------------------------------------- running reopen


def reopen(project: Path, field_id: str, *, batch: str | None = None, reason: str | None = None):
    """In-process ``reopen.run``; ``(exit code, receipt printed on stdout, stderr text)``."""
    from klode_kit.audit import reopen as RO

    err, out = io.StringIO(), io.StringIO()
    code = RO.run(project, field_id, batch_id=batch, reason=reason, out=err, stdout=out)
    lines = [x for x in out.getvalue().splitlines() if x.strip()]
    return code, (json.loads(lines[-1]) if lines else None), err.getvalue()


def argv(project: Path, field_id: str, *extra: str) -> list[str]:
    return [sys.executable, "-m", "klode_kit", "kb-audit", "--project", str(project), "--reopen", field_id, *extra]


def cli(project: Path, field_id: str, env: Mapping[str, str] | None = None, *extra: str,
        timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv(project, field_id, *extra), env=dict(env or base_env()), capture_output=True,
                          timeout=timeout)


def receipt_of(res: subprocess.CompletedProcess) -> dict:
    lines = [x for x in res.stdout.decode().splitlines() if x.strip()]
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- reading the products


def log(adir: Path) -> list[dict]:
    return W.jsonl(adir / "reopen.jsonl")


def log_bytes(adir: Path) -> bytes:
    path = adir / "reopen.jsonl"
    return path.read_bytes() if path.exists() else b""


def pages(adir: Path) -> list[Path]:
    return sorted((adir / "reopen").glob("o*.html")) if (adir / "reopen").is_dir() else []


def page(project: Path, rc: Mapping[str, Any]) -> bytes:
    return (project / rc["page"]).read_bytes()


def choose(project: Path, rc: Mapping[str, Any], kind: str, **kw: Any) -> dict:
    """``choices.json`` for the one field of the reopen page named by receipt ``rc``."""
    return CP.export_choices(page(project, rc), [{"field_id": rc["field_id"], "kind": kind, **kw}])


def decide(project: Path, choices: Mapping[str, Any], where: Path, *, batch: str | None = None):
    """The public writeback on a choices document (``W.submit``)."""
    return W.submit(project, choices, where, batch_id=batch)


def reopen_and_fix(base: Base, field_id: str, value: str, where: Path):
    """Reopen ``field_id``, type ``value``, write back: ``(receipt of the reopen, writeback result)``."""
    code, rc, err = reopen(base.project, field_id)
    assert code == 10, err
    return rc, decide(base.project, choose(base.project, rc, "manual", value=value), where)


def values(project: Path, rec: str, rev: int) -> dict[str, str]:
    return W.body(project, rec, rev)["fields"]


def project_files(project: Path) -> dict[str, bytes]:
    from _support.kb_ocr import tree

    return {k: v for k, v in tree(project).items() if not k.endswith("/")}
