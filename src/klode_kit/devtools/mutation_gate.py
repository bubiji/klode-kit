"""Mutation gate (WI-15c): ``python -m klode_kit.devtools.mutation_gate [--file REL ...] [--jobs N]``.

A manual, pre-release gate (config: ``mutationGateOn: ["manual"]``, ``requireMutation: false``; it is not part of
every commit). It mutates ONLY the files named by ``criticalPaths`` in ``.claude/tdd-guardian/config.json``, runs for
every mutant ONLY the tests that execute the mutated function, and writes

* ``work/mutation.json``  (schema ``klode-kit.mutation/1``: ``code_tree_sha``, ``score``, ``finished_at`` - exactly the
  fields ``release_check`` reads; threshold 0.80, age <= 7 days) - only for a COMPLETE run (every critical file
  scored); a ``--file`` / time-limited run never writes it, because it would not be a release score;
* ``.claude/tdd-guardian/mutation-<YYYYMMDD>.md``  (``-partial`` suffix for incomplete runs): the per-file table and
  every surviving mutant as ``file:line: old -> new``.

Engine. mutmut (PyPI, ``dev`` dependency group) supplies the mutation operators and the trampoline form: a mutated
copy of a module holds ALL mutants of a function side by side and the environment variable ``MUTANT_UNDER_TEST``
selects one (the module is imported once; no per-mutant source rewrite). mutmut's own runner is not used - it copies
the tests into ``mutants/`` and runs from there, which this repository's launcher, relative paths and 2700-test lanes
do not tolerate. Instead:

1. test map   one coverage run of the offline lanes (``unit or contract_replay``, never ``gate_cloud`` /
               ``model_integration`` / ``host_acceptance``; sockets disabled) with ``--cov-context=test`` records which
               test node ids execute which lines; per function that gives its tests (cache: ``work/mutation/testmap.json``,
               keyed by the file hashes and the tests directory fingerprint);
2. worktrees   ``--jobs`` private copies of the repository (``.git``, ``.venv`` (symlinked), ``_trash``, ``work`` left
               out) with the critical files replaced by their mutated form - so the launcher and every subprocess a
               test starts import the mutated module too;
3. baseline    the file's tests run once against the mutated-but-inactive copy; tests that fail there (they cannot
               tell a mutant from the original) are excluded and listed, never silently dropped;
4. mutants     per mutant ``pytest -x <that function's tests>`` with ``MUTANT_UNDER_TEST=<module>.<mutant>``; exit 1/2
               killed, 0 survived, 5 no test selected (untested code, counts as survived), timeout counted as killed,
               anything else is retried once and then counts as survived (never a free pass).

Score = (killed + timeout) / all mutants, per file and pooled over the files for the receipt.

Known limits: mutmut mutates inside functions and methods only (module-level constants are not mutated); a test that
reaches a mutated function only through a process that scrubs the environment will not see the mutant (the pytest
plugin pins it in-process as well).

Exit: 0 complete run, every file >= threshold; 20 otherwise (some file below, or the run is partial / a file could
not be scored); 2 usage; 40 the environment cannot run the engine.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import difflib
import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCHEMA = "klode-kit.mutation/1"
THRESHOLD = 0.80
DEFAULT_MARKERS = "unit or contract_replay"
NEVER_MARKERS = ("gate_cloud", "model_integration", "host_acceptance")
MAX_IDS = 150
CAUGHT = ("killed", "timeout")
STATUSES = ("killed", "timeout", "survived", "no_tests", "error")
FAIL_EXIT = 20
USAGE_EXIT = 2
ENV_EXIT = 40
CONFIG_REL = Path(".claude") / "tdd-guardian" / "config.json"
WORK_REL = Path("work") / "mutation"
SEP = "__mutmut_"
PLUGIN = '''"""Pins the active mutant in-process (the env var alone is lost when a test scrubs os.environ)."""
import os


def pytest_configure(config):
    name = os.environ.get("MUTANT_UNDER_TEST")
    if name:
        from mutmut.mutation.trampoline import set_mutant_under_test

        set_mutant_under_test(name)


def pytest_collection_modifyitems(config, items):
    # the test that killed the previous mutant of the same function runs first: with -x most mutants die at once
    first = os.environ.get("MUTGATE_FIRST")
    if first:
        items.sort(key=lambda item: item.nodeid != first)
'''
TRAMPOLINE_IMPORT = "from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict"
MUTANT_FILE = ".mutant_under_test"
SHIM = """
def _mutgate_install(_depth, _dotted):
    # tests often start subprocesses (the launcher, hooks) with a scrubbed environment: the active mutant is therefore
    # also read from a file in the repository copy, and the environment variable only wins when it is present.
    # A module run as ``python -m pkg.mod`` is ``__main__`` there; its mutants are addressed under that name.
    import mutmut.mutation.trampoline as _t
    from pathlib import Path as _P

    if not hasattr(_t, "_mutgate_alias"):
        _t._mutgate_alias = {}
        _orig = _t.get_mutant_under_test

        def _get():
            value = _orig()
            if not value:
                try:
                    value = (_P(__file__).resolve().parents[_depth] / "%s").read_text().strip()
                except OSError:
                    return ""
            module, _, rest = value.rpartition(".")
            return _t._mutgate_alias.get(module, module) + "." + rest if module else value

        _t.get_mutant_under_test = _get
    if __name__ == "__main__":
        _t._mutgate_alias[_dotted] = "__main__"


_mutgate_install(0, "")
""" % (MUTANT_FILE,)
COPY_SKIP = {".git", ".venv", "_trash", "work", "__pycache__", ".pytest_cache", ".hypothesis", ".mypy_cache",
             "mutants", ".DS_Store", ".coverage"}


# --- pure helpers ---------------------------------------------------------------------------------------------

def score(killed: int, survived: int) -> float:
    """killed / (killed + survived); no mutants at all has no score."""
    total = killed + survived
    if total <= 0:
        raise ValueError("no mutants: a score of nothing is not a score")
    return killed / total


def classify(returncode: int | None, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if returncode == 0:
        return "survived"
    if returncode in (1, 2):
        return "killed"
    if returncode == 5:
        return "no_tests"
    return "error"


def summarize(results: list[dict]) -> dict:
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    total = len(results)
    caught = counts["killed"] + counts["timeout"]
    return {**counts, "mutants": total, "score": (caught / total) if total else None}


def module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def critical_files(root: Path) -> list[str]:
    cfg = json.loads((Path(root) / CONFIG_REL).read_text(encoding="utf-8"))
    out: set[str] = set()
    for entry in cfg.get("criticalPaths", []):
        glob = entry["glob"]
        found = [p for p in Path(root).glob(glob) if p.is_file() and p.suffix == ".py"]
        if not found:
            raise ValueError(f"criticalPaths glob matches no python file: {glob}")
        out.update(p.relative_to(root).as_posix() for p in found)
    if not out:
        raise ValueError("config has no criticalPaths")
    return sorted(out)


def _mangle(name: str, class_name: str | None) -> str:
    from mutmut.mutation.trampoline_templates import mangle_function_name

    return mangle_function_name(name=name, class_name=class_name)


def function_ranges(code: str) -> dict[str, tuple[int, int]]:
    """mutmut function key (``x_f`` / ``xǁClassǁm``) -> (def line, last line), 1-based."""
    tree = ast.parse(code)
    out: dict[str, tuple[int, int]] = {}

    def visit(body: list[ast.stmt], cls: str | None) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(_mangle(node.name, cls), (node.lineno, node.end_lineno or node.lineno))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, node.name if cls is None else f"{cls}.{node.name}")

    visit(tree.body, None)
    return out


@dataclass(frozen=True)
class Mutant:
    name: str
    func: str
    line: int
    old: str
    new: str


def generate(rel: str, code: str, *, root: Path | None = None) -> tuple[str, list[Mutant]]:
    """The mutated (trampolined) source and one Mutant per mutation, located in the ORIGINAL file."""
    from mutmut.mutation.file_mutation import mutate_file_contents

    with contextlib.chdir(root) if root else contextlib.nullcontext():
        mf = mutate_file_contents(rel, code)
    lines = mf.code.splitlines()
    spans = mf.line_span_by_function_name
    ranges = function_ranges(code)

    def body(name: str) -> list[str]:
        sp = spans[name]
        return lines[sp.start - 1:sp.end]

    mutants: list[Mutant] = []
    for name in mf.mutant_names:
        func = name.rpartition(SEP)[0]
        orig, mut = body(func + SEP + "orig"), body(name)
        # the generated copies differ in their def line (the names); neutralise that so the diff shows the mutation only
        orig = [ln.replace(func + SEP + "orig", "f") for ln in orig]
        mut = [ln.replace(name, "f") for ln in mut]
        def_idx = next((i for i, ln in enumerate(orig) if ln.lstrip().startswith(("def ", "async def "))), 0)
        start = ranges.get(func, (0, 0))[0]
        line, old, new = start, "", ""
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, orig, mut, autojunk=False).get_opcodes():
            if tag != "equal":
                line = (start + (i1 - def_idx)) if start else 0
                old, new = "\n".join(s.strip() for s in orig[i1:i2]), "\n".join(s.strip() for s in mut[j1:j2])
                break
        mutants.append(Mutant(name=name, func=func, line=line, old=old, new=new))
    return mf.code, mutants


def tests_for(entry: dict, func: str | None) -> list[str]:
    """The tests that execute ``func`` (coverage contexts) plus the test files that name the module (``static``: code a
    test reaches only through a subprocess leaves no in-process coverage). Unknown function -> the file's union."""
    static = list(entry.get("static", []))
    if func is None:
        return list(entry["all"])
    if func in entry["funcs"]:
        covered = entry["funcs"][func]
    elif "known" in entry and func not in entry["known"]:
        return list(entry["all"])           # a name the AST walk did not produce: never silently "no tests"
    else:
        covered = []
    whole = set(static)
    return [t for t in covered if t.split("::")[0] not in whole] + static


# --- environment / processes -----------------------------------------------------------------------------------

def _python(root: Path) -> str:
    venv = Path(root) / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("COV_CORE") or k.startswith("COVERAGE") or k in ("MUTANT_UNDER_TEST", "PYTEST_ADDOPTS",
                                                                                   "PYTEST_CURRENT_TEST", "KLODE_KIT_REAL",
                                                                                   "KLODE_KIT_GATE_ACK"))}
    env.update(extra or {})
    return env


def _exec(cmd: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int | None, str, float, bool]:
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out, time.monotonic() - t0, False
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return None, out or "", time.monotonic() - t0, True


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"


def _to_trash(root: Path, path: Path) -> None:
    """Cleanup is a move into <root>/_trash, never a delete."""
    if not path.exists():
        return
    dest = Path(root) / "_trash" / f"{path.name}-{_stamp()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))


def _marker_expr(markers: str) -> str:
    if not markers.strip():
        return ""
    return f"({markers}) and " + " and ".join(f"not {m}" for m in NEVER_MARKERS)


def _socket_args(markers: str) -> list[str]:
    return [] if "cli_integration" in markers else ["--disable-socket"]


# --- test map ------------------------------------------------------------------------------------------------

def _tests_fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    tests = Path(root) / "tests"
    for p in sorted(tests.rglob("*")) if tests.is_dir() else []:
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".json", ".txt", ".toml", ".md", ".yaml", ".yml"):
            st = p.stat()
            h.update(f"{p.relative_to(root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _node_of(ctx: str) -> str | None:
    base, _, phase = ctx.rpartition("|")
    if phase in ("run", "setup", "teardown"):
        ctx = base
    return ctx if "::" in ctx else None


STATIC_DIRS = {"unit": False, "contract": False, "replay": False, "cli": True}   # dir -> needs cli_integration in markers


def static_tests(root: Path, rel: str, markers: str) -> list[str]:
    """Test files (of the lanes the markers select) that name the module: its dotted path, ``from pkg import name``,
    or - when the basename is distinctive (contains ``_``) - the basename / its kebab form."""
    import re

    mod = module_name(rel)
    pkg, _, name = mod.rpartition(".")
    pats = [re.escape(mod), rf"from\s+{re.escape(pkg)}\s+import\s[^\n]*\b{re.escape(name)}\b" if pkg else re.escape(name)]
    if "_" in name:
        pats += [rf"\b{re.escape(name)}\b", rf"\b{re.escape(name.replace('_', '-'))}\b"]
    rx = re.compile("|".join(pats))
    out = []
    tests = Path(root) / "tests"
    if not (tests / "unit").is_dir():
        return []                                        # no lane-directory layout: coverage contexts alone decide
    for p in sorted(tests.rglob("test_*.py")):
        sub = p.relative_to(tests).parts[0] if len(p.relative_to(tests).parts) > 1 else ""
        if sub not in STATIC_DIRS or (STATIC_DIRS[sub] and "cli_integration" not in markers):
            continue                                     # host / model / docs ... never run in this gate
        if rx.search(p.read_text(encoding="utf-8", errors="replace")):
            out.append(p.relative_to(root).as_posix())
    return out


def _with_static(root: Path, rel: str, entry: dict, markers: str) -> dict:
    static = static_tests(root, rel, markers)
    whole = set(static)
    covered = sorted({t for ids in entry["funcs"].values() for t in ids if t.split("::")[0] not in whole})
    return {**entry, "static": static, "all": covered + static}


def build_test_map(root: Path, files: list[str], markers: str, *, refresh: bool = False, log: Callable[[str], None] = lambda s: None) -> dict:
    root = Path(root)
    cache_path = root / WORK_REL / "testmap.json"
    fp = _tests_fingerprint(root)
    srcs = {rel: _sha((root / rel).read_text(encoding="utf-8")) for rel in files}
    cache: dict = {}
    if cache_path.is_file() and not refresh:
        with contextlib.suppress(OSError, ValueError):
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if (cache.get("tests_fp") == fp and cache.get("markers") == markers
            and all(cache.get("files", {}).get(rel, {}).get("src_sha") == srcs[rel] for rel in files)):
        log("test map: cache hit")
        return {rel: _with_static(root, rel, cache["files"][rel], markers) for rel in files}

    import coverage

    cov_dir = root / WORK_REL / "cov"
    _to_trash(root, cov_dir)
    cov_dir.mkdir(parents=True, exist_ok=True)
    cov_file = cov_dir / ".coverage"
    # own rc file: the project's `patch = subprocess` would write one data file per spawned process (thousands) and
    # those carry no test context anyway - only the in-process contexts are used
    rc_file = cov_dir / "coveragerc"
    rc_file.write_text("[run]\nbranch = false\n", encoding="utf-8")
    packages = sorted({module_name(rel).split(".")[0] for rel in files})
    cmd = [_python(root), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *_socket_args(markers),
           *[f"--cov={p}" for p in packages], "--cov-context=test", "--cov-report=",
           f"--cov-config={rc_file}"]
    expr = _marker_expr(markers)
    if expr:
        cmd += ["-m", expr]
    log("test map: coverage run of the offline lanes (this is the slow, one-time step) ...")
    rc, out, secs, timed = _exec(cmd, root, _env({"COVERAGE_FILE": str(cov_file)}), 3600)
    if timed or rc not in (0, 1):
        raise RuntimeError(f"test-map coverage run failed (exit {rc}, timeout={timed}):\n{out[-1500:]}")
    log(f"test map: coverage run exit {rc} in {secs:.0f}s")
    cov = coverage.Coverage(data_file=str(cov_file))
    with contextlib.suppress(coverage.exceptions.NoDataError):
        cov.combine(strict=False, keep=True)
    data = coverage.CoverageData(basename=str(cov_file))
    data.read()
    measured = {os.path.realpath(p): p for p in data.measured_files()}
    entries: dict[str, dict] = dict(cache.get("files", {})) if cache.get("tests_fp") == fp and cache.get("markers") == markers else {}
    for rel in files:
        code = (root / rel).read_text(encoding="utf-8")
        ranges = function_ranges(code)
        path = measured.get(os.path.realpath(root / rel))
        by_line = data.contexts_by_lineno(path) if path else {}
        funcs: dict[str, list[str]] = {}
        for key, (a, b) in ranges.items():
            ids: set[str] = set()
            for ln in range(a + 1 if b > a else a, b + 1):
                for ctx in by_line.get(ln, ()):
                    nid = _node_of(ctx)
                    if nid:
                        ids.add(nid)
            funcs[key] = sorted(ids)
        entries[rel] = {"src_sha": srcs[rel], "funcs": funcs, "known": sorted(ranges)}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"tests_fp": fp, "markers": markers, "files": entries}, indent=1), encoding="utf-8")
    return {rel: _with_static(root, rel, entries[rel], markers) for rel in files}


# --- worktrees ----------------------------------------------------------------------------------------------

def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in COPY_SKIP or n.endswith(".pyc")}


def _with_shim(rel: str, code: str) -> str:
    depth = len(Path(rel).parts) - 1                    # parents[depth] of the module file is the repository root
    shim = SHIM.replace('_mutgate_install(0, "")', f"_mutgate_install({depth}, {module_name(rel)!r})")
    if TRAMPOLINE_IMPORT not in code:
        return code
    return code.replace(TRAMPOLINE_IMPORT, TRAMPOLINE_IMPORT + "\n" + shim, 1)


def make_worktrees(root: Path, n: int, mutated: dict[str, str]) -> list[Path]:
    root = Path(root)
    base = root / WORK_REL / "wt"
    _to_trash(root, base)
    base.mkdir(parents=True, exist_ok=True)
    first = base / "0"
    shutil.copytree(root, first, ignore=_ignore, symlinks=True)
    trees = [first]
    for i in range(1, n):
        dst = base / str(i)
        shutil.copytree(first, dst, symlinks=True)
        trees.append(dst)
    for wt in trees:
        (wt / "work").mkdir(exist_ok=True)
        if (root / ".venv").exists() and not (wt / ".venv").exists():
            (wt / ".venv").symlink_to(root / ".venv")
        for rel, code in mutated.items():
            (wt / rel).write_text(_with_shim(rel, code), encoding="utf-8")
        (wt / MUTANT_FILE).write_text("", encoding="utf-8")
        (wt / "_mutgate_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return trees


# --- running ------------------------------------------------------------------------------------------------

def _select(ids: list[str], excluded: set[str]) -> tuple[list[str], list[str]]:
    """(pytest args, deselect args) for a test-id list; very long lists run by file."""
    ids = [i for i in ids if i not in excluded]
    if len(ids) > MAX_IDS:
        ids = sorted({i.split("::")[0] for i in ids})
    whole = {i for i in ids if "::" not in i}
    return ids, [a for e in sorted(excluded) if e.split("::")[0] in whole for a in ("--deselect", e)]


def _pytest_cmd(root: Path, wt_args: list[str], markers: str, *, fail_fast: bool, extra: list[str] | None = None) -> list[str]:
    return [_python(root), "-m", "pytest", *(["-x"] if fail_fast else ["-rfE"]), "-q", "--no-header", "-p", "no:cacheprovider",
            "-p", "_mutgate_plugin", *_socket_args(markers), *(extra or []), *wt_args]


def _failed_ids(out: str) -> list[str]:
    ids = []
    for ln in out.splitlines():
        if ln.startswith(("FAILED ", "ERROR ")) and "::" in ln:
            ids.append(ln.split(" ", 1)[1].split(" - ", 1)[0].strip())
    return ids


def _score_file(root: Path, rel: str, code: str, mutants: list[Mutant], entry: dict, trees: queue.Queue, markers: str,
                jobs: int, log: Callable[[str], None]) -> dict:
    t_wall = time.monotonic()
    module = module_name(rel)
    selected = sorted(entry["all"])
    excluded: set[str] = set()
    result: dict = {"status": "scored", "selected_tests": selected, "excluded_tests": []}
    if not selected:
        rows = [{"name": m.name, "func": m.func, "line": m.line, "old": m.old, "new": m.new, "status": "no_tests",
                 "tests": 0, "seconds": 0.0} for m in mutants]
        return {**result, **summarize(rows), "mutants": rows, "seconds": 0.0, "wall_seconds": 0.0}

    wt = trees.get()
    try:
        args, deselect = _select(selected, set())
        rc, out, base_secs, timed = _exec(_pytest_cmd(root, args, markers, fail_fast=False), wt, _env({"PYTHONPATH": str(wt)}), 3600)
        if rc not in (0,) or timed:
            failed = set(_failed_ids(out))
            if timed or not failed:
                return {**result, "status": "baseline_failed", "mutants": [], "baseline_tail": out[-1500:],
                        "seconds": base_secs, "wall_seconds": time.monotonic() - t_wall}
            excluded = failed
            args, deselect = _select(selected, excluded)
            rc, out, base_secs2, timed = _exec(_pytest_cmd(root, args, markers, fail_fast=False, extra=deselect), wt,
                                               _env({"PYTHONPATH": str(wt)}), 3600)
            base_secs += base_secs2
            if rc not in (0, 5) or timed:
                return {**result, "status": "baseline_failed", "excluded_tests": sorted(excluded), "mutants": [],
                        "baseline_tail": out[-1500:], "seconds": base_secs, "wall_seconds": time.monotonic() - t_wall}
        log(f"{rel}: baseline ok ({len(selected)} tests, {len(excluded)} excluded, {base_secs:.0f}s); {len(mutants)} mutants")
    finally:
        trees.put(wt)

    timeout = min(900.0, max(30.0, base_secs * 3 + 20))
    done = 0
    lock = threading.Lock()
    killers: dict[str, str] = {}

    def one(m: Mutant) -> dict:
        nonlocal done
        ids, deselect = _select(tests_for(entry, m.func), excluded)
        base = {"name": m.name, "func": m.func, "line": m.line, "old": m.old, "new": m.new, "tests": len(ids)}
        if not ids:
            return {**base, "status": "no_tests", "seconds": 0.0}
        tree = trees.get()
        marker = tree / MUTANT_FILE
        try:
            status, secs = "error", 0.0
            marker.write_text(f"{module}.{m.name}", encoding="utf-8")
            for _attempt in range(2):
                extra_env = {"PYTHONPATH": str(tree), "MUTANT_UNDER_TEST": f"{module}.{m.name}"}
                if m.func in killers:
                    extra_env["MUTGATE_FIRST"] = killers[m.func]
                rc, out, secs, timed = _exec(_pytest_cmd(root, ids, markers, fail_fast=True, extra=deselect), tree,
                                             _env(extra_env), timeout)
                status = classify(rc, timed)
                if status == "killed" and _failed_ids(out):
                    killers[m.func] = _failed_ids(out)[0]
                if status != "error":
                    break
        finally:
            marker.write_text("", encoding="utf-8")
            trees.put(tree)
        with lock:
            done += 1
            if done % 100 == 0:
                log(f"{rel}: {done}/{len(mutants)}")
        return {**base, "status": status, "seconds": round(secs, 2)}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rows = list(pool.map(one, mutants))
    return {**result, "excluded_tests": sorted(excluded), **summarize(rows), "mutants": rows,
            "seconds": round(sum(r["seconds"] for r in rows) + base_secs, 1), "wall_seconds": round(time.monotonic() - t_wall, 1)}


def run(root: Path, *, files: list[str] | None = None, jobs: int = 1, markers: str = DEFAULT_MARKERS,
        refresh_map: bool = False, max_seconds: float | None = None, resume: bool = False, sha: str | None = None,
        now: dt.datetime | None = None, log: Callable[[str], None] = lambda s: None) -> dict:
    root = Path(root)
    everything = critical_files(root)
    chosen = files or everything
    unknown = [f for f in chosen if f not in everything]
    if unknown:
        raise ValueError("not a criticalPaths file: " + ", ".join(unknown))
    started = now or dt.datetime.now(dt.timezone.utc)
    t0 = time.monotonic()
    manifest = None
    if sha is None:
        from klode_kit.devtools import release_manifest as manifest

        sha = manifest.release_sha(root)
    codes = {rel: (root / rel).read_text(encoding="utf-8") for rel in chosen}
    tmap = build_test_map(root, chosen, markers, refresh=refresh_map, log=log)
    generated = {rel: generate(rel, codes[rel], root=root) for rel in chosen}
    trees = make_worktrees(root, max(1, jobs), {rel: generated[rel][0] for rel in chosen})
    pool: queue.Queue = queue.Queue()
    for t in trees:
        pool.put(t)
    results: dict[str, dict] = {}
    chosen = sorted(chosen, key=lambda rel: (len(generated[rel][1]), rel))      # cheap files first: a time limit loses the big ones
    unrun: list[str] = [f for f in everything if f not in chosen]
    saved = root / WORK_REL / "files"
    saved.mkdir(parents=True, exist_ok=True)
    for rel in chosen:
        slot = saved / (rel.replace("/", "__") + ".json")
        key = {"src_sha": _sha(codes[rel]), "markers": markers, "tests_fp": _tests_fingerprint(root),
               "map_sha": _sha(json.dumps(tmap[rel], sort_keys=True))}
        if resume and slot.is_file():
            with contextlib.suppress(OSError, ValueError):
                prev = json.loads(slot.read_text(encoding="utf-8"))
                if prev.get("key") == key:
                    results[rel] = prev["result"]
                    log(f"{rel}: resumed from {slot.name}")
                    continue
        if max_seconds is not None and time.monotonic() - t0 > max_seconds:
            unrun.append(rel)                 # a finished (resumed) file is never "unrun"; only work not yet started is
            continue
        mutated_code, mutants = generated[rel]
        if not mutants:
            results[rel] = {"status": "no_mutants", "mutants": [], "excluded_tests": [], "seconds": 0.0, "wall_seconds": 0.0}
            continue
        results[rel] = _score_file(root, rel, mutated_code, mutants, tmap[rel], pool, markers, jobs, log)
        r = results[rel]
        log(f"{rel}: {r['status']} score={r.get('score')} killed={r.get('killed')} survived={r.get('survived')} "
            f"no_tests={r.get('no_tests')} wall={r['wall_seconds']}s")
        slot.write_text(json.dumps({"key": key, "result": r}), encoding="utf-8")
    finished = dt.datetime.now(dt.timezone.utc) if now is None else now
    report = {"schema": "klode-kit.mutation.report/1", "files": results, "unrun": sorted(unrun),
              "partial": bool(unrun), "release_sha": sha, "started_at": started.isoformat(timespec="seconds"),
              "finished_at": finished.isoformat(timespec="seconds"), "markers": markers, "threshold": THRESHOLD,
              "jobs": jobs, "engine": _engine_version()}
    if manifest is not None and manifest.release_sha(root) != sha:
        report["tree_changed"] = True
    return report


def report_from_saved(root: Path, *, sha: str | None = None, now: dt.datetime | None = None) -> dict:
    """Report over the per-file results already on disk (``work/mutation/files``), running nothing.

    The saved results are NOT checked against the current sources / tests, so this report can never become the release
    receipt (``from_saved`` makes it incomplete by definition); it is for reading progress and writing the survivor list.
    """
    root = Path(root)
    if sha is None:
        from klode_kit.devtools import release_manifest

        sha = release_manifest.release_sha(root)
    results, unrun = {}, []
    for rel in critical_files(root):
        slot = root / WORK_REL / "files" / (rel.replace("/", "__") + ".json")
        try:
            results[rel] = json.loads(slot.read_text(encoding="utf-8"))["result"]
        except (OSError, ValueError, KeyError):
            unrun.append(rel)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    return {"schema": "klode-kit.mutation.report/1", "files": results, "unrun": unrun, "partial": True, "from_saved": True,
            "release_sha": sha, "started_at": stamp, "finished_at": stamp, "markers": DEFAULT_MARKERS, "threshold": THRESHOLD,
            "jobs": 0, "engine": _engine_version()}


def _engine_version() -> str:
    try:
        from importlib.metadata import version

        return f"mutmut {version('mutmut')}"
    except Exception:  # noqa: BLE001 - metadata is decoration, never a failure
        return "mutmut"


# --- outputs ------------------------------------------------------------------------------------------------

def _pooled(report: dict) -> dict:
    rows = [m for r in report["files"].values() for m in r.get("mutants", [])]
    return summarize(rows)


def survivors(report: dict) -> list[str]:
    out = []
    for rel in sorted(report["files"]):
        for m in report["files"][rel].get("mutants", []):
            if m["status"] in ("survived", "no_tests", "error"):
                out.append(f"{rel}:{m['line']}: {m['old']} -> {m['new']}")
    return out


def _complete(report: dict) -> bool:
    return (bool(report["files"]) and not report["unrun"] and not report.get("tree_changed") and not report.get("from_saved")
            and all(r["status"] == "scored" for r in report["files"].values()))


def write_receipt(root: Path, report: dict, *, now: dt.datetime | None = None) -> Path | None:
    """``work/mutation.json`` for release_check - only when the run covered every critical file and scored each."""
    if not _complete(report):
        return None
    total = _pooled(report)
    finished = now.isoformat(timespec="seconds") if now else report["finished_at"]
    doc = {
        "schema": SCHEMA,
        "code_tree_sha": report["release_sha"],
        "score": total["score"],
        "finished_at": finished,
        "threshold": report["threshold"],
        "markers": report["markers"],
        "engine": report.get("engine", "mutmut"),
        "totals": {k: total[k] for k in ("mutants", *STATUSES)},
        "files": {rel: {k: r[k] for k in (*STATUSES, "score", "seconds", "wall_seconds")} | {
            "mutants": len(r["mutants"]), "status": r["status"], "excluded_tests": len(r["excluded_tests"])}
            for rel, r in sorted(report["files"].items())},
    }
    target = Path(root) / "work" / "mutation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".mutation-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, target)
    return target


def render_markdown(report: dict) -> str:
    total = _pooled(report)
    lines = [
        f"# Mutation gate {report['finished_at'][:10]}{' (PARTIAL)' if not _complete(report) else ''}",
        "",
        f"- engine: {report.get('engine', 'mutmut')}; markers: `{report['markers']}`; jobs: {report['jobs']}; release sha: `{report['release_sha'][:12]}`",
        f"- threshold {report['threshold']:.2f}; pooled score "
        + (f"{total['score']:.4f}" if total["score"] is not None else "n/a")
        + f" over {total['mutants']} mutants ({total['killed']} killed, {total['timeout']} timeout, {total['survived']} survived, "
        f"{total['no_tests']} untested, {total['error']} error)",
    ]
    if report["unrun"]:
        lines.append("- NOT RUN: " + ", ".join(report["unrun"]))
    lines += ["", "| file | mutants | killed | timeout | survived | untested | error | score | status | wall s |",
              "|------|---------|--------|---------|----------|----------|-------|-------|--------|--------|"]
    for rel in sorted(report["files"]):
        r = report["files"][rel]
        sc = r.get("score")
        shown = f"{sc:.2f}" if sc is not None else "n/a"
        mark = "" if sc is None or sc >= report["threshold"] else " **BELOW**"
        lines.append(f"| {rel} | {len(r.get('mutants', []))} | {r.get('killed', 0)} | "
                     f"{r.get('timeout', 0)} | {r.get('survived', 0)} | {r.get('no_tests', 0)} | {r.get('error', 0)} | "
                     f"{shown}{mark} | {r['status']} | {r.get('wall_seconds', 0)} |")
    lines += ["", "## Surviving mutants (file:line: original -> mutant)", ""]
    for rel in sorted(report["files"]):
        r = report["files"][rel]
        bad = [m for m in r.get("mutants", []) if m["status"] in ("survived", "no_tests", "error")]
        if r.get("excluded_tests"):
            lines += [f"### {rel} - baseline-excluded tests", ""] + [f"- `{t}`" for t in r["excluded_tests"]] + [""]
        if not bad:
            continue
        lines += [f"### {rel} ({len(bad)})", ""]
        for m in sorted(bad, key=lambda m: (m["line"], m["name"])):
            old = m["old"].replace("\n", " ⏎ ")
            new = m["new"].replace("\n", " ⏎ ")
            tag = "" if m["status"] == "survived" else f" [{m['status']}]"
            lines.append(f"- {rel}:{m['line']}: `{old}` -> `{new}` ({m['func']}, {m['tests']} tests ran){tag}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _write_md(root: Path, report: dict) -> Path:
    day = report["finished_at"][:10].replace("-", "")
    path = Path(root) / ".claude" / "tdd-guardian" / f"mutation-{day}{'' if _complete(report) else '-partial'}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
    return path


def _compact(report: dict) -> dict:
    out = dict(report)
    out["files"] = {rel: {k: v for k, v in r.items() if k not in ("mutants", "selected_tests", "excluded_tests", "baseline_tail")}
                    | {"excluded_tests": len(r.get("excluded_tests", []))} for rel, r in report["files"].items()}
    out["pooled"] = _pooled(report)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev mutation-gate", description="mutation gate over the criticalPaths files")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--file", action="append", default=[], help="only this critical file (repeatable); never writes work/mutation.json")
    parser.add_argument("--jobs", type=int, default=4, help="parallel mutant runs (one repository copy each)")
    parser.add_argument("--markers", default=DEFAULT_MARKERS, help="pytest -m expression for the tests used (empty = all)")
    parser.add_argument("--refresh-map", action="store_true", help="rebuild the per-function test map")
    parser.add_argument("--max-minutes", type=float, default=None, help="stop starting new files after this long (run is then partial)")
    parser.add_argument("--resume", action="store_true", help="reuse finished per-file results whose inputs did not change")
    parser.add_argument("--from-saved", action="store_true",
                        help="run nothing: report (markdown + json) over the per-file results on disk; never a release receipt")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    root = args.root
    if not root.is_dir():
        print(f"mutation_gate: not a directory: {root}", file=sys.stderr)
        return USAGE_EXIT
    if args.jobs < 1:
        print("mutation_gate: --jobs must be >= 1", file=sys.stderr)
        return USAGE_EXIT
    try:
        everything = critical_files(root)
    except (OSError, ValueError) as exc:
        print(f"mutation_gate: {exc}", file=sys.stderr)
        return USAGE_EXIT
    bad = [f for f in args.file if f not in everything]
    if bad:
        print("mutation_gate: not a criticalPaths file: " + ", ".join(bad), file=sys.stderr)
        return USAGE_EXIT

    def log(msg: str) -> None:
        print(f"mutation_gate: {msg}", file=sys.stderr, flush=True)

    if args.from_saved:
        report = report_from_saved(root)
        md = _write_md(root, report)
        sys.stdout.write(json.dumps(_compact(report), ensure_ascii=False, indent=2) + "\n")
        print(f"mutation_gate: report {md} (from saved results; no release receipt)", file=sys.stderr)
        return FAIL_EXIT
    try:
        report = run(root, files=args.file or None, jobs=args.jobs, markers=args.markers, refresh_map=args.refresh_map,
                     max_seconds=args.max_minutes * 60 if args.max_minutes else None, resume=args.resume, log=log)
    except (RuntimeError, OSError) as exc:
        print(f"mutation_gate: environment cannot run the engine: {exc}", file=sys.stderr)
        return ENV_EXIT
    md = _write_md(root, report)
    receipt = write_receipt(root, report)
    sys.stdout.write(json.dumps(_compact(report), ensure_ascii=False, indent=2) + "\n")
    below = [rel for rel, r in report["files"].items() if r.get("score") is None or r["score"] < THRESHOLD]
    log(f"report {md}" + (f"; receipt {receipt}" if receipt else "; no release receipt (run is partial or a file was not scored)"))
    for rel in below:
        log(f"BELOW {THRESHOLD:.2f}: {rel} ({report['files'][rel].get('status')}, score {report['files'][rel].get('score')})")
    return 0 if receipt and not below else FAIL_EXIT


if __name__ == "__main__":
    sys.exit(main())
