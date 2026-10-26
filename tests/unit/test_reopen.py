"""WI-08e: ``kb-audit --reopen <field_id>`` and the writeback of its page - unit lane (matrix 08e-01 .. 08e-10, 08e-13,
08e-14, 08e-16, 08e-18, 08e-22, 08e-24; spec ``.claude/tdd-guardian/wi-08e-spec.md``).

The project is the one the public stages leave for ``fx-u1-3labs`` (``_support/reopen_fx``): record ``AUTO_REC`` is
``verified(auto)`` at r1 because all four routes read ``119`` for the haemoglobin value; the two other records wait for
a human. Everything is asserted on files (the data project's tree), exit codes and receipts, never on internal calls.

Law: a reopen changes nothing the project already knows (it appends one log line and writes one page), reopening is
idempotent, and the commit that follows replaces exactly the reopened field (08e-22 is the S4 case).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.kb_ocr import tree, tree_sha
from _support.reopen_fx import (AUTO_REC, HB, NEW_HB, OLD_HB, PENDING_REC, PLT, ROUTES, base,  # noqa: F401
                                template)
from klode_kit.audit import confirm_page as CP
from klode_kit.audit import views as V
from klode_kit.build import records_to_txt as R
from klode_kit.contract import schema

pytestmark = pytest.mark.unit


def records_tree(project: Path) -> dict[str, bytes]:
    return tree(project / "records") if (project / "records").is_dir() else {}


# --------------------------------------------------------------------------- 08e-01


def test_reopen_verified_auto_field_opens_a_page(base, tmp_path):
    """08e-01 (S1): the reopened field is logged, its page is written read-only, and nothing the records know changes."""
    p, b, adir = base.project, base.batch, base.adir
    before = records_tree(p)
    front_before = W.front(p, AUTO_REC)

    code, rc, err = RF.reopen(p, HB)

    assert code == 10, err
    assert (rc["result"], rc["exit_code"]) == ("opened", 10)
    assert (rc["field_id"], rc["rec_id"], rc["batch_id"], rc["audit_rev"]) == (HB, AUTO_REC, b, f"{b}/a1")
    assert (rc["reopen_id"], rc["rev_base"], rc["effective_rev"]) == (f"{b}/a1/o0001", 1, 1)
    assert rc["page"] == f"work/{b}/audit/a1/reopen/o0001.html"
    (row,) = RF.log(adir)
    assert W.TS.fullmatch(row.pop("at").encode())
    assert row == {"op": "reopen", "reopen_id": f"{b}/a1/o0001", "field_id": HB, "rec_id": AUTO_REC,
                   "audit_rev": f"{b}/a1", "rev_base": 1, "effective_rev": 1, "value": OLD_HB, "dropped": False,
                   "reason": None, "by": "human", "page": "reopen/o0001.html"}
    page = p / rc["page"]
    assert page.is_file() and os.stat(page).st_mode & 0o222 == 0
    assert rc["confirm_sha256"] == CP.confirm_sha256(page.read_bytes())
    assert records_tree(p) == before                       # records/ not a byte different
    assert W.front(p, AUTO_REC) == front_before
    assert W.front(p, AUTO_REC)["effective"]["rev"] == 1


# --------------------------------------------------------------------------- 08e-02


def test_reopen_page_holds_exactly_the_field(base):
    """08e-02 (S1): the page has one record with the one field, the routes' readings, the current value; sealed; the
    exporter accepts it."""
    code, rc, err = RF.reopen(base.project, HB)
    assert code == 10, err
    html = RF.page(base.project, rc)
    data = CP.page_data(html)
    (rec,) = data["records"]
    assert (rec["rec_id"], rec["rev_base"], rec["clickable"]) == (AUTO_REC, 1, True)
    (field,) = rec["fields"]
    assert field["field_id"] == HB
    assert field["seen"] == {r: OLD_HB for r in ROUTES}
    assert field["item_label"] == "hemoglobin"
    assert data["reopen"] == {"reopen_id": rc["reopen_id"], "field_id": HB, "value": OLD_HB}
    assert (data["batch_id"], data["audit_rev"]) == (base.batch, f"{base.batch}/a1")
    text = html.decode("utf-8")
    assert text.count('class="kk-item"') == 1 and f'data-field-id="{HB}"' in text
    for needle in ('value="manual"', 'value="none:not_a_field"', 'value="none"', 'value="route:vision"', "重开"):
        assert needle in text
    assert OLD_HB in text
    choices = CP.export_choices(html, [{"field_id": HB, "kind": "manual", "value": NEW_HB}])
    assert [(i["field_id"], i["rev_base"], i["choice"]) for i in choices["items"]] == [
        (HB, 1, {"kind": "manual", "value": NEW_HB})]
    assert choices["confirm_sha256"] == rc["confirm_sha256"]


# --------------------------------------------------------------------------- 08e-03


@pytest.mark.parametrize("field_id,code", [
    ("r-000000000000/r005-number-0", 30),     # well formed, no such record
    (f"{AUTO_REC}/r999-number-0", 30),         # the record exists, the slot does not
    (f"{AUTO_REC}/", 2),
    ("not-a-field-id", 2),
    ("", 2),
    (f"{AUTO_REC}/r005 number", 2),
])
def test_unknown_or_malformed_field_id_refused_nothing_written(base, field_id, code):
    """08e-03 (S3): a field that is not there is refused (30), a malformed id is usage (2); the project is untouched."""
    before = tree_sha(base.project)
    got, rc, _ = RF.reopen(base.project, field_id)
    assert got == code
    assert rc["result"] == "refused" and rc["exit_code"] == code and rc["error"]
    assert tree_sha(base.project) == before
    assert RF.log(base.adir) == [] and RF.pages(base.adir) == []


# --------------------------------------------------------------------------- 08e-04


def test_field_of_unverified_record_refused(base):
    """08e-04 (S3): a record nobody verified has nothing to reopen - it belongs on the ordinary confirm page."""
    before = tree_sha(base.project)
    code, rc, _ = RF.reopen(base.project, f"{PENDING_REC}/r005-number-0")
    assert code == 30 and rc["result"] == "refused"
    assert "verified" in rc["error"]
    assert tree_sha(base.project) == before


def test_record_never_written_back_refused(tmp_path):
    """08e-04 (S3): audited but not ingested: there is no record file at all."""
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.LABS3)
    before = tree_sha(project)
    code, rc, _ = RF.reopen(project, HB)
    assert code == 30 and rc["result"] == "refused"
    assert tree_sha(project) == before


def _redo_project(tmp_path: Path, *, ingest_second: bool):
    """fx-redo: a first attempt that verifies r1 (auto), then a second audit revision where codex disagrees."""
    project = tmp_path / "proj"
    project.mkdir()
    b, a1 = W.audited(project, W.REDO, engine="engine_t3", seq=1)
    code, _, err = W.submit(project, None, tmp_path, batch_id=b)
    assert code == 0, err
    b2, a2 = W.audited(project, W.REDO, engine="engine", seq=2)
    assert (b2, a2.name) == (b, "a2")
    if ingest_second:
        code, _, err = W.submit(project, None, tmp_path, batch_id=b)
        assert code == 0, err                                  # r1 is still effective and verified: nothing "awaits"
    rec = W.candidates(a1)[0]["rec_id"]
    return project, b, rec, W.candidates(a1)[0]["fields"][1]["field_id"]


def test_record_with_a_newer_unverified_candidate_refused(tmp_path):
    """08e-04 (S3): r1 is effective but r2 (a2, pending) is newer: confirm it first, a reopen would bury it."""
    project, b, rec, fid = _redo_project(tmp_path, ingest_second=True)
    assert W.front(project, rec)["effective"]["rev"] == 1 and W.front(project, rec)["candidates"][-1]["rev"] == 2
    before = tree_sha(project)
    code, rc, _ = RF.reopen(project, fid)
    assert code == 30 and rc["result"] == "refused" and "newer" in rc["error"]
    assert tree_sha(project) == before


def test_newest_audit_revision_not_yet_written_back_refused(tmp_path):
    """08e-04 (S3): a2 exists but its candidates were never ingested: the reopen would stamp a1 over it."""
    project, b, rec, fid = _redo_project(tmp_path, ingest_second=False)
    before = tree_sha(project)
    code, rc, _ = RF.reopen(project, fid)
    assert code == 30 and rc["result"] == "refused" and "kb-audit --writeback" in rc["error"]
    assert tree_sha(project) == before


# --------------------------------------------------------------------------- 08e-05


def test_dropped_field_can_be_reopened_and_typed(base, tmp_path):
    """08e-05 (S2): a field a human dropped (not_a_field) is reopenable; typing a value moves it from ``dropped`` to
    ``fields``."""
    p, b, adir = base.project, base.batch, base.adir
    sel = W.picks(adir, [PENDING_REC])
    victim = sel[0]["field_id"]
    sel[0] = {"field_id": victim, "kind": "none", "reason": "not_a_field"}
    code, _, err = RF.decide(p, W.export(p, b, adir, sel), tmp_path)
    assert code == 0, err                                      # the exit code covers the records of the choices
    assert W.body(p, PENDING_REC, 2)["dropped"] == [victim] and victim not in values_of(p, PENDING_REC, 2)

    code, rc, err = RF.reopen(p, victim)
    assert code == 10, err
    assert (rc["effective_rev"], rc["rev_base"]) == (2, 2)
    (row,) = RF.log(adir)
    assert (row["dropped"], row["value"]) == (True, None)

    code, _, err = RF.decide(p, RF.choose(p, rc, "manual", value="6.5"), tmp_path / "w")
    assert code in (0, 10), err
    b3 = W.body(p, PENDING_REC, 3)
    assert b3["fields"][victim] == "6.5" and b3["dropped"] == []
    assert {k: v for k, v in b3["fields"].items() if k != victim} == values_of(p, PENDING_REC, 2)


def values_of(project, rec, rev):
    return W.body(project, rec, rev)["fields"]


# --------------------------------------------------------------------------- 08e-06


def test_reopening_twice_is_one_event_and_a_new_one_after_the_commit(base, tmp_path):
    """08e-06 (S5): a pending reopen is reused; once the correction is committed a new reopen is a new event and the
    old event and page stay."""
    p, adir = base.project, base.adir
    code1, rc1, _ = RF.reopen(p, HB)
    after_first = tree_sha(p)
    code2, rc2, _ = RF.reopen(p, HB)
    assert (code1, code2) == (10, 10)
    assert (rc2["result"], rc2["reopen_id"], rc2["page"], rc2["confirm_sha256"]) == (
        "unchanged", rc1["reopen_id"], rc1["page"], rc1["confirm_sha256"])
    assert tree_sha(p) == after_first and len(RF.log(adir)) == 1 and len(RF.pages(adir)) == 1

    first_page = RF.page(p, rc1)
    first_row = RF.log_bytes(adir)
    code, _, err = RF.decide(p, RF.choose(p, rc1, "manual", value=NEW_HB), tmp_path)
    assert code == 0, err
    code3, rc3, err = RF.reopen(p, HB)
    assert code3 == 10, err
    assert rc3["result"] == "opened" and rc3["reopen_id"] == rc1["reopen_id"][:-1] + "2"
    assert (rc3["rev_base"], rc3["effective_rev"]) == (2, 2)
    assert RF.page(p, rc1) == first_page                       # the old page is still the old page
    assert RF.log_bytes(adir).startswith(first_row) and len(RF.log(adir)) == 2
    assert CP.page_data(RF.page(p, rc3))["reopen"]["value"] == NEW_HB   # the new page shows the corrected value


# --------------------------------------------------------------------------- 08e-07


def test_two_fields_of_one_record(base, tmp_path):
    """08e-07 (S5): the page of a stale reopen is refused after the other correction, and a fresh reopen goes on."""
    p, adir = base.project, base.adir
    _, rc_a, _ = RF.reopen(p, HB)
    _, rc_b, _ = RF.reopen(p, PLT)
    assert (rc_a["reopen_id"][-5:], rc_b["reopen_id"][-5:]) == ("o0001", "o0002")
    assert (rc_a["rev_base"], rc_b["rev_base"]) == (1, 1)
    choices_b = RF.choose(p, rc_b, "manual", value="201")
    code, _, err = RF.decide(p, RF.choose(p, rc_a, "manual", value=NEW_HB), tmp_path / "a")
    assert code == 0, err

    before = tree_sha(p)
    code, receipt, err = RF.decide(p, choices_b, tmp_path / "b")
    assert code == 30 and "stale" in err
    assert tree_sha(p) == before

    code, rc_b2, err = RF.reopen(p, PLT)
    assert code == 10 and rc_b2["result"] == "opened" and rc_b2["reopen_id"].endswith("o0003")
    assert (rc_b2["rev_base"], rc_b2["effective_rev"]) == (2, 2)
    code, _, err = RF.decide(p, RF.choose(p, rc_b2, "manual", value="201"), tmp_path / "c")
    assert code == 0, err
    r1, r3 = values_of(p, AUTO_REC, 1), values_of(p, AUTO_REC, 3)
    assert r3 == {**r1, HB: NEW_HB, PLT: "201"}
    assert len(RF.pages(adir)) == 3


# --------------------------------------------------------------------------- 08e-08


def test_manual_correction_becomes_the_new_effective_rev(base, tmp_path):
    """08e-08 (S1/S5): 119 -> 11.9: r2 is effective, r1 is kept byte for byte, every other value is carried over, and
    the decision carries the evidence the provenance chain needs."""
    p, b, adir = base.project, base.batch, base.adir
    r1_path = W.body_path(p, AUTO_REC, 1)
    r1_bytes = r1_path.read_bytes()
    code, rc, err = RF.reopen(p, HB)
    assert code == 10, err

    code, receipt, err = RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path)

    assert code == 0, err
    (res,) = receipt["per_record"]
    assert (res["rec_id"], res["result"], res["rev_new"], res["status"]) == (AUTO_REC, "committed", 2, "verified")
    front = W.front(p, AUTO_REC)
    assert front["effective"] == {"rev": 2, "audit_rev": f"{b}/a1", "status": "verified", "verified_by": "human"}
    assert front["status"] == "verified"
    assert [(c["rev"], c.get("superseded_by")) for c in front["candidates"]] == [(1, 2), (2, None)]
    assert schema.issues("record", front) == []
    b1, b2 = W.body(p, AUTO_REC, 1), W.body(p, AUTO_REC, 2)
    assert b2["fields"] == {**b1["fields"], HB: NEW_HB}
    assert (b2["rev"], b2["status"], b2["verified_by"], b2["audit_rev"]) == (2, "verified", "human", f"{b}/a1")
    assert (b2["dropped"], b2["open"], b2["uncertain"]) == ([], [], [])
    assert (b2["type"], b2["date"]) == (b1["type"], b1["date"])
    assert b2["reopened"] == rc["reopen_id"] and b2["commit_id"] == res["commit_id"]
    assert r1_path.read_bytes() == r1_bytes and os.stat(r1_path).st_mode & 0o222 == 0
    assert os.stat(W.body_path(p, AUTO_REC, 2)).st_mode & 0o222 == 0

    (d,) = [d for d in W.fixes(adir) if d["rev_new"] == 2]
    assert (d["field_id"], d["by"], d["choice"], d["value"], d["rev_base"]) == (HB, "human", "manual", NEW_HB, 1)
    assert d["reopen_id"] == rc["reopen_id"] and d["commit_id"] == res["commit_id"]
    assert d["evidence"]["seen"] == {r: OLD_HB for r in ROUTES}
    assert d["evidence"]["confirm_sha256"] == rc["confirm_sha256"]
    assert set(d["evidence"]["region"]) == {"der_sha256", "bbox_px"}
    marks = [m for m in W.commits(adir) if m["commit_id"] == res["commit_id"]]
    assert [m["op"] for m in marks] == ["begin", "end"]


def test_resubmitting_the_same_choices_changes_nothing(base, tmp_path):
    """08e-08 (S5, idempotence): the same reopen choices a second time are ``unchanged``."""
    p = base.project
    _, rc, _ = RF.reopen(p, HB)
    choices = RF.choose(p, rc, "manual", value=NEW_HB)
    assert RF.decide(p, choices, tmp_path / "1")[0] == 0
    after = tree_sha(p)
    code, receipt, err = RF.decide(p, choices, tmp_path / "2")
    assert code == 0, err
    assert [r["result"] for r in receipt["per_record"]] == ["unchanged"]
    assert tree_sha(p) == after


# --------------------------------------------------------------------------- 08e-09


def test_route_choice_keeps_the_value_and_records_the_route(base, tmp_path):
    """08e-09 (S2): pointing at a route's reading is a legal decision of a reopened field."""
    p, adir = base.project, base.adir
    _, rc, _ = RF.reopen(p, HB)
    code, _, err = RF.decide(p, RF.choose(p, rc, "route", route="vision"), tmp_path)
    assert code == 0, err
    assert values_of(p, AUTO_REC, 2) == values_of(p, AUTO_REC, 1)
    (d,) = [d for d in W.fixes(adir) if d["rev_new"] == 2]
    assert (d["by"], d["choice"], d["route"], d["value"], d["reopen_id"]) == (
        "human", "route", "vision", OLD_HB, rc["reopen_id"])


def test_plain_none_is_not_an_answer_for_a_reopened_field(base, tmp_path):
    """08e-09 (S3): "none of these" would leave a verified field undecided: the record is refused, nothing written."""
    p = base.project
    _, rc, _ = RF.reopen(p, HB)
    before = tree_sha(p)
    code, receipt, err = RF.decide(p, RF.choose(p, rc, "none"), tmp_path)
    assert code == 30 and "decide" in err
    assert tree_sha(p) == before
    assert W.front(p, AUTO_REC)["effective"]["rev"] == 1
    # a blank typed value is the same "none" (r2.3)
    code, _, err = RF.decide(p, RF.choose(p, rc, "manual", value="   "), tmp_path / "blank")
    assert code == 30
    assert tree_sha(p) == before


def test_not_a_field_drops_the_value(base, tmp_path):
    """08e-09 (S2): reopen + "not a field" removes the value from the effective record."""
    p, adir = base.project, base.adir
    _, rc, _ = RF.reopen(p, HB)
    code, _, err = RF.decide(p, RF.choose(p, rc, "none", reason="not_a_field"), tmp_path)
    assert code == 0, err
    b1, b2 = W.body(p, AUTO_REC, 1), W.body(p, AUTO_REC, 2)
    assert b2["dropped"] == [HB] and HB not in b2["fields"]
    assert b2["fields"] == {k: v for k, v in b1["fields"].items() if k != HB}
    (d,) = [d for d in W.fixes(adir) if d["rev_new"] == 2]
    assert (d["choice"], d["value"], d["evidence"]["reason"]) == ("none", None, "not_a_field")


# --------------------------------------------------------------------------- 08e-10


def test_audit_trail_links_event_decision_commit_body_receipt(base, tmp_path):
    """08e-10 (S5): from the reopen event one walks to the decision, the commit, the body and the run receipt; every log
    only grew, every sealed file is identical."""
    p, b, adir = base.project, base.batch, base.adir
    _, rc, _ = RF.reopen(p, HB)
    watched = [adir / n for n in ("reopen.jsonl", "fixes.jsonl", "commits.jsonl", "writeback_receipts.jsonl")]
    before = {path: path.read_bytes() if path.exists() else b"" for path in watched}
    sealed = {path: path.read_bytes() for path in [p / rc["page"], adir / "cells.jsonl", adir / "candidates.jsonl",
                                                   adir / "runs.json", adir / "confirm.html"] if path.exists()}
    bodies = {W.body_path(p, AUTO_REC, 1): W.body_path(p, AUTO_REC, 1).read_bytes()}
    code, receipt, err = RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path)
    assert code == 0, err

    for path in watched:
        assert path.exists() and path.read_bytes().startswith(before[path]), path   # append-only
    for path, data in {**sealed, **bodies}.items():
        assert path.read_bytes() == data, path
    (event,) = RF.log(adir)
    (decision,) = [d for d in W.fixes(adir) if d.get("reopen_id") == event["reopen_id"]]
    begin, end = [m for m in W.commits(adir) if m["commit_id"] == decision["commit_id"]]
    assert (begin["op"], end["op"]) == ("begin", "end")
    assert [d["decision_id"] for d in begin["decisions"]] == [decision["decision_id"]]
    body = W.body(p, AUTO_REC, decision["rev_new"])
    assert (body["commit_id"], body["reopened"]) == (decision["commit_id"], event["reopen_id"])
    run = W.jsonl(adir / "writeback_receipts.jsonl")[-1]
    assert [(r["rec_id"], r["commit_id"]) for r in run["per_record"]] == [(AUTO_REC, decision["commit_id"])]
    assert run["choices_sha256"] == hashlib.sha256((tmp_path / "choices.json").read_bytes()).hexdigest()


# --------------------------------------------------------------------------- 08e-13


def test_export_follows_the_new_rev(base, tmp_path):
    """08e-13 (S1): the library text carries the corrected value, the map and the bindings say rev 2; before the
    commit the export is still the r1 text."""
    p = base.project
    old = R.export(p, tmp_path / "out0", kb_id="k")
    _, rc, _ = RF.reopen(p, HB)
    assert R.export(p, tmp_path / "out1", kb_id="k")["files"] == old["files"]      # a reopen alone changes nothing
    assert f"血红蛋白 {OLD_HB} g/L 2026-10-22 ⟨{AUTO_REC}⟩" in X.lines(old, "items")
    code, _, err = RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path / "w")
    assert code == 0, err

    new = R.export(p, tmp_path / "out2", kb_id="k")
    assert new["audit_bindings"][AUTO_REC]["rev"] == 2
    rows = [r for r in new["export_map"] if HB in r["field_ids"]]
    assert rows and {r["rev"] for r in rows} == {2} and {r["value_audited"] for r in rows} == {NEW_HB}
    mine = [ln for ln in X.lines(new) if f"⟨{AUTO_REC}⟩" in ln]
    assert f"血红蛋白 {NEW_HB} g/L 2026-10-22 ⟨{AUTO_REC}⟩" in mine       # the item name still resolves
    assert not [ln for ln in mine if f" {OLD_HB} " in ln]
    other = [ln for ln in X.lines(new) if f"⟨{AUTO_REC}⟩" not in ln]
    assert other == [ln for ln in X.lines(old) if f"⟨{AUTO_REC}⟩" not in ln]


def test_item_label_still_resolves_from_the_audit_revision(base, tmp_path):
    """08e-24 (S1): the label is not in the ``_rev`` body (spec §6: kept as is); the body's own ``audit_rev`` has it."""
    p, b, adir = base.project, base.batch, base.adir
    _, rc, _ = RF.reopen(p, HB)
    assert RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path)[0] == 0
    body = W.body(p, AUTO_REC, 2)
    assert body["audit_rev"] == f"{b}/a1"
    labels = {f["field_id"]: f["item_label"] for c in W.candidates(adir) for f in c["fields"]}
    assert labels[HB] == "hemoglobin"
    assert any(ln.startswith(f"血红蛋白 {NEW_HB} ") for ln in X.lines(R.export(p, tmp_path / "o", kb_id="k"), "items"))


# --------------------------------------------------------------------------- 08e-14


def _view_targets(project: Path) -> tuple[dict[str, str], dict[str, str]]:
    out = V.rebuild(project)
    assert out["problems"] == []
    pick = lambda d: {n: t for n, t in d.items() if AUTO_REC in t}  # noqa: E731
    return pick(out["records"]), pick(out["verified"])


def test_views_follow_effective(base, tmp_path):
    """08e-14 (S1): the compatibility views point at r1 until the correction is committed, then at r2."""
    p = base.project
    records, verified = _view_targets(p)
    assert list(records.values()) == [f"_rev/{AUTO_REC}.r1.md"]
    assert list(verified.values()) == [f"../records/_rev/{AUTO_REC}.r1.md"]
    _, rc, _ = RF.reopen(p, HB)
    assert _view_targets(p) == (records, verified)
    assert RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path)[0] == 0

    records2, verified2 = _view_targets(p)
    assert set(records2) == set(records) and set(verified2) == set(verified)
    assert list(records2.values()) == [f"_rev/{AUTO_REC}.r2.md"]
    assert list(verified2.values()) == [f"../records/_rev/{AUTO_REC}.r2.md"]
    (name,) = verified2
    assert NEW_HB in (p / "verified" / name).read_text(encoding="utf-8")
    assert (p / "verified" / name).resolve() == W.body_path(p, AUTO_REC, 2).resolve()
    assert W.body_path(p, AUTO_REC, 1).is_file()


# --------------------------------------------------------------------------- 08e-15 (isolation, unit part)


def test_reopen_writes_only_its_log_and_page(base):
    """08e-15 (S2): the only new files are the log and the page; no existing file changes."""
    before = RF.project_files(base.project)
    code, rc, _ = RF.reopen(base.project, HB)
    assert code == 10
    after = RF.project_files(base.project)
    prefix = f"work/{base.batch}/audit/a1/"
    assert sorted(set(after) - set(before)) == [prefix + "reopen.jsonl", prefix + "reopen/o0001.html"]
    assert {k for k in before if after[k] != before[k]} == set()


def test_lock_held_by_another_process_exit_40(base, tmp_path):
    """08e-15 (S2): the project lock is the writeback's; a reopen and a reopen commit both stop with 40."""
    p = base.project
    _, rc, _ = RF.reopen(p, HB)
    choices = RF.choose(p, rc, "manual", value=NEW_HB)
    lock = p / "work" / ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = tree_sha(p)
        code, rc2, _ = RF.reopen(p, PLT)
        assert code == 40 and rc2["exit_code"] == 40
        assert RF.decide(p, choices, tmp_path)[0] == 40
        assert tree_sha(p) == before
    finally:
        os.close(fd)
    assert RF.decide(p, choices, tmp_path / "again")[0] == 0           # the lock was the only obstacle


# --------------------------------------------------------------------------- 08e-16


def _items(choices):
    return choices["items"]


def test_tampered_page_is_refused_by_the_exporter(base):
    """08e-16 (S3): the page is sealed: edited bytes no longer export."""
    _, rc, _ = RF.reopen(base.project, HB)
    html = RF.page(base.project, rc)
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html.replace(b"kk-item", b"kk-iten"), [{"field_id": HB, "kind": "manual", "value": "1"}])


def _forge(rc, base, mutate):
    choices = RF.choose(base.project, rc, "manual", value=NEW_HB)
    mutate(choices)
    return choices


@pytest.mark.parametrize("name,mutate", [
    ("sha", lambda c: c.update(confirm_sha256="0" * 64)),
    ("fingerprints", lambda c: c["fingerprints"].update(vision="0" * 12)),
    ("rev_base", lambda c: _items(c)[0].update(rev_base=0)),
    ("cell", lambda c: _items(c)[0].update(cell={"row": 99, "col": "c9"})),
    ("seen", lambda c: _items(c)[0]["seen"].update(vision="999")),
])
def test_forged_or_stale_reopen_choices_refused(base, tmp_path, name, mutate):
    """08e-16 (S3): every check of the ordinary page applies to the reopen page; nothing is written on refusal."""
    _, rc, _ = RF.reopen(base.project, HB)
    before = tree_sha(base.project)
    code, receipt, err = RF.decide(base.project, _forge(rc, base, mutate), tmp_path)
    assert code == 30, err
    assert tree_sha(base.project) == before


def test_items_outside_the_reopen_page_are_refused(base, tmp_path):
    """08e-16 (S3): a reopen page speaks for its field only: other fields of the record and other records are not on it."""
    p, adir = base.project, base.adir
    _, rc, _ = RF.reopen(p, HB)
    good = RF.choose(p, rc, "manual", value=NEW_HB)["items"][0]
    cells = W.cells(adir)

    def item(fid, rec, **kw):
        c = cells[fid]
        seen = {e: (c["candidates"].get(e) or {}).get("value") for e in ROUTES}
        return {**good, "rec_id": rec, "field_id": fid, "cell": c["cell"], "seen": seen, **kw}

    pending_fid = W.to_confirm(adir)[PENDING_REC][0]
    for bad in (item(PLT, AUTO_REC), item(pending_fid, PENDING_REC, choice={"kind": "route", "route": "vision"})):
        choices = RF.choose(p, rc, "manual", value=NEW_HB)
        choices["items"] = [bad]
        before = tree_sha(p)
        code, receipt, err = RF.decide(p, choices, tmp_path)
        assert code == 30, err
        assert receipt["per_record"][0]["result"] == "rejected"
        assert tree_sha(p) == before


def test_ordinary_page_and_reopen_page_coexist(base, tmp_path):
    """08e-16 (S2, regression): the ordinary confirm page of the same audit revision keeps working next to a reopen page."""
    p, b, adir = base.project, base.batch, base.adir
    _, rc, _ = RF.reopen(p, HB)
    code, receipt, err = RF.decide(p, W.export(p, b, adir, W.picks(adir)), tmp_path / "ordinary")
    assert code == 0, err
    assert {r["rec_id"] for r in receipt["per_record"]} == {PENDING_REC, "r-27bf950bdbbd"}
    code, _, err = RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path / "reopened")
    assert code == 0, err
    assert W.front(p, AUTO_REC)["effective"]["rev"] == 2


# --------------------------------------------------------------------------- 08e-18


def test_fixes_schema_takes_an_optional_reopen_id(base, tmp_path):
    """08e-18 (S2): ``reopen_id`` is an optional, well-formed key of a decision; the record schema is untouched."""
    _, rc, _ = RF.reopen(base.project, HB)
    assert RF.decide(base.project, RF.choose(base.project, rc, "manual", value=NEW_HB), tmp_path)[0] == 0
    fixes = W.fixes(base.adir)
    assert all(schema.issues("fixes", d) == [] for d in fixes)
    with_id = next(d for d in fixes if "reopen_id" in d)
    without = next(d for d in fixes if "reopen_id" not in d)
    assert schema.issues("fixes", without) == []
    for bad in ("o0001", f"{base.batch}/a1/o1", f"{base.batch}/o0001", 7):
        assert schema.issues("fixes", {**with_id, "reopen_id": bad}) != [], bad
    assert schema.issues("fixes", {**with_id, "surprise": 1}) != []


# --------------------------------------------------------------------------- 08e-22


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                                HealthCheck.function_scoped_fixture])
@given(ops=st.lists(st.sampled_from([HB, PLT]), min_size=1, max_size=6))
def test_reopen_is_idempotent_for_any_call_sequence(template, ops):
    """08e-22 (S4): however often and in whatever order fields are reopened, there is one log line and one page per
    distinct field, repeats are ``unchanged``, and ``records/`` never moves."""
    with tempfile.TemporaryDirectory() as tmp:
        b = RF.copy(template, Path(tmp))
        records = records_tree(b.project)
        seen: list[str] = []
        for fid in ops:
            code, rc, err = RF.reopen(b.project, fid)
            assert code == 10, err
            assert rc["result"] == ("unchanged" if fid in seen else "opened")
            seen.append(fid)
        distinct = list(dict.fromkeys(ops))
        assert [r["field_id"] for r in RF.log(b.adir)] == distinct
        assert [p.name for p in RF.pages(b.adir)] == [f"o{n:04d}.html" for n in range(1, len(distinct) + 1)]
        assert records_tree(b.project) == records


# --------------------------------------------------------------------------- added while implementing (guards)


def test_reopen_page_without_its_event_is_refused(base, tmp_path):
    """08e-16 (S3): a page whose event is not in ``reopen.jsonl`` is not a reopen page the writeback believes."""
    p, adir = base.project, base.adir
    _, rc, _ = RF.reopen(p, HB)
    choices = RF.choose(p, rc, "manual", value=NEW_HB)
    (adir / "reopen.jsonl").write_text("", encoding="utf-8")
    before = tree_sha(p)
    code, _, err = RF.decide(p, choices, tmp_path)
    assert code == 30 and "reopen.jsonl" in err
    assert tree_sha(p) == before


def test_foreign_files_in_the_reopen_directory_are_skipped(base, tmp_path):
    """08e-16 (S2): junk and ordinary pages next to the reopen pages do not stop the real one being found."""
    p, adir = base.project, base.adir
    _, rc, _ = RF.reopen(p, HB)
    (adir / "reopen" / "o0099.html").write_bytes(b"not a page")
    (adir / "reopen" / "o0098.html").write_bytes(W.page(p, base.batch, adir))     # sealed, but no reopen block
    code, _, err = RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path)
    assert code == 0, err
    assert W.front(p, AUTO_REC)["effective"]["rev"] == 2


def test_a_page_that_matches_nothing_is_refused(base, tmp_path):
    """08e-16 (S3): with an ordinary confirm page present and a reopen directory, a stranger's sha still fails."""
    p, b, adir = base.project, base.batch, base.adir
    _, rc, _ = RF.reopen(p, HB)
    W.page(p, b, adir)
    choices = RF.choose(p, rc, "manual", value=NEW_HB)
    choices["confirm_sha256"] = "1" * 64
    code, _, err = RF.decide(p, choices, tmp_path)
    assert code == 30 and "sealed sha" in err


def test_usage_of_the_options(base, capsys):
    """08e-17 (S2): ``--reopen`` and ``--writeback`` exclude each other; ``--reason`` needs ``--reopen``."""
    from klode_kit.audit import stage

    common = ["--project", str(base.project)]
    before = tree_sha(base.project)
    assert stage.main([*common, "--reopen", HB, "--writeback"]) == 2
    assert stage.main([*common, "--batch", base.batch, "--reason", "x"]) == 2
    assert tree_sha(base.project) == before
    assert stage.main([*common, "--reopen", HB, "--reason", "note"]) == 10
    assert RF.log(base.adir)[0]["reason"] == "note"
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["result"] == "opened"


def test_batch_option_checked(base):
    """08e-17 (S3): a malformed ``--batch`` is usage, another batch is refused, the record's own batch is fine."""
    before = tree_sha(base.project)
    assert RF.reopen(base.project, HB, batch="nope")[0] == 2
    assert RF.reopen(base.project, HB, batch="0" * 12)[0] == 30
    assert tree_sha(base.project) == before
    assert RF.reopen(base.project, HB, batch=base.batch)[0] == 10


def test_damaged_projects_are_refused_not_crashed(base, tmp_path):
    """08e-04 (S3): a record file, a revision body or an audit revision that is not what the writeback left."""
    p, adir = base.project, base.adir
    # no work/ at all
    empty = tmp_path / "empty"
    empty.mkdir()
    assert RF.reopen(empty, HB)[0] == 30
    # a revision body the record names is gone
    body = W.body_path(p, AUTO_REC, 1)
    os.rename(body, body.with_name("moved-away"))
    code, rc, _ = RF.reopen(p, HB)
    assert code == 30 and "missing" in rc["error"]
    os.rename(body.with_name("moved-away"), body)
    # a field the effective revision claims but the audit revision does not have
    os.chmod(body, 0o644)
    data = body.read_text(encoding="utf-8")
    ghost = f"{AUTO_REC}/r999-number-0"
    body.write_text(data.replace('"fields": {', f'"fields": {{\n  "{ghost}": "1",', 1), encoding="utf-8")
    code, rc, _ = RF.reopen(p, ghost)
    assert code == 30 and "not a cell" in rc["error"]
    # the audit revision lost a file
    os.rename(adir / "cells.jsonl", adir / "cells.jsonl.moved")
    assert RF.reopen(p, HB)[0] == 2
    os.rename(adir / "cells.jsonl.moved", adir / "cells.jsonl")
    # a record file that is not front matter
    path = W.record_path(p, AUTO_REC)
    path.write_text("not a record", encoding="utf-8")
    code, rc, _ = RF.reopen(p, HB)
    assert code == 30 and "front matter" in rc["error"]
    assert RF.log(adir) == [] and RF.pages(adir) == []
