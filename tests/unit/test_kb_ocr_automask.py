"""WI-32a: ``kb-ocr`` masks by itself before a cloud route may read (impl-deviations WI-31 finding 2).

Matrix (unit lane; the process-level rows are in ``tests/contract/test_kb_ocr_automask_cli.py``):

- 32a-01 (S1) a requested cloud route -> ``_masked/`` image, ``_mask_review/`` picture, MANIFEST row and the derived
  registration exist after the run; the masking happens before the first cloud-route call.
- 32a-02 (S4) over any non-empty subset of the four routes: ``_masked/`` exists iff a cloud route is requested.
- 32a-03 (S3) no cloud route requested / every cloud route disabled -> nothing is masked, the engine is never asked.
- 32a-04 (S3) masking fails -> both cloud routes ``route_failed`` with zero calls, nothing unmasked is registered,
  the local routes finish; the batch receipt says ``mask.status == "failed"``.
- 32a-05 (S5) idempotence: a rerun asks the engine nothing and changes no byte (images, review, MANIFEST,
  ``derived.jsonl``, receipt); a masked image that changed on disk is restored.
- 32a-06 (S5) the lock: ``work/.lock`` is held while the engine reads and is free afterwards (one lock hold, no second
  acquisition); ``mask.mask_batch(..., lock_held=True)`` registers without taking the lock again.
- 32a-07 (S5) invariant: MANIFEST shas == masked image shas == the ``_masked/`` lines of ``derived.jsonl``; the sha of
  a review picture is in none of them.
- 32a-08 (S1) in replay mode the masker reads the recorded Vision rows (header lines blacked out).

Law: idempotence and the three-way whitelist invariant (32a-05, 32a-07).
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import tempfile
import threading
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image

from _support import kb_ocr as K
from klode_kit.audit import mask
from klode_kit.ocr import run_batch as RB

pytestmark = pytest.mark.unit

FX = K.FIXTURES / "fx-labs-1x"
ALL = ("vision", "mlx", "claude", "codex")
CLOUD = ("claude", "codex")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Engine:
    """Stands in for ``OcrMacEngine`` (the masker's Vision boundary): the fixture's recorded Vision rows."""

    name = "vision"

    def __init__(self, *, fail: bool = False, probe=None):
        self.calls: list[str] = []
        self.fail = fail
        self.probe = probe

    def ocr_page(self, png, *, page_id: str, der_sha256: str) -> list[dict]:
        self.calls.append(page_id)
        if self.probe is not None:
            self.probe()
        if self.fail:
            raise RuntimeError("vision exploded")
        rows = [json.loads(x) for x in (FX / "engine" / "vision" / "p001.jsonl").read_text(encoding="utf-8")
                .splitlines() if x.strip()]
        return [{**r, "page_id": page_id, "der_sha256": der_sha256} for r in rows]


@pytest.fixture()
def replay_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KLODE_KIT_TEST_SEAMS", "1")
    monkeypatch.delenv("KLODE_KIT_REAL", raising=False)
    monkeypatch.setenv("KLODE_KIT_ENGINE_REPLAY", str(FX / "engine"))
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("KLODE_KIT_FAKE_LOG", str(log))
    return log


def _inbox(tmp_path: Path) -> Path:
    return K.make_inbox(tmp_path / "_inbox" / "day", [FX / "images" / "p001.png"])


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


def _run(project, inbox, routes, engine=None, **kw) -> tuple[int, str]:
    out = io.StringIO()
    code = RB.run(project, inbox, list(routes), out=out, auto_mask=True, mask_engine=engine, **kw)
    return code, out.getvalue()


def _receipt(project: Path) -> dict:
    return json.loads((K.only_batch(project) / "receipt.json").read_text(encoding="utf-8"))


def _calls(log: Path) -> list[str]:
    return [c["tool"] for c in K.log_calls(log)]


def test_requested_cloud_route_masks_before_dispatch(tmp_path, replay_env):
    """32a-01 (L1 S1): the pictures exist, are registered, and the engine ran before any cloud call."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    seen_at_mask: list[list[str]] = []
    engine = Engine(probe=lambda: seen_at_mask.append(_calls(replay_env)))
    code, _out = _run(project, inbox, ALL, engine)
    assert code == 0
    assert len(engine.calls) == 1
    assert seen_at_mask and all(not ({"claude", "codex"} & set(c)) for c in seen_at_mask)
    assert {"claude", "codex"} <= set(_calls(replay_env))  # and the cloud routes did run after it
    (png,) = (project / "_masked").rglob("*.m.png")
    (review,) = (project / "_mask_review").rglob("*.review.png")
    rows = mask.read_manifest(project)
    assert [r["der_sha256"] for r in rows] == [_sha(png)]
    derived = [json.loads(x) for x in (K.only_batch(project) / "derived.jsonl").read_text(encoding="utf-8")
               .splitlines()]
    masked_lines = [d for d in derived if d["path"].startswith("_masked/")]
    assert [d["der_sha256"] for d in masked_lines] == [_sha(png)]
    assert _sha(review) not in {r["der_sha256"] for r in rows}
    assert _receipt(project)["mask"]["status"] == "done"
    assert _receipt(project)["mask"]["pages"] == 1


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(routes=st.lists(st.sampled_from(ALL), min_size=1, max_size=4, unique=True))
def test_masked_dir_exists_iff_a_cloud_route_is_requested(routes, monkeypatch):
    """32a-02 (L1 S4): for any non-empty set of routes, ``_masked/`` and ``_mask_review/`` exist iff claude or codex is
    among them; local routes alone never cause a masking call."""
    monkeypatch.setenv("KLODE_KIT_TEST_SEAMS", "1")
    monkeypatch.delenv("KLODE_KIT_REAL", raising=False)
    monkeypatch.setenv("KLODE_KIT_ENGINE_REPLAY", str(FX / "engine"))
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        project, inbox = _project(tmp), _inbox(tmp)
        engine = Engine()
        code, _ = _run(project, inbox, routes, engine)
        assert code == 0
        wants = bool(set(routes) & set(CLOUD))
        assert (project / "_masked").exists() is wants
        assert (project / "_mask_review").exists() is wants
        assert len(engine.calls) == (1 if wants else 0)
        assert ("mask" in _receipt(project)) is wants


def test_local_routes_only_never_mask(tmp_path, replay_env):
    """32a-03 (L1 S3): ``vision,mlx`` -> no ``_masked/``, no engine call, no ``mask`` key in the receipt."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    engine = Engine()
    code, _ = _run(project, inbox, ("vision", "mlx"), engine)
    assert code == 0 and engine.calls == []
    assert not (project / "_masked").exists() and not (project / "_mask_review").exists()
    assert "mask" not in _receipt(project)


def test_disabled_cloud_routes_do_not_cause_masking(tmp_path, replay_env):
    """32a-03 (L1 S3): both cloud routes disabled in ``routes_enabled.json`` -> nothing is masked."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    engine = Engine()
    code, _ = _run(project, inbox, ALL, engine, routes_enabled={"claude": False, "codex": False})
    assert code == 0 and engine.calls == []
    assert not (project / "_masked").exists()
    page = _receipt(project)["pages"][0]["routes"]
    assert page["claude"]["status"] == "route_failed" and "route_disabled" in page["claude"]["error"]


def test_masking_failure_fails_both_cloud_routes_and_nothing_goes_out(tmp_path, replay_env):
    """32a-04 (L1 S3): the engine raises -> claude and codex are ``route_failed`` with zero calls, vision and mlx are
    done, no masked image or registration exists, the receipt records the failure; exit 0 (the run finished)."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    code, out = _run(project, inbox, ALL, Engine(fail=True))
    assert code == 0
    rec = _receipt(project)
    routes = rec["pages"][0]["routes"]
    for name in CLOUD:
        assert routes[name]["status"] == "route_failed"
        assert "mask" in routes[name]["error"] and "vision exploded" in routes[name]["error"]
        assert routes[name].get("tries", 0) == 0
    assert routes["vision"]["status"] == "done" and routes["mlx"]["status"] == "done"
    assert not (set(_calls(replay_env)) & set(CLOUD))
    assert rec["mask"]["status"] == "failed" and "vision exploded" in rec["mask"]["error"]
    assert not list((project / "_masked").rglob("*.m.png"))
    derived = (K.only_batch(project) / "derived.jsonl").read_text(encoding="utf-8")
    assert "_masked/" not in derived
    assert "mask" in out.lower()
    assert str(project) not in json.dumps(rec)  # a receipt carries no absolute path


def test_failure_does_not_reuse_older_masks_for_the_cloud(tmp_path, replay_env):
    """32a-04 (L1 S3): a later run whose masking fails sends nothing, even if an earlier run left masked pictures."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    assert _run(project, inbox, ALL, Engine())[0] == 0
    # a changed picture on disk makes the next run re-mask; the engine now fails
    (png,) = (project / "_masked").rglob("*.m.png")
    png.write_bytes(b"tampered")
    code, _ = _run(project, inbox, ALL, Engine(fail=True), redo=True)
    assert code == 0
    routes = _receipt(project)["pages"][0]["routes"]
    assert routes["claude"]["status"] == "route_failed" and routes["codex"]["status"] == "route_failed"


def test_rerun_is_idempotent_and_restores_a_changed_picture(tmp_path, replay_env):
    """32a-05 (L1 S5): second run: no engine call, every byte of the outputs equal; a tampered masked picture is
    detected by its sha and written again with the original bytes."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    engine = Engine()
    assert _run(project, inbox, ALL, engine)[0] == 0
    first = {d: _tree(project / d) for d in ("_masked", "_mask_review")}
    batch = K.only_batch(project)
    derived, receipt = (batch / "derived.jsonl").read_bytes(), (batch / "receipt.json").read_bytes()

    assert _run(project, inbox, ALL, engine)[0] == 0
    assert len(engine.calls) == 1, "the rerun asked the engine again"
    assert {d: _tree(project / d) for d in ("_masked", "_mask_review")} == first
    assert (batch / "derived.jsonl").read_bytes() == derived and (batch / "receipt.json").read_bytes() == receipt

    (png,) = (project / "_masked").rglob("*.m.png")
    png.write_bytes(b"tampered")
    assert _run(project, inbox, ALL, engine)[0] == 0
    assert len(engine.calls) == 2
    assert {d: _tree(project / d) for d in ("_masked", "_mask_review")} == first
    assert (batch / "derived.jsonl").read_bytes() == derived


def test_lock_is_held_while_masking_and_free_afterwards(tmp_path, replay_env):
    """32a-06 (L1 S5): while the engine reads, ``work/.lock`` is held by the run (another descriptor cannot take it);
    after the run it can be taken. The run finishing proves the registration did not wait on its own lock."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    held: list[bool] = []

    def probe() -> None:
        fd = os.open(project / "work" / ".lock", os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except OSError:
                held.append(True)
        finally:
            os.close(fd)

    box: dict = {}
    t = threading.Thread(target=lambda: box.update(res=_run(project, inbox, ALL, Engine(probe=probe))), daemon=True)
    t.start()
    t.join(60)
    assert not t.is_alive(), "kb-ocr deadlocked on work/.lock while masking"
    assert box["res"][0] == 0 and held == [True]
    fd = os.open(project / "work" / ".lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again
    finally:
        os.close(fd)


def test_mask_batch_lock_held_registers_without_a_second_lock(tmp_path, replay_env):
    """32a-06 (L1 S5): the caller holds ``work/.lock``; ``mask_batch(..., lock_held=True)`` must not wait for it."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    assert _run(project, inbox, ("vision",), Engine())[0] == 0
    batch = K.only_batch(project).name
    box: dict = {}

    def work() -> None:
        fd = os.open(project / "work" / ".lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                box["rows"] = mask.mask_batch(project, batch, engine=Engine(), lock_held=True)
            except BaseException as exc:  # noqa: BLE001 - the test reports it
                box["exc"] = exc
        finally:
            os.close(fd)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive(), "mask_batch(lock_held=True) waited for the lock its caller holds"
    assert "exc" not in box, box.get("exc")
    assert len(box["rows"]) == 1
    derived = (project / "work" / batch / "derived.jsonl").read_text(encoding="utf-8")
    assert "_masked/" in derived


def test_whitelist_invariant(tmp_path, replay_env):
    """32a-07 (L1 S5): MANIFEST shas == shas of the files in ``_masked/`` == the ``_masked/`` lines of derived.jsonl;
    the review picture's sha is in none of them; ``_masked/`` holds only images and the MANIFEST."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    assert _run(project, inbox, ALL, Engine())[0] == 0
    manifest = {r["der_sha256"] for r in mask.read_manifest(project)}
    files = {p.name for p in (project / "_masked").rglob("*") if p.is_file()}
    assert files == {"MANIFEST.jsonl"} | {p.name for p in (project / "_masked").rglob("*.m.png")}
    images = {_sha(p) for p in (project / "_masked").rglob("*.m.png")}
    derived = {json.loads(x)["der_sha256"] for x in (K.only_batch(project) / "derived.jsonl")
               .read_text(encoding="utf-8").splitlines() if json.loads(x)["path"].startswith("_masked/")}
    reviews = {_sha(p) for p in (project / "_mask_review").rglob("*") if p.is_file()}
    assert manifest == images == derived and reviews and not (reviews & manifest)


def test_replay_masker_reads_the_recorded_vision_rows(tmp_path, replay_env):
    """32a-08 (L1 S1): no injected engine, replay seam on: the recorded Vision rows decide the boxes - the line that
    holds the patient name is blacked out in the masked picture, the body is not."""
    project, inbox = _project(tmp_path), _inbox(tmp_path)
    code, _ = _run(project, inbox, ALL)
    assert code == 0
    (png,) = (project / "_masked").rglob("*.m.png")
    rows = [json.loads(x) for x in (FX / "engine" / "vision" / "p001.jsonl").read_text(encoding="utf-8").splitlines()]
    name = next(r for r in rows if "姓名" in r["text"])
    masked = Image.open(png).convert("L")
    x0, y0, x1, y1 = name["bbox_px"]
    assert masked.crop((x0, y0, x1, y1)).getextrema() == (0, 0)
    derived = Image.open(FX / "images" / "p001.png")  # an upright page: the derived picture is the inbox picture
    body = (0, masked.size[1] - 40, masked.size[0], masked.size[1])
    assert masked.crop(body).tobytes() == derived.convert("L").crop(body).tobytes()
