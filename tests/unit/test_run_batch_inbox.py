"""WI-04b: kb-ocr inbox handling (matrix 04b-10, 04b-11; r5 decisions F4).

r5: an empty inbox is exit 2 with ``no images`` and creates no directory at all; a non-image file is skipped
and listed in the receipt's ``skipped[]`` - never silently. Engines are replayed (``KLODE_KIT_ENGINE_REPLAY``,
see tests/_support/kb_ocr.py); the runner is called through its CLI entry ``run_batch.main``.
"""

from __future__ import annotations

import json

import pytest

from _support.kb_ocr import FIXTURES, ROUTES, jsonl, make_inbox, receipt, sha256, tree
from klode_kit.contract import ids
from klode_kit.ocr import run_batch

pytestmark = pytest.mark.unit

LABS_1X = FIXTURES / "fx-labs-1x"


@pytest.fixture
def project(tmp_path, monkeypatch, fake_log):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("KLODE_KIT_ENGINE_REPLAY", str(LABS_1X / "engine"))
    monkeypatch.delenv("KLODE_KIT_REAL", raising=False)
    return proj


def kb_ocr(project, inbox, *args):
    return run_batch.main(["--batch", str(inbox), "--project", str(project), *args])


def test_empty_inbox(project, capsys):
    """04b-10 (S3): exit 2, ``no images``, and the project tree is unchanged (no work/, no batch dir)."""
    inbox = project / "_inbox" / "empty"
    inbox.mkdir(parents=True)
    before = tree(project)
    assert kb_ocr(project, inbox) == 2
    assert "no images" in capsys.readouterr().err
    assert tree(project) == before


def test_inbox_with_only_non_images_is_no_images(project, capsys):
    """Boundary: zero images after skipping -> the same exit 2 / no directories; the skipped file is named."""
    inbox = project / "_inbox" / "notes"
    inbox.mkdir(parents=True)
    (inbox / "notes.txt").write_text("not an image\n", encoding="utf-8")
    before = tree(project)
    assert kb_ocr(project, inbox) == 2
    err = capsys.readouterr().err
    assert "no images" in err and "notes.txt" in err
    assert tree(project) == before


def test_missing_inbox_is_usage_error(project, capsys):
    before = tree(project)
    assert kb_ocr(project, project / "_inbox" / "nope") == 2
    assert "nope" in capsys.readouterr().err
    assert tree(project) == before


def test_unknown_route_is_usage_error(project, capsys):
    inbox = make_inbox(project / "_inbox" / "a", [LABS_1X / "images" / "p001.png"])
    before = tree(project)
    assert kb_ocr(project, inbox, "--routes", "vision,ocrx") == 2
    assert "ocrx" in capsys.readouterr().err
    assert tree(project) == before


def test_non_image_file_listed(project, capsys):
    """04b-11 (S3): the .txt is skipped and listed in receipt skipped[] (and on stderr); the image is processed."""
    inbox = make_inbox(project / "_inbox" / "a", [LABS_1X / "images" / "p001.png"])
    (inbox / "notes.txt").write_text("not an image\n", encoding="utf-8")
    assert kb_ocr(project, inbox) == 0
    assert "notes.txt" in capsys.readouterr().err
    rec = receipt(project)
    assert rec["skipped"] == [{"path": "notes.txt", "reason": "not_image"}]
    assert [p["key"] for p in rec["pages"]] == ["p001"]
    batch = project / "work" / rec["batch_id"]
    assert len(jsonl(batch / "manifest.jsonl")) == 1


def test_unreadable_image_listed(project):
    """A file named like an image that does not decode is skipped as unreadable_image, not processed."""
    inbox = make_inbox(project / "_inbox" / "a", [LABS_1X / "images" / "p001.png"])
    (inbox / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    assert kb_ocr(project, inbox) == 0
    rec = receipt(project)
    assert rec["skipped"] == [{"path": "broken.png", "reason": "unreadable_image"}]
    assert len(rec["pages"]) == 1


def test_duplicate_image_is_one_page(project):
    """r5: the same image twice -> batch_id over the set, manifest marks the repeat duplicate, one page."""
    src = LABS_1X / "images" / "p001.png"
    inbox = make_inbox(project / "_inbox" / "a", [src, src], ["a.png", "b.png"])
    assert kb_ocr(project, inbox) == 0
    rec = receipt(project)
    sha = sha256(src.read_bytes())
    assert rec["batch_id"] == ids.batch_id([sha])
    lines = jsonl(project / "work" / rec["batch_id"] / "manifest.jsonl")
    assert [(m["img_sha256"], m.get("duplicate", False)) for m in lines] == [(sha, False), (sha, True)]
    assert [m["doc_id"] for m in lines] == [ids.doc_id(sha)] * 2
    assert len(rec["pages"]) == 1


def test_receipt_routes_all_done_for_clean_page(project):
    """Success path through the CLI entry: every requested route reaches done with a page file."""
    inbox = make_inbox(project / "_inbox" / "a", [LABS_1X / "images" / "p001.png"])
    assert kb_ocr(project, inbox) == 0
    rec = receipt(project)
    page = rec["pages"][0]
    assert {e: r["status"] for e, r in page["routes"].items()} == {e: "done" for e in ROUTES}
    assert rec["issues"] == [] and page["uncertain"] == []
    batch = project / "work" / rec["batch_id"]
    for engine in ROUTES:
        rows = jsonl(batch / page["routes"][engine]["file"])
        assert rows == jsonl(LABS_1X / "engine" / engine / "p001.jsonl")
    assert json.loads((batch / "receipt.json").read_text())["schema"] == run_batch.RECEIPT_SCHEMA
