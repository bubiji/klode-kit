"""WI-15e: ``klode_kit.ocr.isolation_guard`` pinned exactly (mutation-gate survivors of WI-15c, score 0.70 -> >= 0.80).

The existing contract tests drive the hook as a subprocess and check the decision. They do not pin what a reader of the
log or of stderr relies on: the reason of every branch, the key set / key order / encoding / permissions of a
``.guard.jsonl`` line, the relative-path resolution against the stdin ``cwd`` (not the process cwd), and the fail-closed
answer when the log cannot be written. All of that is asserted here in-process (``main()`` with a fake stdin).

Law: none beyond "one decision, one log line" - the guard is a pure function of stdin plus one append; the cases below
are S1/S2 per branch, and the log-line shape is the (S5) invariant: every line is one complete JSON object whose keys
are sorted and whose base keys are always present.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

from klode_kit.ocr import isolation_guard as IG

pytestmark = pytest.mark.unit

BASE_KEYS = {"at", "decision", "reason", "pid", "file_path", "tool_name", "cwd"}


def drive(monkeypatch, capsys, stdin: bytes, process_cwd: Path) -> tuple[int, str]:
    """Run ``main()`` with ``stdin`` as the hook input and ``process_cwd`` as the process directory."""
    process_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(process_cwd)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin)))
    code = IG.main([])
    return code, capsys.readouterr().err


def event(cwd, file_path="UNSET", **over) -> bytes:
    doc = {"cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": "Read"}
    if file_path != "UNSET":
        doc["tool_input"] = {"file_path": file_path}
    doc.update(over)
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def log_file(cwd: Path) -> Path:
    return Path(os.path.realpath(cwd) + ".guard.jsonl")


def entries(cwd: Path) -> list[dict]:
    return [json.loads(line) for line in log_file(cwd).read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def send(tmp_path) -> Path:
    d = tmp_path / "send"
    d.mkdir()
    (d / "page.png").write_bytes(b"x")
    return d


@pytest.fixture
def elsewhere(tmp_path) -> Path:
    return tmp_path / "process-cwd"


# --------------------------------------------------------------------------- reason of every branch


def test_not_json_reason_and_null_fields(send, elsewhere, monkeypatch, capsys):
    code, err = drive(monkeypatch, capsys, b"{", elsewhere)
    assert code == 2
    assert err == "isolation_guard: deny None: stdin is not a JSON object\n"
    [e] = entries(elsewhere)  # no usable cwd: the log goes next to the process directory
    assert set(e) == BASE_KEYS
    assert (e["decision"], e["reason"]) == ("deny", "stdin is not a JSON object")
    assert (e["file_path"], e["tool_name"], e["cwd"]) == (None, None, None)


def test_not_utf8_reason(elsewhere, monkeypatch, capsys):
    code, err = drive(monkeypatch, capsys, b"\xff\xfe{}", elsewhere)
    assert code == 2 and err == "isolation_guard: deny None: stdin is not a JSON object\n"
    assert entries(elsewhere)[0]["reason"] == "stdin is not a JSON object"


def test_json_but_not_an_object_reason(elsewhere, monkeypatch, capsys):
    code, err = drive(monkeypatch, capsys, b'["cwd"]', elsewhere)
    assert code == 2 and err == "isolation_guard: deny None: stdin is not a JSON object\n"
    [e] = entries(elsewhere)
    assert e["reason"] == "stdin is not a JSON object" and set(e) == BASE_KEYS


def test_unreadable_stdin_is_treated_as_empty(elsewhere, monkeypatch, capsys):
    class Broken:
        class buffer:  # noqa: N801 - mimics sys.stdin.buffer
            @staticmethod
            def read():
                raise OSError("stdin closed")

    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "stdin", Broken())
    assert IG.main([]) == 2
    assert capsys.readouterr().err == "isolation_guard: deny None: stdin is not a JSON object\n"
    assert entries(elsewhere)[0]["decision"] == "deny"


@pytest.mark.parametrize("cwd", ["relative/dir", 7, None, ""], ids=["relative", "int", "null", "empty"])
def test_cwd_not_absolute_reason_keeps_tool_name(cwd, elsewhere, monkeypatch, capsys):
    stdin = json.dumps({"cwd": cwd, "hook_event_name": "PreToolUse", "tool_name": "Read",
                        "tool_input": {"file_path": "/x"}}).encode()
    code, err = drive(monkeypatch, capsys, stdin, elsewhere)
    assert code == 2 and err == "isolation_guard: deny None: cwd missing or not absolute\n"
    [e] = entries(elsewhere)
    assert e["reason"] == "cwd missing or not absolute"
    assert (e["tool_name"], e["cwd"], e["file_path"]) == ("Read", None, None)
    assert set(e) == BASE_KEYS


@pytest.mark.parametrize("name,shown", [("PostToolUse", "'PostToolUse'"), (None, "None"), (3, "3")])
def test_wrong_event_reason(name, shown, send, elsewhere, monkeypatch, capsys):
    over = {"hook_event_name": name} if name is not None else {}
    doc = json.loads(event(send, str(send / "page.png")))
    doc.pop("hook_event_name")
    doc.update(over)
    code, err = drive(monkeypatch, capsys, json.dumps(doc).encode(), elsewhere)
    assert code == 2
    assert err == f"isolation_guard: deny None: unexpected hook_event_name {shown}\n"
    [e] = entries(send)
    assert e["reason"] == f"unexpected hook_event_name {shown}"
    assert (e["cwd"], e["tool_name"], e["file_path"]) == (str(send), "Read", None)
    assert "resolved" not in e


def test_structured_output_is_allowed_without_file_path(send, elsewhere, monkeypatch, capsys):
    stdin = event(send, tool_name="StructuredOutput", tool_input={"answer": "x", "file_path": "/etc/passwd"})
    code, err = drive(monkeypatch, capsys, stdin, elsewhere)
    assert code == 0 and err == ""
    [e] = entries(send)
    assert (e["decision"], e["reason"]) == ("allow", "structured output (no file access)")
    assert (e["tool_name"], e["cwd"], e["file_path"]) == ("StructuredOutput", str(send), None)
    assert "resolved" not in e and set(e) == BASE_KEYS


def test_structured_output_needs_the_exact_name_and_a_pretooluse_event(send, elsewhere, monkeypatch, capsys):
    code, _ = drive(monkeypatch, capsys, event(send, tool_name="structuredoutput", tool_input={}), elsewhere)
    assert code == 2 and entries(send)[-1]["reason"] == "tool_input.file_path missing"
    code, err = drive(monkeypatch, capsys,
                      event(send, tool_name="StructuredOutput", hook_event_name="PostToolUse"), elsewhere)
    assert code == 2 and "unexpected hook_event_name 'PostToolUse'" in err


def test_non_string_tool_name_is_logged_as_null(send, elsewhere, monkeypatch, capsys):
    code, _ = drive(monkeypatch, capsys, event(send, str(send / "page.png"), tool_name=7), elsewhere)
    assert code == 0
    assert entries(send)[0]["tool_name"] is None


@pytest.mark.parametrize("tool_input", [None, "x", [], {}, {"file_path": ""}, {"file_path": 7}, {"path": "/x"}],
                         ids=["absent", "str", "list", "empty", "empty-path", "int-path", "other-key"])
def test_missing_file_path_reason(tool_input, send, elsewhere, monkeypatch, capsys):
    doc = json.loads(event(send))
    if tool_input is not None:
        doc["tool_input"] = tool_input
    code, err = drive(monkeypatch, capsys, json.dumps(doc).encode(), elsewhere)
    assert code == 2 and err == "isolation_guard: deny None: tool_input.file_path missing\n"
    [e] = entries(send)
    assert e["reason"] == "tool_input.file_path missing" and e["file_path"] is None and "resolved" not in e


def test_inside_cwd_reason_and_resolved(send, elsewhere, monkeypatch, capsys):
    code, err = drive(monkeypatch, capsys, event(send, str(send / "page.png")), elsewhere)
    assert code == 0 and err == ""
    [e] = entries(send)
    assert (e["decision"], e["reason"]) == ("allow", "inside cwd")
    assert e["file_path"] == str(send / "page.png") and e["resolved"] == os.path.realpath(send / "page.png")
    assert e["tool_name"] == "Read" and e["cwd"] == str(send) and set(e) == BASE_KEYS | {"resolved"}


def test_the_cwd_itself_is_inside(send, elsewhere, monkeypatch, capsys):
    code, _ = drive(monkeypatch, capsys, event(send, "."), elsewhere)
    assert code == 0 and entries(send)[0]["resolved"] == os.path.realpath(send)


def test_outside_cwd_reason_and_stderr(send, tmp_path, elsewhere, monkeypatch, capsys):
    outside = tmp_path / "canary.txt"
    outside.write_text("n", encoding="utf-8")
    code, err = drive(monkeypatch, capsys, event(send, str(outside)), elsewhere)
    assert code == 2
    assert err == f"isolation_guard: deny {str(outside)!r}: outside cwd\n"
    [e] = entries(send)
    assert (e["decision"], e["reason"]) == ("deny", "outside cwd")
    assert e["file_path"] == str(outside) and e["resolved"] == os.path.realpath(outside)


def test_sibling_with_common_prefix_is_outside(send, tmp_path, elsewhere, monkeypatch, capsys):
    sibling = tmp_path / "send-other"
    sibling.mkdir()
    code, _ = drive(monkeypatch, capsys, event(send, str(sibling / "a.png")), elsewhere)
    assert code == 2 and entries(send)[0]["reason"] == "outside cwd"


def test_unresolvable_path_reason(send, elsewhere, monkeypatch, capsys):
    code, err = drive(monkeypatch, capsys, event(send, "a\x00b"), elsewhere)
    assert code == 2
    [e] = entries(send)
    assert e["reason"].startswith("cannot resolve path: ") and "resolved" not in e
    assert err == f"isolation_guard: deny {'a\x00b'!r}: {e['reason']}\n"


# --------------------------------------------------------------------------- relative paths follow the stdin cwd


def test_relative_path_resolves_against_stdin_cwd_not_process_cwd(send, elsewhere, monkeypatch, capsys):
    """The file exists only under the stdin cwd; the process runs elsewhere (where it would not resolve inside)."""
    code, _ = drive(monkeypatch, capsys, event(send, "page.png"), elsewhere)
    assert code == 0
    [e] = entries(send)
    assert e["file_path"] == "page.png" and e["resolved"] == os.path.realpath(send / "page.png")
    assert not log_file(elsewhere).exists()  # the log is keyed by the stdin cwd, not the process directory


def test_relative_traversal_is_outside(send, elsewhere, monkeypatch, capsys):
    code, _ = drive(monkeypatch, capsys, event(send, "../escape.txt"), elsewhere)
    assert code == 2
    assert entries(send)[0]["resolved"] == os.path.realpath(send.parent / "escape.txt")


# --------------------------------------------------------------------------- the log line


def test_log_line_is_utf8_unescaped_sorted_one_line(send, elsewhere, monkeypatch, capsys):
    name = "页面-é.png"
    (send / name).write_bytes(b"x")
    code, _ = drive(monkeypatch, capsys, event(send, name), elsewhere)
    assert code == 0
    raw = log_file(send).read_bytes()
    text = raw.decode("utf-8")
    assert raw.endswith(b"\n") and text.count("\n") == 1
    assert name in text and "\\u" not in text  # ensure_ascii=False
    keys = list(json.loads(text))
    assert keys == sorted(keys)  # sort_keys=True


def test_log_line_time_and_pid(send, elsewhere, monkeypatch, capsys):
    drive(monkeypatch, capsys, event(send, "page.png"), elsewhere)
    [e] = entries(send)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00", e["at"])  # UTC, milliseconds
    assert e["pid"] == os.getpid()


def test_log_file_mode_is_0600_whatever_the_umask(send, elsewhere, monkeypatch, capsys):
    old = os.umask(0)
    try:
        drive(monkeypatch, capsys, event(send, "page.png"), elsewhere)
    finally:
        os.umask(old)
    assert log_file(send).stat().st_mode & 0o777 == 0o600


def test_every_decision_appends_exactly_one_line(send, elsewhere, monkeypatch, capsys):
    for i, fp in enumerate(["page.png", "../x", "page.png"], start=1):
        drive(monkeypatch, capsys, event(send, fp), elsewhere)
        assert len(entries(send)) == i
    assert [e["decision"] for e in entries(send)] == ["allow", "deny", "allow"]


# --------------------------------------------------------------------------- a log that cannot be written refuses


def test_unwritable_log_turns_an_allow_into_a_refusal(send, elsewhere, monkeypatch, capsys):
    log_file(send).mkdir()  # a directory where the log file should be: open() fails with EISDIR
    code, err = drive(monkeypatch, capsys, event(send, "page.png"), elsewhere)
    assert code == 2
    first, second = err.splitlines()
    assert first.startswith("isolation_guard: cannot write log: ") and "Is a directory" in first
    assert re.fullmatch(r"isolation_guard: \w+ 'page\.png': inside cwd", second)


def test_unwritable_log_with_a_deny_keeps_the_deny_text(send, tmp_path, elsewhere, monkeypatch, capsys):
    log_file(send).mkdir()
    outside = str(tmp_path / "o.txt")
    code, err = drive(monkeypatch, capsys, event(send, outside), elsewhere)
    assert code == 2
    lines = err.splitlines()
    assert lines[0].startswith("isolation_guard: cannot write log: ")
    assert lines[1] == f"isolation_guard: deny {outside!r}: outside cwd"


def test_unwritable_log_refusal_is_worded_as_a_deny(send, elsewhere, monkeypatch, capsys):
    """User decision 2026-10-27: an allow turned into a refusal because the log could not be written is reported
    as ``deny`` on stderr, matching the exit code."""
    log_file(send).mkdir()
    code, err = drive(monkeypatch, capsys, event(send, "page.png"), elsewhere)
    assert code == 2
    assert err.splitlines()[1] == "isolation_guard: deny 'page.png': inside cwd"
