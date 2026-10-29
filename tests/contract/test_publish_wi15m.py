"""WI-15m: ``publish`` pinned exactly - the part that needs a child process of its own (mutation-gate survivors; see
``tests/unit/test_publish_wi15m.py`` for the whole picture and the equivalent mutants).

``PUBLISH.log`` and the JSON files the publish protocol reads are UTF-8 whatever the locale of the process: the child
runs in the C locale with Python's UTF-8 modes off, so only an explicit ``encoding="utf-8"`` reads (or an explicit
``.encode("utf-8")`` writes) a non-ASCII character. No socket is opened, no model is called, nothing is written outside
the test directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _support import publish_fx as F
from klode_kit import publish as P

pytestmark = pytest.mark.contract_replay

SRC = Path(P.__file__).resolve().parents[1]  # the src directory of the module under test (also in a mutant's copy)
A = F.build_id(0)
DETAIL = "café 知识库"

C_LOCALE = {"LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
            "PYTHONIOENCODING": "ascii:backslashreplace"}


def child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_REAL", "KLODE_KIT_GATE_ACK")}
    env["PYTHONPATH"] = str(SRC)
    env.update(extra)
    return env


def run_child(code: str, *args: str, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code, *args], env=child_env(**C_LOCALE), capture_output=True,
                          timeout=120, cwd=str(tmp))


def test_the_c_locale_child_really_reads_text_as_ascii(tmp_path):
    """The premise of the tests below: without an explicit encoding this child could not read the file."""
    f = tmp_path / "t.txt"
    f.write_text(DETAIL, encoding="utf-8")
    code = "import sys, pathlib\ntry:\n    pathlib.Path(sys.argv[1]).read_text()\nexcept UnicodeDecodeError:\n    print('ascii')\n"
    proc = run_child(code, str(f), tmp=tmp_path)
    assert proc.stdout.decode() == "ascii\n"


def test_the_log_is_read_as_utf8_in_the_c_locale(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    P.append_row(kb, A, "switch", "done", detail=DETAIL)
    code = ("import json, sys\nfrom klode_kit import publish as P\n"
            "print(json.dumps([r['detail'] for r in P.read_log(sys.argv[1])]))\n")
    proc = run_child(code, str(kb), tmp=tmp_path)
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout.decode("ascii")) == [DETAIL]


def test_the_log_is_written_as_utf8_in_the_c_locale(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    code = ("import sys\nfrom klode_kit import publish as P\n"
            "P.append_row(sys.argv[1], sys.argv[2], 'switch', 'done', detail=sys.argv[3])\n")
    detail_hex = DETAIL.encode("utf-8").hex()
    proc = run_child(code.replace("sys.argv[3]", "bytes.fromhex(sys.argv[3]).decode('utf-8')"), str(kb), A, detail_hex,
                     tmp=tmp_path)
    assert proc.returncode == 0, proc.stderr.decode()
    assert DETAIL.encode("utf-8") in (kb / "PUBLISH.log").read_bytes()
    assert F.rows(kb)[0]["detail"] == DETAIL


def test_json_documents_of_a_build_are_read_as_utf8_in_the_c_locale(tmp_path):
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps({"note": DETAIL}, ensure_ascii=False), encoding="utf-8")
    code = ("import json, sys\nfrom pathlib import Path\nfrom klode_kit import publish as P\n"
            "print(json.dumps(P._json(Path(sys.argv[1]))))\n")
    proc = run_child(code, str(doc), tmp=tmp_path)
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout.decode("ascii")) == {"note": DETAIL}


def test_main_as_a_program_usage_error_is_exit_2_on_stderr_and_one_json_line_on_stdout(tmp_path):
    child = "import sys\nfrom klode_kit import publish as P\nsys.exit(P.main(sys.argv[1:]))\n"
    proc = subprocess.run([sys.executable, "-c", child, "--kb", str(tmp_path / "nope"), "--prune"], env=child_env(),
                          capture_output=True, timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 2
    assert proc.stderr.decode() == f"kb-publish: --kb is not a directory: {tmp_path / 'nope'}\n"
    assert json.loads(proc.stdout) == {"schema": "klode-kit.publish/1", "exit_code": 2, "reason": "usage",
                                       "detail": f"--kb is not a directory: {tmp_path / 'nope'}"}
    assert proc.stdout.count(b"\n") == 1
    proc = subprocess.run([sys.executable, "-c", child, "--bogus"], env=child_env(), capture_output=True,
                          timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 2 and proc.stdout == b""
    assert proc.stderr.decode().startswith("usage: klode-kit kb-publish ")
