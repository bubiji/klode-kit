"""WI-38: ``kb-audit --again --batch B`` - a second confirm pass on the pending / disputed records of a batch's newest
audit revision, and the writeback of its page - unit lane (spec ``.claude/tdd-guardian/wi-38-spec.md``, P33).

The project is the one the public stages leave for ``fx-u1-3labs`` (``_support/reopen_fx``): ``AUTO_REC`` is
``verified(auto)`` at r1, ``PENDING_REC`` waits for a human on three fields (R005 majority, R007 all differ, R009 a
missing route), ``OTHER_REC`` on two. The first human pass goes through the ordinary confirm page and leaves some
fields as "都不对" (plain ``none``): the record becomes ``disputed`` at r2 and the ordinary page is stale for it.
Everything is asserted on files, exit codes and receipts, never on internal calls; no mock.

Laws:
- splitting the human decisions of a record over two sittings (ordinary page, then the again page) commits the same
  values and dropped fields as deciding them in one sitting (S4 over generated splits, S6 metamorphic);
- ``--again`` changes nothing the records say (one log line, one page) and is idempotent until a writeback moves a
  record on (S5).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.flows import base_env
from _support.kb_ocr import tree, tree_sha
from _support.reopen_fx import AUTO_REC, PENDING_REC, base, template  # noqa: F401
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import schema

pytestmark = pytest.mark.unit

OTHER_REC = "r-27bf950bdbbd"
R005, R007, R009 = (f"{PENDING_REC}/{s}" for s in ("r005-number-0", "r007-number-0", "r009-unit-0"))
PENDING_TODO = (R005, R007, R009)
CRASHPOINTS = ("again.logged", "again.mkdir", "again.page_tmp", "again.page_renamed", "again.page_written")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# --------------------------------------------------------------------------- helpers


def again(project: Path, batch: str | None):
    """In-process ``again.run``; ``(exit code, receipt printed on stdout, stderr text)``."""
    from klode_kit.audit import again as AG

    err, out = io.StringIO(), io.StringIO()
    code = AG.run(project, batch_id=batch, out=err, stdout=out)
    lines = [x for x in out.getvalue().splitlines() if x.strip()]
    return code, (json.loads(lines[-1]) if lines else None), err.getvalue()


def log(adir: Path) -> list[dict]:
    return W.jsonl(adir / "again.jsonl")


def pages(adir: Path) -> list[Path]:
    return sorted((adir / "again").glob("g*.html")) if (adir / "again").is_dir() else []


def records_tree(project: Path) -> dict[str, bytes]:
    return tree(project / "records") if (project / "records").is_dir() else {}


def pick(adir: Path, fid: str, kind: str = "route") -> dict:
    if kind == "drop":
        return {"field_id": fid, "kind": "none", "reason": "not_a_field"}
    if kind == "none":
        return {"field_id": fid, "kind": "none"}
    return {"field_id": fid, "kind": "route", "route": W.route_pick(adir, fid)}


def first_pass(b: RF.Base, where: Path, decided: dict[str, str], *, other: bool = True):
    """The ordinary confirm page: PENDING_REC's fields in ``decided`` get their kind, the rest "都不对"; OTHER_REC is
    decided in full (``other``)."""
    sel = [pick(b.adir, f, decided.get(f, "none")) for f in PENDING_TODO]
    if other:
        sel += W.picks(b.adir, [OTHER_REC])
    return W.submit(b.project, W.export(b.project, b.batch, b.adir, sel), where)


def on_page(project: Path, rc: dict, selections: list[dict]) -> dict:
    return CP.export_choices((project / rc["page"]).read_bytes(), selections)


def project_files(project: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(project).items() if not k.endswith("/")}


# --------------------------------------------------------------------------- 1: the page of a disputed record


def test_disputed_record_gets_a_page_of_only_its_undecided_fields(base, tmp_path):
    """Spec 1 (S1): R005 and R007 decided, R009 left "都不对" -> r2 disputed; ``--again`` logs one event and writes
    ``again/g0001.html`` listing R009 alone at ``rev_base 2``; records/ does not change by a byte."""
    p, b, adir = base.project, base.batch, base.adir
    code, _, err = first_pass(base, tmp_path, {R005: "route", R007: "route"})
    assert code == 10, err
    front = W.front(p, PENDING_REC)
    assert (front["status"], front["candidates"][-1]["rev"]) == ("disputed", 2)
    before_records, before_files = records_tree(p), project_files(p)

    code, rc, err = again(p, b)

    assert code == 10, err
    assert (rc["schema"], rc["result"], rc["exit_code"]) == ("klode-kit.again_receipt/1", "opened", 10)
    assert (rc["batch_id"], rc["audit_rev"], rc["again_id"]) == (b, f"{b}/a1", f"{b}/a1/g0001")
    assert (rc["records"], rc["fields"]) == (1, 1)
    assert rc["page"] == f"work/{b}/audit/a1/again/g0001.html"
    page = p / rc["page"]
    assert page.is_file() and os.stat(page).st_mode & 0o222 == 0
    assert rc["confirm_sha256"] == CP.confirm_sha256(page.read_bytes())
    (row,) = log(adir)
    assert W.TS.fullmatch(row.pop("at").encode())
    assert row == {"op": "again", "again_id": f"{b}/a1/g0001", "batch_id": b, "audit_rev": f"{b}/a1",
                   "records": [{"rec_id": PENDING_REC, "rev_base": 2, "fields": [R009]}], "by": "human",
                   "page": "again/g0001.html"}
    data = CP.page_data(page.read_bytes())
    assert [(r["rec_id"], r["rev_base"], r["clickable"], [f["field_id"] for f in r["fields"]])
            for r in data["records"]] == [(PENDING_REC, 2, True, [R009])]
    assert data["again"] == {"again_id": rc["again_id"]}
    assert (data["batch_id"], data["audit_rev"]) == (b, f"{b}/a1")
    text = page.read_text(encoding="utf-8")
    assert text.count('class="kk-item"') == 1 and f'data-field-id="{R009}"' in text
    assert "第二轮" in text and rc["again_id"] in text
    assert records_tree(p) == before_records
    after = project_files(p)
    prefix = f"work/{b}/audit/a1/"
    assert sorted(set(after) - set(before_files)) == [prefix + "again.jsonl", prefix + "again/g0001.html"]
    assert {k for k in before_files if after[k] != before_files[k]} == set()


def test_pending_records_never_decided_are_listed_with_all_their_fields(base):
    """Spec "pending_confirm or disputed" (S2): before any human pass both waiting records are on the page with every
    field to confirm at their current rev 1; the verified(auto) record is not."""
    code, rc, err = again(base.project, base.batch)
    assert code == 10, err
    assert (rc["records"], rc["fields"]) == (2, 5)
    (row,) = log(base.adir)
    assert row["records"] == [{"rec_id": PENDING_REC, "rev_base": 1, "fields": list(PENDING_TODO)},
                              {"rec_id": OTHER_REC, "rev_base": 1, "fields": W.to_confirm(base.adir)[OTHER_REC]}]
    data = CP.page_data((base.project / rc["page"]).read_bytes())
    assert AUTO_REC not in {r["rec_id"] for r in data["records"]}


# --------------------------------------------------------------------------- 2: idempotent


def test_second_run_with_nothing_changed_is_unchanged(base, tmp_path):
    """Spec 2 (S5): the same records at the same revs -> ``unchanged``, the same page, not a byte written."""
    p = base.project
    assert first_pass(base, tmp_path, {R005: "route", R007: "route"})[0] == 10
    code1, rc1, _ = again(p, base.batch)
    after_first = tree_sha(p)
    code2, rc2, err = again(p, base.batch)
    assert (code1, code2) == (10, 10), err
    assert rc2["result"] == "unchanged"
    assert {k: rc2[k] for k in ("again_id", "page", "confirm_sha256", "records", "fields")} == \
        {k: rc1[k] for k in ("again_id", "page", "confirm_sha256", "records", "fields")}
    assert tree_sha(p) == after_first and len(log(base.adir)) == 1 and len(pages(base.adir)) == 1


# --------------------------------------------------------------------------- 3: finishing the record


def test_deciding_the_rest_on_the_again_page_verifies_the_record(base, tmp_path):
    """Spec 3 (S1 + S6): the again page's choice for R009 commits r3 ``verified`` (human); ``effective`` moves to r3;
    its values are those one sitting with the same picks gives; only R009 has a new decision, tagged ``again_id``."""
    p, b, adir = base.project, base.batch, base.adir
    ref = RF.copy(base, tmp_path / "ref")                       # the same project, decided in one sitting
    picks = {R005: "route", R007: "route", R009: "route"}
    code, _, err = W.submit(ref.project, W.export(ref.project, b, ref.adir, [pick(ref.adir, f) for f in PENDING_TODO]),
                            tmp_path / "ref-w")
    assert code == 0, err

    assert first_pass(base, tmp_path / "w1", {R005: "route", R007: "route"})[0] == 10
    code, rc, err = again(p, b)
    assert code == 10, err
    choices = on_page(p, rc, [pick(adir, R009, picks[R009])])
    assert [(i["field_id"], i["rev_base"]) for i in choices["items"]] == [(R009, 2)]
    code, receipt, err = W.submit(p, choices, tmp_path / "w2")

    assert code == 0, err
    (res,) = receipt["per_record"]
    assert (res["rec_id"], res["result"], res["rev_new"], res["status"]) == (PENDING_REC, "committed", 3, "verified")
    front = W.front(p, PENDING_REC)
    assert front["effective"] == {"rev": 3, "audit_rev": f"{b}/a1", "status": "verified", "verified_by": "human"}
    assert front["status"] == "verified" and schema.issues("record", front) == []
    assert [(c["rev"], c.get("superseded_by")) for c in front["candidates"]] == [(1, 3), (2, 3), (3, None)]
    b3, one = W.body(p, PENDING_REC, 3), W.body(ref.project, PENDING_REC, 2)
    assert b3["fields"] == one["fields"] and b3["dropped"] == one["dropped"]
    assert R005 in b3["fields"] and R007 in b3["fields"] and R009 in b3["fields"]
    assert (b3["status"], b3["verified_by"], b3["open"], b3["uncertain"]) == ("verified", "human", [], [])
    assert b3["again"] == rc["again_id"]
    human3 = [d for d in W.fixes(adir) if d["rev_new"] == 3 and d["by"] == "human"]
    assert [(d["field_id"], d["rev_base"], d["again_id"]) for d in human3] == [(R009, 2, rc["again_id"])]
    assert human3[0]["evidence"]["confirm_sha256"] == rc["confirm_sha256"]
    assert all(schema.issues("fixes", d) == [] for d in W.fixes(adir))
    # the same choices a second time change nothing
    after = tree_sha(p)
    code, receipt, err = W.submit(p, choices, tmp_path / "w3")
    assert code == 0, err
    assert [r["result"] for r in receipt["per_record"]] == ["unchanged"] and tree_sha(p) == after


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                               HealthCheck.function_scoped_fixture])
@given(kinds=st.tuples(*[st.sampled_from(["route", "drop"])] * 3),
       first=st.lists(st.sampled_from(PENDING_TODO), max_size=2, unique=True))
def test_any_split_into_two_sittings_equals_one_sitting(template, kinds, first):
    """LAW (S4 over generated splits): whatever subset is decided first and whatever kind each field gets (a route or
    "不是字段"), the record ends verified with the values and dropped fields of one sitting."""
    want = dict(zip(PENDING_TODO, kinds))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        one, two = RF.copy(template, root / "one"), RF.copy(template, root / "two")
        code, _, err = W.submit(one.project, W.export(one.project, one.batch, one.adir,
                                                      [pick(one.adir, f, want[f]) for f in PENDING_TODO]), root / "w0")
        assert code == 0, err
        code, _, err = first_pass(two, root / "w1", {f: want[f] for f in first})
        assert code == 10, err
        code, rc, err = again(two.project, two.batch)
        assert code == 10, err
        rest = [f for f in PENDING_TODO if f not in first]
        assert log(two.adir)[-1]["records"] == [{"rec_id": PENDING_REC, "rev_base": 2, "fields": rest}]
        code, _, err = W.submit(two.project, on_page(two.project, rc, [pick(two.adir, f, want[f]) for f in rest]),
                                root / "w2")
        assert code == 0, err
        a, z = W.body(one.project, PENDING_REC, 2), W.body(two.project, PENDING_REC, 3)
        assert (z["status"], z["fields"], z["dropped"]) == ("verified", a["fields"], a["dropped"])


# --------------------------------------------------------------------------- 4: a second pass that stays partial


def test_partial_second_pass_stays_disputed_and_the_next_run_lists_the_rest(base, tmp_path):
    """Spec 4 (S5): R005 decided first; the again page decides R007 and leaves R009 -> r3 disputed keeping R009's
    uncertain entry; the next ``--again`` is a new event ``g0002`` listing R009 alone at ``rev_base 3``."""
    p, b, adir = base.project, base.batch, base.adir
    assert first_pass(base, tmp_path / "w1", {R005: "route"})[0] == 10
    code, rc1, err = again(p, b)
    assert code == 10, err
    assert log(adir)[0]["records"] == [{"rec_id": PENDING_REC, "rev_base": 2, "fields": [R007, R009]}]
    g1 = (p / rc1["page"]).read_bytes()
    log1 = (adir / "again.jsonl").read_bytes()

    code, receipt, err = W.submit(p, on_page(p, rc1, [pick(adir, R007), pick(adir, R009, "none")]), tmp_path / "w2")

    assert code == 10, err
    (res,) = receipt["per_record"]
    assert (res["result"], res["rev_new"], res["status"]) == ("committed", 3, "disputed")
    front = W.front(p, PENDING_REC)
    assert "effective" not in front and front["status"] == "disputed"
    assert {u.get("field_id") for u in front["candidates"][-1]["uncertain"]} == {R009}
    b3 = W.body(p, PENDING_REC, 3)
    assert b3["fields"] == {} and R009 in b3["open"] and R005 not in b3["open"] and R007 not in b3["open"]
    assert b3["again"] == rc1["again_id"]

    code, rc2, err = again(p, b)
    assert code == 10, err
    assert (rc2["result"], rc2["again_id"], rc2["fields"]) == ("opened", f"{b}/a1/g0002", 1)
    assert log(adir)[1]["records"] == [{"rec_id": PENDING_REC, "rev_base": 3, "fields": [R009]}]
    assert (p / rc1["page"]).read_bytes() == g1 and (adir / "again.jsonl").read_bytes().startswith(log1)
    assert [x.name for x in pages(adir)] == ["g0001.html", "g0002.html"]
    # and the second again page finishes it
    code, _, err = W.submit(p, on_page(p, rc2, [pick(adir, R009)]), tmp_path / "w3")
    assert code == 0, err
    assert W.front(p, PENDING_REC)["effective"]["rev"] == 4
    assert set(W.body(p, PENDING_REC, 4)["fields"]) >= {R005, R007, R009}


# --------------------------------------------------------------------------- 5: what stays refused


def test_choices_from_the_stale_ordinary_page_are_still_refused(base, tmp_path):
    """Spec 5 (S3, regression guard): after the first writeback the ordinary page speaks for r1: refused, nothing
    written, even with an again page present."""
    p, b, adir = base.project, base.batch, base.adir
    assert first_pass(base, tmp_path / "w1", {R005: "route", R007: "route"})[0] == 10
    assert again(p, b)[0] == 10
    stale = W.export(p, b, adir, [pick(adir, f) for f in PENDING_TODO])
    before = tree_sha(p)
    code, receipt, err = W.submit(p, stale, tmp_path / "w2")
    assert code == 30
    assert "rev_base 1 is not the record's current rev 2 (stale page)" in err
    assert receipt["per_record"][0]["result"] == "rejected"
    assert tree_sha(p) == before


def test_a_field_that_is_not_on_the_again_page_is_rejected(base, tmp_path):
    """Spec 5 (S3): an again page speaks for its event's fields only - another field of the record, or a record not
    in the event, is ``not on this page``; nothing is written."""
    p, b, adir = base.project, base.batch, base.adir
    assert first_pass(base, tmp_path / "w1", {R005: "route", R007: "route"})[0] == 10
    _, rc, _ = again(p, b)
    good = on_page(p, rc, [pick(adir, R009)])
    cells = W.cells(adir)

    def item(fid, rec):
        c = cells[fid]
        seen = {e: (c["candidates"].get(e) or {}).get("value") for e in RF.ROUTES}
        return {**good["items"][0], "rec_id": rec, "field_id": fid, "cell": c["cell"], "seen": seen,
                "choice": {"kind": "route", "route": W.route_pick(adir, fid)}}

    # the writeback is record-atomic (a rejected record does not stop the others), so the item of a record outside
    # the event is submitted on its own: with R009 beside it PENDING_REC would rightly commit
    for extra, alone in ((item(R005, PENDING_REC), False), (item(W.to_confirm(adir)[OTHER_REC][0], OTHER_REC), True)):
        choices = W.forged(good)
        choices["items"] = [extra] if alone else [*choices["items"], extra]
        before = tree_sha(p)
        code, receipt, err = W.submit(p, choices, tmp_path / "w2")
        assert code == 30, err
        res = next(r for r in receipt["per_record"] if r["rec_id"] == extra["rec_id"])
        assert res["result"] == "rejected"
        assert [e["reason"] for e in res["errors"]] == ["not on this page"]
        assert "not on this page" in err
        assert tree_sha(p) == before


def test_a_record_of_the_page_without_an_item_is_unchanged(base, tmp_path):
    """Spec "a record of the event with no item is unchanged" (S2): the page holds both waiting records; answering
    OTHER_REC alone commits it and leaves PENDING_REC exactly as it was."""
    p, b, adir = base.project, base.batch, base.adir
    _, rc, _ = again(p, b)
    pending_before = W.record_path(p, PENDING_REC).read_bytes()
    code, receipt, err = W.submit(p, on_page(p, rc, W.picks(adir, [OTHER_REC])), tmp_path / "w")
    assert code == 0, err
    assert {r["rec_id"]: r["result"] for r in receipt["per_record"]} == {OTHER_REC: "committed",
                                                                         PENDING_REC: "unchanged"}
    assert W.front(p, OTHER_REC)["effective"]["rev"] == 2
    assert W.record_path(p, PENDING_REC).read_bytes() == pending_before


# --------------------------------------------------------------------------- 6: nothing to do


def test_batch_whose_records_are_all_verified_is_nothing(base, tmp_path):
    """Spec 6 (S2): every record verified -> ``nothing``, exit 0, not a byte written; the message names ``--reopen``
    for a verified record."""
    p, b = base.project, base.batch
    assert first_pass(base, tmp_path, {R005: "route", R007: "route", R009: "route"})[0] == 0
    before = tree_sha(p)
    code, rc, err = again(p, b)
    assert code == 0, err
    assert rc == {"schema": "klode-kit.again_receipt/1", "result": "nothing", "exit_code": 0, "batch_id": b,
                  "audit_rev": f"{b}/a1", "again_id": None, "records": 0, "fields": 0, "page": None,
                  "confirm_sha256": None}
    assert "--reopen" in err
    assert tree_sha(p) == before and log(base.adir) == [] and pages(base.adir) == []


# --------------------------------------------------------------------------- 7: page items


def _page_item_project(tmp_path: Path):
    """fx-dup10 with one unplaced value line read by claude (no box): one record, every cell agrees, one page item."""
    fx = tmp_path / "fx" / "fx-dup10"
    shutil.copytree(FIXTURES / "fx-dup10", fx)
    path = fx / "engine" / "claude" / "p001.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows.append({**rows[-1], "bbox_px": None, "line_idx": max(r["line_idx"] for r in rows) + 1,
                 "text": "血糖 5.6 mmol/L", "tokens": None})
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    b, adir = W.audited(proj, fx)
    code, _, err = W.submit(proj, None, tmp_path, batch_id=b)
    assert code == 10, err
    return proj, b, adir


def test_unacknowledged_page_item_is_on_the_again_page_and_acknowledged_there(tmp_path):
    """Spec 7 (S1): a pending record's page item is listed (``rev_base 1``) and acknowledged on the again page; the
    record is verified by a human and the next ``--again`` has nothing to do."""
    proj, b, adir = _page_item_project(tmp_path)
    (c,) = W.candidates(adir)
    pid = f"{c['rec_id']}/p-extra_values-claude"
    code, rc, err = again(proj, b)
    assert code == 10, err
    assert log(adir)[0]["records"] == [{"rec_id": c["rec_id"], "rev_base": 1, "fields": [pid]}]
    (rec,) = CP.page_data((proj / rc["page"]).read_bytes())["records"]
    (field,) = rec["fields"]
    assert (field["field_id"], field.get("page_item")) == (pid, True)
    choices = on_page(proj, rc, [{"field_id": pid, "kind": "none", "reason": "acknowledged"}])
    code, _, err = W.submit(proj, choices, tmp_path / "w")
    assert code == 0, err
    front = W.front(proj, c["rec_id"])
    assert front["effective"] == {"rev": 2, "audit_rev": f"{b}/a1", "status": "verified", "verified_by": "human"}
    assert W.body(proj, c["rec_id"], 2)["open"] == []
    (d,) = [d for d in W.fixes(adir) if d["field_id"] == pid]
    assert (d["by"], d["choice"], d["evidence"]["reason"], d["again_id"]) == ("human", "none", "acknowledged",
                                                                             rc["again_id"])
    assert again(proj, b)[1]["result"] == "nothing"


# --------------------------------------------------------------------------- 8: crash points


def _cli(project: Path, batch: str, env: dict) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "klode_kit", "kb-audit", "--project", str(project), "--again", "--batch", batch]
    return subprocess.run(argv, env=env, capture_output=True, timeout=120)


@pytest.fixture(scope="module")
def again_template(template, tmp_path_factory) -> RF.Base:
    """The base project after the first pass (R009 left), not yet ``--again``-ed."""
    root = tmp_path_factory.mktemp("again-template")
    b = RF.copy(template, root)
    assert first_pass(b, root / "w", {R005: "route", R007: "route"})[0] == 10
    return b


_REFERENCE: dict[str, dict[str, bytes]] = {}


def again_reference(tpl: RF.Base, root: Path) -> dict[str, bytes]:
    """The project after one uninterrupted ``--again`` CLI run on a copy of ``tpl`` (built once per module)."""
    if "ref" not in _REFERENCE:
        b = RF.copy(tpl, root)
        res = _cli(b.project, b.batch, base_env())
        assert res.returncode == 10, res.stderr.decode()
        _REFERENCE["ref"] = W.normalised(project_files(b.project))
    return _REFERENCE["ref"]


@pytest.mark.parametrize("point", [*CRASHPOINTS, "writeback.lock"])
def test_crash_then_rerun_equals_uninterrupted(again_template, tmp_path, point):
    """Spec 8 (S5): SIGKILL at ``point``; the rerun completes the page under the same ``again_id`` with one event
    line, no temporary file, and the project equals an uninterrupted run."""
    reference = again_reference(again_template, tmp_path / "ref")
    b = RF.copy(again_template, tmp_path)
    res = _cli(b.project, b.batch, base_env({"KLODE_KIT_CRASH_AT": point}))
    assert res.returncode == -signal.SIGKILL, (point, res.returncode, res.stderr.decode())
    assert len(log(b.adir)) <= 1
    rerun = _cli(b.project, b.batch, base_env())
    assert rerun.returncode == 10, rerun.stderr.decode()
    rc = json.loads(rerun.stdout.decode().strip().splitlines()[-1])
    assert rc["again_id"] == f"{b.batch}/a1/g0001"
    assert len(log(b.adir)) == 1 and len(pages(b.adir)) == 1
    assert not [x for x in b.project.rglob("*") if x.name.endswith(".tmp")]
    assert os.stat(b.project / rc["page"]).st_mode & 0o222 == 0
    assert W.normalised(project_files(b.project)) == reference


# --------------------------------------------------------------------------- refused, usage, lock


def test_refusals_write_nothing(base, tmp_path):
    """Spec "Refused" (S3): no ``work/``, a bad batch id, a batch without an audit revision, an audit revision whose
    candidates were never written back -> exit 30 with a ``refused`` receipt; nothing written."""
    empty = tmp_path / "empty"
    empty.mkdir()
    cases = [(empty, base.batch, "work"), (base.project, "nope", "batch"), (base.project, "0" * 12, "audit revision")]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    fb, _ = W.audited(fresh, W.LABS3)                          # audited, never ingested
    cases.append((fresh, fb, "--writeback"))
    for project, batch, needle in cases:
        before = tree_sha(project)
        code, rc, err = again(project, batch)
        assert code == 30, (batch, err)
        assert (rc["schema"], rc["result"], rc["exit_code"]) == ("klode-kit.again_receipt/1", "refused", 30)
        assert needle in rc["error"], rc["error"]
        assert tree_sha(project) == before


def test_usage_of_the_option(base, capsys):
    """``--again`` excludes ``--reopen`` / ``--writeback`` / ``--reason`` and needs ``--batch`` (usage, 2); with
    ``--batch`` it is the again run."""
    from klode_kit.audit import stage

    common = ["--project", str(base.project)]
    before = tree_sha(base.project)
    assert stage.main([*common, "--again", "--batch", base.batch, "--writeback"]) == 2
    assert stage.main([*common, "--again", "--batch", base.batch, "--reopen", f"{AUTO_REC}/r005-number-0"]) == 2
    assert stage.main([*common, "--again", "--batch", base.batch, "--reason", "x"]) == 2
    assert stage.main([*common, "--again"]) == 2
    assert tree_sha(base.project) == before
    capsys.readouterr()
    assert stage.main([*common, "--again", "--batch", base.batch]) == 10
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["result"] == "opened"


def test_lock_held_by_another_process_exit_40(base):
    """The project lock is the writeback's: a held lock stops ``--again`` with 40 and nothing written."""
    import fcntl

    lock = base.project / "work" / ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = tree_sha(base.project)
        code, rc, _ = again(base.project, base.batch)
        assert code == 40 and rc["exit_code"] == 40 and rc["result"] == "refused"
        assert tree_sha(base.project) == before
    finally:
        os.close(fd)
    assert again(base.project, base.batch)[0] == 10
