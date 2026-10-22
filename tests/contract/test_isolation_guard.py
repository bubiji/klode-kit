"""WI-00c1: ``isolation_guard`` PreToolUse hook as a real subprocess (matrix 00c1-13 .. 00c1-17).

Hook protocol (docs: https://code.claude.com/docs/en/hooks - "Common input fields",
"PreToolUse input", "Exit code 2"): Claude Code writes one JSON object on stdin carrying ``cwd``,
``hook_event_name`` and ``tool_input`` (``tool_input.file_path`` is absolute for Read/Edit/Write);
exit code 2 on ``PreToolUse`` blocks the tool call before permission rules are evaluated.

Guard contract under test:
- ``python -m klode_kit.ocr.isolation_guard`` (and ``klode-kit hook isolation-guard``, the
  exec-form command pinned in ``cloud_settings.json``);
- ``tool_input.file_path`` resolving (realpath) inside ``cwd`` -> exit 0; outside -> exit 2;
  anything it cannot judge (bad JSON, empty stdin, no ``file_path``) -> exit 2 (fail closed);
- every decision appends one JSON line to ``<realpath(cwd)>.guard.jsonl`` with at least
  ``decision`` (``allow``/``deny``) and ``file_path``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from _support.flows import REPO, base_env
from klode_kit.ocr import cloud_argv as CA

pytestmark = pytest.mark.contract_replay

GUARD = [sys.executable, "-m", "klode_kit.ocr.isolation_guard"]


def payload(cwd: Path, file_path: str | None, *, tool: str = "Read") -> bytes:
    tool_input = {} if file_path is None else {"file_path": file_path}
    return json.dumps({
        "session_id": "synthetic-session",
        "cwd": str(cwd),
        "permission_mode": "dontAsk",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "toolu_synthetic",
    }).encode("utf-8")


def run_guard(stdin: bytes, cwd: Path, cmd: list[str] | None = None, env: dict | None = None):
    return subprocess.run(cmd or GUARD, input=stdin, cwd=cwd, env=env or base_env(),
                          capture_output=True, timeout=60)


def log_of(cwd: Path) -> Path:
    return Path(str(cwd.resolve()) + ".guard.jsonl")


def log_lines(cwd: Path) -> list[str]:
    p = log_of(cwd)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


@pytest.fixture
def cwd(tmp_path) -> Path:
    d = tmp_path / "klode-kit-send-test"
    d.mkdir(mode=0o700)
    (d / "page.png").write_bytes(b"synthetic")
    return d


def test_guard_hook_subprocess_outside_denied(cwd, tmp_path):
    """00c1-13: file_path outside cwd -> exit 2, one new log line naming the path with deny."""
    outside = tmp_path / "canary-synthetic.txt"
    outside.write_text("nonce", encoding="utf-8")
    before = log_lines(cwd)

    res = run_guard(payload(cwd, str(outside)), cwd)

    assert res.returncode == 2, res.stderr
    assert res.stderr.strip()
    after = log_lines(cwd)
    assert len(after) == len(before) + 1
    entry = json.loads(after[-1])
    assert entry["decision"] == "deny"
    assert entry["file_path"] == str(outside)
    assert str(outside) in after[-1] and "deny" in after[-1]


def test_guard_hook_inside_allowed(cwd):
    """00c1-14: file_path inside cwd -> exit 0, one log line with allow."""
    res = run_guard(payload(cwd, str(cwd / "page.png")), cwd)

    assert res.returncode == 0, res.stderr
    lines = log_lines(cwd)
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["decision"] == "allow"
    assert entry["file_path"] == str(cwd / "page.png")


def test_guard_hook_relative_inside_allowed(cwd):
    """A relative file_path is resolved against cwd."""
    res = run_guard(payload(cwd, "page.png"), cwd)
    assert res.returncode == 0, res.stderr
    assert json.loads(log_lines(cwd)[-1])["decision"] == "allow"


@pytest.mark.parametrize("stdin", [
    b"{",
    b"",
    "missing-file_path",
    b"[]",
    b'{"cwd": "relative/dir", "hook_event_name": "PreToolUse", "tool_input": {"file_path": "/x"}}',
    "tool_input-not-object",
    "file_path-not-string",
    "not-pretooluse",
    b"\xff\xfe not utf-8",
])
def test_guard_hook_malformed_fail_closed(cwd, stdin):
    """00c1-15: '{', empty, no file_path (and other unjudgeable input) -> exit 2 every time."""
    if stdin == "missing-file_path":
        stdin = payload(cwd, None)
    elif stdin == "tool_input-not-object":
        stdin = json.dumps({"cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_input": "x"}).encode()
    elif stdin == "file_path-not-string":
        stdin = json.dumps({"cwd": str(cwd), "hook_event_name": "PreToolUse",
                            "tool_input": {"file_path": 7}}).encode()
    elif stdin == "not-pretooluse":
        stdin = json.dumps({"cwd": str(cwd), "hook_event_name": "PostToolUse",
                            "tool_input": {"file_path": str(cwd / "page.png")}}).encode()

    res = run_guard(stdin, cwd)

    assert res.returncode == 2, (stdin, res.stdout, res.stderr)
    lines = log_lines(cwd)
    assert lines and json.loads(lines[-1])["decision"] == "deny"


def test_guard_hook_traversal_and_symlink(cwd, tmp_path):
    """00c1-16: cwd/../x and cwd/link -> outside target are both denied (realpath)."""
    target = tmp_path / "x"
    target.write_text("outside", encoding="utf-8")
    link = cwd / "link"
    link.symlink_to(target)

    traversal = run_guard(payload(cwd, f"{cwd}/../x"), cwd)
    via_link = run_guard(payload(cwd, str(link)), cwd)
    nul = run_guard(payload(cwd, f"{cwd}/a\x00b"), cwd)

    assert traversal.returncode == 2
    assert via_link.returncode == 2
    assert nul.returncode == 2
    assert [json.loads(line)["decision"] for line in log_lines(cwd)] == ["deny", "deny", "deny"]


def test_guard_hook_cwd_symlink_does_not_widen(tmp_path):
    """A cwd that is itself a symlink: a file inside its real target is inside; a sibling is not."""
    real = tmp_path / "real-send"
    real.mkdir()
    (real / "a.png").write_bytes(b"a")
    link = tmp_path / "send-link"
    link.symlink_to(real, target_is_directory=True)
    (tmp_path / "sibling.txt").write_text("s", encoding="utf-8")

    inside = run_guard(payload(link, str(link / "a.png")), real)
    outside = run_guard(payload(link, str(link / ".." / "sibling.txt")), real)

    assert inside.returncode == 0, inside.stderr
    assert outside.returncode == 2


def test_guard_concurrent_log_intact(cwd, tmp_path):
    """00c1-17: 20 concurrent hook processes -> exactly 20 log lines, each one complete JSON."""
    outside = tmp_path / "o.txt"
    procs = []
    for i in range(20):
        fp = str(cwd / "page.png") if i % 2 else str(outside)
        p = subprocess.Popen(GUARD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=cwd, env=base_env())
        procs.append((p, payload(cwd, fp), 0 if i % 2 else 2))
    codes = []
    for p, data, _ in procs:
        p.stdin.write(data)
        p.stdin.close()
    for p, _, expected in procs:
        p.wait(timeout=120)
        p.stdout.close()
        p.stderr.close()
        codes.append((p.returncode, expected))

    assert all(rc == exp for rc, exp in codes), codes
    lines = log_lines(cwd)
    assert len(lines) == 20
    entries = [json.loads(line) for line in lines]
    assert sorted(e["decision"] for e in entries) == ["allow"] * 10 + ["deny"] * 10


def test_pinned_settings_hook_runs_guard_via_launcher(tmp_path, monkeypatch):
    """The exec-form hook in cloud_settings.json, resolved on the builder's PATH, is this guard."""
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    png = b"synthetic png for hook wiring"
    send = CA.prepare_send_dir(png, hashlib.sha256(png).hexdigest())
    inv = CA.claude(send, model="opus", effort="max", max_budget_usd=5.0)
    settings = json.loads(Path(CA.CLOUD_SETTINGS).read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]

    exe = shutil.which(hook["command"], path=inv.env["PATH"])
    assert exe is not None
    assert Path(exe).resolve() == (REPO / "bin" / "klode-kit").resolve()

    outside = tmp_path / "canary.txt"
    env = dict(inv.env)
    denied = run_guard(payload(send, str(outside)), send, cmd=[exe, *hook["args"]], env=env)
    allowed = run_guard(payload(send, str(send / "prompt.txt")), send, cmd=[exe, *hook["args"]], env=env)

    assert denied.returncode == 2, denied.stderr
    assert allowed.returncode == 0, allowed.stderr
    assert [json.loads(line)["decision"] for line in log_lines(send)] == ["deny", "allow"]
