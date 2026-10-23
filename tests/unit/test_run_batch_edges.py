"""WI-04b: run_batch guard and recovery edges (added after the red of WI-04b.unit; they extend, not replace,
the recorded matrix cases).

- rows that do not belong to the page or violate ``ocr_line`` are never accepted as ``done``;
- an attempt left without a receipt is continued in place: valid page files are kept, a page file that does
  not belong to its page is called again;
- an unreadable attempt receipt is not reused;
- the project lock held by someone else is exit 40 and nothing is written.
"""

from __future__ import annotations

import fcntl
import json
import os

import pytest

from _support.kb_ocr import FIXTURES, attempt_dirs, jsonl, log_calls, make_inbox, make_replay, only_batch, receipt, tree
from klode_kit.ocr import run_batch

pytestmark = pytest.mark.unit

LABS_1X = FIXTURES / "fx-labs-1x"
U1 = FIXTURES / "fx-u1-3labs"
ROWS = jsonl(LABS_1X / "engine" / "claude" / "p001.jsonl")


@pytest.fixture
def project(tmp_path, monkeypatch, fake_log):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.delenv("KLODE_KIT_REAL", raising=False)
    return proj


def kb_ocr(monkeypatch, project, inbox, replay, *args):
    monkeypatch.setenv("KLODE_KIT_ENGINE_REPLAY", str(replay))
    return run_batch.main(["--batch", str(inbox), "--project", str(project), *args])


def replay_rows(tmp_path, engine, rows):
    d = tmp_path / "replay" / engine
    d.mkdir(parents=True, exist_ok=True)
    (d / "p001.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return tmp_path / "replay"


@pytest.mark.parametrize(
    ("label", "mutate", "needle"),
    [
        ("schema", lambda rows: [{**rows[0], "bbox_px": [1, 2, 3]}, *rows[1:]], "ocr_line"),
        ("engine", lambda rows: [{**r, "engine": "codex"} for r in rows], "engine"),
        ("line_idx", lambda rows: [{**r, "line_idx": r["line_idx"] + 1} for r in rows], "line_idx"),
    ],
)
def test_foreign_rows_are_route_failed(tmp_path, monkeypatch, project, label, mutate, needle):
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    replay = replay_rows(tmp_path, "claude", mutate(ROWS))
    assert kb_ocr(monkeypatch, project, inbox, replay, "--routes", "claude") == 0
    route = receipt(project)["pages"][0]["routes"]["claude"]
    assert route["status"] == "route_failed" and needle in route["error"], (label, route)
    assert route["tries"] == 3


def test_empty_rows_are_missing_page(tmp_path, monkeypatch, project):
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    replay = replay_rows(tmp_path, "claude", [])
    assert kb_ocr(monkeypatch, project, inbox, replay, "--routes", "claude") == 0
    assert receipt(project)["pages"][0]["routes"]["claude"]["status"] == "route_missing_page"


def test_receiptless_attempt_is_continued_and_foreign_page_recalled(tmp_path, monkeypatch, project, fake_log):
    """A killed run left t1 without a receipt: the resumed run writes into t1, keeps the valid p001/p003 files
    without calling them again, and calls p002 again because its file belongs to another page."""
    inbox = make_inbox(tmp_path / "inbox", sorted((U1 / "images").glob("*.png")))
    assert kb_ocr(monkeypatch, project, inbox, U1 / "engine", "--routes", "vision") == 0
    (t1,) = attempt_dirs(only_batch(project), "vision")
    os.remove(t1 / "receipt.json")
    (t1 / "p002.jsonl").write_bytes((t1 / "p001.jsonl").read_bytes())
    fake_log.write_text("", encoding="utf-8")
    assert kb_ocr(monkeypatch, project, inbox, U1 / "engine", "--routes", "vision") == 0
    assert [(c["tool"], c["page"]) for c in log_calls(fake_log)] == [("vision", "p002")]
    assert attempt_dirs(only_batch(project), "vision") == [t1]
    assert jsonl(t1 / "p002.jsonl") == jsonl(U1 / "engine" / "vision" / "p002.jsonl")
    assert json.loads((t1 / "receipt.json").read_text())["status"] == "done"


def test_unreadable_attempt_receipt_is_not_reused(tmp_path, monkeypatch, project, fake_log):
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    assert kb_ocr(monkeypatch, project, inbox, LABS_1X / "engine", "--routes", "mlx") == 0
    (t1,) = attempt_dirs(only_batch(project), "mlx")
    (t1 / "receipt.json").write_text("{not json", encoding="utf-8")
    fake_log.write_text("", encoding="utf-8")
    assert kb_ocr(monkeypatch, project, inbox, LABS_1X / "engine", "--routes", "mlx") == 0
    assert [p.name.rsplit(".", 1)[1] for p in attempt_dirs(only_batch(project), "mlx")] == ["t1", "t2"]
    assert len(log_calls(fake_log)) == 1


def test_lock_held_elsewhere_is_exit_40(tmp_path, monkeypatch, project, capsys):
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    (project / "work").mkdir()
    fd = os.open(project / "work" / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = tree(project)
        assert kb_ocr(monkeypatch, project, inbox, LABS_1X / "engine") == 40
        assert "unknown holder" in capsys.readouterr().err
        assert tree(project) == before
    finally:
        os.close(fd)


def test_config_error_is_exit_40(tmp_path, monkeypatch, project, capsys):
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    (project / "klode-kit.toml").write_text("route_retry = 'two'\n", encoding="utf-8")
    assert kb_ocr(monkeypatch, project, inbox, LABS_1X / "engine") == 40
    assert "route_retry" in capsys.readouterr().err
    assert not (project / "work").exists()


def test_bad_arguments_are_usage_error(project):
    assert run_batch.main(["--project", str(project)]) == 2
    assert run_batch.main(["--batch", str(project), "--routes", ","]) == 2
