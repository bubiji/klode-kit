"""WI-15l: ``u5_probe`` pinned exactly - the parts that need a child process of their own (mutation-gate survivors; see
``tests/unit/test_u5_probe_wi15l.py`` for the whole picture and the equivalent mutants).

- ``.mcp.json`` is read as UTF-8 whatever the locale: the child runs in the C locale with Python's UTF-8 modes off, so
  only an explicit ``encoding="utf-8"`` reads a non-ASCII byte;
- the receipt of ``main`` goes through ``atomic_write`` with its two named crash points: a child killed (SIGKILL, by the
  test seam ``KLODE_KIT_CRASH_AT``) at ``u5.receipt_tmp`` leaves no receipt, one killed at ``u5.receipt_written`` leaves a
  complete receipt and printed nothing;
- ``main`` as a program: exit code, one JSON line on stdout equal to the receipt file, usage errors on stderr.

The server is a stand-in script behind the ``.mcp.json`` entry; no klode is needed, no socket is opened, no model is
called.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from _support import publish_fx as F
from _support import u5_fx as U
from klode_kit import u5_probe as P

pytestmark = pytest.mark.contract_replay

SRC = Path(P.__file__).resolve().parents[1]  # the src directory of the module under test (also in a mutant's copy)
T = F.build_id(1)
OLD = F.build_id(0)
SERVER = "demo-klode"

SERVER_PY = r'''#!%(python)s
import json, os, sys
for raw in sys.stdin.buffer:
    msg = json.loads(raw)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        out = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "s", "version": "0"}}
    else:
        out = {"content": [{"type": "text", "text": json.dumps({"build_id": os.environ["SRV_BUILD"]})}],
               "isError": False}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": out}) + "\n")
    sys.stdout.flush()
'''


def world(tmp: Path, build: str = T, note: str | None = None) -> tuple[Path, Path]:
    project = tmp / "proj"
    kb = project / "kb" / "demo"
    (kb / "builds" / T).mkdir(parents=True)
    exe = U.executable(tmp / "srv.py", SERVER_PY % {"python": sys.executable})
    doc = {"mcpServers": {SERVER: {"command": str(exe), "args": [], "env": {"SRV_BUILD": build}}}}
    if note is not None:
        doc["note"] = note
    (project / ".mcp.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return project, kb


def child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_REAL", "KLODE_KIT_GATE_ACK")}
    env["PYTHONPATH"] = str(SRC)
    env.update(extra)
    return env


C_LOCALE = {"LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
            "PYTHONIOENCODING": "ascii:backslashreplace"}

MAIN_CHILD = "import sys\nfrom klode_kit import u5_probe as P\nsys.exit(P.main(sys.argv[1:]))\n"
PROBE_CHILD = ("import json, sys\nfrom klode_kit import u5_probe as P\n"
               "print(json.dumps(P.direct_probe(sys.argv[1], sys.argv[2])))\n")


def test_mcp_json_with_non_ascii_text_is_read_as_utf8_in_the_c_locale(tmp_path):
    project, _ = world(tmp_path, note="café 知识库")
    proc = subprocess.run([sys.executable, "-c", PROBE_CHILD, str(project), SERVER], env=child_env(**C_LOCALE),
                          capture_output=True, timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr.decode()
    out = json.loads(proc.stdout.decode("ascii"))
    assert out["ok"] is True and out["build_id"] == T and out["error"] is None


def test_the_c_locale_child_really_reads_text_as_ascii(tmp_path):
    """The premise of the test above: without an explicit encoding this child could not read the file."""
    project, _ = world(tmp_path, note="café")
    code = "import sys, pathlib\ntry:\n    pathlib.Path(sys.argv[1]).read_text()\nexcept UnicodeDecodeError:\n    print('ascii')\n"
    proc = subprocess.run([sys.executable, "-c", code, str(project / ".mcp.json")], env=child_env(**C_LOCALE),
                          capture_output=True, timeout=120)
    assert proc.stdout.decode() == "ascii\n"


def run_main(tmp: Path, kb: Path, project: Path, *extra: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", MAIN_CHILD, "--kb", str(kb), "--build", T, "--project", str(project),
                           *extra], env=child_env(**env), capture_output=True, timeout=120, cwd=str(tmp))


def test_main_as_a_program_prints_one_json_line_equal_to_the_receipt(tmp_path):
    project, kb = world(tmp_path, build=OLD)
    receipt = tmp_path / "out" / "u5.json"
    proc = run_main(tmp_path, kb, project, "--receipt", str(receipt))
    assert proc.returncode == 21 and proc.stderr == b""
    assert proc.stdout.count(b"\n") == 1 and proc.stdout == receipt.read_bytes()
    body = json.loads(proc.stdout)
    assert body["u5_ready"] is False and body["probe_build_id"] == OLD and body["exit_code"] == 21
    assert body["reason"] == "post_publish_failed"


def test_main_as_a_program_ready_direct_probe_is_exit_zero(tmp_path):
    project, kb = world(tmp_path)
    proc = run_main(tmp_path, kb, project)
    assert proc.returncode == 0 and json.loads(proc.stdout)["probe_build_id"] == T


def test_main_as_a_program_usage_error_goes_to_stderr_only(tmp_path):
    proc = subprocess.run([sys.executable, "-c", MAIN_CHILD, "--bogus"], env=child_env(), capture_output=True,
                          timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 2 and proc.stdout == b""
    assert proc.stderr.decode().startswith("usage: klode-kit u5-probe ")


def test_receipt_is_not_there_when_the_process_dies_before_the_atomic_replace(tmp_path):
    project, kb = world(tmp_path, build=OLD)
    receipt = tmp_path / "u5.json"
    proc = run_main(tmp_path, kb, project, "--receipt", str(receipt), KLODE_KIT_CRASH_AT="u5.receipt_tmp")
    assert proc.returncode == -signal.SIGKILL and proc.stdout == b""
    assert not receipt.exists()


def test_receipt_is_complete_when_the_process_dies_right_after_the_replace_and_nothing_was_printed(tmp_path):
    project, kb = world(tmp_path, build=OLD)
    receipt = tmp_path / "u5.json"
    proc = run_main(tmp_path, kb, project, "--receipt", str(receipt), KLODE_KIT_CRASH_AT="u5.receipt_written")
    assert proc.returncode == -signal.SIGKILL and proc.stdout == b""
    assert json.loads(receipt.read_text(encoding="utf-8"))["probe_build_id"] == OLD
