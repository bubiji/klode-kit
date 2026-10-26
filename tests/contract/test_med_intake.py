"""WI-13b: ``/med-intake`` orchestration, contract_replay lane (matrix 13b-01, 13b-02, 13b-05, 13b-06, 13b-07 + edges).

Replay boundary: the four OCR routes - the cloud routes (claude, codex) included - are the engine replay seam, i.e.
recorded rows of the synthetic fixtures; ``doctor`` is the launcher wrapper's stub. kb-ocr, kb-audit and confirm-page
run for real as children of the pipeline kernel. Nothing here starts a real CLI. Paired real paths: 06-11 / 06-12
(model lane replays against the real CLIs) and ``tests/cli/test_pipeline_cli.py`` (real doctor).

Law (13b-02, S5): the date aliases are stable - a rerun, a crash and a rerun, or a later batch of the same day never
changes where an existing alias points, and never overwrites one.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from _support import pipeline_fx as F
from _support.intake_fx import AUDIT_LINE, OCR_LINE, IntakeRig
from klode_kit.contract.schema import issues

pytestmark = pytest.mark.contract_replay


@pytest.fixture()
def rig(tmp_path: Path) -> IntakeRig:
    return IntakeRig(tmp_path)


def _stable_tree(rig: IntakeRig) -> dict[str, bytes]:
    return {k: v for k, v in rig.tree().items() if not k.endswith(".lock")}


# ---------------------------------------------------------------- 13b-01
def test_u1_stdout_and_receipt(rig: IntakeRig) -> None:
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    lines = res.stdout.decode().splitlines()
    assert OCR_LINE in lines
    assert AUDIT_LINE in lines
    assert lines.index(OCR_LINE) < lines.index(AUDIT_LINE)
    assert lines[-1] == "status: awaiting_confirm"

    # the aliases of U1: work/20261022 -> work/<batch_id>, confirm/20261022.html
    work_alias = rig.work / "20261022"
    assert work_alias.is_symlink() and str(work_alias.readlink()) == rig.batch_id
    assert work_alias.resolve() == rig.batch_dir.resolve()
    confirm_alias = rig.project / "confirm" / "20261022.html"
    assert confirm_alias.is_symlink()
    assert confirm_alias.resolve() == (rig.batch_dir / "audit" / "a1" / "confirm.html").resolve()
    assert confirm_alias.read_bytes() == (rig.batch_dir / "audit" / "a1" / "confirm.html").read_bytes()

    # the registration receipt
    rec = rig.intake_receipt()
    assert rec["schema"] == "klode-kit.med_intake/1"
    assert rec["batch_id"] == rig.batch_id
    assert (rec["alias"], rec["confirm"]) == ("20261022", "confirm/20261022.html")
    assert (rec["record_type"], rec["type_source"]) == ("lab", "header")
    assert (rec["date"], rec["date_source"]) == ("2026-10-22", "inbox_dir")
    assert rec["source"] == {"inbox": "2026-10-22", "files": ["p001.png", "p002.png", "p003.png"]}
    assert rec["pages"] == 3 and rec["routes"] == ["vision", "mlx", "claude", "codex"]
    assert rec["ocr_failures"] == [] and rec["disputes"] == 5
    assert (rec["status"], rec["exit_code"]) == ("awaiting_confirm", 10)
    assert [(s["stage"], s["status"], s["exit_code"]) for s in rec["stages"]] == [
        ("preflight", "done", 0), ("ocr", "done", 0), ("audit", "done", 10)]
    # a public repository and a private project: no absolute path, no user name, no wall clock in the receipt
    text = json.dumps(rec, ensure_ascii=False)
    assert str(rig.project) not in text and str(rig.inbox) not in text and "started" not in text

    # the pipeline's own receipts are all there and valid; the kernel was reused, not rewritten (no second stage chain)
    assert rig.receipt_names() == ["preflight", "ocr", "audit"]
    for name in ("preflight", "ocr", "audit"):
        assert issues("receipt", rig.receipt(name)) == []
    # replay only: the commands that ran are the launcher's own subcommands, never a bare claude / codex
    assert {c["argv"][0] for c in rig.calls()} == {"doctor", "kb-ocr", "kb-audit", "confirm-page"}
    # and it stopped at the human: nothing was written back
    assert not (rig.project / "records").exists()


def test_stdout_counts_follow_the_batch_not_the_example(tmp_path: Path) -> None:
    """The counts are measured: one page (fx-labs-1x, four routes agree) prints ``1 页 × 4 路 OK`` and no dispute."""
    rig = IntakeRig(tmp_path, "fx-labs-1x")
    res = rig.run()
    assert res.returncode == 0, res.stderr.decode()
    lines = res.stdout.decode().splitlines()
    assert lines[:2] == ["ocr: 1 页 × 4 路 OK", "audit: 0 条分歧，无需确认"]
    assert lines[-1] == "status: nothing_to_confirm"
    rec = rig.intake_receipt()
    assert (rec["disputes"], rec["status"], rec["exit_code"], rec["confirm"]) == (0, "nothing_to_confirm", 0, None)
    assert not (rig.project / "confirm").exists()  # no page, so no link
    assert (rig.work / "20261022").is_symlink()


def test_rx_header_is_typed_rx(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path, "fx-rx-stamp")
    res = rig.run()
    assert res.returncode in (0, 10), res.stderr.decode()
    rec = rig.intake_receipt()
    assert (rec["record_type"], rec["type_source"]) == ("rx", "header")


# ---------------------------------------------------------------- 13b-02
def test_alias_symlink_stable(rig: IntakeRig) -> None:
    first = rig.run()
    assert first.returncode == 10
    links = rig.link_targets()
    assert links == {"confirm/20261022.html": f"../work/{rig.batch_id}/audit/a1/confirm.html",
                     "work/20261022": rig.batch_id}
    tree = _stable_tree(rig)
    for _ in range(2):  # law: a rerun changes nothing - links, receipt, pages, bytes
        again = rig.run()
        assert again.returncode == 10, again.stderr.decode()
        assert again.stdout == first.stdout
        assert rig.link_targets() == links
        assert _stable_tree(rig) == tree
    # an explicit --type that agrees with the header changes the source of the type, not the aliases
    explicit = rig.run("--type", "lab")
    assert explicit.returncode == 10
    assert rig.link_targets() == links
    assert rig.intake_receipt()["type_source"] == "explicit"


@pytest.mark.parametrize("point", ["med_intake.lock", "med_intake.aliased", "med_intake.linked", "med_intake.receipt"])
def test_alias_survives_a_crash(tmp_path: Path, point: str) -> None:
    clean = IntakeRig(tmp_path / "clean")
    assert clean.run().returncode == 10
    victim = IntakeRig(tmp_path / "victim")
    killed = victim.run(env={"KLODE_KIT_CRASH_AT": point})
    assert killed.returncode == -signal.SIGKILL
    again = victim.run()
    assert again.returncode == 10, again.stderr.decode()
    assert victim.link_targets() == clean.link_targets()
    assert victim.intake_receipt() == clean.intake_receipt()


# ---------------------------------------------------------------- 13b-05 (spec: 02 r5, new alias <date>-2)
def test_alias_collision_same_date(tmp_path: Path) -> None:
    first = IntakeRig(tmp_path / "a")
    assert first.run().returncode == 10
    second = IntakeRig(tmp_path / "b", "fx-u3-mixed")
    second.project = first.project  # the same data project, another inbox with the same date directory
    assert second.batch_id != first.batch_id
    before = first.link_targets()
    res = second.run("--type", "lab")
    assert res.returncode == 10, res.stderr.decode()
    after = first.link_targets()
    # the first batch's aliases are exactly as they were; the second batch got -2, not an overwrite
    for name, target in before.items():
        assert after[name] == target
    assert str((first.work / "20261022-2").readlink()) == second.batch_id
    assert (first.project / "confirm" / "20261022-2.html").resolve() == (
        second.batch_dir / "audit" / "a1" / "confirm.html").resolve()
    lines = res.stdout.decode().splitlines()
    assert any(line.endswith("→ confirm/20261022-2.html") for line in lines)
    assert second.intake_receipt()["alias"] == "20261022-2"
    # a third one is -3; and rerunning the second keeps -2
    third = IntakeRig(tmp_path / "c", "fx-rx-stamp")
    third.project = first.project
    assert third.run().returncode in (0, 10)
    assert str((first.work / "20261022-3").readlink()) == third.batch_id
    assert second.run("--type", "lab").returncode == 10
    assert str((first.work / "20261022-2").readlink()) == second.batch_id
    assert not (first.work / "20261022-4").exists()


def test_a_real_directory_named_like_the_alias_is_not_overwritten(rig: IntakeRig) -> None:
    squatter = rig.work / "20261022"
    squatter.mkdir(parents=True)
    (squatter / "keep.txt").write_text("mine", encoding="utf-8")
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    assert (squatter / "keep.txt").read_text(encoding="utf-8") == "mine" and not squatter.is_symlink()
    assert rig.intake_receipt()["alias"] == "20261022-2"


# ---------------------------------------------------------------- 13b-06
def test_empty_inbox_exit2(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path)
    for img in rig.inbox.iterdir():
        shutil.move(str(img), str(tmp_path / img.name))
    res = rig.run()
    assert res.returncode == 2
    assert b"no images" in res.stderr
    assert list(rig.project.iterdir()) == []  # no directory, no link, no lock


def test_inbox_with_only_non_images_and_missing_inbox_exit2(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path)
    for img in list(rig.inbox.iterdir()):
        img.unlink()
    (rig.inbox / "note.txt").write_text("not an image", encoding="utf-8")
    assert rig.run().returncode == 2
    assert list(rig.project.iterdir()) == []
    gone = IntakeRig(tmp_path / "x")
    # point at a path that never existed
    gone.inbox = gone.inbox.parent / "2099-01-01"
    assert gone.run().returncode == 2
    assert list(gone.project.iterdir()) == []


def test_missing_project_exit2(rig: IntakeRig, tmp_path: Path) -> None:
    argv = rig.argv()
    argv[argv.index("--project") + 1] = str(tmp_path / "nope")
    res = subprocess.run(argv, env=rig.env, capture_output=True, timeout=60, cwd=str(rig.project))
    assert res.returncode == 2
    assert not (tmp_path / "nope").exists()


# ---------------------------------------------------------------- type (13b-03 / 13b-04 end to end)
def _headerless_engine(tmp_path: Path, rig: IntakeRig) -> Path:
    """A copy of the recorded rows whose title lost the 检验 keyword (every route reads 报告单)."""
    engine = tmp_path / "engine-no-keyword"
    shutil.copytree(rig.fx / "engine", engine)
    for path in engine.rglob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("检验报告单", "报告单").replace("检验报告", "报告"), encoding="utf-8")
    return engine


def test_no_header_keyword_requires_type(tmp_path: Path) -> None:
    probe = IntakeRig(tmp_path / "probe")
    engine = _headerless_engine(tmp_path, probe)
    rig = IntakeRig(tmp_path / "run", engine=engine)
    res = rig.run()
    assert res.returncode == 2
    assert b"--type" in res.stderr
    assert not (rig.work / "20261022").exists() and not (rig.project / "confirm").exists()
    assert "audit" not in rig.receipt_names()  # it did not go on to the audit
    # with --type it goes through, and the receipt says where the type came from
    ok = rig.run("--type", "lab")
    assert ok.returncode == 10, ok.stderr.decode()
    rec = rig.intake_receipt()
    assert (rec["record_type"], rec["type_source"]) == ("lab", "explicit")


def test_explicit_type_overrides_the_header(rig: IntakeRig) -> None:
    res = rig.run("--type", "rx")
    assert res.returncode == 10, res.stderr.decode()
    rec = rig.intake_receipt()
    assert (rec["record_type"], rec["type_source"]) == ("rx", "explicit")


def test_invalid_type_exit2(rig: IntakeRig) -> None:
    res = rig.run("--type", "scan")
    assert res.returncode == 2
    assert rig.link_targets() == {}


# ---------------------------------------------------------------- date
def test_date_without_a_dated_inbox_comes_from_the_page(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path, day="today-batch")
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    rec = rig.intake_receipt()
    assert (rec["date"], rec["date_source"], rec["alias"]) == ("2026-10-22", "page", "20261022")


def test_compact_date_directory(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path, day="20261022")
    assert rig.run().returncode == 10
    assert rig.intake_receipt()["date_source"] == "inbox_dir"


# ---------------------------------------------------------------- failures pass through
def test_preflight_failure_ends_the_run_without_aliases(rig: IntakeRig) -> None:
    res = rig.run(env={"FAKE_DOCTOR_EXIT": "40"})
    assert res.returncode == 40
    assert res.stdout.decode().strip() == ""
    assert rig.link_targets() == {}
    assert not (rig.batch_dir / "med_intake.json").exists()


def test_route_failure_is_printed_not_hidden(tmp_path: Path) -> None:
    probe = IntakeRig(tmp_path / "probe")
    engine = tmp_path / "engine-missing-codex"
    shutil.copytree(probe.fx / "engine", engine)
    (engine / "codex" / "p002.jsonl").unlink()
    rig = IntakeRig(tmp_path / "run", engine=engine)
    res = rig.run()
    assert res.returncode in (0, 10, 40), res.stderr.decode()
    out = res.stdout.decode()
    assert "ocr: 3 页 × 4 路 OK" not in out
    assert "codex:p002" in out
    if res.returncode == 10:
        assert rig.intake_receipt()["ocr_failures"] == ["codex:p002"]


# ---------------------------------------------------------------- 13b-07
def test_concurrent_med_intake_exit40(tmp_path: Path) -> None:
    one = IntakeRig(tmp_path / "one")
    other = IntakeRig(tmp_path / "other", "fx-u3-mixed")
    other.project = one.project
    point = "med_intake.lock"
    first = one.popen("--type", "lab", env=one.pause_env(point))
    try:
        one.wait_reached(point, first)
        second = other.run("--type", "lab")
        assert second.returncode == 40, second.stderr.decode()
        assert b"another med-intake" in second.stderr
        # the loser wrote nothing for its batch and took no alias
        assert not other.batch_dir.exists()
        assert not (one.work / "20261022").exists()
        one.release(point)
        out, err = first.communicate(timeout=180)
        assert first.returncode == 10, err.decode()
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
    assert OCR_LINE in out.decode() and AUDIT_LINE in out.decode()
    assert str((one.work / "20261022").readlink()) == one.batch_id
    # the loser can simply be run again: it queues behind the day's first batch with the next alias
    retry = other.run("--type", "lab")
    assert retry.returncode == 10, retry.stderr.decode()
    assert str((one.work / "20261022-2").readlink()) == other.batch_id
