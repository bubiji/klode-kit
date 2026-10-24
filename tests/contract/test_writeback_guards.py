"""WI-08c: further guards and paths of the writeback (added while implementing, after the recorded red; matrix
08c-* "invalid input / guard clauses" category). Observable results only: exit codes, record files, decisions.
"""

from __future__ import annotations

import json
import os
import signal

import pytest

from _support import writeback_fx as W
from _support.flows import base_env, writeback_setup
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import provenance as P

pytestmark = pytest.mark.contract_replay


def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.mark.parametrize("text", ["no front matter at all\n", "---\n[1, 2]\n---\n"])
def test_corrupt_record_front_matter_is_refused_not_rewritten(tmp_path, text):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    rec_id = W.candidates(adir)[0]["rec_id"]
    path = W.record_path(project, rec_id)
    path.write_text(text, encoding="utf-8")
    _, a2 = W.audited(project, W.U8, seq=2)  # a new audit revision makes the ingest read every record
    code, _, err = W.submit(project, None, tmp_path, batch_id=b)
    assert code == 30, err
    assert path.read_text(encoding="utf-8") == text


def test_incomplete_newest_revision_is_usage(tmp_path):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    choices = W.export(project, b, adir, W.picks(adir))
    (adir.parent / "a2").mkdir()
    before = W.tree_sha(project)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 2
    assert W.submit(project, choices, tmp_path)[0] == 2
    assert W.tree_sha(project) == before


def test_batch_without_receipt_gets_default_type_and_no_date(tmp_path):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    receipt = project / "work" / b / "receipt.json"
    os.rename(receipt, receipt.with_name("receipt.moved.json"))
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    front = W.front(project, W.candidates(adir)[0]["rec_id"])
    assert (front["type"], front["date"]) == ("doc", None)


def test_resubmit_with_manual_and_dropped_is_unchanged(tmp_path):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    sel = W.picks(adir)
    sel[0] = {"field_id": sel[0]["field_id"], "kind": "manual", "value": "6.4"}
    sel[1] = {"field_id": sel[1]["field_id"], "kind": "none", "reason": "not_a_field"}
    choices = W.export(project, b, adir, sel)
    assert W.submit(project, choices, tmp_path)[0] == 0
    before = W.state(project, b)
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 0, err
    assert {r["result"] for r in receipt["per_record"]} == {"unchanged"}
    assert W.state(project, b) == before


def test_route_outside_the_revision_rejected(tmp_path):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    choices = W.forged(W.export(project, b, adir, W.picks(adir)))
    choices["items"][0]["choice"] = {"kind": "route", "route": "pdftext"}
    code, _, err = W.submit(project, choices, tmp_path)
    assert code == 30 and "pdftext" in err
    assert W.front(project, choices["items"][0]["rec_id"]) is None  # the other record commits; this one never


def test_newest_revision_without_confirm_page_exit30(tmp_path):
    project = proj(tmp_path)
    b, a1 = W.audited(project, W.REDO)
    html = W.page(project, b, a1)
    _, a2 = W.audited(project, W.REDO, engine="engine_t3", seq=3)
    runs = json.loads((a2 / "runs.json").read_text(encoding="utf-8"))
    choices = W.forged(W.export(project, b, a1, W.picks(a1)))
    choices["audit_rev"] = f"{b}/a2"
    choices["fingerprints"] = {e: r for e, r in runs.items() if r}
    choices["confirm_sha256"] = CP.confirm_sha256(html)
    before = W.tree_sha(project)
    code, _, err = W.submit(project, choices, tmp_path)
    assert code == 30 and "confirm page" in err
    assert W.tree_sha(project) == before


def test_pending_commit_never_overwrites_a_foreign_body(tmp_path):
    """A revision body is written once: if the path of a pending commit's body is taken by other bytes, the
    recovery refuses (exit 30) and leaves both the foreign file and the record untouched."""
    project, b, adir, choices = writeback_setup(tmp_path / "p")
    res = W.cli(project, choices, base_env({"KLODE_KIT_CRASH_AT": "writeback.fixes_appended"}))
    assert res.returncode == -signal.SIGKILL
    (rec_id,) = {r["field_id"].split("/")[0] for r in W.fixes(adir)}
    body = W.body_path(project, rec_id, 1)
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text("---\n{\"foreign\": true}\n---\n", encoding="utf-8")
    again = W.cli(project, choices, base_env())
    assert again.returncode == 30, again.stderr.decode()
    assert body.read_text(encoding="utf-8") == "---\n{\"foreign\": true}\n---\n"
    assert W.front(project, rec_id) is None


def test_manual_on_cloud_only_page_takes_the_crop_box(tmp_path):
    """No geometric route read the page (claude and codex only): a typed value's evidence.region is the box of
    the cell's crop on its registered derived image; the products still pass provenance segments 1-2."""
    project = proj(tmp_path)
    b, bdir = W.batch(project, W.U8)
    rec = json.loads((bdir / "receipt.json").read_text(encoding="utf-8"))
    rec["routes"] = ["claude", "codex"]
    rec["attempts"] = {r: rec["attempts"][r] for r in rec["routes"]}
    for page in rec["pages"]:
        page["routes"] = {r: page["routes"][r] for r in rec["routes"]}
    (bdir / "receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    from _support.confirm_fx import audit

    audit(project, b)
    adir = W.revisions(bdir)[-1]
    sel = W.picks(adir)
    sel[0] = {"field_id": sel[0]["field_id"], "kind": "manual", "value": "6.2"}
    code, _, err = W.submit(project, W.export(project, b, adir, sel), tmp_path)
    assert code == 0, err
    manual = next(r for r in W.fixes(adir) if r["choice"] == "manual")
    field = next(f for c in W.candidates(adir) for f in c["fields"] if f["field_id"] == manual["field_id"])
    crops = W.jsonl(adir / "crops" / f"{field['crop'].rsplit('/', 1)[1].rsplit('-r', 1)[0]}.jsonl")
    crop = next(c for c in crops if c["path"] == field["crop"])
    assert manual["evidence"]["region"] == {"der_sha256": crop["source_der_sha256"], "bbox_px": crop["box_px"]}
    nobuild = tmp_path / "nobuild"
    nobuild.mkdir()
    (nobuild / "build_receipt.json").write_text(json.dumps({"audit_bindings": {}}), encoding="utf-8")
    layout = P.Layout(root=project, manifest=[bdir / "manifest.jsonl"], derived=[bdir / "derived.jsonl"],
                      ocr_dirs=[bdir / "ocr"], audit_dir=bdir, records_dir=project / "records", build_dir=nobuild,
                      artifact_base=project)
    assert P.validate(project, layout) == []
