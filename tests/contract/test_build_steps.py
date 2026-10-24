"""WI-09b: the 13-step build into the immutable ``kb/<id>/builds/<build_id>/`` (matrix 09b-01..07, 10, 11, 12;
design 02 r3.2 "不可变版本目录", r3.1 audit_bindings, r4.1 stale_candidates; K8).

Real processes everywhere: the project's own commands through the launcher, the real ``klode`` 0.5.0 of PATH (the
recorded-output replay of the matrix is the fixtures' recorded engine rows: the project is made by the real stages
on a fixture), the fake ``claude`` replay for card writers. Faults are injected at the double boundary by
``tests/_support/build_fx.Fault`` (a launcher wrapper and a ``klode`` wrapper that fail one call).

Laws (K8): after a failure at step k no later step has a receipt, ``current`` stays, nothing outside
``builds/<id>/`` is written; a finished build tree is read-only recursively and its MANIFEST detects any change.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import export_fx as X
from _support import views_fx as V
from _support import writeback_fx as W
from _support.confirm_fx import audit
from klode_kit import pipeline as P
from klode_kit.build import run_build as RB
from klode_kit.contract.schema import issues

pytestmark = pytest.mark.contract_replay

CLOSED = set(P.EXIT_CODE.values())


def one_build(project: Path) -> Path:
    (bd,) = B.builds(project)
    return bd


def inside(path: str, bd: Path, project: Path) -> bool:
    """``path`` (project-relative) is the build directory, below it, or an ancestor directory of it."""
    rel = bd.relative_to(project).as_posix()
    return path == rel or path.startswith(rel + "/") or rel.startswith(path + "/")


# --------------------------------------------------------------------------- 09b-01


def test_steps_fixed_order_all_pass(tmp_path):
    """09b-01 (S1): 13 steps in the fixed order, all PASS, started times non-decreasing, one receipt per step."""
    project = B.project(tmp_path)
    res = B.build(project, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    rec = B.receipt(bd)
    assert issues("build_receipt", rec) == []
    assert B.step_names(rec) == list(RB.STEPS) and len(RB.STEPS) == 13
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * 13 and rec["status"] == "PASS"
    started = [s["started"] for s in rec["steps"]]
    assert started == sorted(started)
    assert sorted(p.stem for p in (bd / "receipts").glob("*.json")) == sorted(RB.STEPS)
    for s in rec["steps"]:
        assert s["receipt"] == f"receipts/{s['step']}.json" and (bd / s["receipt"]).is_file()
        step = json.loads((bd / s["receipt"]).read_text(encoding="utf-8"))
        assert issues("receipt", step) == [] and step["step"] == s["step"] and step["exit_code"] == 0
    s = B.summary(res)
    assert (s["status"], s["exit_code"], s["build_id"]) == ("PASS", 0, rec["build_id"])
    assert rec["deident_mode"] == "pseudonym" and rec["kb_id"] == B.kb_dir(project).name
    assert len(rec["audit_bindings"]) == 3 and rec["excluded"] == []
    # the medical path is deterministic: cards_content came from records-to-l1, zero model calls
    assert (bd / "library" / "items").is_dir() and (bd / "library" / "records").is_dir()
    assert list((bd / "cards_content").glob("*.md")) and (bd / "agent").is_dir()
    assert not (B.kb_dir(project) / "current").exists()  # publishing is WI-09d


# --------------------------------------------------------------------------- 09b-01 through the pipeline


def _pipeline_rig(tmp_path):
    from _support import pipeline_fx as F

    return F.Rig(tmp_path, "fx-labs-3x")


def _choices(rig):
    from klode_kit.audit import confirm_page as CP

    adir = rig.batch_dir / "audit" / "a1"
    return W.write_choices(rig.project.parent / "choices.json", CP.export_choices((adir / "confirm.html").read_bytes(),
                                                                                     W.picks(adir)))


def test_pipeline_stage_build_runs_the_13_steps_and_is_reused(tmp_path):
    """09b-01 (S1) through ``pipeline --stage build``: the build stage replaces the unimplemented interface, its
    stage receipt lists the build receipt, and a second run reuses it (three hashes) without a new build."""
    rig = _pipeline_rig(tmp_path)
    first = rig.run()
    if first.returncode == 10:
        first = rig.run("--choices", str(_choices(rig)), "--stage", "build")
    else:
        first = rig.run("--stage", "build")
    assert first.returncode == 0, first.stderr.decode()
    (bd,) = B.builds(rig.project)
    assert B.receipt(bd)["status"] == "PASS"
    stage = rig.receipt("build")
    assert (stage["status"], stage["exit_code"]) == ("done", 0)
    assert [o["path"] for o in stage["outputs"]] == [bd.relative_to(rig.project).as_posix() + "/build_receipt.json"]
    again = rig.run("--stage", "build")
    assert again.returncode == 0, again.stderr.decode()
    assert B.builds(rig.project) == [bd]
    rows = json.loads(again.stdout.decode().splitlines()[-1])["stages"]
    assert [r["how"] for r in rows if r["stage"] == "build"] == ["reused"]
    calls = [c["argv"][0] for c in rig.calls()]
    assert calls.count("build") == 1


# --------------------------------------------------------------------------- 09b-02


@pytest.mark.parametrize("fx", ["fx-labs-3x", "fx-u8"])
def test_no_writes_outside_builds(tmp_path, fx):
    """09b-02 (S5): the tree snapshot of the data project and of the (tmp) HOME before / after: every difference
    is the new ``builds/<id>/`` (or an ancestor directory of it)."""
    project = B.project(tmp_path, fx)
    home = B.home(tmp_path)
    before_p, before_h = B.snapshot(project), B.snapshot(home)
    res = B.build(project, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    diff = B.changed(before_p, B.snapshot(project))
    assert diff and all(inside(p, bd, project) for p in diff), sorted(p for p in diff if not inside(p, bd, project))
    assert any(p.startswith(bd.relative_to(project).as_posix() + "/") for p in diff)
    assert B.changed(before_h, B.snapshot(home)) == set()


def test_first_build_writes_only_the_deident_key_besides_the_build(tmp_path):
    """09b-02 (documented exception): the de-identification key is the project's one-time secret, created at its
    first use; the first build of a project without one writes it (and its ``.gitignore``) and nothing else."""
    project = B.project(tmp_path)
    key = project / "_private"
    assert key.is_dir()
    moved = tmp_path / "private-aside"
    key.rename(moved)
    before = B.snapshot(project)
    res = B.build(project, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    diff = B.changed(before, B.snapshot(project))
    outside = sorted(p for p in diff if not inside(p, bd, project))
    assert outside and all(p == "_private" or p.startswith("_private/") for p in outside), outside
    assert (project / "_private" / "deident_key.json").is_file()


# --------------------------------------------------------------------------- 09b-03


FAULTS = [  # (k, mode, tool, tokens)
    (1, "records", "launcher", ["records-to-txt"]),
    (2, "records", "launcher", ["init-kb"]),
    (3, "book", "klode", ["ingest"]),
    (4, "book", "launcher", ["gen-manifest"]),
    (5, "records", "klode", ["build"]),
    (6, "records", "launcher", ["records-to-l1"]),
    (7, "records", "launcher", ["check-cards"]),
    (8, "records", "launcher", ["write-card"]),
    (9, "records", "klode", ["check", "--strict"]),
    (10, "records", "launcher", ["gen-desk"]),
    (11, "records", "launcher", ["desk-build", "--verify"]),
    (12, "records", "launcher", ["gen-agent"]),
    (13, "records", "launcher", ["smoke-mcp"]),
]


@pytest.mark.parametrize("k,mode,tool,tokens", FAULTS, ids=[f"step{f[0]:02d}-{f[3][0]}" for f in FAULTS])
def test_failure_at_step_k_stops(tmp_path, k, mode, tool, tokens):
    """09b-03 (S5): a failure injected into step k - the steps before PASS, step k FAIL, the later ones NOT_RUN
    without a receipt; ``current`` unchanged; nothing outside the build directory written."""
    if mode == "records":
        project = B.project(tmp_path)
        args = ["--kb", "kb/main"]
    else:
        project = tmp_path / "proj"
        project.mkdir()
        args = ["--kb", "kb/main", *B.book_args(tmp_path)]
    kb = project / "kb" / "main"
    (kb / "builds").mkdir(parents=True)
    (kb / "current").symlink_to("builds/b-20260101T000000Z-00000000")
    before = B.snapshot(project)
    fault = B.Fault(tmp_path)
    res = B.run_with(project, tmp_path, fault, tool, tokens, *args, extra_env=B.writer_replay(tmp_path))
    assert res.returncode in CLOSED - {0}, res.stderr.decode()
    assert any(c["tool"] == tool and all(t in c["argv"] for t in tokens) for c in fault.calls())
    (bd,) = B.builds(project)
    rec = B.receipt(bd)
    assert issues("build_receipt", rec) == []
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * (k - 1) + ["FAIL"] + ["NOT_RUN"] * (13 - k)
    assert rec["status"] == "FAIL"
    assert sorted(p.stem for p in (bd / "receipts").glob("*.json")) == sorted(RB.STEPS[:k])
    failed = json.loads((bd / "receipts" / f"{RB.STEPS[k - 1]}.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed" and failed["exit_code"] == res.returncode
    assert all("receipt" not in s for s in rec["steps"][k:])
    assert os.readlink(kb / "current") == "builds/b-20260101T000000Z-00000000"
    diff = B.changed(before, B.snapshot(project))
    assert all(inside(p, bd, project) for p in diff), sorted(p for p in diff if not inside(p, bd, project))
    assert not (bd / "MANIFEST.sha256").exists() or RB.verify_manifest(bd) == []
    assert B.writable_bits(bd) == []


# --------------------------------------------------------------------------- 09b-04 / 09b-12


def test_build_dir_manifest_detects_a_change(tmp_path):
    """09b-04 (S3): MANIFEST.sha256 verifies; after the owner makes a file writable and changes it - or adds one -
    the verification names it."""
    project = B.project(tmp_path)
    assert B.build(project, tmp_path).returncode == 0
    bd = one_build(project)
    manifest = bd / "MANIFEST.sha256"
    rows = [ln.split("  ", 1) for ln in manifest.read_text(encoding="utf-8").splitlines()]
    assert rows == sorted(rows, key=lambda r: r[1]) and all(re.fullmatch(r"[0-9a-f]{64}", h) for h, _ in rows)
    listed = {p for _, p in rows}
    on_disk = {p.relative_to(bd).as_posix() for p in bd.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256"
               and ".readers" not in p.relative_to(bd).parts}
    assert listed == on_disk and "build_receipt.json" in listed
    assert RB.verify_manifest(bd) == []
    B.unlock(bd)
    target = next(bd.glob("cards_content/*.md"))
    target.write_text(target.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
    problems = RB.verify_manifest(bd)
    assert problems and any(target.relative_to(bd).as_posix() in p for p in problems)
    (bd / "stray.txt").write_text("x", encoding="utf-8")
    assert any("stray.txt" in p for p in RB.verify_manifest(bd))


def test_build_tree_fully_readonly(tmp_path):
    """09b-12 (S3): every file and directory of the finished build has no write bit; opening for write raises
    PermissionError, creating a file in any directory fails (the reader leases ``.readers/`` are outside)."""
    assert os.geteuid() != 0
    project = B.project(tmp_path)
    assert B.build(project, tmp_path).returncode == 0
    bd = one_build(project)
    assert B.writable_bits(bd) == []
    files = [p for p in bd.rglob("*") if p.is_file()]
    dirs = [bd, *[p for p in bd.rglob("*") if p.is_dir()]]
    assert files and len(dirs) > 5
    for f in files:
        with pytest.raises(PermissionError):
            open(f, "r+b")
    for d in dirs:
        with pytest.raises(PermissionError):
            (d / "new-file.txt").write_text("x")


# --------------------------------------------------------------------------- 09b-05 / U6 (rebuild after a failed redo)


def test_audit_bindings_effective_only(tmp_path):
    """09b-05 (S3): after a failed redo (candidate disputed) the build binds the old effective revision, the record
    is not excluded and is listed in ``stale_candidates``; the export still carries revision 1."""
    project = tmp_path / "proj"
    project.mkdir()
    b, a1 = W.audited(project, W.REDO)
    assert W.submit(project, W.export(project, b, a1, W.picks(a1)), tmp_path)[0] == 0
    (x,) = W.to_confirm(a1)
    V.views_ok(project)
    B.deident_key(project)
    assert B.build(project, tmp_path).returncode == 0
    W.batch(project, W.REDO, engine="engine_t2", seq=2)
    audit(project, b)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 0
    assert W.front(project, x)["effective"]["rev"] == 1 and W.front(project, x)["candidates"][-1]["rev"] == 2
    assert B.build(project, tmp_path).returncode == 0
    first, second = B.builds(project)
    assert B.receipt(first).get("stale_candidates", []) == []
    rec = B.receipt(second)
    assert rec["audit_bindings"][x] == {"rev": 1, "audit_rev": f"{b}/a1"}
    assert x not in [e["rec_id"] for e in rec["excluded"]]
    stale = [s for s in rec["stale_candidates"] if s["rec_id"] == x]
    assert len(stale) == 1 and stale[0]["candidate_rev"] == 2 and stale[0]["reason"].startswith("candidate_")
    rows = [json.loads(x_) for x_ in (second / "src" / "export_map.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["rev"] for r in rows if r["rec_id"] == x} == {1}
    assert issues("build_receipt", rec) == []


# --------------------------------------------------------------------------- 09b-06 / U8


def test_excluded_not_in_inputs_u8(tmp_path):
    """09b-06 (S3): a record whose click was ``none`` (disputed) is in ``excluded`` with its reason and appears in
    no exported line, card, desk file or input list of the build (only in ``build_receipt.json``)."""
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.U8)
    sel = W.picks(adir)
    victim_fid = sel[0]["field_id"]
    victim = victim_fid.split("/")[0]
    sel[0] = {"field_id": victim_fid, "kind": "none"}
    assert W.submit(project, W.export(project, b, adir, sel), tmp_path)[0] in (0, 10)  # 10: a record is left disputed (08c-03)
    assert W.front(project, victim)["status"] == "disputed"
    B.deident_key(project)
    res = B.build(project, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    rec = B.receipt(bd)
    entry = [e for e in rec["excluded"] if e["rec_id"] == victim]
    assert len(entry) == 1 and entry[0]["reason"] == "not_verified:disputed"
    assert victim not in rec["audit_bindings"]
    leaks = [p.relative_to(bd).as_posix() for p in bd.rglob("*")
             if p.is_file() and p.name != "build_receipt.json" and victim.encode() in p.read_bytes()]
    assert leaks == []
    assert issues("build_receipt", rec) == []


# --------------------------------------------------------------------------- 09b-07 (book path, writer replay)


def test_pipeline_never_passes_force(tmp_path):
    """09b-07 (S3): a writer whose card does not pass gate 1 - the build fails at gate1 with exit 20, write_card
    never runs, and neither the fake claude's nor the launcher's / klode's call log contains ``--force``."""
    project = tmp_path / "proj"
    project.mkdir()
    fault = B.Fault(tmp_path)
    replay = B.writer_replay(tmp_path, bad=["ch02"])
    res = B.run_with(project, tmp_path, fault, None, (), "--kb", "kb/main", *B.book_args(tmp_path), extra_env=replay)
    assert res.returncode == 20, res.stderr.decode()
    bd = one_build(project)
    rec = B.receipt(bd)
    by = {s["step"]: s["status"] for s in rec["steps"]}
    assert by["dispatch_writers"] == "PASS" and by["gate1"] == "FAIL" and by["write_card"] == "NOT_RUN"
    claude_log = Path(replay["KLODE_KIT_FAKE_LOG"]).read_text(encoding="utf-8")
    assert "cards_content/ch01.md" in claude_log  # the writers really ran (replay)
    argv_logs = [json.dumps(c["argv"]) for c in fault.calls()]
    assert argv_logs and not any("--force" in a for a in argv_logs) and "--force" not in claude_log


def test_forced_write_receipt_fails_the_step(tmp_path):
    """09b-07 (S3): ``write_receipt.forced == true`` is a step FAIL even when write-card exited 0."""
    project = B.project(tmp_path)
    fault = B.Fault(tmp_path)
    res = B.run_with(project, tmp_path, fault, "launcher", ["write-card"], "--kb", "kb/main", mutate="forced")
    assert res.returncode == 20, res.stderr.decode()
    rec = B.receipt(one_build(project))
    by = {s["step"]: s["status"] for s in rec["steps"]}
    assert by["write_card"] == "FAIL" and by["check_strict"] == "NOT_RUN"


# --------------------------------------------------------------------------- 09b-08 (contract half)


def test_build_id_follows_the_inputs(tmp_path):
    """09b-08 (S4, contract half): the same project twice - same hash part, different directories; a changed
    input (the desk spec) - another hash part; ``inputs_hash`` of the receipt is the hash the id carries."""
    project = B.project(tmp_path)
    assert B.build(project, tmp_path, "--kb", "kb/main").returncode == 0
    assert B.build(project, tmp_path, "--kb", "kb/main").returncode == 0
    first, second = B.builds(project)
    assert first.name != second.name and first.name.split("-")[-1] == second.name.split("-")[-1]
    ra, rb = B.receipt(first), B.receipt(second)
    assert ra["inputs_hash"] == rb["inputs_hash"] and first.name.endswith(ra["inputs_hash"][:8])
    desk = json.loads((first / "src" / "desk.json").read_text(encoding="utf-8"))
    desk["dimensions"][0]["cues"] = [*desk["dimensions"][0]["cues"], "extra-cue-for-this-test"]
    changed = tmp_path / "desk-changed.json"
    changed.write_text(json.dumps(desk, ensure_ascii=False, indent=1), encoding="utf-8")
    res = B.build(project, tmp_path, "--kb", "kb/main", "--desk", str(changed))
    third = [p for p in B.builds(project) if p not in (first, second)]
    assert len(third) == 1 and third[0].name.split("-")[-1] != first.name.split("-")[-1], res.stderr.decode()
    assert B.receipt(third[0])["inputs_hash"] != ra["inputs_hash"]


# --------------------------------------------------------------------------- 09b-10


def test_same_build_concurrent_exit40(tmp_path):
    """09b-10 (S3): two processes build the same inputs at once - one runs, the other exits 40 and creates
    nothing; exactly one ``builds/<id>/`` exists."""
    project = B.project(tmp_path)
    argv = [sys.executable, "-m", "klode_kit", "build", "--project", str(project), "--kb", "kb/main"]
    e = B.env(tmp_path)
    p1 = subprocess.Popen(argv, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(project))
    root = project / "kb" / "main" / "builds"
    deadline = time.time() + 60
    while time.time() < deadline and not (root.is_dir() and any(root.glob("b-*"))):
        assert p1.poll() is None, p1.communicate()
        time.sleep(0.02)
    assert root.is_dir() and any(root.glob("b-*"))
    p2 = subprocess.run(argv, env=e, capture_output=True, timeout=120, cwd=str(project))
    out1, err1 = p1.communicate(timeout=600)
    assert p2.returncode == 40, p2.stderr.decode()
    assert b"lock" in p2.stderr.lower()
    assert p1.returncode == 0, err1.decode()
    assert len([p for p in root.iterdir()]) == 1
    assert B.receipt(one_build(project))["status"] == "PASS"


# --------------------------------------------------------------------------- 09b-11


def test_zero_verified_records_build(tmp_path):
    """09b-11 (S2; spec open, design 03 F4): records, none of them verified -> exit 20 ``no_verified_records``;
    ``excluded`` lists every record; no step after export ran."""
    project = tmp_path / "proj"
    project.mkdir()
    for n in (1, 2):
        rid = f"r-{n:012x}"
        X.put_record(project, rid, {1: {"status": "disputed", "audit_rev": "0123456789ab/a1",
                                        "fields": {f"{rid}/r001-number-0": "1"}}}, effective=None, date="2026-10-01")
    B.deident_key(project)
    res = B.build(project, tmp_path)
    assert res.returncode == 20, res.stderr.decode()
    s = B.summary(res)
    assert (s["exit_code"], s["reason"]) == (20, "no_verified_records")
    rec = B.receipt(one_build(project))
    assert rec["status"] == "FAIL" and rec["audit_bindings"] == {}
    assert sorted(e["rec_id"] for e in rec["excluded"]) == ["r-000000000001", "r-000000000002"]
    assert all(e["reason"] == "not_verified:disputed" for e in rec["excluded"])
    assert [s_["status"] for s_ in rec["steps"]] == ["FAIL"] + ["NOT_RUN"] * 12


def test_no_records_at_all_is_the_same_exit(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    res = B.build(project, tmp_path)
    assert res.returncode == 20 and B.summary(res)["reason"] == "no_verified_records"


# --------------------------------------------------------------------------- deviations carried into 09b


def test_src_is_the_private_side_and_the_agent_points_at_the_pointer(tmp_path):
    """WI-01c: ``src/`` holds audited original values (export_map ``transforms[].from``) - the build marks it private
    (``src/PRIVATE.md`` and ignore lines) and records the de-identification mode. WI-02d: the expert agent is
    generated with ``--kb-root kb/<id>/current`` and the build id."""
    project = B.project(tmp_path)
    assert B.build(project, tmp_path, "--kb", "kb/main").returncode == 0
    bd = one_build(project)
    rec = B.receipt(bd)
    assert rec["deident_mode"] == "pseudonym"
    assert (bd / "src" / "PRIVATE.md").is_file()
    ignore = (bd / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "src/export_map.jsonl" in ignore and "src/audit_bindings.json" in ignore
    agent = (bd / "agent" / "main-klode-expert.md").read_text(encoding="utf-8")
    assert "kb/main/current" in agent and f"klode-kit build {rec['build_id']}" in agent


def test_dateshift_build_keeps_originals_in_src_only(tmp_path):
    """WI-01c: under ``deident.mode = dateshift`` the library lines carry shifted dates, ``src/export_map.jsonl``
    keeps the audited ones, the receipt says so and the provenance check of the step passes."""
    project = B.project(tmp_path)
    (project / "klode-kit.toml").write_text('[deident]\nmode = "dateshift"\n', encoding="utf-8")
    res = B.build(project, tmp_path, "--kb", "kb/main")
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    assert B.receipt(bd)["deident_mode"] == "dateshift"
    rows = [json.loads(x) for x in (bd / "src" / "export_map.jsonl").read_text(encoding="utf-8").splitlines()]
    shifted = [t for r in rows for t in r["transforms"] if t["kind"] == "dateshift"]
    assert shifted
    library = "\n".join(p.read_text(encoding="utf-8") for p in (bd / "library").glob("**/*.txt"))
    for t in shifted:
        assert t["to"] in library and t["from"] not in library


def test_book_items_and_records_shelves_do_not_call_gen_manifest(tmp_path):
    """WI-03a: the medical shelves (one file is one source) never call gen-manifest; the book path does."""
    project = B.project(tmp_path)
    fault = B.Fault(tmp_path)
    assert B.run_with(project, tmp_path, fault, None, (), "--kb", "kb/main").returncode == 0
    cmds = [c["argv"][0] for c in fault.calls() if c["tool"] == "launcher"]
    assert "gen-manifest" not in cmds and "split-chapters" not in cmds and "records-to-l1" in cmds
    order = [c for c in cmds if c in ("write-card", "gen-desk", "desk-build")]
    assert order[:2] == ["write-card", "gen-desk"] and order.index("desk-build") > order.index("gen-desk")
    book_project = tmp_path / "book"
    book_project.mkdir()
    sub = tmp_path / "second"
    sub.mkdir()
    fault2 = B.Fault(sub)
    res = B.run_with(book_project, tmp_path, fault2, None, (), "--kb", "kb/demo-klode", *B.book_args(tmp_path),
                     extra_env=B.writer_replay(tmp_path))
    assert res.returncode == 0, res.stderr.decode()
    assert "gen-manifest" in [c["argv"][0] for c in fault2.calls() if c["tool"] == "launcher"]


def test_missing_syntheses_fail_the_desk_step_with_the_dimension_names(tmp_path):
    """Book path without the desk workers' syntheses: step desk_apply FAILS (exit 20) naming the missing
    dimensions; later steps NOT_RUN (the workers are dispatched by the skill layer, not by the kernel)."""
    project = tmp_path / "proj"
    project.mkdir()
    args = [a for a in B.book_args(tmp_path)]
    i = args.index("--syntheses")
    del args[i:i + 2]
    res = B.build(project, tmp_path, "--kb", "kb/demo-klode", *args, extra_env=B.writer_replay(tmp_path))
    assert res.returncode == 20, res.stderr.decode()
    rec = B.receipt(one_build(project))
    by = {s["step"]: s for s in rec["steps"]}
    assert by["desk_apply"]["status"] == "FAIL" and "construction" in by["desk_apply"]["detail"]
    assert by["desk_verify"]["status"] == "NOT_RUN"


def test_environment_error_of_the_writer_is_exit_61_when_the_gate_is_closed(tmp_path):
    """09a-08, full version: a real ``claude`` behind a closed G2 gate (no replay fake first on PATH) - the bridge
    refuses, the build ends with exit 61 at dispatch_writers, no loop, no later step."""
    import shutil

    project = tmp_path / "proj"
    project.mkdir()
    bindir = tmp_path / "realish"
    bindir.mkdir()
    (bindir / "claude").write_text("#!/bin/sh\necho real >&2\nexit 3\n", encoding="utf-8")
    (bindir / "claude").chmod(0o755)
    klode_dir = str(Path(shutil.which("klode")).parent)
    path = ":".join([str(bindir), klode_dir, "/usr/bin", "/bin", str(Path(sys.executable).parent)])
    res = B.build(project, tmp_path, "--kb", "kb/demo-klode", *B.book_args(tmp_path), extra_env={"PATH": path})
    assert res.returncode == 61, res.stderr.decode()
    rec = B.receipt(one_build(project))
    by = {s["step"]: s["status"] for s in rec["steps"]}
    assert by["dispatch_writers"] == "FAIL" and by["gate1"] == "NOT_RUN"
    assert not (one_build(project) / "loops").exists()


def test_authentication_failure_is_exit_40_without_loops(tmp_path):
    """09a-08, full version (S3): the fake ``claude`` answers every writer call with an authentication failure - the
    build ends with exit 40 at dispatch_writers (an environment-class result), later steps NOT_RUN, no loop
    receipt or directory anywhere in the build."""
    project = tmp_path / "proj"
    project.mkdir()
    scenario = tmp_path / "auth.json"
    scenario.write_text(json.dumps({"steps": [{"stdout": "", "stderr": "Not logged in · Please run /login\n",
                                               "exit_code": 1}]}), encoding="utf-8")
    res = B.build(project, tmp_path, "--kb", "kb/demo-klode", *B.book_args(tmp_path),
                  extra_env={"KLODE_KIT_FAKE_SCENARIO": str(scenario), "KLODE_KIT_FAKE_LOG": str(tmp_path / "c.jsonl")})
    assert res.returncode == 40, res.stderr.decode()
    bd = one_build(project)
    by = {s["step"]: s["status"] for s in B.receipt(bd)["steps"]}
    assert by["dispatch_writers"] == "FAIL" and by["gate1"] == "NOT_RUN"
    assert not [p for p in bd.rglob("*") if "loop" in p.name]
    assert (bd / "receipts" / "dispatch_writers.json").is_file()
