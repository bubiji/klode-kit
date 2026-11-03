"""WI-39: reviewer attribution - tell an AI reviewer's confirm-page decisions from the owner's (D-43) - unit lane
(spec ``.claude/tdd-guardian/wi-39-spec.md``).

Same project as the WI-38 tests (``_support/reopen_fx``): ``AUTO_REC`` is ``verified(auto)``, ``PENDING_REC`` waits for a
human on three fields (R005, R007, R009), ``OTHER_REC`` on two. A choices file may carry a top-level
``reviewer: {model, rule}``; the writeback then writes ``by: reviewer`` decisions and ``verified_by: reviewer``.
``kb-audit --attribute`` marks the earlier ``human`` commits of a batch as the reviewer's without rewriting anything.
Everything is asserted on files, exit codes and receipts; no mock of the record store.

Law (S4/S5): a verified revision says ``reviewer`` iff at least one decision it rests on is a reviewer's, whichever
order the sittings came in (checked for every owner/reviewer pairing of two sittings); ``--attribute`` is append-only
and idempotent (a second run changes nothing).
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.kb_ocr import tree, tree_sha
from _support.reopen_fx import AUTO_REC, PENDING_REC, base, template  # noqa: F401
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import schema

pytestmark = pytest.mark.unit

OTHER_REC = "r-27bf950bdbbd"
R005, R007, R009 = (f"{PENDING_REC}/{s}" for s in ("r005-number-0", "r007-number-0", "r009-unit-0"))
PENDING_TODO = (R005, R007, R009)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REV = {"model": "test-model-1", "rule": "test-rule-a"}
REV2 = {"model": "test-model-2", "rule": "test-rule-b"}


# --------------------------------------------------------------------------- helpers


def again(project: Path, batch: str):
    from klode_kit.audit import again as AG

    err, out = io.StringIO(), io.StringIO()
    code = AG.run(project, batch_id=batch, out=err, stdout=out)
    lines = [x for x in out.getvalue().splitlines() if x.strip()]
    return code, (json.loads(lines[-1]) if lines else None), err.getvalue()


def pick(adir: Path, fid: str, kind: str = "route") -> dict:
    if kind == "none":
        return {"field_id": fid, "kind": "none"}
    return {"field_id": fid, "kind": "route", "route": W.route_pick(adir, fid)}


def signed(choices: dict, reviewer: dict | None) -> dict:
    return {**choices, "reviewer": dict(reviewer)} if reviewer is not None else choices


def first_pass(b: RF.Base, where: Path, decided: tuple[str, ...], reviewer: dict | None, *, other: bool = False):
    """The ordinary page: PENDING_REC's fields in ``decided`` get a route, the rest "都不对"; OTHER_REC in full."""
    sel = [pick(b.adir, f, "route" if f in decided else "none") for f in PENDING_TODO]
    if other:
        sel += W.picks(b.adir, [OTHER_REC])
    return W.submit(b.project, signed(W.export(b.project, b.batch, b.adir, sel), reviewer), where)


def again_pass(b: RF.Base, where: Path, reviewer: dict | None):
    code, rc, err = again(b.project, b.batch)
    assert code == 10, err
    left = W.jsonl(b.adir / "again.jsonl")[-1]["records"][0]["fields"]
    choices = CP.export_choices((b.project / rc["page"]).read_bytes(), [pick(b.adir, f) for f in left])
    return W.submit(b.project, signed(choices, reviewer), where)


def decisions(adir: Path, rec: str) -> list[dict]:
    return [d for d in W.fixes(adir) if d["field_id"].startswith(rec + "/")]


def verified_by_everywhere(p: Path, rec: str, rev: int) -> set:
    front = W.front(p, rec)
    return {W.body(p, rec, rev)["verified_by"], front["effective"]["verified_by"],
            next(c for c in front["candidates"] if c["rev"] == rev)["verified_by"]}


def project_files(project: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(project).items() if not k.endswith("/")}


def attribute(project: Path, batch, model="m-attr", rule="r-attr", capsys=None, extra=()):
    from klode_kit.audit import stage

    argv = ["--project", str(project), "--attribute", *extra]
    if batch is not None:
        argv += ["--batch", batch]
    if model is not None:
        argv += ["--reviewer-model", model]
    if rule is not None:
        argv += ["--reviewer-rule", rule]
    code = stage.main(argv)
    out = capsys.readouterr().out if capsys is not None else ""
    lines = [x for x in out.splitlines() if x.strip()]
    return code, (json.loads(lines[-1]) if lines else None)


# --------------------------------------------------------------------------- 1: writeback with a reviewer


def test_reviewer_choices_are_written_as_reviewer(base, tmp_path):
    """Spec 1 (S1): a choices file with ``reviewer`` -> every decision of it ``by: reviewer`` carrying
    ``evidence.reviewer``; the new revision is ``verified_by: reviewer`` in body, candidate and ``effective``; the
    record nobody decided stays ``auto``."""
    p, adir = base.project, base.adir
    sel = W.picks(adir, [PENDING_REC, OTHER_REC])
    code, receipt, err = W.submit(p, signed(W.export(p, base.batch, adir, sel), REV), tmp_path / "w")
    assert code == 0, err
    for rec in (PENDING_REC, OTHER_REC):
        assert verified_by_everywhere(p, rec, 2) == {"reviewer"}
        ds = [d for d in decisions(adir, rec) if d["rev_new"] == 2 and d["by"] != "auto"]   # auto: the agreed fields
        assert ds and {d["by"] for d in ds} == {"reviewer"}
        assert all(d["evidence"]["reviewer"] == REV for d in ds)
        assert all(schema.issues("fixes", d) == [] for d in ds)
        assert schema.issues("record", W.front(p, rec)) == []
    assert W.front(p, AUTO_REC)["effective"]["verified_by"] == "auto"
    assert {d["by"] for d in decisions(adir, AUTO_REC)} == {"auto"}
    assert all("reviewer" not in d["evidence"] for d in decisions(adir, AUTO_REC))


def test_without_reviewer_the_writeback_is_as_before(base, tmp_path):
    """Spec 1 (regression, S1): no ``reviewer`` -> ``by: human`` / ``verified_by: human`` and no ``evidence.reviewer``."""
    p, adir = base.project, base.adir
    sel = W.picks(adir, [PENDING_REC])
    code, _, err = W.submit(p, W.export(p, base.batch, adir, sel), tmp_path / "w")
    assert code == 0, err
    assert verified_by_everywhere(p, PENDING_REC, 2) == {"human"}
    ds = [d for d in decisions(adir, PENDING_REC) if d["rev_new"] == 2 and d["by"] != "auto"]
    assert ds and {d["by"] for d in ds} == {"human"} and all("reviewer" not in d["evidence"] for d in ds)


# --------------------------------------------------------------------------- 2: the schema


@pytest.mark.parametrize("bad", [
    {"model": "m"},
    {"rule": "r"},
    {"model": "m", "rule": "r", "extra": "x"},
    {"model": "", "rule": "r"},
    {"model": "m", "rule": ""},
    {"model": "x" * 65, "rule": "r"},
    {"model": "m", "rule": "y" * 65},
    {"model": 3, "rule": "r"},
    "sonnet",
    None,
], ids=["no-rule", "no-model", "extra-key", "empty-model", "empty-rule", "model-65", "rule-65", "model-int", "string",
        "null"])
def test_malformed_reviewer_is_refused_before_any_write(base, tmp_path, bad):
    """Spec 2 (S3): a ``reviewer`` with a missing / extra key, an empty or 65-char value, or another type -> exit 30,
    not a byte written; the same file with a good reviewer is accepted (so the refusal is the reviewer's)."""
    p, adir = base.project, base.adir
    good = W.export(p, base.batch, adir, W.picks(adir, [PENDING_REC, OTHER_REC]))
    before = tree_sha(p)
    code, _, err = W.submit(p, {**good, "reviewer": bad}, tmp_path / "w1")
    assert code == 30, err
    assert "reviewer" in err
    assert tree_sha(p) == before
    ok = {**good, "reviewer": {"model": "x" * 64, "rule": "y" * 64}}   # the 64-char edge is legal
    assert W.submit(p, ok, tmp_path / "w2")[0] == 0


# --------------------------------------------------------------------------- 3: mixed sittings (the law)


@pytest.mark.parametrize("first,second,want", [
    (None, REV, "reviewer"),        # owner leaves it disputed, the reviewer finishes it
    (REV, None, "reviewer"),        # the reverse order
    (REV, REV2, "reviewer"),
    (None, None, "human"),          # owner both times
])
def test_two_sittings_verified_by_follows_any_reviewer_decision(base, tmp_path, first, second, want):
    """Spec 3 (S4/S5 over the four pairings, either order): ``verified_by`` is ``reviewer`` iff some decision the
    verified revision rests on is a reviewer's, and the decisions keep the ``by`` of the sitting that made them."""
    p, adir = base.project, base.adir
    code, _, err = first_pass(base, tmp_path / "w1", (R005, R007), first)
    assert code == 10, err
    front = W.front(p, PENDING_REC)
    assert front["status"] == "disputed" and "effective" not in front
    assert front["candidates"][-1]["verified_by"] is None
    code, _, err = again_pass(base, tmp_path / "w2", second)
    assert code == 0 or code == 10, err
    assert W.front(p, PENDING_REC)["effective"]["rev"] == 3
    assert verified_by_everywhere(p, PENDING_REC, 3) == {want}
    by = {d["field_id"]: d["by"] for d in decisions(adir, PENDING_REC) if d["rev_new"] in (2, 3) and d["by"] != "auto"
          and d["choice"] != "none"}
    assert by == {R005: "reviewer" if first else "human", R007: "reviewer" if first else "human",
                  R009: "reviewer" if second else "human"}
    assert schema.issues("record", W.front(p, PENDING_REC)) == []


# --------------------------------------------------------------------------- 4: a reviewer decision is a decided field


def test_reviewer_decided_fields_are_not_listed_again(base, tmp_path):
    """Spec 4 (S1): R005 and R007 decided by the reviewer, R009 left -> the again page lists R009 alone; a reviewer's
    "不是字段" drop is carried into the final verified body as a drop, exactly like an owner's."""
    p, adir = base.project, base.adir
    sel = [pick(adir, R005), {"field_id": R007, "kind": "none", "reason": "not_a_field"}, pick(adir, R009, "none")]
    code, _, err = W.submit(p, signed(W.export(p, base.batch, adir, sel), REV), tmp_path / "w1")
    assert code == 10, err
    code, rc, err = again(p, base.batch)
    assert code == 10, err
    (row,) = W.jsonl(adir / "again.jsonl")
    mine = [r for r in row["records"] if r["rec_id"] == PENDING_REC]   # OTHER_REC waits too and is listed in full
    assert mine == [{"rec_id": PENDING_REC, "rev_base": 2, "fields": [R009]}]
    choices = CP.export_choices((p / rc["page"]).read_bytes(), [pick(adir, R009)])
    code, _, err = W.submit(p, signed(choices, REV), tmp_path / "w2")
    assert code in (0, 10), err
    b3 = W.body(p, PENDING_REC, 3)
    assert (b3["status"], b3["verified_by"], b3["dropped"]) == ("verified", "reviewer", [R007])
    assert R005 in b3["fields"] and R009 in b3["fields"] and R007 not in b3["fields"]


def test_reviewer_acknowledged_page_item_counts_as_decided(tmp_path):
    """Spec 4 (S1): the page item of fx-dup10 acknowledged by the reviewer on the ordinary page -> verified
    (reviewer); ``--again`` then has nothing to do (the item is decided, not listed again)."""
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
    assert W.submit(proj, None, tmp_path, batch_id=b)[0] == 10
    (c,) = W.candidates(adir)
    pid = f"{c['rec_id']}/p-extra_values-claude"
    choices = W.export(proj, b, adir, [{"field_id": pid, "kind": "none", "reason": "acknowledged"}])
    code, _, err = W.submit(proj, signed(choices, REV), tmp_path / "w")
    assert code == 0, err
    assert verified_by_everywhere(proj, c["rec_id"], 2) == {"reviewer"}
    (d,) = [d for d in W.fixes(adir) if d["field_id"] == pid]
    assert (d["by"], d["evidence"]["reviewer"]) == ("reviewer", REV)
    assert again(proj, b)[1]["result"] == "nothing"


def test_same_reviewer_choices_twice_change_nothing(base, tmp_path):
    """Spec 1 (S5, idempotence): a reviewer's committed choices are matched by the idempotency key like an owner's."""
    p, adir = base.project, base.adir
    choices = signed(W.export(p, base.batch, adir, W.picks(adir, [PENDING_REC, OTHER_REC])), REV)
    assert W.submit(p, choices, tmp_path / "w1")[0] == 0
    after = tree_sha(p)
    code, receipt, err = W.submit(p, choices, tmp_path / "w2")
    assert code == 0, err
    assert {r["result"] for r in receipt["per_record"]} == {"unchanged"} and tree_sha(p) == after


# --------------------------------------------------------------------------- 5: --attribute


def _owner_sittings(b: RF.Base, where: Path):
    """The owner (no reviewer) decides OTHER_REC fully and PENDING_REC partially: two ended commits with human
    decisions; the ingest commits carry only auto decisions or none."""
    code, _, err = first_pass(b, where, (R005, R007), None, other=True)
    assert code == 10, err


def test_attribute_lists_exactly_the_ended_human_commits_and_is_append_only(base, tmp_path, capsys):
    """Spec 2/5 (S1 + S5): the receipt and the one ``attributions.jsonl`` line name exactly the ended commits with a
    ``by: human`` decision; every other file is byte-identical; a second run is ``nothing`` and writes nothing."""
    p, b, adir = base.project, base.batch, base.adir
    _owner_sittings(base, tmp_path / "w")
    want = sorted({d["commit_id"] for d in W.fixes(adir) if d["by"] == "human"})
    assert len(want) == 2
    before = project_files(p)

    code, rc = attribute(p, b, "mdl-1", "rule-1", capsys)

    assert code == 0
    assert rc == {"schema": "klode-kit.attribute_receipt/1", "result": "attributed", "exit_code": 0, "batch_id": b,
                  "commits": 2, "records": 2}
    after = project_files(p)
    path = f"work/{b}/audit/attributions.jsonl"
    assert set(after) - set(before) == {path}
    assert {k for k in before if after[k] != before[k]} == set()
    (line,) = [json.loads(x) for x in after[path].decode().splitlines()]
    assert W.TS.fullmatch(line.pop("at").encode())
    assert line == {"op": "attribute", "batch_id": b, "commit_ids": want, "by": "reviewer",
                    "reviewer": {"model": "mdl-1", "rule": "rule-1"}}

    again_code, rc2 = attribute(p, b, "mdl-1", "rule-1", capsys)
    assert again_code == 0 and rc2["result"] == "nothing" and (rc2["commits"], rc2["records"]) == (0, 0)
    assert project_files(p) == after


def test_attribute_has_a_single_reader_and_record_verified_by(base, tmp_path, capsys):
    """Spec 2/5 (S1): ``decided_by`` says ``reviewer`` for an attributed commit's decision, else the decision's own
    ``by``; ``record_verified_by`` says ``reviewer`` for a record those commits verified, ``auto`` for the auto
    one, ``None`` for the disputed one."""
    from klode_kit.audit import attribution as AT

    p, b, adir = base.project, base.batch, base.adir
    _owner_sittings(base, tmp_path / "w")
    human = [d for d in W.fixes(adir) if d["by"] == "human"]
    auto = [d for d in W.fixes(adir) if d["by"] == "auto"]
    assert human and auto
    assert {AT.decided_by(p, b, d) for d in human} == {"human"}      # before: the decision's own by
    assert (AT.record_verified_by(p, OTHER_REC), AT.record_verified_by(p, AUTO_REC),
            AT.record_verified_by(p, PENDING_REC)) == ("human", "auto", None)

    assert attribute(p, b, capsys=capsys)[0] == 0

    assert {AT.decided_by(p, b, d) for d in human} == {"reviewer"}
    assert {AT.decided_by(p, b, d) for d in auto} == {"auto"}
    assert (AT.record_verified_by(p, OTHER_REC), AT.record_verified_by(p, AUTO_REC),
            AT.record_verified_by(p, PENDING_REC)) == ("reviewer", "auto", None)
    assert W.front(p, OTHER_REC)["effective"]["verified_by"] == "human"   # the record itself is never rewritten


def test_owner_commit_on_top_of_an_attributed_reviewer_commit_is_reviewer(base, tmp_path, capsys):
    """Spec 2 (S5): after the attribution, the owner finishes PENDING_REC on the again page -> the new revision is
    ``verified_by: reviewer`` (it rests on an attributed reviewer decision); its own decision stays ``by: human``."""
    from klode_kit.audit import attribution as AT

    p, b, adir = base.project, base.batch, base.adir
    _owner_sittings(base, tmp_path / "w1")
    assert attribute(p, b, "mdl-1", "rule-1", capsys)[0] == 0
    code, _, err = again_pass(base, tmp_path / "w2", None)
    assert code == 0, err
    assert verified_by_everywhere(p, PENDING_REC, 3) == {"reviewer"}
    last = [d for d in W.fixes(adir) if d["rev_new"] == 3 and d["by"] != "auto"]
    assert last and {d["by"] for d in last} == {"human"}
    assert AT.record_verified_by(p, PENDING_REC) == "reviewer"


# --------------------------------------------------------------------------- 6: shown in the library


def _txt_line(result: dict, rec: str) -> str:
    return result["files"][f"library/records/{rec}.txt"].splitlines()[-1]


def test_export_shows_who_verified_each_record(base, tmp_path, capsys):
    """Spec 3/6 (S1): the last line of every exported record is ``verified_by: auto`` | ``human`` | ``reviewer
    (<model>)``, anchored like the others; the export is deterministic; an attributed record shows ``reviewer``
    with the attribution's model; the export map row for the line has no field_id and obeys its contract."""
    from klode_kit.build import records_to_txt as RT

    p, b, adir = base.project, base.batch, base.adir
    code, _, err = W.submit(p, signed(W.export(p, b, adir, W.picks(adir, [PENDING_REC])), REV), tmp_path / "w1")
    assert code == 0, err
    code, _, err = W.submit(p, W.export(p, b, adir, W.picks(adir, [OTHER_REC])), tmp_path / "w2")
    assert code == 0, err

    res = RT.export(p)
    assert _txt_line(res, AUTO_REC) == f"verified_by: auto ⟨{AUTO_REC}⟩"
    assert _txt_line(res, OTHER_REC) == f"verified_by: human ⟨{OTHER_REC}⟩"
    assert _txt_line(res, PENDING_REC) == f"verified_by: reviewer (test-model-1) ⟨{PENDING_REC}⟩"
    assert RT.export(p) == res
    for rec in (AUTO_REC, OTHER_REC, PENDING_REC):
        rows = [r for r in res["export_map"] if r["rec_id"] == rec and r["text"].startswith("verified_by:")]
        (row,) = rows
        assert row["field_ids"] == [] and row["line_kind"] == "source"
        assert row["source_file"] == f"library/records/{rec}.txt" and row["text"] == _txt_line(res, rec)
        assert schema.issues("export_map", row) == []
    # the verified_by lines are not on the item shelves
    assert not any(t.count("verified_by:") for rel, t in res["files"].items() if rel.startswith("library/items/"))

    assert attribute(p, b, "attr-model", "attr-rule", capsys)[0] == 0
    res2 = RT.export(p)
    assert _txt_line(res2, OTHER_REC) == f"verified_by: reviewer (attr-model) ⟨{OTHER_REC}⟩"
    assert _txt_line(res2, AUTO_REC) == f"verified_by: auto ⟨{AUTO_REC}⟩"
    assert _txt_line(res2, PENDING_REC) == f"verified_by: reviewer (test-model-1) ⟨{PENDING_REC}⟩"


# --------------------------------------------------------------------------- 7: refused


def test_attribute_refusals_write_nothing(base, tmp_path, capsys):
    """Spec 7 (S3): a bad batch id, a project without ``work/``, a batch with no audit, ``--attribute`` without
    ``--reviewer-model`` or without ``--reviewer-rule``, an over-long model -> exit 30, ``refused`` receipt, nothing
    written."""
    p, b = base.project, base.batch
    empty = tmp_path / "empty"
    empty.mkdir()
    cases = [(p, "nope", "m", "r"), (p, "0" * 12, "m", "r"), (empty, b, "m", "r"), (p, b, None, "r"),
             (p, b, "m", None), (p, None, "m", "r"), (p, b, "x" * 65, "r"), (p, b, "m", "")]
    for project, batch, model, rule in cases:
        before = tree_sha(project)
        code, rc = attribute(project, batch, model, rule, capsys)
        assert code == 30, (batch, model, rule, rc)
        assert (rc["schema"], rc["result"], rc["exit_code"]) == ("klode-kit.attribute_receipt/1", "refused", 30)
        assert tree_sha(project) == before
