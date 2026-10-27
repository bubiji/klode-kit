"""U6 (04 section 6): redo the OCR of a historical batch - ``kb-ocr --batch archive/2025-q3/ --routes
vision,mlx,claude,codex`` (``--redo``), then ``kb-audit``.

What the user sees (04): a new fingerprint directory ``ocr/<engine>/<new fp>/`` while the old one is untouched;
``fixes.jsonl`` is only appended to; the old ``verified/*.md`` stay ``verified`` until the new confirm page is clicked.

Matrix U6-01 (lane contract_replay, S5): this file goes through the user entries only (``kb-ocr --redo``, ``kb-audit``,
``confirm-page``, ``kb-audit --writeback``, ``views`` as separate processes). The engine-level version that builds the
batch in process is ``tests/replay/test_u6_redo_keeps_verified.py`` (08d-06), which stays.

``fx-redo``: attempt 1 (``engine``): the four routes read the page, codex reads 126 on row 0 - a human clicks and the
record is verified. Attempt 2 (``engine_t2``): codex has no page. Attempt 3 (``engine_t3``): the four routes agree.
(Attempt directories are named ``<fp>.t<seq>``: 04's ``<new fp>`` is the run directory of the new attempt, WI-04b/R-3.)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _support import kb_ocr as K
from _support import writeback_fx as W
from klode_kit.audit import confirm_page as CP

pytestmark = pytest.mark.contract_replay

FX = K.FIXTURES / "fx-redo"
ROUTES = "vision,mlx,claude,codex"
VERIFIED = "verified/2026-10-21-lab.md"
RECORD = "records/2026-10-21-lab.md"


class Archive:
    """One data project, one ``archive/2025-q3/`` inbox; every call is a separate user-entry process."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.project = tmp / "project"
        self.project.mkdir()
        self.inbox = K.make_inbox(tmp / "archive" / "2025-q3", [FX / "images" / "p001.png"])
        self.log = tmp / "calls.jsonl"

    def cli(self, *args: str, engine: str = "engine") -> subprocess.CompletedProcess:
        env = K.kb_ocr_env(FX / engine, self.log)
        return subprocess.run([sys.executable, "-m", "klode_kit", *args], env=env, capture_output=True,
                              cwd=str(self.project), timeout=180)

    def ocr(self, engine: str, *extra: str) -> subprocess.CompletedProcess:
        return self.cli("kb-ocr", "--batch", str(self.inbox), "--project", str(self.project), "--routes", ROUTES,
                        *extra, engine=engine)

    @property
    def batch(self) -> Path:
        return K.only_batch(self.project)

    def audit(self) -> subprocess.CompletedProcess:
        return self.cli("kb-audit", "--batch", self.batch.name, "--project", str(self.project))

    def page_and_choices(self, tmp: Path) -> Path:
        """The confirm page of the newest audit revision and the choices a human gets by clicking one route per field
        (what the page's own exporter returns)."""
        done = self.cli("confirm-page", "--batch", self.batch.name, "--project", str(self.project))
        assert done.returncode == 0, done.stderr.decode()
        adir = W.revisions(self.batch)[-1]
        return W.write_choices(tmp / f"choices-{adir.name}.json", W.export(self.project, self.batch.name, adir,
                                                                           W.picks(adir)))

    def writeback(self, choices: Path | None = None) -> subprocess.CompletedProcess:
        argv = ["kb-audit", "--batch", self.batch.name, "--project", str(self.project), "--writeback"]
        if choices is not None:
            argv.append(str(choices))
        return self.cli(*argv)

    def views(self) -> None:
        assert self.cli("views", "--project", str(self.project)).returncode == 0

    def links(self) -> dict[str, str]:
        return {f"{d}/{p.name}": os.readlink(p) for d in ("records", "verified")
                if (self.project / d).is_dir() for p in sorted((self.project / d).iterdir()) if p.is_symlink()}

    def sha(self, rel: str) -> str:
        return hashlib.sha256((self.project / rel).read_bytes()).hexdigest()

    def attempts(self) -> dict[str, bytes]:
        return W.tree(self.batch / "ocr")


def test_u6_redo_keeps_verified_acceptance(tmp_path):
    a = Archive(tmp_path)
    # --- the first read, the audit, one click: the record is verified
    assert a.ocr("engine").returncode == 0
    assert a.audit().returncode == 10
    old_choices = a.page_and_choices(tmp_path)
    done = a.writeback(old_choices)
    assert done.returncode == 0, done.stderr.decode()
    a.views()
    a1 = W.revisions(a.batch)[-1]
    (rec,) = [c["rec_id"] for c in W.candidates(a1)]
    links0 = a.links()
    assert links0[VERIFIED].endswith(f"{rec}.r1.md") and links0[RECORD].endswith(f"{rec}.r1.md")
    view_sha0 = a.sha(VERIFIED)
    body_r1 = W.body_path(a.project, rec, 1)
    r1_sha = W.sha(body_r1)
    fixes0 = (a1 / "fixes.jsonl").read_bytes()
    old_tree = a.attempts()
    assert old_tree and not any(".t2" in k for k in old_tree)

    # --- the redo: codex has no page this time
    redo = a.ocr("engine_t2", "--redo")
    assert redo.returncode == 0, redo.stderr.decode()
    new_tree = a.attempts()
    assert {k: new_tree[k] for k in old_tree} == old_tree  # the old attempt directories: not one byte moved
    new_dirs = sorted({k.split("/")[1] for k in new_tree if k.endswith(".t2/") or ".t2/" in k})
    assert new_dirs and all(d.endswith(".t2") for d in new_dirs)
    assert {d.split(".")[0] for d in new_dirs} == {k.split("/")[1].split(".")[0] for k in old_tree if ".t1" in k}
    assert a.audit().returncode in (0, 10)
    assert W.revisions(a.batch)[-1].name == "a2"
    assert a.writeback().returncode == 0
    a.views()
    # --- verified stays: same links, same bytes, the old revision body untouched, fixes only appended to
    assert a.links() == links0 and a.sha(VERIFIED) == view_sha0
    assert W.sha(body_r1) == r1_sha
    assert (a1 / "fixes.jsonl").read_bytes().startswith(fixes0)
    front = W.front(a.project, rec)
    assert front["effective"]["rev"] == 1 and front["effective"]["status"] == "verified"
    assert front["candidates"][-1]["status"] == "disputed"  # the failed redo is a candidate, not the truth
    # a click from the old page (answering the old reading) is refused and changes nothing
    stale = a.writeback(old_choices)
    assert stale.returncode == 30, stale.stderr.decode()
    a.views()
    assert a.links() == links0 and a.sha(VERIFIED) == view_sha0

    # --- a good redo: the four routes agree; only now the view moves (to the new revision), the old one is kept
    assert a.ocr("engine_t3", "--redo").returncode == 0
    assert a.audit().returncode == 0
    assert a.writeback().returncode == 0
    a.views()
    assert a.attempts().keys() >= new_tree.keys()
    assert {k: v for k, v in a.attempts().items() if k in old_tree} == old_tree
    front = W.front(a.project, rec)
    assert front["effective"]["rev"] == 3 and front["effective"]["verified_by"] == "auto"
    assert a.links()[VERIFIED].endswith(f"{rec}.r3.md")
    assert a.sha(VERIFIED) != view_sha0 and W.sha(body_r1) == r1_sha
    assert (a1 / "fixes.jsonl").read_bytes().startswith(fixes0)
    # the redo never made a cloud call: the routes were the replay seam (zero real claude/codex processes)
    assert {c["tool"] for c in K.log_calls(a.log)} <= {"vision", "mlx", "claude", "codex"}
