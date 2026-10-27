"""U2 (04 section 6, with the 9b clarification): a crooked outpatient photo, ``kb-ocr --batch _inbox/clinic-0918/
--routes vision,mlx,claude,codex``.

What the user sees (04): ``receipt.json`` says ``rotate: 90`` for the page; ``_masked/`` holds the masked picture with
the header ("测试患者甲 / T-0000001") blacked out, the before/after review picture is in ``_mask_review/`` (9b: the
review picture shows the unmasked header, so it is not beside the files a cloud route may read);
``ocr/vision/<fp>/p001.jsonl`` carries a pixel box (``bbox_px``, four integers) on every line.

Matrix U2-01, U2-02, U2-03. Lane contract_replay (U2-02: cli_integration, macOS: the Vision route is the real one).

Current state (WI-32a): ``kb-ocr`` (hence ``pipeline`` and ``med-intake``) runs the masker by itself, inside its own lock,
before a cloud route is dispatched; ``test_u2_kb_ocr_makes_masked_images_by_itself`` states the literal U2 step. (WI-31
had found that no user entry called ``audit.mask.mask_batch``; the first masking test below still calls it directly.)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from _support import kb_ocr as K
from klode_kit.audit import mask

replay = pytest.mark.contract_replay

FX = K.FIXTURES / "fx-rotated90"
TRUTH = json.loads((FX / "truth.json").read_text(encoding="utf-8"))
HEADER_BOX = TRUTH["layout"]["p001"]["header_box"]  # in the upright (turned back) page, [x0, y0, x1, y1]
SECRETS = ("测试患者甲", "T-0000001")


class FixtureVision:
    """The Vision route's recorded rows of the fixture, as the masker's engine (``OcrMacEngine``'s interface)."""

    name = "vision"

    def ocr_page(self, png, *, page_id: str, der_sha256: str) -> list[dict]:
        rows = [json.loads(x) for x in (FX / "engine" / "vision" / "p001.jsonl").read_text(encoding="utf-8")
                .splitlines() if x.strip()]
        return [{**r, "page_id": page_id, "der_sha256": der_sha256} for r in rows]


@pytest.fixture()
def run(tmp_path):
    """``kb-ocr`` on the rotated page in ``_inbox/clinic-0918/`` with all four routes replayed."""
    inbox = K.make_inbox(tmp_path / "_inbox" / "clinic-0918", [FX / "images" / "p001.png"])
    project = tmp_path / "project"
    project.mkdir()
    env = K.kb_ocr_env(FX / "engine", tmp_path / "calls.jsonl")
    res = K.run_kb_ocr(project, inbox, env, "--routes", "vision,mlx,claude,codex", launcher=True)
    assert res.returncode == 0, res.stderr.decode()
    return project, inbox, res


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _all_zero(img: Image.Image, box) -> bool:
    x0, y0, x1, y1 = box
    px = img.convert("L").crop((x0, y0, x1, y1))
    return px.getextrema() == (0, 0)


@replay
def test_u2_kb_ocr_rotated(run):
    """U2-01 (L2 S1), the OCR half: ``rotate: 90`` on the page, a four-integer pixel box on every Vision line."""
    project, _inbox, res = run
    batch = K.only_batch(project)
    receipt = json.loads((batch / "receipt.json").read_text(encoding="utf-8"))
    assert [p["rotate"] for p in receipt["pages"]] == [90]
    assert receipt["routes"] == ["vision", "mlx", "claude", "codex"]
    assert all(receipt["pages"][0]["routes"][r]["status"] == "done" for r in receipt["routes"])
    # the derived page is the turned-back picture, and its sha is what the routes' lines name
    derived = Image.open(batch / "derived" / "p001.png")
    assert derived.size == tuple(TRUTH["layout"]["p001"]["size"])
    (vision_dir,) = sorted((batch / "ocr" / "vision").iterdir())
    lines = _rows(vision_dir / "p001.jsonl")
    assert len(lines) >= 10
    for ln in lines:
        box = ln["bbox_px"]
        assert len(box) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in box), ln
        assert box[0] < box[2] and box[1] < box[3] and box[2] <= derived.size[0] and box[3] <= derived.size[1]
        assert ln["der_sha256"] == receipt["pages"][0]["der_sha256"]


@replay
def test_u2_masking_step_blacks_out_the_header_and_keeps_the_review_apart(run, tmp_path):
    """U2-01 (L2 S1), the masking half, with the masking step the library offers (``mask.mask_batch``; no command
    runs it - see the module note): the masked picture has an all-zero header, the body is untouched, the
    before/after picture is in ``_mask_review/`` only, and only the masked picture's sha is on the whitelist."""
    project, _inbox, _res = run
    batch = K.only_batch(project).name
    rows = mask.mask_batch(project, batch, engine=FixtureVision())
    assert len(rows) == 1
    row = rows[0]
    doc, page = row["page_id"].split("/")
    masked_png = project / "_masked" / doc / f"{page}.m.png"
    review_png = project / "_mask_review" / doc / f"{page}.review.png"
    assert masked_png.is_file() and review_png.is_file()
    masked = Image.open(masked_png)
    upright = Image.open(K.only_batch(project) / "derived" / "p001.png")
    assert masked.size == upright.size
    assert _all_zero(masked, HEADER_BOX), "the header region (name and number lines) is all black"
    # the body (the table region) is exactly what the page shows
    table = TRUTH["layout"]["p001"]["table_box"]
    assert masked.convert("RGB").crop(table).tobytes() == upright.convert("RGB").crop(table).tobytes()
    # 9b: the review picture shows the unmasked header and is nowhere under _masked/, nor on the whitelist
    assert not list((project / "_masked").rglob("*.review.png"))
    shas = {r["der_sha256"] for r in mask.read_manifest(project)}
    assert row["der_sha256"] in shas
    assert hashlib.sha256(review_png.read_bytes()).hexdigest() not in shas
    review = Image.open(review_png)
    assert review.size[0] >= 2 * upright.size[0] - 2 and review.size[1] >= upright.size[1]  # before | after


@replay
def test_u2_kb_ocr_makes_masked_images_by_itself(run):
    """U2-01 literal (04): after ``kb-ocr`` alone, ``_masked/`` holds the masked picture and ``_mask_review/`` the
    review picture (WI-32a wired the masker into ``kb-ocr``; this was a strict xfail before)."""
    project, _inbox, _res = run
    assert list((project / "_masked").rglob("*.m.png"))
    assert list((project / "_mask_review").rglob("*.review.png"))


@replay
def test_u2_unmasked_never_sent(tmp_path):
    """U2-03 (L2 S3): skipping the masking and handing a cloud route the picture itself: ``kb-ocr`` makes no cloud
    call (the route fails with the instruction to mask first), and the sender - given the original picture and its
    own sha - answers exit 60 and starts no model call."""
    from _support.send_fake import FakeCloud, isolation_receipt, routes_state, sha256, source_env
    import subprocess
    import sys

    fake = FakeCloud(tmp_path / "fake")
    home = tmp_path / "home"
    home.mkdir()
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    env = {**source_env(fake, home, systmp), "PYTHONPATH": str(K.REPO / "src")}
    inbox = K.make_inbox(tmp_path / "_inbox" / "clinic-0918", [FX / "images" / "p001.png"])
    project = tmp_path / "project"
    project.mkdir()

    # (1) the user entry, cloud route only, no masking step before it: zero calls, a named reason
    res = subprocess.run([sys.executable, "-m", "klode_kit", "kb-ocr", "--batch", str(inbox), "--project",
                          str(project), "--routes", "claude"], env=env, capture_output=True, cwd=str(project))
    assert res.returncode == 0, res.stderr.decode()
    assert fake.model_calls() == []
    receipt = json.loads((K.only_batch(project) / "receipt.json").read_text(encoding="utf-8"))
    claude = receipt["pages"][0]["routes"]["claude"]
    assert claude["status"] == "route_failed"
    assert "route_failed" in res.stdout.decode() or "route_failed" in res.stderr.decode() or "mask" in res.stdout.decode()

    # (2) the sender itself: the original picture is not on the whitelist -> exit 60, no model call
    # the user's own photo is not a registered synthetic picture: one pixel differs from the fixture, so its sha is
    # on neither whitelist (the masked pictures' MANIFEST, the synthetic fixtures' MANIFEST)
    original = tmp_path / "user-photo.png"
    with Image.open(FX / "images" / "p001.png") as im:
        photo = im.convert("RGB")
    photo.putpixel((0, 0), (1, 2, 3))
    photo.save(original)
    other = FakeCloud(tmp_path / "fake-iso")
    iso = isolation_receipt("claude", other, home, tmp_path / "iso", tmp_path / "iso.json")
    state = routes_state(tmp_path / "routes.json")
    sent = subprocess.run([sys.executable, "-m", "klode_kit", "ocr", "send", "--route", "claude", "--project",
                           str(project), "--receipt-dir", str(tmp_path / "receipts"), "--isolation-receipt", str(iso),
                           "--routes-state", str(state), str(original), sha256(original.read_bytes())],
                          env=env, capture_output=True, cwd=str(project))
    assert sent.returncode == 60, sent.stderr.decode()
    assert fake.model_calls() == []


@pytest.mark.cli_integration
@pytest.mark.macos
def test_u2_kb_ocr_rotated_macos(tmp_path):
    """U2-02 (L3 S1): the Vision route is the real one (Apple Vision): ``rotate: 90`` is found on the real picture, the
    Vision lines carry four-integer pixel boxes, and real Vision masking blacks out the real header lines."""
    inbox = K.make_inbox(tmp_path / "_inbox" / "clinic-0918", [FX / "images" / "p001.png"])
    project = tmp_path / "project"
    project.mkdir()
    env = K.base_env()
    env.pop("KLODE_KIT_ENGINE_REPLAY", None)
    env.pop("KLODE_KIT_REAL", None)
    res = K.run_kb_ocr(project, inbox, env, "--routes", "vision", timeout=300)
    assert res.returncode == 0, res.stderr.decode()
    batch = K.only_batch(project)
    receipt = json.loads((batch / "receipt.json").read_text(encoding="utf-8"))
    assert [p["rotate"] for p in receipt["pages"]] == [90]
    assert receipt["pages"][0]["routes"]["vision"]["status"] == "done"
    (vision_dir,) = sorted((batch / "ocr" / "vision").iterdir())
    lines = _rows(vision_dir / "p001.jsonl")
    assert len(lines) >= 8 and any("检验" in ln["text"] or "报告" in ln["text"] for ln in lines)
    for ln in lines:
        assert len(ln["bbox_px"]) == 4 and all(isinstance(v, int) for v in ln["bbox_px"])
    from klode_kit.ocr import engines as E

    rows = mask.mask_batch(project, batch.name, engine=E.OcrMacEngine())
    doc, page = rows[0]["page_id"].split("/")
    masked = Image.open(project / "_masked" / doc / f"{page}.m.png")
    # the real Vision lines of the name and the number are covered (the whole header box can contain a gap row)
    name_line = next(ln for ln in lines if "姓名" in ln["text"].replace(" ", ""))
    number_line = next(ln for ln in lines if "编号" in ln["text"].replace(" ", ""))
    assert _all_zero(masked, name_line["bbox_px"]) and _all_zero(masked, number_line["bbox_px"])
    assert (project / "_mask_review" / doc / f"{page}.review.png").is_file()
