"""WI-08e / 04 U5 / design r3.1: the whole U5 premise through public entry points only (matrix 08e-20).

fx-manualfix: all four routes read ``10.5``, the image says ``1.05``. The routes agree, so the field is verified(auto)
at r1 through the real stages. ``tests/cli/test_records_to_txt_klode.py`` could only write the human correction in the
writeback's on-disk format by hand ("no public stage lets a human override an auto-agreed field yet"); here the
correction goes through ``kb-audit --reopen`` -> the reopen page -> ``kb-audit --writeback`` -> ``records_to_txt``, and
the real ``klode verify`` (klode 0.5.0) finds ``示例片 1.05 2026-09-18`` while ``10.5`` is nowhere. A missing ``klode``
FAILS (never skips).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import export_fx as X
from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.reopen_fx import HB, NEW_HB
from klode_kit.audit import confirm_page as CP
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.cli_integration

MANUALFIX = X.FIXTURES / "fx-manualfix"
PHRASE = "示例片 1.05 2026-09-18"


def _klode(build: Path, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    return subprocess.run([exe, "-c", str(build / "library.toml"), *args], capture_output=True, text=True,
                          timeout=120)


def test_u5_after_reopen_and_manual_fix(tmp_path):
    """08e-20 (S3): reopen -> 1.05 -> export (rev 2) -> real klode finds the corrected phrase, never the misreading."""
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = X.pipeline(project, MANUALFIX)
    (rec,) = [c["rec_id"] for c in W.candidates(adir)]
    assert W.front(project, rec)["effective"] == {"rev": 1, "audit_rev": f"{b}/a1", "status": "verified",
                                                  "verified_by": "auto"}
    fid = next(f for f, v in W.body(project, rec, 1)["fields"].items() if v == "10.5")

    code, rc, err = RF.reopen(project, fid)
    assert code == 10, err
    code, receipt, err = RF.decide(project, RF.choose(project, rc, "manual", value="1.05"), tmp_path / "w")
    assert code == 0, err
    assert W.front(project, rec)["effective"]["verified_by"] == "human"
    assert W.body_path(project, rec, 1).is_file()                       # the misreading's revision is kept

    build = tmp_path / "build"
    res = R.export(project, build, kb_id="fx-manualfix-reopen")
    assert res["audit_bindings"][rec]["rev"] == 2
    row = next(r for r in res["export_map"] if r["source_file"] == "library/items/sample-tablet.txt")
    assert (row["value_audited"], row["rev"], row["text"]) == ("1.05", 2, f"{PHRASE} ⟨{rec}⟩")

    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    init = subprocess.run([exe, "init", "--shelf", "items", "--shelf", "records", str(build)], capture_output=True,
                          text=True, timeout=120)
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


@pytest.mark.needs_node
def test_node_exporter_agrees_with_python_on_the_reopen_page(tmp_path):
    """08e-02 (S1, paired real path): the page's own exporter run by node (the script the user's browser runs) prints
    the choices the Python exporter gives, with the reopen page's sealed sha."""
    node = shutil.which("node")
    assert node is not None, "node is required by the cli_integration lane (needs_node): install node"
    base = RF.build(tmp_path / "b")
    code, rc, err = RF.reopen(base.project, HB)
    assert code == 10, err
    page = base.project / rc["page"]
    clicks = [{"field_id": HB, "kind": "manual", "value": NEW_HB}]
    sel = tmp_path / "selections.json"
    sel.write_text(json.dumps(clicks, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run([node, str(CP.EXPORT_JS), str(page), str(sel)], capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode()
    assert json.loads(out.stdout) == CP.export_choices(page.read_bytes(), clicks)
    assert json.loads(out.stdout)["confirm_sha256"] == rc["confirm_sha256"]
