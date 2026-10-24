"""klode-mcp smoke run: initialize -> tools/list -> search -> zoom -> verify (real phrase VERIFIED / fake NOT FOUND).

Vendored from the user's klode-build skill ``scripts/smoke_mcp.py`` (read-only source, 2026-10-24) (WI-02d).

    klode-kit smoke-mcp KB --card ID --real PHRASE [--query Q] [--desk SYMPTOM=DIMENSION] [--timeout S]
    exit 0 every check PASS; 1 any FAIL; 2 usage; 40 no klode-mcp on PATH

Whether the MCP works is judged only by running it, never by "the path exists" (kept, 03 A5). It talks to
``klode-mcp -c KB/library.toml`` directly over stdio, so it needs no registration (03 A5 "不登记也能跑"): in a
versioned build it always starts a new process on ``builds/<build_id>`` (02 r3.2).

Kept: the six checks and their judgements - ``initialize`` serverInfo name ``klode``; ``tools/list`` holds
search_sources / zoom_card / verify_quote / list_kbs; ``search_sources(query, full=true)`` names the card;
``zoom_card`` thin longer than 40 characters; ``verify_quote`` of ``--real`` says ``VERIFIED``; of a fixed fake
sentence says ``NOT FOUND``. ``--desk`` adds list_lenses (names the dimension), diagnose (dimension in the first
three lines) and consult_dimension (``Craft`` or ``机制``). The MCP argument names are kept (``id`` not ``card``;
``symptom``; ``dimension``) - a wrong name is not an error, the server just says "not installed".

Changed:

- **A5** (03 §5 次要, 02 §3.1 "默认 query 改取 alias 首项"): without ``--query`` the search query is the card's first
  alias; the original took the first two words of ``--real`` (``"one part"`` in the baseline run - a hit by luck).
  A card without aliases falls back to that original rule.
- ``klode-mcp`` is found on PATH (the original hard-coded its install path under the home directory); missing
  -> exit 40.
- A server that dies, answers a JSON-RPC error or stays silent past ``--timeout`` seconds fails the affected check
  and every later one (exit 1), instead of a traceback or a hang; the process is always terminated.
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_FAIL, EXIT_USAGE, EXIT_ENV = 1, 2, 40
FAKE = "this sentence was never written in any book zzqx"
REQUIRED_TOOLS = {"search_sources", "zoom_card", "verify_quote", "list_kbs"}
ALIASES_RE = re.compile(r"^aliases:\s*\[(.*)\]\s*$", re.M)


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = ""


class ServerGone(Exception):
    """The server died, answered with an error, or stayed silent past the timeout."""


def default_query(kb: Path, card: str, real: str) -> str:
    """The card's first alias (03 A5); without aliases (or card file) the original rule: ``--real``'s first two
    words."""
    path = kb / "library" / "cards" / f"{card}.md"
    if path.is_file():
        m = ALIASES_RE.search(path.read_text(encoding="utf-8"))
        if m:
            first = m.group(1).split(",")[0].strip().strip("'\"").strip()
            if first:
                return first
    return " ".join(real.split()[:2])


class _Client:
    def __init__(self, exe: str, config: Path, timeout: float) -> None:
        self.timeout = timeout
        self.proc = subprocess.Popen([exe, "-c", str(config)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.n = 0
        self.dead: str | None = None

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._gone(f"write failed: {exc}") from exc

    def _gone(self, why: str) -> ServerGone:
        self.dead = self.dead or why
        return ServerGone(self.dead)

    def rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        if self.dead:
            raise ServerGone(self.dead)
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if notify:
            self._send(msg)
            return None
        self.n += 1
        msg["id"] = self.n
        self._send(msg)
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty:
                raise self._gone(f"no answer to {method} within {self.timeout:g}s") from None
            if line is None:
                raise self._gone(f"server exited during {method}")
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # not a JSON-RPC line
            if reply.get("id") != self.n:
                continue  # a notification or a stale reply
            if "error" in reply:
                raise ServerGone(f"{method}: {reply['error'].get('message', reply['error'])}")
            return reply.get("result")

    def call(self, name: str, args: dict[str, Any]) -> str:
        result = self.rpc("tools/call", {"name": name, "arguments": args}) or {}
        content = result.get("content") or [{}]
        return str(content[0].get("text", ""))

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - a server ignoring SIGTERM
            self.proc.kill()
            self.proc.wait()


def _first(text: str) -> str:
    return text.splitlines()[0][:80] if text else ""


def run(kb: Path, card: str, real: str, *, query: str | None = None, desk: tuple[str, str] | None = None,
        timeout: float = 30.0, exe: str) -> list[Check]:
    """Every check of one smoke run, in order (six, nine with ``desk``)."""
    query = query or default_query(kb, card, real)
    client = _Client(exe, (kb / "library.toml").resolve(), timeout)
    checks: list[Check] = []

    def probe(label: str, fn) -> None:
        try:
            ok, detail = fn()
        except ServerGone as exc:
            ok, detail = False, str(exc)
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            ok, detail = False, f"malformed answer: {exc!r}"
        checks.append(Check(label, bool(ok), detail))

    def initialize():
        info = client.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                         "clientInfo": {"name": "klode-kit-smoke", "version": "0"}})["serverInfo"]
        client.rpc("notifications/initialized", notify=True)
        return info.get("name") == "klode", f"{info.get('name')} {info.get('version')}"

    def tools():
        names = {t["name"] for t in client.rpc("tools/list", {})["tools"]}
        return REQUIRED_TOOLS <= names, f"{len(names)} tools"

    def search():
        r = client.call("search_sources", {"query": query, "full": True})
        return card in r, _first(r)

    def zoom():
        r = client.call("zoom_card", {"id": card, "level": "thin"})
        return len(r.strip()) > 40, ""

    def verify_real():  # MCP takes `id`; the CLI's klode verify takes `card` - a wrong name only says "not installed"
        r = client.call("verify_quote", {"id": card, "phrase": real, "max_lines": 1})
        return "VERIFIED" in r, _first(r)

    def verify_fake():
        r = client.call("verify_quote", {"id": card, "phrase": FAKE})
        return "NOT FOUND" in r, ""

    try:
        probe("initialize", initialize)
        probe("tools/list", tools)
        probe(f"search_sources({query!r}, full) 命中 {card}", search)
        probe("zoom_card thin 非空", zoom)
        probe("verify_quote 真句 VERIFIED", verify_real)
        probe("verify_quote 伪句 NOT FOUND", verify_fake)
        if desk:
            symptom, dim = desk

            def lenses():
                r = client.call("list_lenses", {})
                return dim in r, _first(r)

            def diagnose():  # MCP diagnose takes `symptom`, consult_dimension `dimension` (not the CLI names)
                r = client.call("diagnose", {"symptom": symptom})
                return dim in "\n".join(r.split("\n")[:3]), _first(r)

            def consult():
                r = client.call("consult_dimension", {"dimension": dim})
                return "Craft" in r or "机制" in r, _first(r)

            probe(f"list_lenses 含 {dim}", lenses)
            probe(f"diagnose({symptom!r}) → {dim}", diagnose)
            probe(f"consult_dimension({dim}) 回 Craft", consult)
    finally:
        client.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit smoke-mcp", description=__doc__.split("\n")[0])
    ap.add_argument("kb", type=Path)
    ap.add_argument("--card", required=True)
    ap.add_argument("--real", required=True, help="a sentence that occurs verbatim in the card's source")
    ap.add_argument("--query", default=None, help="search query (default: the card's first alias)")
    ap.add_argument("--desk", default=None, metavar="SYMPTOM=DIMENSION",
                    help="desk smoke: a plain question = the expected dimension (list_lenses / diagnose / consult)")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for each answer")
    a = ap.parse_args(argv)
    desk = None
    if a.desk is not None:
        if "=" not in a.desk:
            print("klode-kit smoke-mcp: usage error: --desk takes SYMPTOM=DIMENSION", file=sys.stderr)
            return EXIT_USAGE
        symptom, dim = a.desk.split("=", 1)
        desk = (symptom, dim)
    exe = shutil.which("klode-mcp")
    if not exe:
        print("klode-kit smoke-mcp: klode-mcp not found on PATH (install klode 0.5.0)", file=sys.stderr)
        return EXIT_ENV
    checks = run(a.kb, a.card, a.real, query=a.query, desk=desk, timeout=a.timeout, exe=exe)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.label}" + (f"  — {c.detail}" if c.detail else ""))
    fails = [c.label for c in checks if not c.ok]
    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{', '.join(fails)}"))
    return EXIT_FAIL if fails else 0


if __name__ == "__main__":
    sys.exit(main())
