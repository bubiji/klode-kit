"""WI-13b: edges of ``/med-intake`` added after the red record (contract_replay lane; additions only).

Same replay boundary as ``test_med_intake.py``: recorded OCR rows for all four routes, doctor stubbed by the launcher
wrapper, everything else real.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from _support.intake_fx import IntakeRig
from klode_kit import med_intake as M

pytestmark = pytest.mark.contract_replay


def test_non_image_files_are_listed_as_skipped_in_the_receipt(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path)
    (rig.inbox / "note.txt").write_text("not an image", encoding="utf-8")
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    batch = (rig.work / "20261022").readlink()  # the rig's own batch_id hashes every file, the note included
    source = json.loads((rig.work / batch / "med_intake.json").read_text(encoding="utf-8"))["source"]
    assert source["files"] == ["p001.png", "p002.png", "p003.png"]
    assert source["skipped"] == [{"name": "note.txt", "reason": "not_image"}]


def test_routes_option_is_passed_to_the_ocr(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path)
    res = rig.run("--routes", "vision,mlx")
    assert res.returncode in (0, 10), res.stderr.decode()
    assert res.stdout.decode().splitlines()[0] == "ocr: 3 页 × 2 路 OK"
    assert rig.intake_receipt()["routes"] == ["vision", "mlx"]
    ocr_calls = rig.calls("kb-ocr")
    assert ocr_calls and ocr_calls[0]["argv"][ocr_calls[0]["argv"].index("--routes") + 1] == "vision,mlx"


def test_a_foreign_file_at_the_confirm_name_is_refused_and_kept(tmp_path: Path) -> None:
    rig = IntakeRig(tmp_path)
    assert rig.run().returncode == 10
    page = rig.project / "confirm" / "20261022.html"
    foreign = tmp_path / "foreign.html"
    foreign.write_text("someone else's page", encoding="utf-8")
    os.replace(foreign, page)  # the link is replaced by a real file (nothing is deleted)
    res = rig.run()
    assert res.returncode == 30
    assert b"is not a link to a confirm page" in res.stderr
    assert page.read_text(encoding="utf-8") == "someone else's page" and not page.is_symlink()


def test_today_is_the_last_resort_for_the_date(tmp_path: Path) -> None:
    """No dated inbox and no date on any page: the injected clock decides (in-process run, replay env)."""
    probe = IntakeRig(tmp_path / "probe")
    engine = tmp_path / "engine-no-date"
    shutil.copytree(probe.fx / "engine", engine)
    for path in engine.rglob("*.jsonl"):
        path.write_text(path.read_text(encoding="utf-8").replace("2026-10-22", "（缺）"), encoding="utf-8")
    rig = IntakeRig(tmp_path / "run", day="loose-batch", engine=engine)
    out, err = io.StringIO(), io.StringIO()
    code = M.run(rig.inbox, project=rig.project, launcher=rig.wrapper, env=rig.env, out=err, stdout=out,
                 today=date(2030, 1, 2))
    assert code in (0, 10), err.getvalue()
    rec = json.loads((rig.batch_dir / "med_intake.json").read_text(encoding="utf-8"))
    assert (rec["date"], rec["date_source"], rec["alias"]) == ("2030-01-02", "today", "20300102")
    assert out.getvalue().splitlines()[0] == "ocr: 3 页 × 4 路 OK"
    assert str((rig.work / "20300102").readlink()) == rig.batch_id


def test_another_pipeline_taking_the_kernel_lock_between_the_stages_ends_with_40(tmp_path: Path) -> None:
    """A ``pipeline`` run holding ``work/.pipeline.lock`` when the audit stage starts: exit 40, the day's alias (already
    made after the OCR) stays where it is, no registration receipt and no confirm link are written."""
    import fcntl

    rig = IntakeRig(tmp_path)
    point = "med_intake.aliased"
    proc = rig.popen(env=rig.pause_env(point))
    try:
        rig.wait_reached(point, proc)
        fd = os.open(rig.work / ".pipeline.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rig.release(point)
            out, err = proc.communicate(timeout=120)
        finally:
            os.close(fd)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 40, err.decode()
    assert b"stopped at start: lock_busy" in err
    assert out.decode().splitlines() == ["ocr: 3 页 × 4 路 OK"]
    assert str((rig.work / "20261022").readlink()) == rig.batch_id
    assert not (rig.project / "confirm").exists() and not (rig.batch_dir / "med_intake.json").exists()
    assert rig.run().returncode == 10  # the lock is free again: the same command finishes
