"""Helpers for the repair-loop tests (WI-09c; matrix 09c-*).

- ``Faults``: a launcher wrapper and a ``klode`` wrapper (like ``build_fx.Fault``) whose faults are a list; every
  fault names the call (tool + argv tokens), how many times it fails (``times``; ``None`` = always), what it prints
  and the exit code. Everything else is handed to the real executable. All calls are logged in order.
- ``writer_scenario``: the fake ``claude`` for the three aurora cards, each answer costing ``cost`` USD; ``bad`` cards
  are written with a non-verbatim anchor on the first call, and a prompt carrying the repair marker (the problem list
  of a loop round) gets the good card (fake scenario key ``if_prompt_has``).
- ``run_loops``: the real command ``python -m klode_kit build --loops``.
- readers for the loop receipt and the claude call log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from _support import build_fx as B
from _support import desk_fx as DX
from _support.write_card_fx import content as card_content
from klode_kit import loops as L

WRAPPER = '''#!{python}
import fcntl, json, os, sys
argv = sys.argv[1:]
log = os.environ.get("LOOPS_FAULT_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({{"tool": {tool!r}, "argv": argv}}) + "\\n")
real = {real!r}
faults = json.loads(os.environ.get("LOOPS_FAULTS", "[]"))
state_path = os.environ.get("LOOPS_FAULT_STATE")
for i, f in enumerate(faults):
    if f["tool"] != {tool!r} or not all(t in argv for t in f["tokens"]):
        continue
    fd = os.open(state_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 4096).decode() or "{{}}"
        state = json.loads(raw)
        seen = state.get(str(i), 0)
        state[str(i)] = seen + 1
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(state).encode())
    finally:
        os.close(fd)
    if f.get("times") is None or seen < f["times"]:
        sys.stdout.write(f.get("stdout", ""))
        sys.stderr.write(f.get("stderr", "injected failure\\n"))
        sys.exit(int(f.get("exit", 1)))
os.execv(real, [real, *argv])
'''


class Faults:
    """``<tmp>/lfault`` with the two wrappers; ``faults`` is the list of fault specs (see module doc)."""

    def __init__(self, tmp: Path, faults: Sequence[Mapping] = ()):
        self.dir = tmp / "lfault"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = tmp / "lfault-calls.jsonl"
        self.state = tmp / "lfault-state.json"
        self.faults = [dict(f) for f in faults]
        self.launcher = self._write("launcher", "launcher", str(B.LAUNCHER))
        self._write("klode", "klode", B.klode_exe())

    def _write(self, name: str, tool: str, real: str) -> Path:
        path = self.dir / name
        path.write_text(WRAPPER.format(python=sys.executable, tool=tool, real=real), encoding="utf-8")
        path.chmod(0o755)
        return path

    def env(self, tmp: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        e = B.env(tmp)
        e["PATH"] = str(self.dir) + os.pathsep + e["PATH"]
        e["LOOPS_FAULT_LOG"] = str(self.log)
        e["LOOPS_FAULT_STATE"] = str(self.state)
        e["LOOPS_FAULTS"] = json.dumps(self.faults)
        e.update(extra or {})
        return e

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]

    def commands(self, tool: str = "launcher") -> list[str]:
        """The first argv token of every call of ``tool`` (for ``klode``: the token after ``-c <toml>``)."""
        out = []
        for c in self.calls():
            if c["tool"] != tool:
                continue
            a = c["argv"]
            out.append(a[2] if tool == "klode" and a[:1] == ["-c"] and len(a) > 2 else (a[0] if a else ""))
        return out


def run_loops(project: Path, tmp: Path, faults: Faults | None, *args: str, extra_env: Mapping[str, str] | None = None,
              timeout: float = 900) -> subprocess.CompletedProcess:
    e = faults.env(tmp, extra_env) if faults is not None else {**B.env(tmp), **(extra_env or {})}
    argv = [sys.executable, "-m", "klode_kit", "build", "--loops", "--project", str(project)]
    if faults is not None:
        argv += ["--launcher", str(faults.launcher)]
    return subprocess.run([*argv, *args], env=e, capture_output=True, timeout=timeout, cwd=str(project))


def loops_dir(project: Path) -> Path:
    root = B.kb_dir(project) / "loops"
    runs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    assert len(runs) == 1, runs
    return runs[0]


def loops_receipt(project: Path) -> dict:
    return json.loads((loops_dir(project) / "loops_receipt.json").read_text(encoding="utf-8"))


def claude_calls(tmp: Path) -> list[dict]:
    log = tmp / "claude-calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def card_of(call: dict) -> str:
    """The card id a writer call was for (the last argument is the prompt: ``card: cards_content/<id>.md``)."""
    prompt = call["argv"][-1]
    line = next(ln for ln in prompt.splitlines() if ln.startswith("card: "))
    return Path(line[len("card: "):]).stem


def writer_scenario(tmp: Path, *, bad: Sequence[str] = (), cost: float = 0.01) -> dict[str, str]:
    """Environment for the fake ``claude`` (see module doc)."""
    by_card = {}
    for cid in DX.CH:
        card = f"cards_content/{cid}.md"
        good = B.ok_step(card)
        good_out = json.loads(good["stdout"])
        good_out["total_cost_usd"] = cost
        good["stdout"] = json.dumps(good_out)
        steps = [dict(good)]
        if cid in bad:
            anchors = list(DX.ANCHORS[cid])
            anchors[0] = anchors[0].replace("a", "A", 1) + " (not verbatim)"
            first = dict(good)
            first["write_files"] = {card: card_content(anchors, DX.ALIASES[cid])}
            steps = [first, {**good, "if_prompt_has": L.REPAIR_MARKER}]
        by_card[card] = steps
    path = tmp / "writer-scenario.json"
    path.write_text(json.dumps({"by_card": by_card}), encoding="utf-8")
    return {"KLODE_KIT_FAKE_SCENARIO": str(path), "KLODE_KIT_FAKE_LOG": str(tmp / "claude-calls.jsonl")}


def l1_desk_args(tmp: Path) -> list[str]:
    """Book args of the U7 form whose desk misses the whistle questions: its only cue is a dummy, and the tuning
    question of the dimension carries the card aliases the holdout questions use (``valve cap``, ``reed``)."""
    import copy

    spec = copy.deepcopy(B.DESK_SPEC)
    dim = next(d for d in spec["dimensions"] if d["dimension"] == "whistle-valve")
    dim["cues"] = ["zzz-dummy-cue"]
    dim["tests"] = ["How does the valve cap and the reed give the whistle valve a two-tone note?"]
    desk_dir = tmp / "l1desk"
    desk_dir.mkdir(parents=True, exist_ok=True)
    (desk_dir / "desk.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    (desk_dir / "holdout.jsonl").write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in B.DESK_HOLDOUT),
                                            encoding="utf-8")
    return ["--source", str(B.AURORA / "aurora-kettle.txt"), "--shelf", "book",
            "--manifest", str(B.aurora_manifest(tmp / "chapters-book.json")),
            "--desk", str(desk_dir / "desk.json"), "--syntheses", str(B.syntheses(tmp / "syntheses"))]


STRICT_ROT = ("[F citation-rot] cards/{card}.md: grep `copper shell that is` no longer in library/book/{card}.txt\n"
              "klode check --strict: 1 errors, 0 warning(s)\n")
