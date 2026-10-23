"""The only path by which a page image leaves the machine (WI-05b; design 02 r2.2-B, r3.5, r4.3; matrix K6).

``send(path, expect_sha, *, route, project, receipt_dir, isolation_receipt, ...)`` starts one cloud read-image
process (``claude`` or ``codex``, argv from ``cloud_argv``) on a copy of the image, or refuses:

=====  ===========================================================================================================
exit   refusal (``SendRefused.exit_code``; nothing is started for it except, for 61, the review's ``--version``)
=====  ===========================================================================================================
60     privacy: ``path`` (or its real path) lies in a ``_mask_review`` directory; it cannot be read as a regular
       file; its bytes do not hash to ``expect_sha``; that sha is not registered - registered means a
       ``der_sha256`` of ``mask.read_manifest(project)`` (WI-05a) or a png sha of the synthetic
       ``tests/fixtures/MANIFEST.json``; an unreadable / corrupt MANIFEST registers nothing; the copy in the send
       directory does not hash to the same registered sha
61     isolation: the route is not enabled in the routes state (``isolation_probe.route_exit_code``); the layer (a)
       review of the exact invocation fails (``isolation_check.check`` with the configured effort and RR-7's
       ``/tmp`` rule); the stored isolation receipt is not a schema-valid PASS of this route taken on the CLI
       version now on PATH (claude: also with the pinned ``cloud_settings.json`` sha)
=====  ===========================================================================================================

Order (matrix section 0.3, 05b-16): route state -> ``_mask_review`` rule -> the source is opened ONCE and read into
memory (``send.after_source_read``) -> sha == ``expect_sha`` and registered (``send.after_source_check``) -> those
in-memory bytes are written into a fresh send directory (``cloud_argv.prepare_send_dir``: system TMPDIR, 0700,
exactly ``<sha>.png`` + ``prompt.txt`` + ``schema.json``; ``send.after_copy``) -> the copy is hashed again
(``send.after_copy_check``) -> invocation built from the project configuration (model, effort, budget) and
reviewed; stored receipt checked (``send.before_spawn``) -> the reviewed ``Invocation`` is executed as it is
(argv, env, cwd: 05b-15). The source path is never read again and never given to a process, so replacing the
source after the check cannot change what is sent (TOCTOU, 05b-08/16).

Always, also on failure, timeout or ``KeyboardInterrupt``: the send directory is removed - its three known files
are unlinked and the directory is ``rmdir``-ed (a copy of an image whose original stays in ``_masked/``, plus two
packaged texts; nothing else is ever deleted); if anything unexpected is left in it, the directory is moved to
``<receipt_dir>/_send_leftovers/`` instead. The isolation guard log ``<send dir>.guard.jsonl`` (claude route,
WI-00c1) is moved next to the receipt.

Receipt (``klode-kit.send_receipt/1``, defined here): one file ``<receipt_dir>/<route>-<sha>.<n>.json`` per call
(``n`` = first free number, claimed with ``O_EXCL``), guard log ``<route>-<sha>.<n>.guard.jsonl``. It records the
der sha, size from the PNG header, route, model, effort, CLI version, argv sha, the review check names with their
result, exit code, timing and status (``sent`` / ``refused`` / ``interrupted``) - never a path, a file name from
the data project, or pixels (05b-11). Review details are left out because they carry paths.

CLI: ``python -m klode_kit.ocr.send`` / ``klode-kit ocr send --route R --project P --receipt-dir D
--isolation-receipt F [--routes-state S] [--timeout SEC] PATH SHA``: the CLI's stdout is passed through; exit 0
(CLI exit 0), 1 (CLI failed or timed out), 2 (usage), 40 (configuration), 60, 61, 130 (interrupted).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from klode_kit import config as config_mod
from klode_kit.audit import mask
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr.isolation_guard import LOG_SUFFIX
from klode_kit.ocr.isolation_probe import route_exit_code
from klode_kit.seams import crashpoint, startup

PRIVACY_EXIT = 60
ISOLATION_EXIT = 61
CHILD_FAILED_EXIT = 1
USAGE_EXIT = 2
INTERRUPTED_EXIT = 130
ROUTES = ("claude", "codex")
SCHEMA = "klode-kit.send_receipt/1"
ROUTES_STATE = CA.PACKAGE / "config" / "routes_enabled.json"
LEFTOVERS = "_send_leftovers"
POINTS = ("send.after_source_read", "send.after_source_check", "send.after_copy", "send.after_copy_check",
          "send.before_spawn")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


class SendRefused(Exception):
    """Nothing was sent. ``exit_code`` 60 (privacy) or 61 (isolation); ``reason`` is a stable code."""

    def __init__(self, exit_code: int, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.exit_code = exit_code
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SendResult:
    route: str
    der_sha256: str
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    receipt: dict[str, Any]
    receipt_path: Path


@dataclass
class _State:
    route: str
    expect: str
    started: datetime
    t0: float
    fields: dict[str, Any] = field(default_factory=dict)  # receipt values only - never a path
    send_dir: Path | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- whitelist and source


def approved_shas(project: str | os.PathLike) -> frozenset[str]:
    """Registered shas: ``mask.read_manifest(project)`` rows plus the synthetic fixtures MANIFEST.

    Raises ``mask.MaskError`` for a corrupt MANIFEST (the caller refuses: nothing is registered then)."""
    return frozenset(r["der_sha256"] for r in mask.read_manifest(project)) | IC.fixture_shas()


def in_review_dir(path: str | os.PathLike) -> bool:
    """True when ``path`` as given or its real path has a ``_mask_review`` component."""
    given = Path(os.path.abspath(os.fspath(path)))
    real = Path(os.path.realpath(os.fspath(path)))
    return mask.REVIEW_DIR in given.parts or mask.REVIEW_DIR in real.parts


def _read_once(path: str | os.PathLike) -> bytes:
    """One read-only open of ``path``; regular files only (``O_NONBLOCK``: a FIFO cannot stall the sender)."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(f"not a regular file: {os.fspath(path)}")
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def png_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 24 and data[:8] == _PNG_SIG and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    return None, None


# --------------------------------------------------------------------------- isolation receipt


def check_stored_receipt(path: str | os.PathLike, route: str, cli_version: str | None) -> None:
    """Refuse (61) unless ``path`` holds a schema-valid PASS isolation receipt of ``route`` on ``cli_version``."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SendRefused(ISOLATION_EXIT, "isolation_receipt_unreadable", type(exc).__name__) from None
    if not isinstance(data, dict) or issues("isolation_receipt", data):
        raise SendRefused(ISOLATION_EXIT, "isolation_receipt_invalid")
    if data["route"] != route:
        raise SendRefused(ISOLATION_EXIT, "isolation_receipt_route", f"receipt is for {data['route']}")
    if data["cloud_isolation"] != "PASS" or not all(c["ok"] is True for c in data["checks"]):
        raise SendRefused(ISOLATION_EXIT, "isolation_receipt_not_pass")
    if cli_version is None or data["cli_version"] != cli_version:
        raise SendRefused(ISOLATION_EXIT, "isolation_receipt_stale",
                          f"receipt cli {data['cli_version']}, now {cli_version}")
    if route == "claude" and data.get("cloud_settings_sha") != IC.CLOUD_SETTINGS_SHA256:
        raise SendRefused(ISOLATION_EXIT, "isolation_receipt_settings")


def _file_sha_or_none(path: str | os.PathLike) -> str | None:
    try:
        return _sha(Path(path).read_bytes())
    except OSError:
        return None


# --------------------------------------------------------------------------- send directory


def _remove_send_dir(send_dir: Path, sha: str, receipt_dir: Path) -> None:
    for name in (f"{sha}.png", CA.PROMPT_FILE, CA.SCHEMA_FILE):
        with contextlib.suppress(FileNotFoundError):
            os.remove(send_dir / name)
    try:
        os.rmdir(send_dir)
    except FileNotFoundError:
        pass
    except OSError:
        target = receipt_dir / LEFTOVERS
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(send_dir), str(target / send_dir.name))


def _claim(receipt_dir: Path, route: str, sha: str) -> str:
    """First free ``<route>-<sha>.<n>``; the empty ``.json`` is created with ``O_EXCL`` so concurrent sends differ."""
    receipt_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        stem = f"{route}-{sha}.{n}"
        try:
            os.close(os.open(receipt_dir / f"{stem}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
            return stem
        except FileExistsError:
            n += 1


def _write_json(path: Path, data: dict) -> None:
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    blob = (json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _move_guard_log(send_dir: Path | None, receipt_dir: Path, stem: str) -> dict[str, Any] | None:
    if send_dir is None:
        return None
    log = Path(str(send_dir) + LOG_SUFFIX)
    if not log.is_file():
        return None
    name = f"{stem}.guard.jsonl"
    shutil.move(str(log), str(receipt_dir / name))
    lines = [x for x in (receipt_dir / name).read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    deny = 0
    for x in lines:
        with contextlib.suppress(ValueError):
            deny += json.loads(x).get("decision") == "deny"
    return {"file": name, "lines": len(lines), "deny": deny}


def _finish(st: _State, receipt_dir: Path, clock: Callable[[], datetime], **values: Any) -> tuple[dict[str, Any], Path]:
    stem = _claim(receipt_dir, st.route, st.fields.get("der_sha256") or st.expect)
    guard = _move_guard_log(st.send_dir, receipt_dir, stem)
    receipt: dict[str, Any] = {
        "schema": SCHEMA, "route": st.route, "status": None, "exit_code": None, "reason": None,
        "der_sha256": None, "expect_sha256": st.expect, "bytes": None, "width": None, "height": None,
        "model": None, "effort": None, "cli_version": None, "argv_sha": None, "isolation_receipt_sha": None,
        "review": [], "timed_out": False, "guard_log": guard,
        "started": st.started.isoformat(timespec="milliseconds"),
        "ended": clock().isoformat(timespec="milliseconds"),
        "duration_s": round(time.monotonic() - st.t0, 3),
    }
    receipt.update(st.fields)
    receipt.update(values)
    path = receipt_dir / f"{stem}.json"
    _write_json(path, receipt)
    return receipt, path


# --------------------------------------------------------------------------- send


def send(path: str | os.PathLike, expect_sha: str, *, route: str, project: str | os.PathLike,
         receipt_dir: str | os.PathLike, isolation_receipt: str | os.PathLike,
         routes_state: str | os.PathLike | None = None, config: config_mod.Config | None = None,
         source_env: Mapping[str, str] | None = None, timeout_s: float | None = None,
         max_budget_usd: float | None = None, now: Callable[[], datetime] | None = None) -> SendResult:
    """Send the image at ``path`` (expected sha ``expect_sha``) on ``route``; see the module docstring."""
    if route not in ROUTES:
        raise ValueError(f"route must be one of {ROUTES}, got {route!r}")
    if not isinstance(expect_sha, str) or not _SHA_RE.fullmatch(expect_sha):
        raise ValueError(f"expect_sha must be 64 lowercase hex characters, got {expect_sha!r}")
    clock = now or _utcnow
    st = _State(route, expect_sha, clock(), time.monotonic())
    receipt_dir = Path(receipt_dir)
    project = Path(project)
    cfg = config if config is not None else config_mod.load(project)
    try:
        stdout, stderr, returncode, timed_out = _send(st, path, project, receipt_dir, isolation_receipt,
                                                      routes_state, cfg, source_env, timeout_s, max_budget_usd)
    except SendRefused as exc:
        _finish(st, receipt_dir, clock, status="refused", exit_code=exc.exit_code, reason=exc.reason)
        raise
    except BaseException:
        with contextlib.suppress(Exception):
            _finish(st, receipt_dir, clock, status="interrupted", exit_code=INTERRUPTED_EXIT, reason="interrupted")
        raise
    receipt, receipt_path = _finish(st, receipt_dir, clock, status="sent", exit_code=returncode,
                                    timed_out=timed_out)
    return SendResult(route, st.fields["der_sha256"], returncode, timed_out, stdout, stderr, receipt, receipt_path)


def _send(st: _State, path, project: Path, receipt_dir: Path, isolation_receipt, routes_state, cfg, source_env,
          timeout_s, max_budget_usd) -> tuple[str, str, int | None, bool]:
    route = st.route
    if route_exit_code(routes_state if routes_state is not None else ROUTES_STATE, route) != 0:
        raise SendRefused(ISOLATION_EXIT, "route_disabled")
    if in_review_dir(path):
        raise SendRefused(PRIVACY_EXIT, "review_dir", "images under _mask_review/ are never sent")
    try:
        data = _read_once(path)
    except OSError as exc:
        raise SendRefused(PRIVACY_EXIT, "unreadable", type(exc).__name__) from None
    crashpoint("send.after_source_read")
    sha = _sha(data)
    width, height = png_size(data)
    st.fields.update(der_sha256=sha, bytes=len(data), width=width, height=height)
    if sha != st.expect:
        raise SendRefused(PRIVACY_EXIT, "sha_mismatch", "the bytes at the path are not the expected image")
    try:
        approved = approved_shas(project)
    except mask.MaskError:
        raise SendRefused(PRIVACY_EXIT, "manifest_unreadable", "the _masked MANIFEST cannot be read") from None
    if sha not in approved:
        raise SendRefused(PRIVACY_EXIT, "not_registered", f"{sha[:12]} is not a registered masked image")
    crashpoint("send.after_source_check")

    st.send_dir = CA.prepare_send_dir(data, sha)
    try:
        return _send_copy(st, project, isolation_receipt, cfg, source_env, timeout_s, max_budget_usd, approved)
    finally:  # whatever happened: refusal, child failure, timeout, KeyboardInterrupt
        _remove_send_dir(st.send_dir, sha, receipt_dir)


def _send_copy(st: _State, project: Path, isolation_receipt, cfg, source_env, timeout_s, max_budget_usd,
               approved: frozenset[str]) -> tuple[str, str, int | None, bool]:
    route, sha, send_dir = st.route, st.fields["der_sha256"], st.send_dir
    assert send_dir is not None
    crashpoint("send.after_copy")
    copy_sha = _file_sha_or_none(send_dir / f"{sha}.png")
    if copy_sha != sha or copy_sha not in approved:
        raise SendRefused(PRIVACY_EXIT, "copy_mismatch", "the copy in the send directory changed")
    crashpoint("send.after_copy_check")

    model, effort = cfg[f"{route}.model"], cfg[f"{route}.effort"]
    st.fields.update(model=model, effort=effort)
    if route == "claude":
        budget = float(max_budget_usd if max_budget_usd is not None else cfg["cloud.max_cost_usd_per_batch"])
        inv = CA.claude(send_dir, model=model, effort=effort, max_budget_usd=budget, source_env=source_env)
    else:
        inv = CA.codex(send_dir, model=model, effort=effort, source_env=source_env)
    review = IC.check(inv, project=project, approved_shas=approved, expected_effort=effort, refuse_shared_tmp=True)
    st.fields.update(cli_version=review.cli_version, argv_sha=review.argv_sha,
                     review=[{"name": c.name, "ok": c.ok} for c in review.checks],
                     isolation_receipt_sha=_file_sha_or_none(isolation_receipt))
    if not review.ok:
        raise SendRefused(ISOLATION_EXIT, "isolation_review_failed", ", ".join(review.failed))
    check_stored_receipt(isolation_receipt, route, review.cli_version)
    crashpoint("send.before_spawn")
    timeout = float(timeout_s if timeout_s is not None else cfg["cloud.timeout_s"])
    return _execute(inv, timeout)


def _execute(inv: CA.Invocation, timeout: float) -> tuple[str, str, int | None, bool]:
    """Run the reviewed invocation exactly: its argv (``argv[0]`` resolved on its own PATH), env and cwd."""
    try:
        proc = subprocess.run(list(inv.argv), cwd=inv.cwd, env=dict(inv.env), stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, check=False)
        out, err, code, timed_out = proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out, err, code, timed_out = exc.stdout or b"", exc.stderr or b"", None, True
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), code, timed_out


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    startup()
    parser = argparse.ArgumentParser(prog="klode-kit ocr send", description=__doc__.splitlines()[0])
    parser.add_argument("--route", required=True, choices=ROUTES)
    parser.add_argument("--project", required=True)
    parser.add_argument("--receipt-dir", required=True)
    parser.add_argument("--isolation-receipt", required=True)
    parser.add_argument("--routes-state")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("path")
    parser.add_argument("sha")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    try:
        res = send(args.path, args.sha, route=args.route, project=args.project, receipt_dir=args.receipt_dir,
                   isolation_receipt=args.isolation_receipt, routes_state=args.routes_state, timeout_s=args.timeout)
    except SendRefused as exc:
        print(f"klode-kit ocr send: refused (exit {exc.exit_code}): {exc}", file=sys.stderr)
        return exc.exit_code
    except config_mod.ConfigError as exc:
        print(f"klode-kit ocr send: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"klode-kit ocr send: {exc}", file=sys.stderr)
        return USAGE_EXIT
    except KeyboardInterrupt:
        print("klode-kit ocr send: interrupted; send directory removed", file=sys.stderr)
        return INTERRUPTED_EXIT
    sys.stdout.write(res.stdout)
    sys.stdout.flush()
    if res.returncode != 0:
        print(f"klode-kit ocr send: {res.route} exited {res.returncode} (timed out: {res.timed_out})",
              file=sys.stderr)
        return CHILD_FAILED_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
