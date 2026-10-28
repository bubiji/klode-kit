"""WI-15h: ``ocr/send`` pinned exactly (mutation-gate survivors, score 0.732 -> >= 0.80).

The existing send tests (``tests/contract/test_send*.py``, ``test_send_gate.py``, ``test_send_receipt.py``) check
which exit code a refusal gives and that a good send sends. They do not pin what the privacy-critical path *says*:
the stable reason code and detail of every refusal, the registered-sha whitelist decision with a swapped copy, the
bytes of the receipt file, the name of the receipt (the actual sha when there is one, the expected one otherwise),
the CLI's stderr texts and streams, the argparse wiring, and the corner cases of the small helpers (PNG header,
review-directory test through symlinks, FIFO that must not stall the sender, > 1 MiB sources, guard-log counting).

This file needs no cloud process: refusals that happen before the review are driven through the real ``send`` with
an unstarted ``FakeCloud`` (its calls log must stay empty); the wrapper and CLI logic are driven with ``S._send`` /
``S.send`` replaced by a recorder (the module's own seam is the observed side effect: receipt file, exceptions,
streams). The cases that start a process are in ``tests/contract/test_send_wi15h.py``.

Law: none new - the sha-whitelist and path-leak laws are held by ``test_send_property`` and ``test_send_receipt``
(hypothesis). These are S1/S2/S3 pins.

Mutants left alone as equivalent (10 of the 230 survivors), with the reason:
``read_text(encoding="UTF-8")`` / ``decode("UTF-8", ...)`` is the same codec as ``"utf-8"`` (``check_stored_receipt``
and both decodes in ``_execute``); ``_move_guard_log``: ``read_text(encoding=None)`` and ``read_text(errors="replace")``
- the guard log is only counted, and in a non-UTF-8 locale the non-ASCII bytes become ``?`` / U+FFFD inside JSON
strings, which parse the same (``encoding="UTF-8"`` likewise); ``_write_json``: ``ensure_ascii=None`` is falsy like
``False`` and ``"UTF-8"`` is the same codec; ``_execute``: ``check=None`` / ``check`` removed - ``False`` is the
default. (``_read_once``'s ``2 << 20`` / ``1 << 21`` are not equivalent: the read size is watched.)
"""

from __future__ import annotations

import json
import os
import stat
import struct
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from _support.send_fake import FIXTURE_PNG, FakeCloud, masked_project, routes_state, sha256, source_env
from klode_kit import config as config_mod
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import send as S

pytestmark = pytest.mark.unit

SHA_A = "a" * 64
SHA_B = "b" * 64
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_header(width: int, height: int) -> bytes:
    """Exactly 24 bytes: signature, chunk length, ``IHDR``, width, height."""
    return PNG_SIG + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)


# --------------------------------------------------------------------------- SendRefused / _utcnow


def test_refused_message_and_attributes_with_detail():
    exc = S.SendRefused(60, "sha_mismatch", "the bytes differ")
    assert str(exc) == "sha_mismatch: the bytes differ"
    assert (exc.exit_code, exc.reason, exc.detail) == (60, "sha_mismatch", "the bytes differ")


def test_refused_message_without_detail_is_the_bare_reason():
    exc = S.SendRefused(61, "route_disabled")
    assert str(exc) == "route_disabled"
    assert exc.args == ("route_disabled",)
    assert exc.detail == ""
    assert (exc.exit_code, exc.reason) == (61, "route_disabled")


def test_utcnow_is_timezone_aware_utc():
    now = S._utcnow()
    assert now.tzinfo is timezone.utc
    assert now.utcoffset() == timedelta(0)
    assert abs((datetime.now(timezone.utc) - now).total_seconds()) < 60


# --------------------------------------------------------------------------- in_review_dir


def test_in_review_dir_plain_paths(tmp_path):
    assert S.in_review_dir(tmp_path / "_mask_review" / "doc" / "p001.review.png") is True
    assert S.in_review_dir(str(tmp_path / "x" / "_mask_review" / "a.png")) is True
    assert S.in_review_dir(tmp_path / "_masked" / "a.png") is False
    assert S.in_review_dir(tmp_path / "_mask_review_not" / "a.png") is False


def test_in_review_dir_real_path_in_review_but_given_path_not(tmp_path):
    """A link outside the review directory pointing into it is still a review image (either test is enough)."""
    review = tmp_path / "proj" / "_mask_review" / "doc"
    review.mkdir(parents=True)
    (review / "a.png").write_bytes(b"x")
    link = tmp_path / "innocent.png"
    link.symlink_to(review / "a.png")
    assert "_mask_review" not in link.parts
    assert S.in_review_dir(link) is True


def test_in_review_dir_given_path_in_review_but_real_path_not(tmp_path):
    """The given path names the review directory (a link to somewhere else): refused as well."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.png").write_bytes(b"x")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "_mask_review").symlink_to(elsewhere)
    path = tmp_path / "proj" / "_mask_review" / "a.png"
    assert "_mask_review" not in Path(os.path.realpath(path)).parts
    assert S.in_review_dir(path) is True


# --------------------------------------------------------------------------- _read_once


def test_read_once_returns_exact_bytes_of_a_multi_chunk_file(tmp_path):
    data = os.urandom((1 << 20) * 2 + 4321)
    f = tmp_path / "big.png"
    f.write_bytes(data)
    assert S._read_once(f) == data
    assert S._read_once(str(f)) == data


def test_read_once_empty_file(tmp_path):
    f = tmp_path / "empty"
    f.write_bytes(b"")
    assert S._read_once(f) == b""


def test_read_once_reads_in_one_mebibyte_chunks(tmp_path, monkeypatch):
    f = tmp_path / "big.png"
    f.write_bytes(b"\x07" * ((1 << 20) + 5))
    sizes: list[int] = []
    real = os.read

    def spy(fd, n):
        sizes.append(n)
        return real(fd, n)

    monkeypatch.setattr(os, "read", spy)
    S._read_once(f)
    monkeypatch.undo()
    assert sizes and set(sizes) == {1 << 20}


def test_read_once_directory_is_refused_with_the_path_in_the_message(tmp_path):
    d = tmp_path / "somedir"
    d.mkdir()
    with pytest.raises(IsADirectoryError) as info:
        S._read_once(d)
    assert str(info.value) == f"not a regular file: {d}"


def test_read_once_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        S._read_once(tmp_path / "nope.png")


def test_read_once_fifo_without_writer_does_not_stall(tmp_path):
    """O_NONBLOCK: opening a FIFO that nobody writes to must return at once and be refused as not regular."""
    fifo = tmp_path / "pipe.png"
    os.mkfifo(fifo)
    outcome: list[BaseException | bytes] = []

    def run():
        try:
            outcome.append(S._read_once(fifo))
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    if t.is_alive():  # release a blocked open() so the thread ends, then fail
        fd = os.open(fifo, os.O_WRONLY)
        os.close(fd)
        t.join(5)
        pytest.fail("_read_once blocked on a FIFO")
    [result] = outcome
    assert isinstance(result, IsADirectoryError)
    assert str(result) == f"not a regular file: {fifo}"


# --------------------------------------------------------------------------- png_size


def test_png_size_exactly_24_bytes():
    assert S.png_size(png_header(640, 480)) == (640, 480)


def test_png_size_is_unsigned_32_bit():
    assert S.png_size(png_header(0x80000000, 0xFFFFFFFF)) == (2147483648, 4294967295)


def test_png_size_ignores_what_follows_the_header():
    assert S.png_size(png_header(3, 5) + b"\x00" * 100) == (3, 5)


def test_png_size_short_input_with_signature_and_ihdr():
    assert S.png_size(png_header(1, 2)[:23]) == (None, None)
    assert S.png_size(png_header(1, 2)[:20]) == (None, None)
    assert S.png_size(b"") == (None, None)


def test_png_size_wrong_signature_with_ihdr_in_place():
    bad = b"\x88PNG\r\n\x1a\n" + png_header(7, 8)[8:]
    assert len(bad) == 24
    assert S.png_size(bad) == (None, None)
    assert S.png_size(bad + b"\x00" * 10) == (None, None)


def test_png_size_right_signature_but_not_ihdr():
    other = PNG_SIG + struct.pack(">I", 13) + b"IDAT" + struct.pack(">II", 7, 8)
    assert S.png_size(other) == (None, None)
    assert S.png_size(other + b"\x00" * 10) == (None, None)


# --------------------------------------------------------------------------- check_stored_receipt


def stored(route: str = "claude", **changes) -> dict:
    data = {"schema": "klode-kit.isolation_receipt/1", "route": route, "cli_version": "2.5.1",
            "effective_config_sha": "c" * 64, "argv_sha": "d" * 64,
            "checks": [{"name": "effort:configured", "ok": True}, {"name": "tmpdir:png_approved", "ok": True}],
            "cloud_isolation": "PASS"}
    if route == "claude":
        data["cloud_settings_sha"] = IC.CLOUD_SETTINGS_SHA256
    data.update(changes)
    return data


def write_receipt(tmp_path: Path, data) -> Path:
    p = tmp_path / "iso.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def refusal(path, route="claude", version="2.5.1") -> S.SendRefused:
    with pytest.raises(S.SendRefused) as info:
        S.check_stored_receipt(path, route, version)
    assert info.value.exit_code == 61
    return info.value


@pytest.mark.parametrize("route", ["claude", "codex"])
def test_stored_receipt_valid_passes(tmp_path, route):
    assert S.check_stored_receipt(write_receipt(tmp_path, stored(route)), route, "2.5.1") is None


def test_stored_receipt_missing_file(tmp_path):
    exc = refusal(tmp_path / "absent.json")
    assert (exc.reason, exc.detail) == ("isolation_receipt_unreadable", "FileNotFoundError")
    assert str(exc) == "isolation_receipt_unreadable: FileNotFoundError"


def test_stored_receipt_not_json(tmp_path):
    p = tmp_path / "iso.json"
    p.write_text("{not json", encoding="utf-8")
    exc = refusal(p)
    assert (exc.reason, exc.detail) == ("isolation_receipt_unreadable", "JSONDecodeError")


def test_stored_receipt_not_utf8(tmp_path):
    p = tmp_path / "iso.json"
    p.write_bytes(b'{"route": "\xff\xfe"}')
    exc = refusal(p)
    assert (exc.reason, exc.detail) == ("isolation_receipt_unreadable", "UnicodeDecodeError")


@pytest.mark.parametrize("data", [["not", "a", "dict"], "text", 5, {}, {"schema": "wrong"},
                                  stored(extra_key=1), stored(cli_version="x.y")])
def test_stored_receipt_invalid_shape(tmp_path, data):
    exc = refusal(write_receipt(tmp_path, data))
    assert (exc.reason, exc.detail) == ("isolation_receipt_invalid", "")
    assert str(exc) == "isolation_receipt_invalid"


def test_stored_receipt_other_route(tmp_path):
    exc = refusal(write_receipt(tmp_path, stored("codex")), route="claude")
    assert (exc.reason, exc.detail) == ("isolation_receipt_route", "receipt is for codex")
    assert str(exc) == "isolation_receipt_route: receipt is for codex"
    exc = refusal(write_receipt(tmp_path, stored("claude")), route="codex")
    assert exc.detail == "receipt is for claude"


def test_stored_receipt_not_pass_by_verdict(tmp_path):
    exc = refusal(write_receipt(tmp_path, stored(cloud_isolation="FAIL")))
    assert (exc.reason, exc.detail) == ("isolation_receipt_not_pass", "")
    assert str(exc) == "isolation_receipt_not_pass"


def test_stored_receipt_not_pass_by_one_failed_check(tmp_path):
    """(The schema itself already forbids PASS next to a failed check, so the verdict is FAIL here.)"""
    checks = [{"name": "a", "ok": True}, {"name": "b", "ok": False}]
    exc = refusal(write_receipt(tmp_path, stored(checks=checks, cloud_isolation="FAIL")))
    assert (exc.reason, exc.detail) == ("isolation_receipt_not_pass", "")


def test_stored_receipt_stale_names_both_versions(tmp_path):
    exc = refusal(write_receipt(tmp_path, stored()), version="2.6.0")
    assert (exc.reason, exc.detail) == ("isolation_receipt_stale", "receipt cli 2.5.1, now 2.6.0")
    assert str(exc) == "isolation_receipt_stale: receipt cli 2.5.1, now 2.6.0"


def test_stored_receipt_stale_when_no_cli_version_now(tmp_path):
    exc = refusal(write_receipt(tmp_path, stored()), version=None)
    assert (exc.reason, exc.detail) == ("isolation_receipt_stale", "receipt cli 2.5.1, now None")


def test_stored_receipt_claude_needs_the_pinned_settings_sha(tmp_path):
    exc = refusal(write_receipt(tmp_path, stored(cloud_settings_sha="e" * 64)))
    assert (exc.reason, exc.detail) == ("isolation_receipt_settings", "")
    data = stored()
    del data["cloud_settings_sha"]
    assert refusal(write_receipt(tmp_path, data)).reason == "isolation_receipt_settings"


def test_stored_receipt_codex_does_not_need_the_settings_sha(tmp_path):
    assert S.check_stored_receipt(write_receipt(tmp_path, stored("codex", cloud_settings_sha="e" * 64)),
                                  "codex", "2.5.1") is None


# --------------------------------------------------------------------------- send directory, claim, receipt file


def populate_send_dir(root: Path, sha: str, extra: str | None = None) -> Path:
    d = root / "send-abc123"
    d.mkdir(parents=True)
    for name in (f"{sha}.png", CA.PROMPT_FILE, CA.SCHEMA_FILE):
        (d / name).write_bytes(b"x")
    if extra:
        (d / extra).write_bytes(b"left")
    return d


def test_remove_send_dir_known_files_only(tmp_path):
    d = populate_send_dir(tmp_path / "t", SHA_A)
    receipts = tmp_path / "receipts"
    S._remove_send_dir(d, SHA_A, receipts)
    assert not d.exists()
    assert not (receipts / S.LEFTOVERS).exists()
    assert not receipts.exists()


def test_remove_send_dir_missing_dir_is_fine(tmp_path):
    S._remove_send_dir(tmp_path / "gone", SHA_A, tmp_path / "receipts")
    assert not (tmp_path / "receipts").exists()


def test_remove_send_dir_unexpected_file_moves_the_directory(tmp_path):
    d = populate_send_dir(tmp_path / "t", SHA_A, extra="surprise.txt")
    receipts = tmp_path / "deep" / "er" / "receipts"  # not there yet: parents are created
    S._remove_send_dir(d, SHA_A, receipts)
    assert not d.exists()
    kept = receipts / "_send_leftovers" / "send-abc123"
    assert sorted(p.name for p in kept.iterdir()) == ["surprise.txt"]
    assert (kept / "surprise.txt").read_bytes() == b"left"


def test_remove_send_dir_second_leftover_reuses_the_leftovers_directory(tmp_path):
    receipts = tmp_path / "receipts"
    first = populate_send_dir(tmp_path / "t1", SHA_A, extra="one.txt")
    S._remove_send_dir(first, SHA_A, receipts)
    second = populate_send_dir(tmp_path / "t2", SHA_A, extra="two.txt")
    second.rename(second.parent / "send-other")
    S._remove_send_dir(second.parent / "send-other", SHA_A, receipts)
    assert sorted(p.name for p in (receipts / "_send_leftovers").iterdir()) == ["send-abc123", "send-other"]


@pytest.fixture
def umask0():
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


def test_claim_numbers_from_one_and_creates_the_directory(tmp_path):
    receipts = tmp_path / "a" / "b" / "receipts"
    assert S._claim(receipts, "claude", SHA_A) == f"claude-{SHA_A}.1"
    assert (receipts / f"claude-{SHA_A}.1.json").read_bytes() == b""
    assert S._claim(receipts, "claude", SHA_A) == f"claude-{SHA_A}.2"
    assert S._claim(receipts, "claude", SHA_A) == f"claude-{SHA_A}.3"
    assert S._claim(receipts, "codex", SHA_A) == f"codex-{SHA_A}.1"
    assert S._claim(receipts, "claude", SHA_B) == f"claude-{SHA_B}.1"
    assert sorted(p.name for p in receipts.iterdir()) == sorted(
        [f"claude-{SHA_A}.1.json", f"claude-{SHA_A}.2.json", f"claude-{SHA_A}.3.json", f"codex-{SHA_A}.1.json",
         f"claude-{SHA_B}.1.json"])


def test_claim_skips_taken_numbers(tmp_path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / f"claude-{SHA_A}.1.json").write_text("kept 1", encoding="utf-8")
    (receipts / f"claude-{SHA_A}.2.json").write_text("kept 2", encoding="utf-8")
    assert S._claim(receipts, "claude", SHA_A) == f"claude-{SHA_A}.3"
    assert (receipts / f"claude-{SHA_A}.1.json").read_text(encoding="utf-8") == "kept 1"


def test_claim_file_mode_is_0644(tmp_path, umask0):
    stem = S._claim(tmp_path / "r", "claude", SHA_A)
    assert stat.S_IMODE(os.stat(tmp_path / "r" / f"{stem}.json").st_mode) == 0o644


def test_write_json_exact_bytes(tmp_path):
    data = {"zeta": 1, "alpha": {"nested": [1, 2], "b": None}, "name": "患者-é", "flag": True}
    p = tmp_path / "out.json"
    S._write_json(p, data)
    expected = ('{\n "alpha": {\n  "b": null,\n  "nested": [\n   1,\n   2\n  ]\n },\n "flag": true,\n'
                ' "name": "患者-é",\n "zeta": 1\n}\n')
    assert p.read_bytes() == expected.encode("utf-8")


def test_write_json_mode_tmp_name_and_replace(tmp_path, monkeypatch, umask0):
    import re

    p = tmp_path / "out.json"
    p.write_text("old", encoding="utf-8")
    seen: list[tuple[str, str]] = []
    real = os.replace

    def spy(src, dst):
        seen.append((os.fspath(src), os.fspath(dst)))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    S._write_json(p, {"k": "v"})
    monkeypatch.undo()
    [(src, dst)] = seen
    assert dst == str(p)
    assert re.fullmatch(re.escape(str(tmp_path)) + r"/\.out\.json\.[0-9a-f]{16}\.tmp", src), src
    assert [x.name for x in tmp_path.iterdir()] == ["out.json"]
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": "v"}


# --------------------------------------------------------------------------- guard log


def test_move_guard_log_without_send_dir_or_log(tmp_path):
    assert S._move_guard_log(None, tmp_path, "stem") is None
    assert S._move_guard_log(tmp_path / "send-x", tmp_path, "stem") is None


def test_move_guard_log_counts_lines_and_denials(tmp_path):
    send_dir = tmp_path / "send-x"
    log = Path(str(send_dir) + S.LOG_SUFFIX)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    log.write_bytes(b'{"decision": "deny"}\n'
                    b'\n'
                    b'{"decision": "allow"}\n'
                    b'this is not json\n'
                    b'{"decision": "deny", "note": "bad byte \xff"}\n'
                    b'   \n'
                    b'{"decision": "DENY"}\n'
                    b'{"other": "deny"}\n')
    info = S._move_guard_log(send_dir, receipts, "claude-abc.1")
    assert info == {"file": "claude-abc.1.guard.jsonl", "lines": 6, "deny": 2}
    assert not log.exists()
    assert (receipts / "claude-abc.1.guard.jsonl").is_file()


def test_move_guard_log_three_denials_all_counted(tmp_path):
    send_dir = tmp_path / "send-x"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    Path(str(send_dir) + S.LOG_SUFFIX).write_text('{"decision":"deny"}\n' * 3 + '{"decision":"allow"}\n',
                                                  encoding="utf-8")
    assert S._move_guard_log(send_dir, receipts, "s") == {"file": "s.guard.jsonl", "lines": 4, "deny": 3}


def test_move_guard_log_non_deny_only(tmp_path):
    send_dir = tmp_path / "send-x"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    Path(str(send_dir) + S.LOG_SUFFIX).write_text('{"decision":"allow"}\n{"decision":"allow"}\n', encoding="utf-8")
    assert S._move_guard_log(send_dir, receipts, "s") == {"file": "s.guard.jsonl", "lines": 2, "deny": 0}


# --------------------------------------------------------------------------- _finish


def make_state(expect: str = SHA_A, t0: float = 100.0, **fields) -> S._State:
    st = S._State("claude", expect, datetime(2026, 10, 28, 1, 2, 3, 999999, tzinfo=timezone.utc), t0)
    st.fields.update(fields)
    return st


def test_finish_default_receipt_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "time", SimpleNamespace(monotonic=lambda: 100.123456))
    ended = datetime(2026, 10, 28, 1, 2, 4, 456789, tzinfo=timezone.utc)
    receipt, path = S._finish(make_state(), tmp_path / "r", lambda: ended)
    assert path == tmp_path / "r" / f"claude-{SHA_A}.1.json"
    assert receipt == {
        "schema": "klode-kit.send_receipt/1", "route": "claude", "status": None, "exit_code": None,
        "reason": None, "der_sha256": None, "expect_sha256": SHA_A, "bytes": None, "width": None,
        "height": None, "model": None, "effort": None, "cli_version": None, "argv_sha": None,
        "isolation_receipt_sha": None, "review": [], "timed_out": False, "guard_log": None,
        "started": "2026-10-28T01:02:03.999+00:00", "ended": "2026-10-28T01:02:04.456+00:00",
        "duration_s": 0.123,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == receipt
    assert type(receipt["timed_out"]) is bool and receipt["timed_out"] is False


def test_finish_values_override_fields_override_defaults(tmp_path):
    st = make_state(bytes=7, status="from-fields", model="m")
    receipt, _ = S._finish(st, tmp_path, lambda: datetime.now(timezone.utc), status="from-values", exit_code=5,
                           timed_out=True)
    assert (receipt["bytes"], receipt["model"], receipt["status"], receipt["exit_code"],
            receipt["timed_out"]) == (7, "m", "from-values", 5, True)


def test_finish_file_is_named_after_the_actual_sha_when_there_is_one(tmp_path):
    receipt, path = S._finish(make_state(expect=SHA_A, der_sha256=SHA_B), tmp_path, datetime.now, status="refused")
    assert path.name == f"claude-{SHA_B}.1.json"
    assert (receipt["der_sha256"], receipt["expect_sha256"]) == (SHA_B, SHA_A)
    assert [p.name for p in tmp_path.iterdir()] == [f"claude-{SHA_B}.1.json"]


def test_finish_file_is_named_after_the_expected_sha_without_one(tmp_path):
    _, path = S._finish(make_state(expect=SHA_A), tmp_path, datetime.now, status="refused")
    assert path.name == f"claude-{SHA_A}.1.json"


def test_finish_moves_the_guard_log_under_the_receipt_stem(tmp_path):
    send_dir = tmp_path / "t" / "send-q"
    send_dir.mkdir(parents=True)
    Path(str(send_dir) + S.LOG_SUFFIX).write_text('{"decision":"deny"}\n', encoding="utf-8")
    st = make_state()
    st.send_dir = send_dir
    receipt, path = S._finish(st, tmp_path / "r", datetime.now, status="sent")
    assert receipt["guard_log"] == {"file": f"claude-{SHA_A}.1.guard.jsonl", "lines": 1, "deny": 1}
    assert (tmp_path / "r" / f"claude-{SHA_A}.1.guard.jsonl").is_file()


# --------------------------------------------------------------------------- send(): validation and wrapper


def boom_if_called(*a, **k):
    raise AssertionError("_send must not run")


@pytest.mark.parametrize("route", ["", "Claude", "gemini", None, "claude "])
def test_send_unknown_route_message(tmp_path, monkeypatch, route):
    monkeypatch.setattr(S, "_send", boom_if_called)
    with pytest.raises(ValueError) as info:
        S.send("x.png", SHA_A, route=route, project=tmp_path, receipt_dir=tmp_path / "r", isolation_receipt="i")
    assert str(info.value) == f"route must be one of ('claude', 'codex'), got {route!r}"
    assert not (tmp_path / "r").exists()


@pytest.mark.parametrize("sha", ["", "ab", "A" * 64, "a" * 63, "a" * 65, "g" * 64, SHA_A + "\n", None, 5, b"a" * 64])
def test_send_bad_expect_sha_message(tmp_path, monkeypatch, sha):
    monkeypatch.setattr(S, "_send", boom_if_called)
    with pytest.raises(ValueError) as info:
        S.send("x.png", sha, route="claude", project=tmp_path, receipt_dir=tmp_path / "r", isolation_receipt="i")
    assert str(info.value) == f"expect_sha must be 64 lowercase hex characters, got {sha!r}"
    assert not (tmp_path / "r").exists()


class Recorder:
    """Stands in for ``S._send``: records its arguments, optionally sets receipt fields, returns or raises."""

    def __init__(self, *, result=None, error=None, fields=None):
        self.result, self.error, self.fields, self.calls = result, error, fields or {}, []

    def __call__(self, st, *args):
        self.calls.append((st, args))
        st.fields.update(self.fields)
        if self.error is not None:
            raise self.error
        return self.result


def test_send_hands_everything_to_the_worker_and_returns_its_result(tmp_path, monkeypatch):
    rec = Recorder(result=("OUT", "ERR", 3, True), fields={"der_sha256": SHA_B, "bytes": 11})
    monkeypatch.setattr(S, "_send", rec)
    cfg = config_mod.resolve({"claude.model": "sonnet"})
    env = {"PATH": "/x"}
    times = iter([datetime(2026, 10, 28, 1, 0, 0, tzinfo=timezone.utc), datetime(2026, 10, 28, 1, 0, 5,
                                                                              tzinfo=timezone.utc)])
    res = S.send("page.png", SHA_A, route="claude", project=str(tmp_path / "proj"), receipt_dir=str(tmp_path / "r"),
                 isolation_receipt="iso.json", routes_state="state.json", config=cfg, source_env=env,
                 timeout_s=12.5, max_budget_usd=0.75, now=lambda: next(times))
    [(st, args)] = rec.calls
    path, project, receipt_dir, iso, state, got_cfg, got_env, timeout, budget = args
    assert (path, iso, state, timeout, budget) == ("page.png", "iso.json", "state.json", 12.5, 0.75)
    assert project == tmp_path / "proj" and isinstance(project, Path)
    assert receipt_dir == tmp_path / "r" and isinstance(receipt_dir, Path)
    assert got_cfg is cfg and got_env is env
    assert (st.route, st.expect) == ("claude", SHA_A)
    assert st.started == datetime(2026, 10, 28, 1, 0, 0, tzinfo=timezone.utc)

    assert isinstance(res, S.SendResult)
    assert (res.route, res.der_sha256, res.returncode, res.timed_out, res.stdout, res.stderr) == (
        "claude", SHA_B, 3, True, "OUT", "ERR")
    assert res.receipt_path == tmp_path / "r" / f"claude-{SHA_B}.1.json"
    assert json.loads(res.receipt_path.read_text(encoding="utf-8")) == res.receipt
    assert (res.receipt["status"], res.receipt["exit_code"], res.receipt["timed_out"], res.receipt["bytes"]) == (
        "sent", 3, True, 11)
    assert res.receipt["started"] == "2026-10-28T01:00:00.000+00:00"
    assert res.receipt["ended"] == "2026-10-28T01:00:05.000+00:00"


def test_send_without_config_loads_the_projects(tmp_path, monkeypatch):
    rec = Recorder(result=("", "", 0, False), fields={"der_sha256": SHA_A})
    monkeypatch.setattr(S, "_send", rec)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "klode-kit.toml").write_text('[claude]\nmodel = "from-the-project-file"\n', encoding="utf-8")
    S.send("p.png", SHA_A, route="claude", project=proj, receipt_dir=tmp_path / "r", isolation_receipt="i")
    cfg = rec.calls[0][1][5]
    assert isinstance(cfg, config_mod.Config)
    assert cfg["claude.model"] == "from-the-project-file"


def test_send_refusal_writes_a_refused_receipt_and_reraises_the_same_exception(tmp_path, monkeypatch):
    exc = S.SendRefused(60, "not_registered", "abc is not a registered masked image")
    monkeypatch.setattr(S, "_send", Recorder(error=exc, fields={"der_sha256": SHA_B, "bytes": 4}))
    with pytest.raises(S.SendRefused) as info:
        S.send("p.png", SHA_A, route="codex", project=tmp_path, receipt_dir=tmp_path / "r", isolation_receipt="i")
    assert info.value is exc
    [p] = list((tmp_path / "r").iterdir())
    assert p.name == f"codex-{SHA_B}.1.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert (rec["status"], rec["exit_code"], rec["reason"]) == ("refused", 60, "not_registered")
    assert (rec["route"], rec["der_sha256"], rec["expect_sha256"], rec["bytes"]) == ("codex", SHA_B, SHA_A, 4)
    assert rec["timed_out"] is False


@pytest.mark.parametrize("error", [RuntimeError("boom"), KeyboardInterrupt(), OSError("disk")])
def test_send_unexpected_failure_writes_an_interrupted_receipt(tmp_path, monkeypatch, error):
    monkeypatch.setattr(S, "_send", Recorder(error=error, fields={"der_sha256": SHA_B, "bytes": 9}))
    with pytest.raises(type(error)) as info:
        S.send("p.png", SHA_A, route="claude", project=tmp_path, receipt_dir=tmp_path / "r", isolation_receipt="i")
    assert info.value is error
    [p] = list((tmp_path / "r").iterdir())
    assert p.name == f"claude-{SHA_B}.1.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert (rec["status"], rec["exit_code"], rec["reason"]) == ("interrupted", 130, "interrupted")
    assert (rec["route"], rec["der_sha256"], rec["expect_sha256"], rec["bytes"]) == ("claude", SHA_B, SHA_A, 9)
    assert rec["schema"] == "klode-kit.send_receipt/1"
    assert rec["started"].endswith("+00:00") and rec["ended"].endswith("+00:00")


def test_send_unexpected_failure_survives_an_unwritable_receipt_dir(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    error = RuntimeError("the original failure")
    monkeypatch.setattr(S, "_send", Recorder(error=error))
    with pytest.raises(RuntimeError) as info:
        S.send("p.png", SHA_A, route="claude", project=tmp_path, receipt_dir=blocker / "r", isolation_receipt="i")
    assert info.value is error


# --------------------------------------------------------------------------- send(): refusals before the review


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("wi15h-world")
    systmp = root / "systmp"
    systmp.mkdir()
    saved = tempfile.tempdir
    tempfile.tempdir = str(systmp)
    try:
        home = root / "home"
        home.mkdir()
        fake = FakeCloud(root / "fake")
        project, row = masked_project(root / "project")
        yield {"root": root, "systmp": systmp, "home": home, "fake": fake, "project": project, "row": row,
               "state": routes_state(root / "routes.json")}
    finally:
        tempfile.tempdir = saved


class Rig:
    def __init__(self, world, tmp_path: Path):
        self.w, self.tmp = world, tmp_path
        self.receipts = tmp_path / "receipts"
        self.fake = world["fake"]
        self.calls_before = len(self.fake.calls())
        self.env = source_env(self.fake, world["home"], world["systmp"])

    def refused(self, path, sha, *, route="claude", project=None, state=None, expect_exit=60) -> tuple[S.SendRefused, dict]:
        with pytest.raises(S.SendRefused) as info:
            S.send(path, sha, route=route, project=project or self.w["project"], receipt_dir=self.receipts,
                   isolation_receipt=self.tmp / "no-such-isolation-receipt.json",
                   routes_state=state or self.w["state"], source_env=self.env)
        exc = info.value
        assert exc.exit_code == expect_exit
        assert len(self.fake.calls()) == self.calls_before, "a refusal before the review starts nothing"
        assert list(self.w["systmp"].iterdir()) == [], "no send directory is left behind"
        [rp] = sorted(self.receipts.glob("*.json"))
        rec = json.loads(rp.read_text(encoding="utf-8"))
        assert (rec["status"], rec["exit_code"], rec["reason"]) == ("refused", expect_exit, exc.reason)
        return exc, rec


@pytest.fixture
def rig(world, tmp_path) -> Rig:
    return Rig(world, tmp_path)


def test_route_disabled_reason_and_receipt(rig, tmp_path):
    data = FIXTURE_PNG.read_bytes()
    state = routes_state(tmp_path / "off.json", claude=False)
    exc, rec = rig.refused(FIXTURE_PNG, sha256(data), state=state, expect_exit=61)
    assert (exc.reason, exc.detail, str(exc)) == ("route_disabled", "", "route_disabled")
    assert rec["der_sha256"] is None and rec["bytes"] is None
    assert rec["expect_sha256"] == sha256(data)
    assert (rig.receipts / f"claude-{sha256(data)}.1.json").is_file()


def test_review_dir_reason_detail_and_receipt(rig, tmp_path):
    inside = tmp_path / "proj2" / "_mask_review" / "doc" / "p001.review.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(FIXTURE_PNG.read_bytes())
    exc, rec = rig.refused(inside, sha256(FIXTURE_PNG.read_bytes()))
    assert (exc.reason, exc.detail) == ("review_dir", "images under _mask_review/ are never sent")
    assert str(exc) == "review_dir: images under _mask_review/ are never sent"
    assert rec["der_sha256"] is None, "a review image is not even read"


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_unreadable_source_reason_and_detail(rig, tmp_path, kind):
    path = tmp_path / "src.png"
    detail = {"missing": "FileNotFoundError", "directory": "IsADirectoryError", "fifo": "IsADirectoryError"}[kind]
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    exc, rec = rig.refused(path, SHA_A)
    assert (exc.reason, exc.detail) == ("unreadable", detail)
    assert str(exc) == f"unreadable: {detail}"
    assert rec["der_sha256"] is None


def test_sha_mismatch_reason_detail_and_receipt_names_actual_bytes(rig, tmp_path):
    stray = tmp_path / "other.png"
    stray.write_bytes(FIXTURE_PNG.read_bytes())
    actual = sha256(stray.read_bytes())
    exc, rec = rig.refused(stray, SHA_A)
    assert (exc.reason, exc.detail) == ("sha_mismatch", "the bytes at the path are not the expected image")
    assert str(exc) == "sha_mismatch: the bytes at the path are not the expected image"
    assert rec["der_sha256"] == actual and rec["expect_sha256"] == SHA_A
    assert rec["bytes"] == len(stray.read_bytes())
    assert (rec["width"], rec["height"]) == S.png_size(stray.read_bytes())
    assert [p.name for p in rig.receipts.glob("*.json")] == [f"claude-{actual}.1.json"]


def test_unregistered_sha_reason_and_detail_names_twelve_characters(rig, tmp_path):
    stray = tmp_path / "loose.png"
    stray.write_bytes(b"\x89PNG not registered anywhere")
    sha = sha256(stray.read_bytes())
    exc, rec = rig.refused(stray, sha)
    assert (exc.reason, exc.detail) == ("not_registered", f"{sha[:12]} is not a registered masked image")
    assert str(exc) == f"not_registered: {sha[:12]} is not a registered masked image"
    assert len(sha[:12]) == 12 and sha[:13] not in exc.detail
    assert rec["der_sha256"] == sha and rec["bytes"] == len(stray.read_bytes())
    assert rec["model"] is None and rec["review"] == []


def test_corrupt_mask_manifest_reason_and_detail(rig, tmp_path):
    proj = tmp_path / "badproj"
    (proj / "_masked").mkdir(parents=True)
    (proj / "_masked" / "MANIFEST.jsonl").write_text('{"der_sha256": "nope"}\n', encoding="utf-8")
    data = FIXTURE_PNG.read_bytes()  # a fixtures-registered image: only the broken whitelist stops it
    exc, rec = rig.refused(FIXTURE_PNG, sha256(data), project=proj)
    assert (exc.reason, exc.detail) == ("manifest_unreadable", "the _masked MANIFEST cannot be read")
    assert str(exc) == "manifest_unreadable: the _masked MANIFEST cannot be read"
    assert rec["der_sha256"] == sha256(data)


def test_swapped_copy_of_another_registered_image_is_a_copy_mismatch(rig, monkeypatch):
    """TOCTOU on the copy: what lands in the send directory hashes to a *different* registered image (the fixture
    image is on the whitelist). Both conditions must hold - same sha as checked AND registered."""
    row = rig.w["row"]
    masked = rig.w["project"] / "_masked" / row["path"]
    other = FIXTURE_PNG.read_bytes()
    assert sha256(other) in IC.fixture_shas() and sha256(other) != row["der_sha256"]
    real = CA.prepare_send_dir

    def swapping(data, sha):
        d = real(data, sha)
        (d / f"{sha}.png").write_bytes(other)
        return d

    monkeypatch.setattr(CA, "prepare_send_dir", swapping)
    exc, rec = rig.refused(masked, row["der_sha256"])
    assert (exc.reason, exc.detail) == ("copy_mismatch", "the copy in the send directory changed")
    assert str(exc) == "copy_mismatch: the copy in the send directory changed"
    assert rec["der_sha256"] == row["der_sha256"]


def test_removed_copy_is_a_copy_mismatch(rig, monkeypatch):
    row = rig.w["row"]
    masked = rig.w["project"] / "_masked" / row["path"]
    real = CA.prepare_send_dir

    def vanishing(data, sha):
        d = real(data, sha)
        (d / f"{sha}.png").unlink()
        return d

    monkeypatch.setattr(CA, "prepare_send_dir", vanishing)
    exc, _ = rig.refused(masked, row["der_sha256"])
    assert exc.reason == "copy_mismatch"


def test_copy_with_unregistered_bytes_is_a_copy_mismatch(rig, monkeypatch):
    row = rig.w["row"]
    masked = rig.w["project"] / "_masked" / row["path"]
    real = CA.prepare_send_dir

    def tampering(data, sha):
        d = real(data, sha)
        (d / f"{sha}.png").write_bytes(data + b"\0")
        return d

    monkeypatch.setattr(CA, "prepare_send_dir", tampering)
    exc, _ = rig.refused(masked, row["der_sha256"])
    assert exc.reason == "copy_mismatch"


# --------------------------------------------------------------------------- CLI (S.send replaced by a recorder)


def sent(stdout="", stderr="", returncode=0, timed_out=False, route="claude"):
    return S.SendResult(route, SHA_A, returncode, timed_out, stdout, stderr, {}, Path("receipt.json"))


def run_main(monkeypatch, capsys, argv, *, result=None, error=None):
    calls: list[tuple[tuple, dict]] = []

    def fake_send(*args, **kwargs):
        calls.append((args, kwargs))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(S, "send", fake_send)
    code = S.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err, calls


GOOD = ["--route", "claude", "--project", "P", "--receipt-dir", "D", "--isolation-receipt", "I", "img.png", SHA_A]


def test_main_passes_every_option_to_send(monkeypatch, capsys):
    code, out, err, [(args, kwargs)] = run_main(
        monkeypatch, capsys, ["--route", "codex", "--project", "P", "--receipt-dir", "D", "--isolation-receipt", "I",
                              "--routes-state", "S", "--timeout", "2.5", "img.png", SHA_A],
        result=sent(route="codex"))
    assert args == ("img.png", SHA_A)
    assert kwargs == {"route": "codex", "project": "P", "receipt_dir": "D", "isolation_receipt": "I",
                      "routes_state": "S", "timeout_s": 2.5}
    assert isinstance(kwargs["timeout_s"], float)
    assert (code, out, err) == (0, "", "")


def test_main_defaults_for_optional_options(monkeypatch, capsys):
    _, _, _, [(_, kwargs)] = run_main(monkeypatch, capsys, GOOD, result=sent())
    assert kwargs["routes_state"] is None and kwargs["timeout_s"] is None


def test_main_success_passes_stdout_through_exactly(monkeypatch, capsys):
    code, out, err, _ = run_main(monkeypatch, capsys, GOOD, result=sent(stdout='{"a": "é"}\nno newline at end',
                                                                         stderr="ignored on success"))
    assert (code, out, err) == (0, '{"a": "é"}\nno newline at end', "")


def test_main_child_failure_exit_1_and_message(monkeypatch, capsys):
    code, out, err, _ = run_main(monkeypatch, capsys, GOOD, result=sent(stdout="partial", returncode=3))
    assert code == 1
    assert out == "partial"
    assert err == "klode-kit ocr send: claude exited 3 (timed out: False)\n"


def test_main_timeout_exit_1_and_message(monkeypatch, capsys):
    code, out, err, _ = run_main(monkeypatch, capsys, GOOD, result=sent(returncode=None, timed_out=True,
                                                                         route="codex"))
    assert (code, out) == (1, "")
    assert err == "klode-kit ocr send: codex exited None (timed out: True)\n"


def test_main_refusal_goes_to_stderr_with_its_exit_code(monkeypatch, capsys):
    for exit_code, reason, detail in ((60, "not_registered", "abc is not a registered masked image"),
                                      (61, "route_disabled", "")):
        exc = S.SendRefused(exit_code, reason, detail)
        code, out, err, _ = run_main(monkeypatch, capsys, GOOD, error=exc)
        assert code == exit_code
        assert out == ""
        assert err == f"klode-kit ocr send: refused (exit {exit_code}): {exc}\n"
    assert err == "klode-kit ocr send: refused (exit 61): route_disabled\n"


def test_main_configuration_error_exit_40_message(monkeypatch, capsys):
    exc = config_mod.ConfigError("bad value for claude.effort", "claude.effort")
    code, out, err, _ = run_main(monkeypatch, capsys, GOOD, error=exc)
    assert code == 40 == exc.exit_code
    assert out == ""
    assert err == f"klode-kit ocr send: configuration error: {exc}\n"
    assert "bad value for claude.effort" in err


def test_main_value_error_is_a_usage_error(monkeypatch, capsys):
    code, out, err, _ = run_main(monkeypatch, capsys, GOOD, error=ValueError("route must be one of x"))
    assert (code, out) == (2, "")
    assert err == "klode-kit ocr send: route must be one of x\n"


def test_main_keyboard_interrupt_exit_130_message(monkeypatch, capsys):
    code, out, err, _ = run_main(monkeypatch, capsys, GOOD, error=KeyboardInterrupt())
    assert (code, out) == (130, "")
    assert err == "klode-kit ocr send: interrupted; send directory removed\n"


def test_main_help_exits_0_and_names_the_program(monkeypatch, capsys):
    code, out, err, calls = run_main(monkeypatch, capsys, ["--help"], result=sent())
    assert code == 0
    assert calls == []
    assert out.startswith("usage: klode-kit ocr send ")
    flat = " ".join(out.split())
    assert "The only path by which a page image leaves the machine (WI-05b; design 02 r2.2-B, r3.5, r4.3; matrix K6)." in flat
    for option in ("--route {claude,codex}", "--project PROJECT", "--receipt-dir RECEIPT_DIR",
                   "--isolation-receipt ISOLATION_RECEIPT", "--routes-state ROUTES_STATE", "--timeout TIMEOUT"):
        assert option in flat
    assert err == ""


@pytest.mark.parametrize("missing", ["--route", "--project", "--receipt-dir", "--isolation-receipt"])
def test_main_each_required_option_is_required(monkeypatch, capsys, missing):
    argv = list(GOOD)
    i = argv.index(missing)
    del argv[i:i + 2]
    code, out, err, calls = run_main(monkeypatch, capsys, argv, result=sent())
    assert code == 2
    assert calls == []
    assert out == ""
    assert f"the following arguments are required: {missing}" in err
    assert err.startswith("usage: klode-kit ocr send ")


def test_main_positionals_are_required(monkeypatch, capsys):
    code, _, err, calls = run_main(monkeypatch, capsys, GOOD[:-2], result=sent())
    assert code == 2 and calls == []
    assert "the following arguments are required: path, sha" in err


def test_main_unknown_route_is_refused_by_the_parser(monkeypatch, capsys):
    code, out, err, calls = run_main(monkeypatch, capsys, ["--route", "gemini", *GOOD[2:]], result=sent())
    assert (code, out, calls) == (2, "", [])
    assert "invalid choice: 'gemini' (choose from" in err


def test_main_timeout_must_be_a_number(monkeypatch, capsys):
    code, out, err, calls = run_main(monkeypatch, capsys, [*GOOD[:-2], "--timeout", "soon", "img.png", SHA_A],
                                     result=sent())
    assert (code, out, calls) == (2, "", [])
    assert "invalid float value: 'soon'" in err
