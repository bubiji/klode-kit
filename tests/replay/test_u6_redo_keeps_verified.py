"""WI-08d / 04 U6: a redone OCR that fails never moves an old verified record (matrix 08d-06; design 02 r4.1 "U6
测试"; K3).

Flow on ``fx-redo`` (one lab, 2026-10-21), every stage the real public entry, engine outputs replayed:

- before: attempt t1 (four routes, codex reads 126 on row 0) -> kb-audit ``a1`` -> human click -> writeback:
  record X ``effective r1 verified (human)``; views rebuilt;
- action: attempt t2 (codex has no file for p001 -> ``route_missing_page``) -> kb-audit ``a2`` -> writeback ingest
  -> views rebuilt;
- continuation: attempt t3 (four routes agree) -> kb-audit ``a3`` -> writeback -> views rebuilt.

r4.1 assertions: (1) ``effective == {rev 1, a1, verified}``, ``candidates[-1] == {rev 2, a2, disputed}``; (2)
``verified/2026-10-21-lab.md`` link target and sha unchanged; (3) the export of X unchanged, ``export_map.rev == 1``;
(4) a build binds ``{1, a1}``, X not excluded, X in ``stale_candidates``. Continuation: ``effective == r3`` and the
views follow. ``_rev/X.r1.md`` never changes. (3) and (4) need ``records_to_txt`` / ``build`` (WI-01a / WI-09b):
strict xfails until then.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _support import views_fx as V
from _support import writeback_fx as W
from _support.confirm_fx import audit
from _support.flows import base_env

pytestmark = pytest.mark.contract_replay

VIEW = "verified/2026-10-21-lab.md"
RECORD_VIEW = "records/2026-10-21-lab.md"


def _before(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    b, a1 = W.audited(project, W.REDO)
    choices = W.export(project, b, a1, W.picks(a1))
    code, _, err = W.submit(project, choices, tmp_path)
    assert code == 0, err
    (x,) = W.to_confirm(a1)
    V.views_ok(project)
    return project, b, a1, x, choices


def _redo(project: Path, b: str, engine: str, seq: int) -> Path:
    W.batch(project, W.REDO, engine=engine, seq=seq)
    audit(project, b)
    return W.revisions(project / "work" / b)[-1]


def _sha_of(project: Path, rel: str) -> str:
    return W.sha(project / rel)


def test_u6_redo_keeps_verified(tmp_path):
    """08d-06 (S5): r4.1 (1), (2) and the continuation; ``_rev/X.r1.md`` byte-identical throughout; ``a1``'s
    fixes only appended to; the t1 route files untouched; a stale click from the old page is refused."""
    project, b, a1, x, old_choices = _before(tmp_path)
    bdir = project / "work" / b
    r1 = W.body_path(project, x, 1)
    r1_sha = W.sha(r1)
    links0 = V.links(project)
    assert links0[VIEW] == V.verified_link(x, 1) and links0[RECORD_VIEW] == V.body_link(x, 1)
    view_sha0 = _sha_of(project, VIEW)
    fixes0 = (a1 / "fixes.jsonl").read_bytes()
    t1_files = {k: v for k, v in W.tree(bdir / "ocr").items()}
    assert t1_files

    # action: the redo with codex missing p001
    a2 = _redo(project, b, "engine_t2", 2)
    assert a2.name == "a2"
    code, _, err = W.submit(project, None, tmp_path, batch_id=b)
    assert code == 0, err  # the record's (effective) status is still verified
    V.views_ok(project)
    front = W.front(project, x)
    # (1)
    assert front["effective"] == {"rev": 1, "audit_rev": f"{b}/a1", "status": "verified", "verified_by": "human"}
    last = front["candidates"][-1]
    assert (last["rev"], last["audit_rev"], last["status"]) == (2, f"{b}/a2", "disputed")
    assert any(u["reason"] == "route_missing_page" and u.get("engine") == "codex" for u in last["uncertain"])
    assert front["status"] == "verified"
    # (2)
    assert V.links(project) == links0
    assert _sha_of(project, VIEW) == view_sha0
    assert W.sha(r1) == r1_sha
    # U6: new attempt directories, the old ones untouched; fixes only appended to
    now = W.tree(bdir / "ocr")
    assert {k: now[k] for k in t1_files} == t1_files
    assert any(".t2/" in k for k in now)
    assert (a1 / "fixes.jsonl").read_bytes().startswith(fixes0)
    # verified stays until a new confirm page is clicked: a click from the old page is stale and refused
    code, _, err = W.submit(project, old_choices, tmp_path)
    assert code == 30, err
    V.views_ok(project)
    assert V.links(project) == links0 and _sha_of(project, VIEW) == view_sha0

    # continuation: codex repaired; the new audit alone moves nothing
    a3 = _redo(project, b, "engine_t3", 3)
    assert a3.name == "a3"
    V.views_ok(project)
    assert V.links(project) == links0 and _sha_of(project, VIEW) == view_sha0
    assert W.to_confirm(a3) == {}  # the four routes agree: the a3 page has nothing left to click
    code, _, err = W.submit(project, None, tmp_path, batch_id=b)
    assert code == 0, err
    V.views_ok(project)
    front = W.front(project, x)
    assert front["effective"] == {"rev": 3, "audit_rev": f"{b}/a3", "status": "verified", "verified_by": "auto"}
    assert V.links(project) == {RECORD_VIEW: V.body_link(x, 3), VIEW: V.verified_link(x, 3)}
    assert _sha_of(project, VIEW) == W.sha(W.body_path(project, x, 3)) != view_sha0
    assert W.sha(r1) == r1_sha
    assert (a1 / "fixes.jsonl").read_bytes().startswith(fixes0)


# --------------------------------------------------------------------------- (3) and (4): need WI-01a / WI-09b


def _build(project: Path) -> dict:
    res = subprocess.run([sys.executable, "-m", "klode_kit", "build", "--project", str(project)], env=base_env(),
                         capture_output=True, timeout=300)
    assert res.returncode in (0, 10), res.stderr.decode()
    receipts = sorted(project.rglob("build_receipt.json"), key=os.path.getmtime)
    assert receipts
    return {"dir": receipts[-1].parent, "receipt": json.loads(receipts[-1].read_text(encoding="utf-8"))}


def _export_of(build_dir: Path, x: str) -> tuple[list[str], list[dict]]:
    # user-approved 2026-10-24: export txt lives under library/ (design r3.2, klode library.toml)
    lines = [ln for p in sorted((build_dir / "library").glob("**/*.txt"))
             for ln in p.read_text(encoding="utf-8").splitlines() if x in ln]
    rows = [json.loads(ln) for ln in (build_dir / "src" / "export_map.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    return lines, [r for r in rows if r.get("rec_id") == x]


@pytest.mark.xfail(strict=True, reason="依赖 WI-01a / WI-09b（records_to_txt、build 尚未实现）")
def test_u6_export_and_build_bind_old_revision(tmp_path):
    """08d-06 (S5), r4.1 (3) and (4): after the failed redo the export lines of X are byte-identical and
    ``export_map.rev == 1``; the build binds ``{rev 1, a1}``, X is not excluded and is listed in
    ``stale_candidates``."""
    project, b, a1, x, _ = _before(tmp_path)
    first = _build(project)
    lines0, _ = _export_of(first["dir"], x)
    assert lines0
    _redo(project, b, "engine_t2", 2)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 0
    second = _build(project)
    lines, rows = _export_of(second["dir"], x)
    assert lines == lines0
    assert rows and {r["rev"] for r in rows} == {1}
    rec = second["receipt"]
    assert rec["audit_bindings"][x] == {"rev": 1, "audit_rev": f"{b}/a1"}
    assert x not in rec.get("excluded", [])
    assert x in [s["rec_id"] for s in rec.get("stale_candidates", [])]
