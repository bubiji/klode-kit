"""WI-08c: guard clauses of the writeback entry (usage, unreadable or malformed choices, lock held) - unit lane.

Every refusal is asserted by its exit code and by the data project being unchanged (tree sha), never by internal
calls. Exit codes (r2.6): 2 usage, 30 contract (the choices or the revision do not match), 40 lock held.
"""

from __future__ import annotations

import fcntl
import io
import json
import os

import pytest

from _support import writeback_fx as W
from klode_kit.audit import stage
from klode_kit.audit import writeback as WB

pytestmark = pytest.mark.unit


@pytest.fixture()
def u8(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.U8)
    choices = W.export(project, b, adir, W.picks(adir))
    return project, b, adir, choices


def run(project, batch=None, choices=None):
    err, out = io.StringIO(), io.StringIO()
    code = WB.run(project, batch, choices, out=err, stdout=out)
    return code, err.getvalue(), out.getvalue()


def test_kb_audit_without_batch_or_choices_is_usage(tmp_path):
    assert stage.main(["--project", str(tmp_path)]) == 2
    assert stage.main(["--project", str(tmp_path), "--writeback"]) == 2


def test_choices_file_missing_is_usage(u8, tmp_path):
    project, b, _, _ = u8
    before = W.tree_sha(project)
    assert stage.main(["--project", str(project), "--writeback", str(tmp_path / "nope.json")]) == 2
    assert W.tree_sha(project) == before


@pytest.mark.parametrize("payload", [
    b"{not json", b"[]", b'"x"',
    json.dumps({"schema": "klode-kit.choices/1"}).encode(),
])
def test_unreadable_or_shapeless_choices_exit30(u8, tmp_path, payload):
    project, b, _, _ = u8
    path = tmp_path / "c.json"
    path.write_bytes(payload)
    before = W.tree_sha(project)
    code, err, _ = run(project, None, path)
    assert code == 30, err
    assert W.tree_sha(project) == before


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(items=[]),                                   # zero clicks (08b: refused by the writeback)
    lambda c: c.update(schema="klode-kit.choices/2"),
    lambda c: c.update(batch_id="not-a-batch"),
    lambda c: c.update(extra=1),
    lambda c: c["items"][0].update(choice={"kind": "partial"}),     # r3.3 removed partial
    lambda c: c["items"][0].update(field_id="no-slash"),
    lambda c: c["items"][0].pop("seen"),
])
def test_malformed_choices_exit30(u8, tmp_path, mutate):
    project, b, _, choices = u8
    bad = W.forged(choices)
    mutate(bad)
    path = W.write_choices(tmp_path / "c.json", bad)
    before = W.tree_sha(project)
    code, err, _ = run(project, None, path)
    assert code == 30, err
    assert W.tree_sha(project) == before


def test_batch_argument_disagrees_with_choices_exit30(u8, tmp_path):
    project, b, _, choices = u8
    path = W.write_choices(tmp_path / "c.json", choices)
    other = "0" * 12 if b != "0" * 12 else "1" * 12
    before = W.tree_sha(project)
    code, err, _ = run(project, other, path)
    assert code == 30, err
    assert W.tree_sha(project) == before


def test_bad_batch_id_and_unaudited_batch_are_usage(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert run(project, "not-a-batch")[0] == 2
    assert run(project, "0" * 12)[0] == 2
    assert not (project / "records").exists()


@pytest.mark.parametrize("bad_item", [
    {"evidence": {"reason": "looks_wrong"}},                     # the only reason is not_a_field (with none)
    {"choice": {"kind": "route", "route": "vision"}, "evidence": {"reason": "not_a_field"}},
    {"cell": {"row": 999, "col": "c9"}},                         # not the cell of that field
    {"field_id": None},                                          # a field that is not to be confirmed
])
def test_illegal_item_rejects_its_record_only(u8, tmp_path, bad_item):
    project, b, adir, choices = u8
    bad = W.forged(choices)
    item = bad["items"][0]
    if "field_id" in bad_item:  # an agreed (not shown) field of the same record
        agreed = next(f["field_id"] for c in W.candidates(adir) if c["rec_id"] == item["rec_id"]
                      for f in c["fields"] if not W.shown(f))
        item["field_id"] = agreed
        item["cell"] = W.cells(adir)[agreed]["cell"]
        item["seen"] = {e: (W.cells(adir)[agreed]["candidates"].get(e) or {}).get("value") for e in item["seen"]}
    else:
        item.update(bad_item)
    rec_id = item["rec_id"]
    path = W.write_choices(tmp_path / "c.json", bad)
    code, err, out = run(project, None, path)
    assert code == 30, err
    assert W.front(project, rec_id) is None
    receipt = json.loads(out.strip().splitlines()[-1])
    assert {r["rec_id"]: r["result"] for r in receipt["per_record"]}[rec_id] == "rejected"


def test_unknown_record_in_choices_exit30(u8, tmp_path):
    project, b, _, choices = u8
    bad = W.forged(choices)
    bad["items"][0]["rec_id"] = "r-000000000000"
    bad["items"][0]["field_id"] = "r-000000000000/" + bad["items"][0]["field_id"].split("/")[1]
    path = W.write_choices(tmp_path / "c.json", bad)
    code, err, _ = run(project, None, path)
    assert code == 30, err
    assert not (project / "records" / "_by_id" / "r-000000000000.md").exists()


def test_lock_held_exit40(u8, tmp_path):
    project, b, _, choices = u8
    path = W.write_choices(tmp_path / "c.json", choices)
    fd = os.open(project / "work" / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = W.tree_sha(project)
        code, err, _ = run(project, None, path)
        assert code == 40, err
        assert W.tree_sha(project) == before
    finally:
        os.close(fd)


def test_ingest_without_choices_is_idempotent(u8, tmp_path):
    project, b, adir, _ = u8
    code, err, _ = run(project, b)
    assert code == 10, err
    before = W.state(project, b)
    assert run(project, b)[0] == 10
    assert W.state(project, b) == before
    for c in W.candidates(adir):
        front = W.front(project, c["rec_id"])
        assert [x["rev"] for x in front["candidates"]] == [1]
        assert front["candidates"][0]["status"] == c["status"]
        assert front["type"] == "lab" and front["date"] == "2026-10-22"
