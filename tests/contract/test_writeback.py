"""WI-08c: writeback, the sole committer (matrix 08c-01..07, 08c-11, 08c-12, 08c-14..16, 08c-18..20; design 02 r2.3,
r3.1 segment 2, r4.1, 1st-round 4.2 remainder; F14, F17; WI-08b -> 08c: tesseract never votes).

Products asserted (no mock anywhere; real files in a temporary data project, ``_support.writeback_fx``):

- ``records/_by_id/<rec_id>.md``: JSON front matter ``{rec_id, doc_id, page_id, record_idx, type, date, status,
  effective?, candidates[]}`` (contract ``record``; candidates carry ``verified_by`` so ``effective`` can equal the
  promoted candidate field by field);
- ``records/_rev/<rec_id>.r<N>.md``: the immutable body of revision N (``fields`` = decided values, ``dropped``);
- ``work/<batch>/audit/a<N>/fixes.jsonl``: append-only decisions (contract ``fixes``), one ``commit_id`` per record
  commit; ``a<N>/commits.jsonl``: the commit markers (``{op: begin|end, commit_id, ...}``);
- the writeback receipt printed on stdout (``per_record[]``) and the exit code: 0 every record in scope verified,
  10 some record awaits confirmation, 30 refused (stale or illegal choices), 40 lock held, 2 usage.

Engine boundary: recorded engine outputs of the fixtures (pairing 06-11/12, 04a cli) - as in WI-08a/08b.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from _support import writeback_fx as W
from _support.flows import base_env
from klode_kit.contract import provenance as P
from klode_kit.contract import ids, schema

pytestmark = pytest.mark.contract_replay


def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def record_ok(front: dict) -> None:
    assert schema.issues("record", front) == [], schema.issues("record", front)


def mine(rows: list[dict], rec_id: str) -> list[dict]:
    return [r for r in rows if r["field_id"].split("/")[0] == rec_id]


# --------------------------------------------------------------------------- 08c-01


def test_human_choice_commits_verified(tmp_path):
    """08c-01 (S1): legal choices on fx-u8 (both records): each record's effective revision is rev_base + 1,
    verified by a human; its fixes lines share one commit_id (different per record); the body holds the chosen
    values; every decision line is a legal ``fixes`` row."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    sel = W.picks(adir)
    choices = W.export(project, b, adir, sel)
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 0, err
    todo = W.to_confirm(adir)
    assert len(todo) == 2
    assert {r["rec_id"]: r["result"] for r in receipt["per_record"]} == {r: "committed" for r in todo}
    cells = W.cells(adir)
    rows = W.fixes(adir)
    assert rows and all(schema.issues("fixes", r) == [] for r in rows)
    commit_ids = set()
    for rec_id, fids in todo.items():
        base = next(i["rev_base"] for i in choices["items"] if i["rec_id"] == rec_id)
        front = W.front(project, rec_id)
        record_ok(front)
        assert front["effective"] == {"rev": base + 1, "audit_rev": f"{b}/a1", "status": "verified",
                                      "verified_by": "human"}
        assert front["status"] == "verified"
        rec_rows = mine(rows, rec_id)
        assert len({r["commit_id"] for r in rec_rows}) == 1
        commit_ids |= {r["commit_id"] for r in rec_rows}
        assert {r["rev_new"] for r in rec_rows} == {base + 1}
        human = {r["field_id"]: r for r in rec_rows if r["by"] == "human"}
        assert set(human) == set(fids)
        body = W.body(project, rec_id, base + 1)
        for s in (s for s in sel if s["field_id"] in fids):
            want = cells[s["field_id"]]["candidates"][s["route"]]["value"]
            assert human[s["field_id"]]["value"] == want
            assert body["fields"][s["field_id"]] == want
        assert set(body["fields"]) == {f["field_id"] for c in W.candidates(adir) if c["rec_id"] == rec_id
                                       for f in c["fields"]}
    assert len(commit_ids) == 2
    assert len({r["decision_id"] for r in rows}) == len(rows)


def test_writeback_products_pass_provenance(tmp_path):
    """r3.1 segments 1 and 2 over the writeback's products (WI-00b3 validator): route, manual (with its
    evidence.region), none + not_a_field (dropped) and auto decisions; an auto-verified record of another batch
    page. Segment 3 (export) and the version binding need a build (WI-01a/09b): the build receipt binds nothing."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.LABS3)
    code, _, err = W.submit(project, None, tmp_path, batch_id=b)  # ingest: p003 is verified(auto)
    assert code == 10, err
    todo = W.to_confirm(adir)
    rec_a, rec_b = list(todo)
    fa = todo[rec_a]
    sel = [{"field_id": fa[0], "kind": "route", "route": W.route_pick(adir, fa[0])},
           {"field_id": fa[1], "kind": "manual", "value": "4.40"},
           {"field_id": fa[2], "kind": "none", "reason": "not_a_field"}]
    sel += W.picks(adir, [rec_b])
    choices = W.export(project, b, adir, sel)
    code, _, err = W.submit(project, choices, tmp_path)
    assert code == 0, err
    manual = next(r for r in W.fixes(adir) if r["choice"] == "manual")
    assert manual["evidence"]["region"]["der_sha256"] and len(manual["evidence"]["region"]["bbox_px"]) == 4
    nobuild = tmp_path / "nobuild"
    nobuild.mkdir()
    (nobuild / "build_receipt.json").write_text(json.dumps({"audit_bindings": {}}), encoding="utf-8")
    bdir = project / "work" / b
    layout = P.Layout(root=project, manifest=[bdir / "manifest.jsonl"], derived=[bdir / "derived.jsonl"],
                      ocr_dirs=[bdir / "ocr"], audit_dir=bdir, records_dir=project / "records", build_dir=nobuild,
                      artifact_base=project)
    assert P.validate(project, layout) == []


# --------------------------------------------------------------------------- 08c-02


def _corrupt(choices: dict, what: str, b: str) -> dict:
    c = W.forged(choices)
    if what == "batch_id":
        c["batch_id"] = "0" * 12 if b != "0" * 12 else "1" * 12
    elif what == "fingerprints":
        route = sorted(c["fingerprints"])[0]
        c["fingerprints"][route] = c["fingerprints"][route].rsplit("/", 1)[0] + "/t9"
    elif what == "rev_base":
        for it in c["items"]:
            it["rev_base"] += 1
    elif what == "seen":
        for it in c["items"]:
            it["seen"]["vision"] = "999.9"
    elif what == "confirm_sha256":
        c["confirm_sha256"] = "f" * 64 if c["confirm_sha256"] != "f" * 64 else "e" * 64
    elif what == "audit_rev":
        c["audit_rev"] = f"{b}/a2"
    else:  # pragma: no cover - test bug
        raise AssertionError(what)
    return c


@pytest.mark.parametrize("what", ["batch_id", "fingerprints", "rev_base", "seen", "confirm_sha256", "audit_rev"])
def test_stale_choices_exit30(tmp_path, what):
    """08c-02 (S3): batch_id, fingerprints, rev_base or seen wrong (plus the sealed page sha and the audit
    revision): exit 30 and the data project's tree sha is unchanged (nothing written, no receipt line)."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    choices = W.export(project, b, adir, W.picks(adir))
    before = W.tree_sha(project)
    code, receipt, err = W.submit(project, _corrupt(choices, what, b), tmp_path)
    assert code == 30, err
    assert W.tree_sha(project) == before
    assert not any(r.get("result") == "committed" for r in (receipt or {}).get("per_record", []))


def test_old_page_after_new_audit_rev_exit30(tmp_path):
    """08c-02 companion (S3): a page of a1 submitted after a new run set gave a2 (new fingerprints): refused with
    exit 30, nothing written."""
    project = proj(tmp_path)
    b, a1 = W.audited(project, W.REDO)
    old = W.export(project, b, a1, W.picks(a1))
    _, a2 = W.audited(project, W.REDO, engine="engine_t3", seq=3)
    assert a2.name == "a2"
    before = W.tree_sha(project)
    code, _, err = W.submit(project, old, tmp_path)
    assert code == 30, err
    assert W.tree_sha(project) == before


# --------------------------------------------------------------------------- 08c-03 / 08c-04


@pytest.mark.parametrize("blank", ["", "  ", "　"])
def test_manual_empty_is_none(tmp_path, blank):
    """08c-03 (S3): a manual value that is empty (or blank) is ``none``: the record's candidate is disputed, no
    effective revision exists, the decision is recorded as a human ``none``."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    choices = W.forged(W.export(project, b, adir, W.picks(adir)))
    target = choices["items"][0]
    target["choice"] = {"kind": "manual", "value": blank}
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 10, err
    front = W.front(project, target["rec_id"])
    record_ok(front)
    assert "effective" not in front
    assert front["candidates"][-1]["status"] == "disputed"
    assert front["status"] == "disputed"
    row = next(r for r in W.fixes(adir) if r["field_id"] == target["field_id"])
    assert (row["by"], row["choice"], row["value"]) == ("human", "none", None)


def test_none_not_a_field_dropped(tmp_path):
    """08c-04 (S2): ``none`` + ``not_a_field`` drops the field: the record is verified, the revision body has no
    value for it and lists it in ``dropped``; the decision is a human ``none`` with that reason."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    sel = W.picks(adir)
    sel[0] = {"field_id": sel[0]["field_id"], "kind": "none", "reason": "not_a_field"}
    choices = W.export(project, b, adir, sel)
    code, _, err = W.submit(project, choices, tmp_path)
    assert code == 0, err
    fid = sel[0]["field_id"]
    rec_id = fid.split("/")[0]
    front = W.front(project, rec_id)
    assert front["effective"]["status"] == "verified"
    body = W.body(project, rec_id, front["effective"]["rev"])
    assert fid not in body["fields"]
    assert body["dropped"] == [fid]
    row = next(r for r in W.fixes(adir) if r["field_id"] == fid)
    assert (row["choice"], row["evidence"]["reason"]) == ("none", "not_a_field")


# WI-01a: XPASSed, xfail removed
def test_none_not_a_field_absent_from_export(tmp_path):
    """08c-04 (S2), export half: the dropped field is not in the ``records_to_txt`` output."""
    from klode_kit.build import records_to_txt  # noqa: F401  (WI-01a)

    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    sel = W.picks(adir)
    sel[0] = {"field_id": sel[0]["field_id"], "kind": "none", "reason": "not_a_field"}
    assert W.submit(project, W.export(project, b, adir, sel), tmp_path)[0] == 0
    out = records_to_txt.export(project)  # type: ignore[attr-defined]
    assert sel[0]["field_id"] not in json.dumps(out, ensure_ascii=False)


# --------------------------------------------------------------------------- 08c-05


def test_record_atomic_multi_field(tmp_path):
    """08c-05 (S3): record A (3 fields to confirm, one of them illegal) is not written at all and its bad field_id
    is listed (exit 30); record B (all legal) is committed; ``per_record[]`` gives both results."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.LABS3)
    todo = W.to_confirm(adir)
    rec_a, rec_b = list(todo)
    assert len(todo[rec_a]) == 3
    choices = W.forged(W.export(project, b, adir, W.picks(adir)))
    bad = next(i for i in choices["items"] if i["field_id"] == todo[rec_a][1])
    bad["seen"]["claude"] = "0.00"
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 30
    assert todo[rec_a][1] in err
    results = {r["rec_id"]: r for r in receipt["per_record"]}
    assert results[rec_a]["result"] == "rejected"
    assert [e["field_id"] for e in results[rec_a]["errors"]] == [todo[rec_a][1]]
    assert results[rec_b]["result"] == "committed"
    assert W.front(project, rec_a) is None
    assert not W.body_path(project, rec_a, 1).exists()
    assert mine(W.fixes(adir), rec_a) == []
    assert W.front(project, rec_b)["effective"]["verified_by"] == "human"


# --------------------------------------------------------------------------- 08c-06 / 08c-07


def test_idempotent_resubmit(tmp_path):
    """08c-06 (S5): the same choices twice: the second run makes no new revision and appends no decision; the
    records, fixes and commit markers are byte-identical; exit 0 and every record ``unchanged``."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    choices = W.export(project, b, adir, W.picks(adir))
    assert W.submit(project, choices, tmp_path)[0] == 0
    before = W.state(project, b)
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 0, err
    assert {r["result"] for r in receipt["per_record"]} == {"unchanged"}
    assert W.state(project, b) == before
    keys = [(r["field_id"], r["rev_base"], r["choice"], r.get("route"), r.get("value")) for r in W.fixes(adir)]
    assert len(keys) == len(set(keys))


def test_idempotent_resubmit_after_ingest(tmp_path):
    """08c-06 (S5) on the pipeline order (writeback ingests the candidates, then the page is rendered with
    rev_base = the candidate's rev): resubmitting does not add a revision."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    choices = W.export(project, b, adir, W.picks(adir))
    assert {i["rev_base"] for i in choices["items"]} == {1}
    assert W.submit(project, choices, tmp_path)[0] == 0
    before = W.state(project, b)
    assert W.submit(project, choices, tmp_path)[0] == 0
    assert W.state(project, b) == before
    for rec_id in W.to_confirm(adir):
        front = W.front(project, rec_id)
        assert [c["rev"] for c in front["candidates"]] == [1, 2]
        assert front["effective"]["rev"] == 2


def test_fixes_append_only(tmp_path):
    """08c-07 (S5): successive commits on one record (none -> disputed r1, legal -> verified r2, changed value ->
    r3): after each, the previous bytes of fixes.jsonl and commits.jsonl are a prefix of the new bytes."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    rec_id, fids = next(iter(W.to_confirm(adir).items()))
    first = W.picks(adir, [rec_id])
    first[0] = {"field_id": first[0]["field_id"], "kind": "none"}
    steps = [W.export(project, b, adir, first)]
    legal = W.forged(W.export(project, b, adir, W.picks(adir, [rec_id])))
    for it in legal["items"]:
        it["rev_base"] = 1
    steps.append(legal)
    changed = W.forged(legal)
    changed["items"][-1]["choice"] = {"kind": "manual", "value": "211"}
    for it in changed["items"]:
        it["rev_base"] = 2
    steps.append(changed)
    prev = {"fixes.jsonl": b"", "commits.jsonl": b""}
    for n, ch in enumerate(steps, start=1):
        code, _, err = W.submit(project, ch, tmp_path)
        assert code in (0, 10), err
        assert W.front(project, rec_id)["candidates"][-1]["rev"] == n
        for name in prev:
            now = (adir / name).read_bytes()
            assert now.startswith(prev[name]) and len(now) > len(prev[name])
            prev[name] = now


# --------------------------------------------------------------------------- 08c-11 / 08c-12


def test_choice_on_page_level_uncertain_rejected(tmp_path):
    """08c-11 (S3): choices forged for a record with ``route_missing_page`` (fx-redo t2: codex lost the page)
    are refused with exit 30; the record stays disputed (its front matter is byte-identical) and nothing is ever
    verified."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.REDO, engine="engine_t2", seq=2)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    (cand,) = W.candidates(adir)
    assert cand["status"] == "disputed"
    rec_id = cand["rec_id"]
    before = W.record_path(project, rec_id).read_bytes()
    html = W.page(project, b, adir)
    from klode_kit.audit import confirm_page as CP

    cells = W.cells(adir)
    runs = json.loads((adir / "runs.json").read_text(encoding="utf-8"))
    items = []
    for f in cand["fields"]:
        seen = {e: (cells[f["field_id"]]["candidates"].get(e) or {}).get("value") for e in runs}
        route = next(e for e in W.ROUTES if seen.get(e) is not None)
        items.append({"rec_id": rec_id, "rev_base": 1, "field_id": f["field_id"],
                      "cell": cells[f["field_id"]]["cell"], "choice": {"kind": "route", "route": route}, "seen": seen})
    choices = {"schema": "klode-kit.choices/1", "batch_id": b, "audit_rev": f"{b}/{adir.name}",
               "confirm_sha256": CP.confirm_sha256(html),
               "fingerprints": {e: r for e, r in runs.items() if r}, "items": items}
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 30, err
    assert W.record_path(project, rec_id).read_bytes() == before
    front = W.front(project, rec_id)
    assert front["status"] == "disputed" and "effective" not in front
    assert all(c["status"] != "verified" for c in front["candidates"])
    assert receipt["per_record"][0]["result"] == "rejected"


def test_rev_files_readonly(tmp_path):
    """08c-12 (S3): after a commit, opening ``_rev/X.r1.md`` for writing raises PermissionError and its bytes
    are unchanged."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    assert W.submit(project, W.export(project, b, adir, W.picks(adir)), tmp_path)[0] == 0
    rec_id = next(iter(W.to_confirm(adir)))
    path = W.body_path(project, rec_id, 1)
    data = path.read_bytes()
    for mode in ("r+b", "ab", "wb"):
        with pytest.raises(PermissionError):
            open(path, mode)  # noqa: SIM115
    assert path.read_bytes() == data
    assert os.stat(path).st_mode & 0o222 == 0


# --------------------------------------------------------------------------- 08c-14 / 08c-15


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_writeback_same_choices(tmp_path, attempt):
    """08c-14 (S5): two processes submit the same choices at once: exactly one new revision per record; the other
    process exits 40 (lock held) or is an idempotent no-op (0); no decision is duplicated."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    path = W.write_choices(tmp_path / "choices.json", W.export(project, b, adir, W.picks(adir)))
    env = base_env()
    procs = [subprocess.Popen(W.cli_argv(project, path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(2)]
    codes = []
    for p in procs:
        _, err = p.communicate(timeout=120)
        codes.append((p.returncode, err.decode()))
    assert sorted(c for c, _ in codes)[0] == 0, codes
    assert sorted(c for c, _ in codes)[1] in (0, 40), codes
    for rec_id in W.to_confirm(adir):
        assert [c["rev"] for c in W.front(project, rec_id)["candidates"]] == [1]
    ids_ = [r["decision_id"] for r in W.fixes(adir)]
    assert len(ids_) == len(set(ids_))
    keys = [(r["field_id"], r["rev_base"]) for r in W.fixes(adir)]
    assert len(keys) == len(set(keys))


def test_same_field_different_choice_new_rev(tmp_path):
    """08c-15 (S2): a second choices file with another value for one field and ``rev_base`` = the current rev
    gives revision N+1 holding the new value; the earlier revision is superseded and its body unchanged."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    rec_id, fids = next(iter(W.to_confirm(adir).items()))
    first = W.export(project, b, adir, W.picks(adir, [rec_id]))
    assert W.submit(project, first, tmp_path)[0] == 0
    r1 = W.sha(W.body_path(project, rec_id, 1))
    old_value = W.body(project, rec_id, 1)["fields"][fids[-1]]
    route = W.route_pick(adir, fids[-1], avoid=old_value)
    second = W.forged(W.export(project, b, adir, W.picks(adir, [rec_id])))
    for it in second["items"]:
        it["rev_base"] = 1
        if it["field_id"] == fids[-1]:
            it["choice"] = {"kind": "route", "route": route}
    code, _, err = W.submit(project, second, tmp_path)
    assert code == 0, err
    front = W.front(project, rec_id)
    record_ok(front)
    assert front["effective"]["rev"] == 2
    new_value = W.cells(adir)[fids[-1]]["candidates"][route]["value"]
    assert new_value != old_value
    assert W.body(project, rec_id, 2)["fields"][fids[-1]] == new_value
    assert front["candidates"][0]["superseded_by"] == 2
    assert W.sha(W.body_path(project, rec_id, 1)) == r1


# --------------------------------------------------------------------------- 08c-16


def _stage(project, b, name):
    env = base_env()
    py = [sys.executable, "-m", "klode_kit"]
    argv = {
        "kb-audit": [*py, "kb-audit", "--batch", b, "--project", str(project)],
        "confirm-page": [*py, "confirm-page", "--batch", b, "--project", str(project)],
        "views": [*py, "views", "--project", str(project)],
        "build": [*py, "build", "--project", str(project)],
        "kb-verifier": [*py, "kb-verifier", "--batch", b, "--project", str(project)],
        "kb-check": [*py, "kb-check", "--kb", str(project), "--entry", "skill:/klode-kit:kb-check"],
    }[name]
    return subprocess.run(argv, env=env, capture_output=True, timeout=120, cwd=str(project))


@pytest.mark.parametrize("name", [
    "kb-audit",
    "confirm-page",
    "views",  # WI-08d: XPASSed, xfail removed
    pytest.param("build", marks=pytest.mark.xfail(strict=True, reason="依赖 WI-09b（build 尚未实现）")),
    pytest.param("kb-verifier", marks=pytest.mark.xfail(strict=True, reason="依赖 WI-12（kb-verifier 尚未实现）")),
    pytest.param("kb-check", marks=pytest.mark.xfail(strict=True, reason="依赖 WI-10a（kb-check 内核尚未实现）")),
])
def test_writeback_sole_writer_of_status(tmp_path, name):
    """08c-16 (S6, metamorphic): after a fx-u8 writeback, running another stage leaves ``records/_by_id/**``
    byte-identical. The stage must actually run (exit 0 or 10), so a missing stage cannot pass vacuously."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    assert W.submit(project, W.export(project, b, adir, W.picks(adir)), tmp_path)[0] == 0
    before = W.by_id_bytes(project)
    assert before
    res = _stage(project, b, name)
    assert res.returncode in (0, 10), res.stderr.decode()
    assert W.by_id_bytes(project) == before


# --------------------------------------------------------------------------- 08c-18 / 08c-19 / 08c-20 / tesseract


def _labs3_record(tmp_path):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.LABS3)
    rec_id, fids = next(iter(W.to_confirm(adir).items()))
    assert len(fids) == 3
    return project, b, adir, rec_id, fids


def test_partial_choices_record_not_verified(tmp_path):
    """08c-18 (S3, F14): choices cover 2 of the record's 3 fields to confirm: exit 30, the missing field_id is
    listed on stderr and in ``per_record[]``; the record is not written at all (no front matter, no fixes, no
    revision)."""
    project, b, adir, rec_id, fids = _labs3_record(tmp_path)
    choices = W.export(project, b, adir, W.picks(adir, [rec_id])[:2])
    before = W.tree_sha(project)
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 30
    assert fids[2] in err
    (res,) = receipt["per_record"]
    assert (res["rec_id"], res["result"], res["missing"]) == (rec_id, "rejected", [fids[2]])
    assert W.tree_sha(project) == before


def test_choice_on_missing_route_cell_rejected(tmp_path):
    """08c-19 (S3): a choice whose route has no value on that cell (mlx on fx-u1-3labs r009 unit) is refused:
    exit 30, the record is not written."""
    project, b, adir, rec_id, fids = _labs3_record(tmp_path)
    missing = [f for f in fids if W.cells(adir)[f]["candidates"].get("mlx") is None]
    assert len(missing) == 1
    choices = W.forged(W.export(project, b, adir, W.picks(adir, [rec_id])))
    item = next(i for i in choices["items"] if i["field_id"] == missing[0])
    item["choice"] = {"kind": "route", "route": "mlx"}
    before = W.tree_sha(project)
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 30
    assert missing[0] in err
    assert [e["field_id"] for e in receipt["per_record"][0]["errors"]] == [missing[0]]
    assert W.tree_sha(project) == before


def test_choices_duplicate_field_id_rejected(tmp_path):
    """08c-20 (S3, F17): 3 items for a record with 3 fields to confirm, one field_id twice (so one field is not
    covered), handed straight to the writeback: exit 30 naming the duplicate and the missing field_id; the record
    is not written."""
    project, b, adir, rec_id, fids = _labs3_record(tmp_path)
    choices = W.forged(W.export(project, b, adir, W.picks(adir, [rec_id])))
    choices["items"][2] = W.forged(choices["items"][1])
    before = W.tree_sha(project)
    code, receipt, err = W.submit(project, choices, tmp_path)
    assert code == 30
    assert fids[1] in err and fids[2] in err
    (res,) = receipt["per_record"]
    assert (res["duplicate"], res["missing"]) == ([fids[1]], [fids[2]])
    assert W.tree_sha(project) == before


def test_tesseract_choice_rejected(tmp_path):
    """WI-08b -> 08c (S3): tesseract supplies geometry and never votes; a page showing its reading still cannot
    make it the chosen value: exit 30, the record is not written."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8, tesseract=True)
    runs = json.loads((adir / "runs.json").read_text(encoding="utf-8"))
    assert runs.get("tesseract")
    sel = W.picks(adir)
    rec_id = sel[0]["field_id"].split("/")[0]
    sel = [s for s in sel if s["field_id"].startswith(rec_id)]
    sel[0] = {"field_id": sel[0]["field_id"], "kind": "route", "route": "tesseract"}
    choices = W.export(project, b, adir, sel)
    before = W.tree_sha(project)
    code, _, err = W.submit(project, choices, tmp_path)
    assert code == 30
    assert "tesseract" in err
    assert W.tree_sha(project) == before


# --------------------------------------------------------------------------- CLI entry


def test_cli_entry_kb_audit_writeback(tmp_path):
    """The entry ``klode-kit kb-audit --writeback choices.json`` (batch taken from the choices) commits, prints the
    receipt as JSON on stdout and records the commit markers (begin + end per commit)."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    path = W.write_choices(tmp_path / "c.json", W.export(project, b, adir, W.picks(adir)))
    res = W.cli(project, path, base_env())
    assert res.returncode == 0, res.stderr.decode()
    receipt = json.loads(res.stdout.decode().strip().splitlines()[-1])
    assert receipt["batch_id"] == b and receipt["audit_rev"] == f"{b}/a1"
    cids = {r["commit_id"] for r in W.fixes(adir)}
    marks = W.commits(adir)
    assert {m["commit_id"] for m in marks if m["op"] == "end"} == cids
    assert {m["commit_id"] for m in marks if m["op"] == "begin"} == cids
    assert all(c.startswith(f"{b}/a1/c") for c in cids)
    assert sorted(cids) == [ids.commit_id(f"{b}/a1", n) for n in (1, 2)]
