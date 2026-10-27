"""Lane runner: ``python -m klode_kit.devtools.run_lane <lane>`` (plan section 0.1).

Wraps ``<python> -m pytest -m <lane>`` (V1: never a console script), writes
``<root>/work/lanes/<lane>.json`` atomically and treats a run that discovers 0 tests as FAIL.

Bootstrap exception: a lane that has NEVER discovered a test only warns (loudly, exit 0,
receipt ``bootstrap: true``, status ``BOOTSTRAP``). The first run that discovers >= 1 test
records the lane in ``<root>/tests/lane_history.json`` (committed, so CI sees it); from then
on the exception is gone for good.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

LANES = ("unit", "contract_replay", "cli_integration", "model_integration", "host_acceptance")
OFFLINE = {"unit", "contract_replay"}
HISTORY_REL = Path("tests") / "lane_history.json"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def release_sha(root: Path) -> str:
    """HEAD commit read from .git without spawning git; 'unversioned' outside a repository."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        ref_file = git / ref
        if ref_file.is_file():
            return ref_file.read_text().strip()
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
        return "unborn"
    except OSError:
        return "unversioned"


def env_fingerprint(root: Path) -> dict:
    from klode_kit.devtools import release_manifest

    return {
        "release_sha": release_sha(root),
        # WI-15b: content digest of the run files (R3-08); release_check compares it with the current tree, so a
        # receipt dies with any change to any run file - including a single template - not only with a new commit.
        "code_tree_sha": release_manifest.release_sha(root),
        "repo_root": os.path.realpath(root),
        "python": os.path.realpath(sys.executable),
        "python_version": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine()}",
    }


@contextlib.contextmanager
def _locked(lock: Path):
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read_history(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("lanes", {}) if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise


def lane_had_tests(root: Path, lane: str) -> bool:
    return bool(_read_history(root / HISTORY_REL).get(lane, {}).get("ever_had_tests"))


def mark_lane_had_tests(root: Path, lane: str) -> None:
    path = root / HISTORY_REL
    with _locked(root / "work" / ".lane_history.lock"):
        lanes = _read_history(path)
        if lanes.get(lane, {}).get("ever_had_tests"):
            return
        lanes[lane] = {"ever_had_tests": True, "first_seen": _now()}
        _atomic_write_json(path, {"lanes": dict(sorted(lanes.items()))})


def pytest_command(lane: str, root: Path, junit: str, cov: bool, extra: list[str]) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", "-m", lane, "--rootdir", str(root), "-p", "no:cacheprovider", "--junitxml", junit]
    if lane in OFFLINE:
        cmd.append("--disable-socket")
    if cov:
        cmd += ["--cov=klode_kit", "--cov-branch", "--cov-append", "--cov-report="]
    tests = root / "tests"
    cmd.append(str(tests if tests.is_dir() else root))
    return cmd + extra


def _junit_counts(path: str) -> tuple[int, int, int]:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return 0, 0, 0
    suites = [tree.getroot()] if tree.getroot().tag == "testsuite" else list(tree.getroot())
    total = failed = skipped = 0
    for s in suites:
        total += int(s.get("tests", 0))
        failed += int(s.get("failures", 0)) + int(s.get("errors", 0))
        skipped += int(s.get("skipped", 0))
    return total, failed, skipped


def run(lane: str, root: Path, *, cov: bool = True, tags: list[str] | None = None, extra: list[str] | None = None) -> int:
    root = root.resolve()
    started = _now()
    with tempfile.TemporaryDirectory(prefix="kk-lane-") as tmp:
        junit = os.path.join(tmp, "junit.xml")
        cmd = pytest_command(lane, root, junit, cov, list(extra or []))
        proc = subprocess.run(cmd, cwd=str(root))
        total, failed, skipped = _junit_counts(junit)

    bootstrap = False
    if total == 0:
        print(f"{lane}: 0 tests discovered")
        if lane_had_tests(root, lane):
            status, code = "FAIL", 1
            print(f"{lane}: FAIL - this lane has had tests before; a 0-test run is a failure, not a pass")
        else:
            status, code, bootstrap = "BOOTSTRAP", 0, True
            print(
                f"WARNING: lane '{lane}' is in bootstrap (it has never had a test): 0 tests discovered is "
                "reported, not blocked. The exception ends permanently at its first test.",
                file=sys.stderr,
            )
    else:
        mark_lane_had_tests(root, lane)
        status, code = ("PASS", 0) if proc.returncode == 0 else ("FAIL", 1)

    receipt = {
        "schema_version": 1,
        "lane": lane,
        "status": status,
        "bootstrap": bootstrap,
        "tests_discovered": total,
        "tests_failed": failed,
        "tests_skipped": skipped,
        "pytest_exit_code": proc.returncode,
        "started_at": started,
        "finished_at": _now(),
        "command": cmd,
        "env_fingerprint": env_fingerprint(root),
        "tags": sorted(set(tags or [])),
    }
    _atomic_write_json(root / "work" / "lanes" / f"{lane}.json", receipt)
    print(f"{lane}: {status} ({total} discovered, {failed} failed, {skipped} skipped)")
    return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1 :]
    parser = argparse.ArgumentParser(prog="klode-kit dev lane", description="Run one test lane and write its receipt.")
    parser.add_argument("lane", choices=[*LANES, *(lane.replace("_", "-") for lane in LANES)])
    parser.add_argument("--root", type=Path, default=None, help="repository root (default: this checkout)")
    parser.add_argument("--no-cov", action="store_true", help="do not collect coverage")
    parser.add_argument("--not-release", action="store_true", help="tag the receipt not-release (CI)")
    args = parser.parse_args(argv)
    lane = args.lane.replace("-", "_")
    tags = ["not-release"] if args.not_release else []
    return run(lane, args.root or repo_root(), cov=not args.no_cov, tags=tags, extra=extra)


if __name__ == "__main__":
    sys.exit(main())
