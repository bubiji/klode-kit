"""WI-13c contract rows (spec .claude/tdd-guardian/wi-13c-spec.md): 13c-01, 03, 04, 05, 07, 08, 11.

Replay boundary: the four OCR routes are the engine replay seam (recorded rows of synthetic fixtures); kb-ocr,
kb-audit, the writeback and the views run for real. Nothing starts a real CLI.

Laws: 13c-03 (S5) the explicit type is sticky - a rerun without ``--type`` leaves the receipt bytes alone, a rerun with
another type replaces it; 13c-08 (S5) the timing of a reused attempt is the stored one, whatever the clock says now.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from _support import pipeline_fx as F
from _support.intake_fx import IntakeRig
from _support.kb_ocr import FIXTURES, ROUTES, kb_ocr_env, make_inbox, only_batch, receipt, run_kb_ocr
from klode_kit.contract.schema import issues
from klode_kit.ocr import run_batch

pytestmark = pytest.mark.contract_replay

LABS_1X = FIXTURES / "fx-labs-1x"
U1 = FIXTURES / "fx-u1-3labs"


def _kb_ocr(tmp_path: Path, *args: str, fx: Path = LABS_1X):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    inbox = make_inbox(tmp_path / "inbox", sorted((fx / "images").glob("*.png")))
    env = kb_ocr_env(fx / "engine", tmp_path / "calls.jsonl")
    return project, inbox, env, run_kb_ocr(project, inbox, env, *args)


# ---------------------------------------------------------------- 13c-01
def test_kb_ocr_type_is_written_on_every_page(tmp_path: Path) -> None:
    project, _inbox, _env, res = _kb_ocr(tmp_path, "--type", "rx", fx=U1)
    assert res.returncode == 0, res.stderr.decode()
    rec = receipt(project)
    assert rec["record_type"] == "rx"
    assert len(rec["pages"]) == 3 and all(p["record_type"] == "rx" for p in rec["pages"])


def test_kb_ocr_without_type_writes_no_type_key(tmp_path: Path) -> None:
    project, _inbox, _env, res = _kb_ocr(tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    rec = receipt(project)
    assert "record_type" not in rec and all("record_type" not in p for p in rec["pages"])


def test_kb_ocr_unknown_type_is_exit_2(tmp_path: Path) -> None:
    project, _inbox, _env, res = _kb_ocr(tmp_path, "--type", "scan")
    assert res.returncode == 2
    assert not (project / "work").exists()


# ---------------------------------------------------------------- 13c-03
def test_type_is_sticky_and_replaceable(tmp_path: Path) -> None:
    project, inbox, env, res = _kb_ocr(tmp_path, "--type", "rx")
    assert res.returncode == 0, res.stderr.decode()
    path = only_batch(project) / "receipt.json"
    first = path.read_bytes()
    again = run_kb_ocr(project, inbox, env)  # no --type: the pipeline's second segment
    assert again.returncode == 0, again.stderr.decode()
    assert path.read_bytes() == first
    swapped = run_kb_ocr(project, inbox, env, "--type", "lab")
    assert swapped.returncode == 0, swapped.stderr.decode()
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["record_type"] == "lab" and all(p["record_type"] == "lab" for p in rec["pages"])


# ---------------------------------------------------------------- 13c-04 / 13c-05
def _writeback(rig: IntakeRig) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "klode_kit", "pipeline", "--batch", str(rig.inbox), "--project", str(rig.project),
            "--launcher", str(rig.wrapper), "--stage", "writeback"]
    return subprocess.run(argv, env=rig.env, capture_output=True, cwd=str(rig.project), timeout=180)


def test_explicit_type_decides_the_record_view(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path, "fx-labs-1x")
    res = rig.run("--type", "rx")
    assert res.returncode == 0, res.stderr.decode()
    assert rig.intake_receipt()["record_type"] == "rx"
    batch = json.loads((rig.batch_dir / "receipt.json").read_text(encoding="utf-8"))
    assert batch["record_type"] == "rx" and batch["pages"][0]["record_type"] == "rx"
    wb = _writeback(rig)  # the second segment never repeats --type
    assert wb.returncode == 0, wb.stderr.decode()
    names = sorted(p.name for p in (rig.project / "records").glob("*.md"))
    assert names and all(n.endswith("-rx.md") for n in names), names
    front = (rig.project / "records" / names[0]).read_text(encoding="utf-8")
    assert re.search(r'^ "type": "rx",?$', front, re.M), front[:300]  # the front matter is JSON


def test_without_type_the_view_still_follows_the_page(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path, "fx-labs-1x")
    assert rig.run().returncode == 0
    assert rig.intake_receipt()["type_source"] == "header"
    batch = json.loads((rig.batch_dir / "receipt.json").read_text(encoding="utf-8"))
    assert "record_type" not in batch and all("record_type" not in p for p in batch["pages"])
    assert _writeback(rig).returncode == 0
    names = sorted(p.name for p in (rig.project / "records").glob("*.md"))
    assert names and all(n.endswith("-lab.md") for n in names), names


def test_explicit_type_on_a_headerless_batch_is_honoured_by_the_view(tmp_path: Path) -> None:
    probe = IntakeRig(tmp_path / "probe", "fx-labs-1x")
    engine = tmp_path / "engine-no-keyword"
    shutil.copytree(probe.fx / "engine", engine)
    for path in engine.rglob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("检验报告单", "报告单").replace("检验报告", "报告"), encoding="utf-8")
    rig = IntakeRig(tmp_path / "run", "fx-labs-1x", engine=engine)
    assert rig.run("--type", "lab").returncode == 0
    assert _writeback(rig).returncode == 0
    names = sorted(p.name for p in (rig.project / "records").glob("*.md"))
    assert names and all(n.endswith("-lab.md") for n in names), names  # not -doc.md


# ---------------------------------------------------------------- 13c-07
def test_u1_batch_receipt_has_four_routes_timed_with_zero_failed_pages(tmp_path: Path) -> None:
    project, _inbox, _env, res = _kb_ocr(tmp_path, fx=U1)
    assert res.returncode == 0, res.stderr.decode()
    stats = receipt(project)["route_stats"]
    assert sorted(stats) == sorted(ROUTES)
    for name, st in stats.items():
        assert isinstance(st["elapsed_ms"], int) and not isinstance(st["elapsed_ms"], bool), name
        assert st["elapsed_ms"] >= 0 and st["failed_pages"] == 0, (name, st)


def test_failed_page_is_counted_per_route(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    shutil.copytree(U1 / "engine", engine)
    (engine / "codex" / "p002.jsonl").unlink()
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", sorted((U1 / "images").glob("*.png")))
    res = run_kb_ocr(project, inbox, kb_ocr_env(engine, tmp_path / "calls.jsonl"))
    assert res.returncode == 0, res.stderr.decode()
    stats = receipt(project)["route_stats"]
    assert stats["codex"]["failed_pages"] == 1
    assert [stats[r]["failed_pages"] for r in ("vision", "mlx", "claude")] == [0, 0, 0]


# ---------------------------------------------------------------- 13c-08
def _ticker(step: float):
    c = itertools.count()
    return lambda: next(c) * step


def test_injected_clock_is_measured_stored_and_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KLODE_KIT_TEST_SEAMS", "1")
    monkeypatch.delenv("KLODE_KIT_REAL", raising=False)
    monkeypatch.setenv("KLODE_KIT_ENGINE_REPLAY", str(LABS_1X / "engine"))
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", sorted((LABS_1X / "images").glob("*.png")))
    log = open(tmp_path / "log.txt", "w", encoding="utf-8")
    assert run_batch.run(project, inbox, list(ROUTES), out=log, clock=_ticker(0.25)) == 0
    batch = only_batch(project)
    path = batch / "receipt.json"
    stats = json.loads(path.read_text(encoding="utf-8"))["route_stats"]
    assert {r: s["elapsed_ms"] for r, s in stats.items()} == {r: 250 for r in ROUTES}
    for attempt in batch.glob("ocr/*/*/receipt.json"):
        rec = json.loads(attempt.read_text(encoding="utf-8"))
        assert rec["elapsed_ms"] == 250 and issues("receipt", rec) == []
    first = path.read_bytes()
    # a rerun reuses every attempt: the stored timing is reported, whatever the clock says now
    assert run_batch.run(project, inbox, list(ROUTES), out=log, clock=_ticker(7.0)) == 0
    assert path.read_bytes() == first


def test_replay_runs_use_a_logical_clock_so_trees_are_equal(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ra = _kb_ocr(a)
    rb = _kb_ocr(b)
    assert ra[3].returncode == rb[3].returncode == 0
    sa, sb = receipt(ra[0])["route_stats"], receipt(rb[0])["route_stats"]
    assert sa == sb and all(s["elapsed_ms"] == 0 for s in sa.values())


# ---------------------------------------------------------------- 13c-11
def test_confirm_directory_link_renders_the_crops(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path)
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    d = rig.project / "confirm" / "20261022"
    assert d.is_dir() and not d.is_symlink()  # a folder of links: the recorded link set of 13b is unchanged
    assert str((d / "crops").readlink()) == f"../../work/{rig.batch_id}/audit/a1/crops"
    page = (d / "confirm.html").read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r"crops/[A-Za-z0-9_./-]+\.png", page)))
    assert refs and all((d / ref).is_file() for ref in refs)
    assert f"confirm/20261022/confirm.html" in res.stderr.decode()
    # the recorded link and the stdout line are unchanged
    assert (rig.project / "confirm" / "20261022.html").is_symlink()
    assert res.stdout.decode().splitlines()[1] == "audit: 5 条分歧 → confirm/20261022.html"


@pytest.mark.parametrize("point", ["med_intake.aliased", "med_intake.linked", "med_intake.receipt"])
def test_confirm_directory_link_survives_a_crash_and_a_rerun(tmp_path: Path, point: str) -> None:
    clean = IntakeRig(tmp_path / "clean")
    assert clean.run().returncode == 10
    victim = IntakeRig(tmp_path / "victim")
    assert victim.run(env={"KLODE_KIT_CRASH_AT": point}).returncode == -signal.SIGKILL
    assert victim.run().returncode == 10
    assert victim.link_targets() == clean.link_targets()
    folder = lambda r: sorted(p.name for p in (r.project / "confirm" / "20261022").iterdir())  # noqa: E731
    assert folder(victim) == folder(clean) == ["confirm.html", "crops"]
    assert victim.run().returncode == 10  # a plain rerun changes nothing
    assert victim.link_targets() == clean.link_targets()
