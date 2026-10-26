"""Helpers for the MCP wrapper tests (WI-11a; matrix 11a-*).

- ``marked(tmp)``: ``fx-publish-ab`` as 11a needs it - a data project whose ``kb/<id>/builds/`` holds three real,
  finished builds A < B < C of ``fx-labs-3x`` (all judged ``PASS`` by the real ``kb-check``), each with **one marker word
  of its own** in the Thin text of the ``hemoglobin`` card (``MARKERAZETA`` / ``MARKERBZETA`` / ``MARKERCZETA``), so a
  query answer says which build it came from. The marker is put in after the build (the write receipt's card sha is
  updated, the tree is sealed again, the check is run again); built once per process, every call hands out a copy.
- ``Server``: the real ``bin/klode-kit mcp --kb ...`` as a subprocess speaking JSON-RPC lines.
- ``mode_env``: the three ways the wrapper can serve - ``normal`` (klode importable: the installed klode's site
  directory on ``PYTHONPATH``, a stand-in for a klode inside the venv), ``proxy`` (a shadow ``klode`` package that has no
  ``mcp_server`` comes first on ``PYTHONPATH``: the import fails although klode is installed) and ``cli`` (the shadow,
  and a ``klode-mcp`` on PATH that dies at once).
- ``publish(...)``: the real publish (``klode_kit.publish.publish``) with a private registry and home.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

from _support import build_fx as B
from _support import kb_check_fx as KX
from _support.flows import REPO, base_env
from klode_kit import publish as P

LAUNCHER = REPO / "bin" / "klode-kit"
MARKERS = {"A": "MARKERAZETA", "B": "MARKERBZETA", "C": "MARKERCZETA"}
MODES = ("normal", "proxy", "cli")
MODE_NAMES = {"normal": "in-process", "proxy": "subprocess-proxy", "cli": "cli"}
_CACHE: dict[str, Path] = {}

# the arguments of one valid call per klode tool (against ``fx-labs-3x``)
TOOL_CALLS: dict[str, dict] = {
    "list_lenses": {},
    "diagnose": {"symptom": "偏高"},
    "consult_dimension": {"dimension": "blood-routine"},
    "consult_framework": {"name": "blood-routine"},
    "search_sources": {"query": "MARKERAZETA MARKERBZETA MARKERCZETA"},
    "zoom_card": {"id": "hemoglobin"},
    "verify_quote": {"id": "hemoglobin", "phrase": "血红蛋白 135 g/L"},
    "list_kbs": {},
}


# --------------------------------------------------------------------------- klode

def klode_exe() -> str:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the MCP wrapper tests"
    return exe


def klode_python() -> str:
    first = Path(klode_exe()).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!"), first
    return first[2:].strip()


def klode_site() -> str:
    """The site-packages directory of the installed klode (what a klode inside the venv would add to the path)."""
    ran = subprocess.run([klode_python(), "-c", "import klode, os; print(os.path.dirname(os.path.dirname(klode.__file__)))"],
                         capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout.strip()


def live_tools() -> list[dict]:
    """``klode.lib.mcp_server.TOOLS`` of the installed klode, taken from its own interpreter."""
    ran = subprocess.run([klode_python(), "-c", "import json; from klode.lib import mcp_server; print(json.dumps(mcp_server.TOOLS))"],
                         capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    return json.loads(ran.stdout)


# --------------------------------------------------------------------------- the marked builds

def _mark(kb: Path, build_id: str, word: str) -> None:
    build = kb / "builds" / build_id
    B.unlock(build)
    card = build / "library" / "cards" / "hemoglobin.md"
    text = card.read_text(encoding="utf-8")
    anchor = "（只描述数值，不作临床解读。）"
    assert anchor in text
    card.write_text(text.replace(anchor, anchor + word, 1), encoding="utf-8")
    receipt = build / "write_receipt.json"
    doc = json.loads(receipt.read_text(encoding="utf-8"))
    doc["cards"]["hemoglobin"]["card_sha256"] = hashlib.sha256(card.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    KX.reseal(build)
    proc, rec = KX.check(kb, "--build", build_id)
    assert proc.returncode == 0 and rec and rec["status"] == "PASS", (proc.stderr.decode(), rec)


def _built() -> Path:
    if "project" not in _CACHE:
        root = Path(tempfile.mkdtemp(prefix="mcp-fx-"))
        project = B.project(root)
        for _ in range(3):
            res = B.build(project, root)
            assert res.returncode == 0, res.stderr.decode()
        kb = B.kb_dir(project)
        ids = [p.name for p in B.builds(project)]
        assert len(ids) == 3 and ids == sorted(ids)
        for tag, build_id in zip("ABC", ids):
            _mark(kb, build_id, MARKERS[tag])
        _CACHE["project"] = project
    return _CACHE["project"]


def marked(tmp: Path) -> SimpleNamespace:
    """A fresh copy of the marked project: ``project``, ``kb``, ``ids`` ({"A": id, ...}), ``env`` (private home,
    registry), ``tmp``."""
    project = tmp / "proj"
    shutil.copytree(_built(), project, symlinks=True)
    kb = B.kb_dir(project)
    ids = dict(zip("ABC", (p.name for p in B.builds(project))))
    reg = tmp / "registry" / "registry.toml"
    env = base_env({"HOME": str(B.home(tmp)), "KLODE_REGISTRY": str(reg), "KLODE_KIT_TEST_SEAMS": "1"})
    env.pop("KLODE_KIT_REAL", None)
    return SimpleNamespace(project=project, kb=kb, ids=ids, env=env, tmp=tmp)


def publish(world: SimpleNamespace, tag: str) -> None:
    code, body = P.publish(world.kb, world.ids[tag], project=world.project, env=world.env)
    assert code == 0, body


def readlink_tag(world: SimpleNamespace) -> str | None:
    link = world.kb / "current"
    if not link.is_symlink():
        return None
    build_id = os.path.basename(os.readlink(link))
    return next((t for t, i in world.ids.items() if i == build_id), None)


def marker_tags(text: str) -> set[str]:
    return {t for t, w in MARKERS.items() if w in text}


def lease_files(kb: Path) -> list[Path]:
    root = kb / ".readers"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


def lease_tags(world: SimpleNamespace) -> set[str]:
    """Which builds hold a lease of the wrapper (the directory name below ``kb/.readers/``)."""
    by_id = {i: t for t, i in world.ids.items()}
    return {by_id[p.parent.name] for p in lease_files(world.kb)}


# --------------------------------------------------------------------------- environments

def _shadow(tmp: Path) -> Path:
    """A ``klode`` package without ``klode.lib.mcp_server``: ``from klode.lib import mcp_server`` fails."""
    root = tmp / "shadow"
    (root / "klode" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "klode" / "__init__.py").write_text("", encoding="utf-8")
    (root / "klode" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    return root


def _dead_klode_mcp(tmp: Path) -> Path:
    bindir = tmp / "deadbin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "klode-mcp"
    exe.write_text("#!/bin/sh\necho 'klode-mcp: gone' >&2\nexit 1\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return bindir


def mode_env(world: SimpleNamespace, mode: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(world.env)
    site = klode_site()
    if mode == "normal":
        env["PYTHONPATH"] = site
    else:
        env["PYTHONPATH"] = os.pathsep.join([str(_shadow(world.tmp)), site])
    if mode == "cli":
        env["PATH"] = os.pathsep.join([str(_dead_klode_mcp(world.tmp)), env.get("PATH", "")])
    if extra:
        env.update(extra)
    return env


def _script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_klode_mcp(tmp: Path, kind: str) -> dict[str, str]:
    """An environment tweak (``PATH`` first) that puts a *misbehaving* ``klode-mcp`` in front of the real one.

    ``noexec`` an executable that is no program (no interpreter line: ``exec`` fails with ENOEXEC); ``noisy`` prints a
    non-JSON line and a response with another id, then becomes the real ``klode-mcp``; ``hang`` answers ``initialize`` and
    then never answers anything, and does not leave at EOF; ``silent`` never answers at all.
    """
    real = shutil.which("klode-mcp")
    assert real
    bindir = tmp / f"fake-{kind}"
    if kind == "noexec":
        _script(bindir / "klode-mcp", "this is not a program\n")
    elif kind == "noisy":
        _script(bindir / "klode-mcp", "#!/bin/sh\necho 'not json at all'\n"
                "echo '{\"jsonrpc\": \"2.0\", \"id\": \"stale\", \"result\": {}}'\n"
                f"exec '{real}' \"$@\"\n")
    elif kind == "hang":
        _script(bindir / "klode-mcp", f"#!{sys.executable}\nimport json, sys, time\n"
                "first = json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'jsonrpc': '2.0', 'id': first['id'], 'result': {'protocolVersion': '2025-06-18', "
                "'capabilities': {'tools': {}}, 'serverInfo': {'name': 'klode', 'version': '0.5.0'}}}), flush=True)\n"
                "sys.stdin.readline()\nwhile True:\n    time.sleep(1)\n")
    elif kind == "silent":
        _script(bindir / "klode-mcp", f"#!{sys.executable}\nimport time\nwhile True:\n    time.sleep(1)\n")
    else:  # pragma: no cover - a typo in a test
        raise AssertionError(kind)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}


def garbage_klode(tmp: Path) -> dict[str, str]:
    """``PATH`` with a ``klode`` that prints no JSON (and complains on stderr), behind a dead ``klode-mcp``."""
    _script(tmp / "garbage-klode" / "klode", "#!/bin/sh\necho 'this is not json'\necho 'klode: oops' >&2\nexit 1\n")
    return {"PATH": os.pathsep.join([str(tmp / "garbage-klode"), str(_dead_klode_mcp(tmp)), os.environ.get("PATH", "")])}


def children(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(x) for x in out]


def is_zombie(pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return out.startswith("Z")


# --------------------------------------------------------------------------- the server process

class Server:
    """``bin/klode-kit mcp --kb KB`` as a subprocess; one JSON-RPC line per request and per answer."""

    def __init__(self, kb: Path, env: Mapping[str, str], *, args: list[str] | None = None):
        self.proc = subprocess.Popen([str(LAUNCHER), "mcp", "--kb", str(kb), *(args or [])], env=dict(env),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=str(REPO), bufsize=0)
        self.pid = self.proc.pid
        self.buf = b""
        self.err: list[bytes] = []
        self.next_id = 1
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.proc.stderr is not None
        for chunk in iter(lambda: self.proc.stderr.read(4096), b""):
            self.err.append(chunk)

    @property
    def stderr(self) -> str:
        return b"".join(self.err).decode("utf-8", "replace")

    def send_raw(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((line + "\n").encode("utf-8"))

    def send(self, payload: Mapping) -> None:
        self.send_raw(json.dumps(payload, ensure_ascii=False))

    def readline(self, timeout: float = 60.0) -> bytes | None:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    def recv(self, timeout: float = 60.0) -> dict:
        line = self.readline(timeout)
        assert line is not None, f"no answer within {timeout}s; stderr: {self.stderr[-800:]}"
        return json.loads(line)

    def silent(self, seconds: float = 0.6) -> bool:
        """True when nothing is written to stdout for ``seconds``."""
        return self.readline(seconds) is None

    def request(self, method: str, params: Mapping | None = None, timeout: float = 60.0) -> dict:
        rid = self.next_id
        self.next_id += 1
        msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        answer = self.recv(timeout)
        assert answer.get("id") == rid, answer
        return answer

    def initialize(self) -> dict:
        answer = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "test", "version": "0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return answer

    def call(self, name: str, arguments: Mapping | None = None, timeout: float = 120.0) -> dict:
        return self.request("tools/call", {"name": name, "arguments": dict(arguments or {})}, timeout)

    def version(self) -> dict:
        res = self.call("kb_version")["result"]
        assert res["isError"] is False, res
        return json.loads(res["content"][0]["text"])

    def close(self, timeout: float = 30.0) -> int:
        """Close stdin (EOF) and wait; returns the exit code."""
        if self.proc.poll() is None:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def text_of(answer: Mapping) -> str:
    return answer["result"]["content"][0]["text"]


def start(world: SimpleNamespace, mode: str = "normal", extra: Mapping[str, str] | None = None) -> Server:
    server = Server(world.kb, mode_env(world, mode, extra))
    server.initialize_answer = server.initialize()  # type: ignore[attr-defined]
    return server


def python() -> str:
    return sys.executable


def wait_until(check, timeout: float = 30.0) -> bool:
    """Poll ``check()`` until it is true (the wrapper lets go of a lease just *after* it has sent the answer)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return check()
