"""Helpers for the 13-step build tests (WI-09b; matrix 09b-*).

- ``project(tmp, fx)``: a data project whose records the real stages wrote (``export_fx.pipeline``: kb-ocr replay ->
  kb-audit -> writeback), the de-identification key created up front (the key is a one-time secret of the data
  project, not a build product: ``deident.load_key``), copied from a per-process cache.
- ``build(project, *args, ...)``: the real command ``python -m klode_kit build`` as a subprocess; stdout's last line
  is the JSON summary (``summary``).
- ``Fault``: two executables written into a tmp directory - a launcher wrapper and a ``klode`` wrapper - that log
  every call (argv) and exit non-zero when a call matches the injected fault ("replay scenario" for the double
  boundary: the launcher is the project's own command surface, ``klode`` the external CLI); everything else is
  handed to the real executable.
- the book path: the aurora text, its explicit chapter manifest (ids ``ch01..``, the ids the aurora desk names) and
  the writer replay (fake ``claude`` behaviour scenario writing valid cards) and desk syntheses.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from _support import desk_fx as DX
from _support import export_fx as X
from _support.flows import REPO, base_env
from _support.write_card_fx import content as card_content
from klode_kit.build import deident as deident_mod

FIXTURES = REPO / "tests" / "fixtures"
LAUNCHER = REPO / "bin" / "klode-kit"
AURORA = FIXTURES / "fx-book-aurora"
_CACHE: dict[str, Path] = {}


# --------------------------------------------------------------------------- projects


def _make(root: Path, fx: str) -> Path:
    project = root / "proj"
    project.mkdir(parents=True)
    X.pipeline(project, FIXTURES / fx)
    deident_mod.load_key(project)
    return project


def project(tmp: Path, fx: str = "fx-labs-3x") -> Path:
    """A fresh copy of the audited, written-back project of ``fx`` (built once per process)."""
    if fx not in _CACHE:
        import tempfile

        _CACHE[fx] = _make(Path(tempfile.mkdtemp(prefix=f"build-fx-{fx}-")), fx)
    dest = tmp / "proj"
    shutil.copytree(_CACHE[fx], dest, symlinks=True)
    return dest


def deident_key(project: Path) -> None:
    """Create the project's de-identification key up front (a one-time secret, not a build product)."""
    deident_mod.load_key(project)


def home(tmp: Path) -> Path:
    h = tmp / "home"
    h.mkdir(exist_ok=True)
    return h


# --------------------------------------------------------------------------- running


def klode_exe() -> str:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the build tests"
    return exe


def env(tmp: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    e = base_env({"HOME": str(home(tmp)), **(extra or {})})
    e.pop("KLODE_KIT_REAL", None)
    return e


def build(project: Path, tmp: Path, *args: str, extra_env: Mapping[str, str] | None = None,
          timeout: float = 600) -> subprocess.CompletedProcess:
    klode_exe()
    return subprocess.run([sys.executable, "-m", "klode_kit", "build", "--project", str(project), *args],
                          env=env(tmp, extra_env), capture_output=True, timeout=timeout, cwd=str(project))


def summary(res: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in res.stdout.decode().splitlines() if ln.strip()]
    assert lines, res.stderr.decode()
    return json.loads(lines[-1])


def kb_dir(project: Path) -> Path:
    dirs = sorted((project / "kb").iterdir())
    assert len(dirs) == 1, dirs
    return dirs[0]


def builds(project: Path) -> list[Path]:
    root = kb_dir(project) / "builds"
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("b-")) if root.is_dir() else []


def receipt(build_dir: Path) -> dict:
    return json.loads((build_dir / "build_receipt.json").read_text(encoding="utf-8"))


def step_names(rec: Mapping) -> list[str]:
    return [s["step"] for s in rec["steps"]]


# --------------------------------------------------------------------------- trees


def snapshot(root: Path) -> dict[str, tuple]:
    """``{relative path: (kind, sha256 or link target, mtime_ns)}`` of everything under ``root`` (links not followed)."""
    import hashlib

    out: dict[str, tuple] = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(dirpath, name)
            rel = p.relative_to(root).as_posix()
            st = p.lstat()
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p), st.st_mtime_ns)
            elif p.is_dir():
                out[rel] = ("dir", "", st.st_mtime_ns)
            else:
                out[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns)
    return out


def changed(before: Mapping[str, tuple], after: Mapping[str, tuple]) -> set[str]:
    """Paths added, removed or whose content/target changed (directory mtimes of untouched dirs are ignored)."""
    keys = set(before) | set(after)
    out = set()
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b is None or a is None or b[:2] != a[:2]:
            out.add(k)
        elif a[0] == "file" and b[2] != a[2]:
            out.add(k)
    return out


def writable_bits(root: Path) -> list[str]:
    """Every path under ``root`` (and ``root``) that has a write permission bit set (links skipped)."""
    bad = []
    for dirpath, dirs, files in os.walk(root):
        for name in [""] + dirs + files:
            p = Path(dirpath, name) if name else Path(dirpath)
            if not p.is_symlink() and p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                bad.append(p.relative_to(root).as_posix() or ".")
    return bad


def unlock(root: Path) -> None:
    """Give the owner write access again (tests that tamper with a finished build)."""
    for dirpath, dirs, files in os.walk(root):
        os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IWUSR)
        for f in files:
            p = Path(dirpath, f)
            if not p.is_symlink():
                os.chmod(p, p.stat().st_mode | stat.S_IWUSR)


# --------------------------------------------------------------------------- fault injection


WRAPPER = '''#!{python}
import json, os, sys
argv = sys.argv[1:]
log = os.environ.get("BUILD_FAULT_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({{"tool": {tool!r}, "argv": argv}}) + "\\n")
spec = json.loads(os.environ.get("BUILD_FAULT", "null"))
real = {real!r}
if spec and spec["tool"] == {tool!r} and all(t in argv for t in spec["tokens"]):
    if spec.get("mutate") == "forced":  # the command succeeds but its write receipt says it was forced
        import subprocess
        rc = subprocess.call([real, *argv])
        path = os.path.join(argv[1], "write_receipt.json")
        doc = json.load(open(path, encoding="utf-8"))
        doc["forced"] = True
        json.dump(doc, open(path, "w", encoding="utf-8"))
        sys.exit(rc)
    sys.stderr.write("injected failure: " + " ".join(argv[:3]) + "\\n")
    sys.exit(int(spec.get("exit", 1)))
os.execv(real, [real, *argv])
'''


class Fault:
    """Wrapper launcher + wrapper ``klode`` in ``<tmp>/fault`` (put first on PATH); ``spec`` picks the failing call."""

    def __init__(self, tmp: Path):
        self.dir = tmp / "fault"
        self.dir.mkdir()
        self.log = tmp / "fault-calls.jsonl"
        self.launcher = self._write("launcher", "launcher", str(LAUNCHER))
        self._write("klode", "klode", klode_exe())

    def _write(self, name: str, tool: str, real: str) -> Path:
        path = self.dir / name
        path.write_text(WRAPPER.format(python=sys.executable, tool=tool, real=real), encoding="utf-8")
        path.chmod(0o755)
        return path

    def env(self, tmp: Path, tool: str | None = None, tokens: Sequence[str] = (), exit_code: int = 1,
            mutate: str | None = None) -> dict[str, str]:
        e = env(tmp)
        e["PATH"] = str(self.dir) + os.pathsep + e["PATH"]
        e["BUILD_FAULT_LOG"] = str(self.log)
        e["BUILD_FAULT"] = (json.dumps({"tool": tool, "tokens": list(tokens), "exit": exit_code, "mutate": mutate})
                            if tool else "null")
        return e

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]


def run_with(project: Path, tmp: Path, fault: Fault, tool: str | None = None, tokens: Sequence[str] = (),
             *args: str, extra_env: Mapping[str, str] | None = None, mutate: str | None = None,
             timeout: float = 600) -> subprocess.CompletedProcess:
    e = fault.env(tmp, tool, tokens, mutate=mutate)
    e.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "klode_kit", "build", "--project", str(project),
                           "--launcher", str(fault.launcher), *args],
                          env=e, capture_output=True, timeout=timeout, cwd=str(project))


# --------------------------------------------------------------------------- the book path (aurora)


def aurora_manifest(dest: Path) -> Path:
    """The explicit chapter manifest of the aurora fixture: ids ``ch01..ch03`` (the ids ``src/desk.json`` names)."""
    text = (AURORA / "aurora-kettle.txt").read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    scen = json.loads((AURORA / "scenario.json").read_text(encoding="utf-8"))
    pieces = []
    for ch in scen["chapters"]:
        n = next(i for i, ln in enumerate(lines, 1) if ln.strip() == ch["head"])
        pieces.append({"id": ch["id"], "title": ch["head"], "start_line": n})
    pieces[0]["start_line"] = 1
    doc = {"shelf": "book", "whole": "library/book/_whole-book.txt", "citation": "The Aurora Kettle", "pieces": pieces}
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def ok_step(card: str) -> dict:
    """A fake-claude step: write ``cards_content/<id>.md`` and answer with the writer receipt."""
    cid = Path(card).stem
    receipt_json = {"card": card, "status": "written", "kind": "authored", "anchors_full": len(DX.ANCHORS[cid]),
                    "aliases": len(DX.ALIASES[cid]), "rounds": 1}
    return {"write_files": {card: card_content(DX.ANCHORS[cid], DX.ALIASES[cid])},
            "stdout": json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                                  "structured_output": receipt_json, "permission_denials": []}),
            "exit_code": 0}


def writer_replay(tmp: Path, *, bad: Sequence[str] = ()) -> dict[str, str]:
    """Environment for the fake ``claude``: a behaviour scenario writing the three aurora cards (``bad``: card ids
    whose card is left with a non-verbatim anchor, so gate 1 fails)."""
    by_card = {}
    for cid in DX.CH:
        step = ok_step(f"cards_content/{cid}.md")
        if cid in bad:
            anchors = list(DX.ANCHORS[cid])
            anchors[0] = anchors[0].replace("a", "A", 1) + " (not verbatim)"
            step["write_files"] = {f"cards_content/{cid}.md": card_content(anchors, DX.ALIASES[cid])}
        by_card[f"cards_content/{cid}.md"] = [step]
    path = tmp / "writer-scenario.json"
    path.write_text(json.dumps({"by_card": by_card}), encoding="utf-8")
    log = tmp / "claude-calls.jsonl"
    return {"KLODE_KIT_FAKE_SCENARIO": str(path), "KLODE_KIT_FAKE_LOG": str(log)}


# The fixture's own desk (``fx-book-aurora/src``) scores 3/5 on its holdout with the real klode 0.5.0 (substring
# routing: "retinning" does not reach the cue "tinning", "darkened copper shell" reaches the cue "copper shell") -
# below the 0.8 gate, a finding for WI-31 (impl-deviations). The build tests use the desk of the desk tests
# (tests/cli/test_desk_cli.py, 02c-01) over the same three aurora cards: it passes the gate with the real klode.
DESK_SPEC = {"threshold": 0.8, "dimensions": [
    {"dimension": "construction", "title": "Construction",
     "question": "How is the hand-made kettle body built to be long-lasting?",
     "cards": ["ch01"], "cues": ["copper shell", "annealing", "domed base", "tinning"],
     "tests": ["Why is the copper shell annealed twice?"]},
    {"dimension": "whistle-valve", "title": "Whistle valve",
     "question": "How does the two-tone whistle stay in tune?",
     "cards": ["ch02"], "cues": ["whistle", "reed", "valve cap"],
     "tests": ["What makes the whistle produce two tones?"]},
    {"dimension": "safety", "title": "Safety at the night market",
     "question": "What keeps a night-market kettle safe from the open flame and well-kept?",
     "cards": ["ch03"], "cues": ["ember bed", "waxed cord", "flame flares", "unattended"],
     "tests": ["Can the kettle be left unattended on the ember bed?"]},
]}
DESK_HOLDOUT = [
    {"q": "How often is tinning renewed inside the kettle?", "gold": "construction"},
    {"q": "Does the domed base matter on a flat stove?", "gold": "construction"},
    {"q": "Why is the valve cap threaded the other way?", "gold": "whistle-valve"},
    {"q": "The reed sounds dull - what now?", "gold": "whistle-valve"},
    {"q": "Why is the handle wrapped in waxed cord?", "gold": "safety"},
]


def desk_files(dest: Path) -> tuple[Path, Path]:
    """``desk.json`` and its ``holdout.jsonl`` (side by side, as the build looks for them)."""
    dest.mkdir(parents=True, exist_ok=True)
    desk, holdout = dest / "desk.json", dest / "holdout.jsonl"
    desk.write_text(json.dumps(DESK_SPEC, ensure_ascii=False, indent=1), encoding="utf-8")
    holdout.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in DESK_HOLDOUT), encoding="utf-8")
    return desk, holdout


def syntheses(dest: Path) -> Path:
    """Desk worker output for the desk above: one passing synthesis per dimension, anchors copied from the cards."""
    dest.mkdir(parents=True, exist_ok=True)
    for d in DESK_SPEC["dimensions"]:
        anchors = [a for c in d["cards"] for a in DX.ANCHORS.get(c, [])][:4]
        (dest / f"{d['dimension']}.md").write_text(DX.synthesis(d["dimension"], d["cards"], anchors), encoding="utf-8")
    return dest


def book_args(tmp: Path) -> list[str]:
    """``--source/--shelf/--manifest/--desk/--syntheses`` of the U7 form on the aurora text."""
    desk, _ = desk_files(tmp / "desk")
    return ["--source", str(AURORA / "aurora-kettle.txt"), "--shelf", "book",
            "--manifest", str(aurora_manifest(tmp / "chapters-book.json")),
            "--desk", str(desk), "--syntheses", str(syntheses(tmp / "syntheses"))]
