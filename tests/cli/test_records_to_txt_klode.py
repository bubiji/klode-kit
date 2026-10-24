"""WI-01a / 04 U5 / R3-07: after a manual fix the exported source carries the audited value, and the real
``klode verify <card> <phrase>`` (klode 0.5.0) finds it while the four routes' common misreading is nowhere
(matrix 01a-10, L3 S3). A missing ``klode`` FAILS (never skips).

fx-manualfix: the four routes all read ``10.5`` (agree -> verified(auto) at r1 through the real stages); the image
says ``1.05``. No public stage lets a human override an auto-agreed field yet, so the human manual decision is
written here in the writeback's on-disk format: decision line in the audit revision's ``fixes.jsonl``, body r2
(``verified_by: human``), the record's ``effective`` moved to r2 and r1 ``superseded_by: 2``.

klode 0.5.0 prints the verdict of a found phrase as ``FOUND`` (``--json``: ``resolution: found``, exit 0); the
word ``VERIFIED`` is klode-mcp's rendering of the same result (11a). Not found: ``NOT-FOUND`` / ``not-found``,
exit 1.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import export_fx as X
from _support import writeback_fx as W
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.cli_integration

MANUALFIX = X.FIXTURES / "fx-manualfix"
PHRASE = "示例片 1.05 2026-09-18"
DER = json.loads((MANUALFIX / "scenario.json").read_text(encoding="utf-8"))["pages"][0]["der_sha256"]


def _manual_fix(project: Path, adir: Path, rec: str) -> None:
    front, body = W.front(project, rec), W.body(project, rec, 1)
    fid = next(f for f, v in body["fields"].items() if v == "10.5")
    audit_rev = body["audit_rev"]
    decision = {"decision_id": f"{audit_rev}/d9001", "commit_id": f"{audit_rev}/c9001", "audit_rev": audit_rev,
                "field_id": fid, "rev_base": 1, "rev_new": 2, "by": "human", "choice": "manual", "value": "1.05",
                "at": "2026-10-24T00:00:00Z",
                "evidence": {"region": {"der_sha256": DER, "bbox_px": [330, 282, 383, 302]},
                             "seen": {"vision": "10.5", "mlx": "10.5", "claude": "10.5", "codex": "10.5"}}}
    with (adir / "fixes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(decision, ensure_ascii=False) + "\n")
    body2 = {**body, "rev": 2, "commit_id": decision["commit_id"], "verified_by": "human",
             "fields": {**body["fields"], fid: "1.05"}}
    W.body_path(project, rec, 2).write_text("---\n" + json.dumps(body2, ensure_ascii=False, indent=1) + "\n---\n",
                                             encoding="utf-8")
    front["candidates"][0]["superseded_by"] = 2
    front["candidates"].append({"rev": 2, "audit_rev": audit_rev, "status": "verified", "verified_by": "human",
                                "uncertain": [], "created": "2026-10-24T00:00:00Z"})
    front["effective"] = {"rev": 2, "audit_rev": audit_rev, "status": "verified", "verified_by": "human"}
    path = W.record_path(project, rec)
    path.write_text("---\n" + json.dumps(front, ensure_ascii=False, indent=1) + "\n---\n", encoding="utf-8")


def _klode(build: Path, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    return subprocess.run([exe, "-c", str(build / "library.toml"), *args], capture_output=True, text=True,
                          timeout=120)


def test_u5_after_manual_fix(tmp_path):
    """01a-10 (S3): the manual fix 10.5 -> 1.05 is exported (``value_audited`` 1.05, rev 2); the real klode finds
    ``示例片 1.05 2026-09-18`` on the item card and the record card, and ``10.5`` on neither."""
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = X.pipeline(project, MANUALFIX)
    (rec,) = [c["rec_id"] for c in W.candidates(adir)]
    _manual_fix(project, adir, rec)

    build = tmp_path / "build"
    res = R.export(project, build, kb_id="fx-manualfix-klode")
    assert res["audit_bindings"][rec]["rev"] == 2
    row = next(r for r in res["export_map"] if r["source_file"] == "library/items/sample-tablet.txt")
    assert (row["value_audited"], row["rev"], row["text"]) == ("1.05", 2, f"{PHRASE} ⟨{rec}⟩")

    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    init = subprocess.run([exe, "init", "--shelf", "items", "--shelf", "records",
                           str(build)], capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, init.stderr
    built = _klode(build, "build")
    assert built.returncode == 0, built.stderr

    for card in ("sample-tablet", rec):
        found = _klode(build, "--json", "verify", card, PHRASE)
        assert found.returncode == 0, found.stdout + found.stderr
        value = json.loads(found.stdout)["value"]
        assert (value["resolution"], value["card"]) == ("found", card)
        assert [ln for _, ln in value["lines"]] == [f"{PHRASE} ⟨{rec}⟩"]
        old = _klode(build, "--json", "verify", card, "10.5")
        assert old.returncode == 1, old.stdout + old.stderr
        assert json.loads(old.stdout)["value"]["resolution"] == "not-found"
    prose = _klode(build, "verify", "sample-tablet", PHRASE)
    assert prose.returncode == 0 and "FOUND" in prose.stdout and "NOT-FOUND" not in prose.stdout
