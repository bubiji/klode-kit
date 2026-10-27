"""Release gate (WI-15b): ``python -m klode_kit.devtools.release_check [--root DIR]``.

A checkout is releasable only when ALL of these hold; every unmet item is reported (the check never stops at the
first one, and nothing is assumed):

``lane:<name>`` (five: unit, contract_replay, cli_integration, model_integration, host_acceptance)
    ``work/lanes/<name>.json`` exists, is a valid ``lane_receipt``, ``status`` PASS (not FAIL / BOOTSTRAP), no failed
    test, not tagged ``not-release`` (CI receipts), ``env_fingerprint.code_tree_sha`` equals the current release sha
    (``release_manifest``: any change to any run file - a single template included - invalidates it), made in this
    checkout (``repo_root`` realpath, venv addendum V6) by this interpreter (``python`` realpath), finished at most
    7 days ago.
``release_manifest``  ``RELEASE.sha256`` exists, is current, and every R3-08 glob matches a file.
``dirty``             ``git status`` is clean (untracked files count unless ``--allow-untracked``).
``privacy_scan``      0 findings.
``doctor``            0 missing items (sections host, seams).
``install_fingerprint``  ``.venv/klode-kit-install.json`` was written for this checkout and this ``uv.lock`` (V6).
``mutation``          ``work/mutation.json`` (``{"schema": "klode-kit.mutation/1", "code_tree_sha", "score",
                      "finished_at"}``) bound to the current sha, <= 7 days old, score >= 0.80 (plan section 0.3).

Waivers are explicit and visible: ``--mutation skip``, ``--no-doctor``, ``--no-privacy`` mark that item ``ok`` with
reason ``waived`` and list it under ``waived`` in the report; nothing is waived by default.

Exit: 0 releasable (waivers listed); 20 at least one item unmet; 2 usage. JSON report on stdout, one
``MISSING <id>: <detail>`` line per unmet item on stderr.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from klode_kit.contract import schema as contract
from klode_kit.devtools import release_manifest as manifest

LANES = ("unit", "contract_replay", "cli_integration", "model_integration", "host_acceptance")
DEFAULT_MAX_AGE = dt.timedelta(days=7)
MUTATION_MIN = 0.80
CLOCK_SKEW = dt.timedelta(minutes=5)
NOT_RELEASABLE = 20
USAGE_EXIT = 2

Probe = Callable[[Path], int]


def current_python() -> str:
    return os.path.realpath(sys.executable)


def _entry(ident: str, reasons: list[str], detail: str, fix: str) -> dict:
    return {"id": ident, "ok": not reasons or reasons == ["waived"], "reasons": reasons, "detail": detail, "fix": fix}


def _parse_time(text: str) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _age_reason(finished: str, now: dt.datetime, max_age: dt.timedelta) -> str | None:
    when = _parse_time(finished)
    if when is None:
        return "bad_timestamp"
    age = now - when
    if age > max_age:
        return "too_old"
    if age < -CLOCK_SKEW:
        return "from_the_future"
    return None


def _lane(root: Path, lane: str, sha: str, now: dt.datetime, python: str, max_age: dt.timedelta) -> dict:
    path = root / "work" / "lanes" / f"{lane}.json"
    fix = f"python -m klode_kit.devtools.run_lane {lane} (a release run, not --not-release)"
    if not path.is_file():
        return _entry(f"lane:{lane}", ["missing"], f"no receipt work/lanes/{lane}.json", fix)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _entry(f"lane:{lane}", ["unreadable"], f"work/lanes/{lane}.json unreadable: {exc}", fix)
    problems = contract.issues("lane_receipt", doc)
    if problems:
        return _entry(f"lane:{lane}", ["invalid_receipt"], "not a valid lane_receipt: " + "; ".join(str(p) for p in problems[:3]), fix)
    reasons: list[str] = []
    if doc["lane"] != lane:
        reasons.append("wrong_lane")
    if doc["status"] != "PASS":
        reasons.append(f"status:{doc['status']}")
    if doc.get("tests_failed", 0):
        reasons.append("failures")
    if "not-release" in doc["tags"]:
        reasons.append("not_release")
    fp = doc["env_fingerprint"]
    if "code_tree_sha" not in fp:
        reasons.append("no_code_tree_sha")
    elif fp["code_tree_sha"] != sha:
        reasons.append("stale_sha")
    if fp["repo_root"] != os.path.realpath(root):
        reasons.append("repo_moved")
    if fp["python"] != python:
        reasons.append("python_changed")
    late = _age_reason(doc["finished_at"], now, max_age)
    if late:
        reasons.append(late)
    detail = "ok" if not reasons else f"work/lanes/{lane}.json: " + ", ".join(reasons)
    return _entry(f"lane:{lane}", reasons, detail, fix)


def _manifest(root: Path) -> dict:
    fix = "python -m klode_kit.devtools.release_manifest --write"
    reasons: list[str] = []
    details: list[str] = []
    missing = manifest.uncovered(root)
    if missing:
        reasons.append("uncovered_glob")
        details.append("globs matching no file: " + ", ".join(missing))
    target = root / manifest.MANIFEST_NAME
    if not target.is_file():
        reasons.append("missing")
        details.append(f"{manifest.MANIFEST_NAME} does not exist")
    elif not missing and target.read_text(encoding="utf-8") != manifest.render(root):
        reasons.append("stale")
        details.append(f"{manifest.MANIFEST_NAME} is stale: the run files changed since it was written")
    return _entry("release_manifest", reasons, "; ".join(details) or "RELEASE.sha256 is current", fix)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env, timeout=120)


def _dirty(root: Path, allow_untracked: bool) -> dict:
    fix = "commit or stash the changes (the release is made from a clean, committed tree)"
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError) as exc:
        return _entry("dirty", ["git_unavailable"], f"cannot run git: {exc}", fix)
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(root):
        return _entry("dirty", ["not_a_git_checkout"], "the root is not the top level of a git checkout", fix)
    status = _git(root, "status", "--porcelain", "-uno" if allow_untracked else "-unormal")
    if status.returncode != 0:
        return _entry("dirty", ["git_failed"], status.stderr.strip()[:200], fix)
    lines = [ln for ln in status.stdout.splitlines() if ln.strip()]
    if lines:
        shown = ", ".join(ln[3:] for ln in lines[:8]) + (f" (+{len(lines) - 8} more)" if len(lines) > 8 else "")
        return _entry("dirty", ["dirty"], f"{len(lines)} changed path(s): {shown}", fix)
    return _entry("dirty", [], "clean", fix)


def _probe(ident: str, fn: Probe | None, root: Path, label: str, fix: str, waived: bool) -> dict:
    if waived:
        return _entry(ident, ["waived"], f"WAIVED: {label} not run (explicit flag)", fix)
    try:
        count = (fn or {"privacy_scan": default_privacy, "doctor": default_doctor}[ident])(root)
    except Exception as exc:  # a probe that dies is a failure, never a pass
        return _entry(ident, ["probe_failed"], f"{label} could not run: {type(exc).__name__}: {exc}", fix)
    if count:
        return _entry(ident, ["findings" if ident == "privacy_scan" else "missing_items"], f"{count} {label}", fix)
    return _entry(ident, [], f"0 {label}", fix)


def default_privacy(root: Path) -> int:
    from klode_kit.devtools import privacy_scan

    return len(privacy_scan.scan(root))


def default_doctor(root: Path) -> int:
    from klode_kit.check import doctor

    receipt = doctor.run(sections=("host", "seams"), root=root, project=None, env=os.environ)
    return int(receipt["missing"])


def _install(root: Path) -> dict:
    fix = "scripts/install.sh"
    path = root / ".venv" / "klode-kit-install.json"
    if not path.is_file():
        return _entry("install_fingerprint", ["missing"], "no .venv/klode-kit-install.json", fix)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _entry("install_fingerprint", ["unreadable"], f"unreadable: {exc}", fix)
    reasons: list[str] = []
    if doc.get("repo_root") != os.path.realpath(root):
        reasons.append("repo_moved")
    lock = root / "uv.lock"
    if not lock.is_file() or doc.get("lock_sha256") != hashlib.sha256(lock.read_bytes()).hexdigest():
        reasons.append("lock_changed")
    return _entry("install_fingerprint", reasons,
                  "ok" if not reasons else "the venv was installed for another location or another uv.lock: " + ", ".join(reasons), fix)


def _mutation(root: Path, sha: str, now: dt.datetime, mode: str, minimum: float, max_age: dt.timedelta) -> dict:
    fix = "run mutmut on the criticalPaths and write work/mutation.json (schema klode-kit.mutation/1)"
    if mode == "skip":
        return _entry("mutation", ["waived"], "WAIVED: mutation score not required (explicit --mutation skip)", fix)
    path = root / "work" / "mutation.json"
    if not path.is_file():
        return _entry("mutation", ["missing"], "no work/mutation.json", fix)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        score = float(doc["score"])
        finished = doc["finished_at"]
        bound = doc["code_tree_sha"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _entry("mutation", ["unreadable"], f"work/mutation.json unusable: {exc}", fix)
    reasons: list[str] = []
    if bound != sha:
        reasons.append("stale_sha")
    late = _age_reason(finished, now, max_age)
    if late:
        reasons.append(late)
    if score < minimum:
        reasons.append("score_below_min")
    return _entry("mutation", reasons, f"score {score:.2f} (minimum {minimum:.2f})" + ("" if not reasons else ": " + ", ".join(reasons)), fix)


def check(root: Path, *, now: dt.datetime | None = None, python: str | None = None, doctor: Probe | None = None,
          privacy: Probe | None = None, allow_untracked: bool = False, mutation: str = "required",
          mutation_min: float = MUTATION_MIN, max_age: dt.timedelta = DEFAULT_MAX_AGE,
          waive_doctor: bool = False, waive_privacy: bool = False) -> dict:
    root = Path(root)
    now = now or dt.datetime.now(dt.timezone.utc)
    python = python or current_python()
    sha = manifest.release_sha(root)
    checks = [_lane(root, lane, sha, now, python, max_age) for lane in LANES]
    checks += [
        _manifest(root),
        _dirty(root, allow_untracked),
        _probe("privacy_scan", privacy, root, "privacy_scan finding(s)", "remove or allowlist the findings", waive_privacy),
        _probe("doctor", doctor, root, "doctor missing item(s)", "klode-kit doctor prints the fix for each", waive_doctor),
        _install(root),
        _mutation(root, sha, now, mutation, mutation_min, max_age),
    ]
    missing = sum(1 for c in checks if not c["ok"])
    return {
        "schema": "klode-kit.release_check/1",
        "ok": missing == 0,
        "missing": missing,
        "waived": [c["id"] for c in checks if c["reasons"] == ["waived"]],
        "release_sha": sha,
        "checks": checks,
    }


def main(argv: list[str] | None = None, *, now: dt.datetime | None = None, doctor: Probe | None = None,
         privacy: Probe | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev release-check", description="release gate: reports every unmet item")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--mutation", choices=("required", "skip"), default="required",
                        help="skip = explicit waiver of the mutation score (reported under 'waived')")
    parser.add_argument("--mutation-min", type=float, default=MUTATION_MIN)
    parser.add_argument("--allow-untracked", action="store_true")
    parser.add_argument("--no-doctor", action="store_true", help="explicit waiver of the doctor item")
    parser.add_argument("--no-privacy", action="store_true", help="explicit waiver of the privacy_scan item")
    parser.add_argument("--now", help="ISO timestamp to judge ages against (test seam: only with KLODE_KIT_TEST_SEAMS=1)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    if not args.root.is_dir():
        print(f"release_check: not a directory: {args.root}", file=sys.stderr)
        return USAGE_EXIT
    if args.now is not None:
        from klode_kit.seams import seams_active

        stamp = _parse_time(args.now)
        if not seams_active() or stamp is None:
            print("release_check: usage error: --now is a test seam (needs KLODE_KIT_TEST_SEAMS=1 and an ISO timestamp)",
                  file=sys.stderr)
            return USAGE_EXIT
        now = stamp
    report = check(args.root, now=now, doctor=doctor, privacy=privacy, allow_untracked=args.allow_untracked,
                   mutation=args.mutation, mutation_min=args.mutation_min,
                   waive_doctor=args.no_doctor, waive_privacy=args.no_privacy)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for c in report["checks"]:
        if not c["ok"]:
            print(f"release_check: MISSING {c['id']}: {c['detail']}\n  fix: {c['fix']}", file=sys.stderr)
    for ident in report["waived"]:
        print(f"release_check: WAIVED {ident}", file=sys.stderr)
    if report["ok"]:
        print(f"release_check: releasable ({len(report['checks'])} items, {len(report['waived'])} waived)", file=sys.stderr)
        return 0
    print(f"release_check: NOT releasable: {report['missing']} item(s) unmet -> exit {NOT_RELEASABLE}", file=sys.stderr)
    return NOT_RELEASABLE


if __name__ == "__main__":
    sys.exit(main())
