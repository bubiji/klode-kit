"""Helpers for the publish protocol tests (WI-09d; matrix 09d-*).

- ``ab(tmp)``: ``fx-publish-ab`` - a data project whose ``kb/<id>/builds/`` holds two real, finished builds of
  ``fx-labs-3x`` (A, then B), both judged by the real ``kb-check`` (``check_receipt.status == PASS``); built once per
  process, every call hands out a fresh copy (symlinks kept, modes kept: the trees are read-only as built).
- ``cli(...)``: the real command ``python -m klode_kit kb-publish`` as a subprocess; ``summary(proc)`` is its JSON line.
- ``sound(kb)``: the K5 invariant written down independently of ``klode_kit.publish`` - ``current`` exists, resolves
  to a build below ``builds/`` whose tree equals its ``MANIFEST.sha256``, whose ``check_receipt.json`` is ``PASS`` for
  exactly that build id, and which is read-only all the way down (``.readers/`` excepted). ``[]`` = sound.
- ``fake_kb(tmp, n)``: ``kb/<id>/builds/`` with ``n`` bare build directories (ids ordered by age) for the pure
  bookkeeping of prune / pointer tests that need no real tree.
- ``write_rows``: append valid ``PUBLISH.log`` rows by hand (states the public commands cannot reach on demand).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

from hypothesis import strategies as st

from _support import build_fx as B
from _support import kb_check_fx as KX
from _support.flows import REPO, base_env
from klode_kit.build import run_build as RB

_CACHE: dict[str, Path] = {}
ACTIONS = ("mcp_json", "registry", "agent", "approve")  # the registrations of a publication, in order


def _built() -> Path:
    if "project" not in _CACHE:
        root = Path(tempfile.mkdtemp(prefix="publish-fx-"))
        project = B.project(root)
        for _ in range(2):
            res = B.build(project, root)
            assert res.returncode == 0, res.stderr.decode()
        for build in B.builds(project):
            proc, receipt = KX.check(B.kb_dir(project), "--build", build.name)
            assert proc.returncode == 0 and receipt["status"] == "PASS", proc.stderr.decode()
        _CACHE["project"] = project
    return _CACHE["project"]


def ab(tmp: Path) -> SimpleNamespace:
    """A fresh copy of fx-publish-ab: ``project``, ``kb`` (``kb/<id>``), ``a``, ``b`` (build ids, A older)."""
    project = tmp / "proj"
    shutil.copytree(_built(), project, symlinks=True)
    kb = B.kb_dir(project)
    a, b = (p.name for p in B.builds(project))
    assert a < b
    return SimpleNamespace(project=project, kb=kb, a=a, b=b, tmp=tmp)


def env(tmp: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    reg = tmp / "registry" / "registry.toml"
    e = base_env({"HOME": str(B.home(tmp)), "KLODE_REGISTRY": str(reg), "KLODE_KIT_TEST_SEAMS": "1", **(extra or {})})
    e.pop("KLODE_KIT_REAL", None)
    return e


def registry_file(tmp: Path) -> Path:
    return tmp / "registry" / "registry.toml"


def argv(kb: Path, project: Path | None, *args: str) -> list[str]:
    out = [sys.executable, "-m", "klode_kit", "kb-publish", "--kb", str(kb)]
    if project is not None:
        out += ["--project", str(project)]
    return [*out, *args]


def cli(kb: Path, project: Path | None, tmp: Path, *args: str, extra_env: Mapping[str, str] | None = None,
        timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run(argv(kb, project, *args), env=env(tmp, extra_env), capture_output=True, timeout=timeout,
                          cwd=str(REPO))


def popen(kb: Path, project: Path | None, tmp: Path, *args: str,
          extra_env: Mapping[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(argv(kb, project, *args), env=env(tmp, extra_env), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=str(REPO))


def summary(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.decode().splitlines() if ln.strip().startswith("{")]
    assert lines, proc.stderr.decode()
    return json.loads(lines[-1])


def rows(kb: Path) -> list[dict]:
    path = kb / "PUBLISH.log"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def log_bytes(kb: Path) -> bytes:
    path = kb / "PUBLISH.log"
    return path.read_bytes() if path.is_file() else b""


def current(kb: Path) -> str | None:
    link = kb / "current"
    return os.path.basename(os.readlink(link)) if link.is_symlink() else None


def by_action(kb: Path, build: str, action: str) -> list[dict]:
    return [r for r in rows(kb) if r["target_build_id"] == build and r["action"] == action]


def sound(kb: Path) -> list[str]:
    """The K5 invariant, independent of ``klode_kit.publish`` (``[]`` = ``current`` is sound)."""
    link = kb / "current"
    if not link.is_symlink():
        return ["current is not a link"]
    target = Path(os.path.join(kb, os.readlink(link)))
    if not target.is_dir():
        return [f"current dangles: {os.readlink(link)}"]
    problems = []
    if target.resolve().parent != (kb / "builds").resolve():
        problems.append("current does not point into builds/")
    problems += RB.verify_manifest(target)
    try:
        receipt = json.loads((target / "check_receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return problems + [f"no readable check_receipt.json: {exc}"]
    if receipt.get("status") != "PASS":
        problems.append(f"check_receipt status {receipt.get('status')}")
    if receipt.get("build_id") != target.name:
        problems.append("check_receipt names another build")
    for dirpath, dirs, files in os.walk(target):
        if ".readers" in dirs:
            dirs.remove(".readers")
        for name in [""] + dirs + files:
            p = Path(dirpath, name) if name else Path(dirpath)
            if not p.is_symlink() and p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                problems.append(f"writable: {p.relative_to(target).as_posix() or '.'}")
    return problems


# --------------------------------------------------------------------------- tampering with a finished build


def edit(build: Path, fn) -> None:
    """Run ``fn(build)`` with the tree writable, then seal it read-only again (``.readers/`` untouched)."""
    B.unlock(build)
    try:
        fn(build)
    finally:
        RB.make_readonly(build)


def set_check_receipt(build: Path, **changes) -> None:
    def fn(b: Path) -> None:
        path = b / "check_receipt.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.update(changes)
        path.write_text(json.dumps(doc), encoding="utf-8")

    edit(build, fn)


# --------------------------------------------------------------------------- bare kbs and hand-written logs


def build_id(n: int) -> str:
    """The ``n``-th (0-based) bare build id; later ids sort after earlier ones."""
    return f"b-202610{10 + n:02d}T000000Z-{n + 1:08x}"


def fake_kb(tmp: Path, n: int, *, current_to: int | None = None) -> tuple[Path, list[str]]:
    kb = tmp / "kb" / "demo"
    ids = [build_id(i) for i in range(n)]
    for bid in ids:
        (kb / "builds" / bid).mkdir(parents=True)
    if current_to is not None:
        os.symlink(f"builds/{ids[current_to]}", kb / "current")
    return kb, ids


def row(seq: int, target: str, action: str, status: str = "done", prev: str | None = None) -> dict:
    return {"seq": seq, "target_build_id": target, "prev_build_id": prev, "action": action, "status": status,
            "at": "2026-10-25T00:00:00Z", "by": "test"}


def published_rows(seq: int, target: str, *, prev: str | None = None, statuses: dict | None = None, action: str = "switch"):
    """The rows of one publication: the pointer row, then one row per action (``statuses``: action -> status or
    ``None`` for no row)."""
    out = [row(seq, target, action, prev=prev)]
    for i, name in enumerate(ACTIONS, start=1):
        status = (statuses or {}).get(name, "done")
        if status is not None:
            out.append(row(seq + i, target, name, status))
    return out


def write_rows(kb: Path, rows_: Sequence[Mapping]) -> None:
    kb.mkdir(parents=True, exist_ok=True)
    with open(kb / "PUBLISH.log", "a", encoding="utf-8") as fh:
        for r in rows_:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def dead_pid() -> int:
    """A pid that belonged to a process which has exited and been reaped."""
    for _ in range(20):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        try:
            os.kill(proc.pid, 0)
        except ProcessLookupError:
            return proc.pid
        except PermissionError:  # reused by someone else's process: try again
            continue
    raise AssertionError("no dead pid found")


def lease(build_dir: Path, pid: int, nonce: str = "n") -> Path:
    d = build_dir / ".readers"
    d.mkdir(exist_ok=True)
    path = d / f"{pid}-{nonce}"
    path.write_text("2026-10-25T00:00:00Z\n", encoding="utf-8")
    return path


def trash(kb: Path) -> list[str]:
    root = kb / "builds" / "_trash"
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def live_builds(kb: Path) -> list[str]:
    root = kb / "builds"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name.startswith("b-")) if root.is_dir() else []


def copy_tree_state(root: Path) -> dict[str, bytes]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath, f)
            out[p.relative_to(root).as_posix()] = p.read_bytes() if not p.is_symlink() else os.readlink(p).encode()
    return out


@st.composite
def worlds(draw):
    """A set of builds with a publication history built *from* the intended pending set (the expectation is known
    by construction, not recomputed from the rows)."""
    n = draw(st.integers(1, 7))
    ids = [build_id(i) for i in range(n)]
    kinds = {b: draw(st.sampled_from(["unpublished", "settled", "retried", "skipped", "failed", "missing",
                                      "republished"])) for b in ids}
    order = draw(st.permutations(ids))
    rows: list[dict] = []
    last_prev: dict[str, str | None] = {}

    def add(target, action, status="done", prev=None):
        rows.append(row(len(rows) + 1, target, action, status, prev))

    for b in order:
        kind = kinds[b]
        if kind == "unpublished":
            continue
        if kind == "republished":  # an earlier publication, fully done, then the one that counts
            add(b, "switch", prev=draw(st.one_of(st.none(), st.sampled_from(ids))))
            for name in ACTIONS:
                add(b, name)
        prev = draw(st.one_of(st.none(), st.sampled_from(ids)))
        add(b, "switch", prev=prev)
        last_prev[b] = prev
        odd = draw(st.integers(0, len(ACTIONS) - 1))  # the action the kind is about
        for i, name in enumerate(ACTIONS):
            if kind == "retried" and i == odd:
                add(b, name, "failed")  # ... then done below
            if kind == "failed" and i == odd:
                add(b, name, "failed")
                continue
            if kind == "missing" and i == odd:
                continue
            add(b, name, "skipped_newer" if kind == "skipped" and i == odd else "done")
    pending = {b for b in ids if kinds[b] in ("failed", "missing")}
    return ids, rows, pending, last_prev


# --------------------------------------------------------------------------- the pipeline up to a stage


def pipeline_rig(tmp: Path, until: str, *, fixture: str = "fx-u3-mixed", extra_env: Mapping[str, str] | None = None):
    """A pipeline test bed (``fx-u3-mixed``: audited, the human's choices given) whose stages up to ``until`` are
    done (``"writeback"`` .. ``"check"``); ``publish`` is never run here. HOME and the registry are the test's own."""
    from _support import pipeline_fx as PF
    from _support import writeback_fx as W
    from klode_kit.audit import confirm_page as CP

    reg = tmp / "registry" / "registry.toml"
    rig = PF.Rig(tmp, fixture, extra_env={"HOME": str(B.home(tmp)), "KLODE_REGISTRY": str(reg),
                                          **(extra_env or {})})
    first = rig.run()
    assert first.returncode == 10, first.stderr.decode()
    adir = rig.batch_dir / "audit" / "a1"
    choices = W.write_choices(tmp / "choices.json",
                              CP.export_choices((adir / "confirm.html").read_bytes(), W.picks(adir)))
    res = rig.run("--choices", str(choices), "--stage", until)
    assert res.returncode == 0, res.stderr.decode()
    return rig


def pipeline_kb(rig) -> Path:
    return B.kb_dir(rig.project)
