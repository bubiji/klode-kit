"""``klode-kit kb-publish``: the publish protocol - the ``current`` pointer, ``PUBLISH.log``, replay, rollback, prune
(WI-09d; design 02 r3.2 "单一生效指针" / "切换前提" / "外部登记", r4.2 "读者租约" / "发布日志" / "replay 规则" /
"prune 排除集"; impl-deviations WI-09b, 10a).

    kb-publish --kb kb/<id> --build ID       point ``current`` to a checked build, then register it
    kb-publish --kb kb/<id> --replay ID      do the registrations of the *current* build that are still open
    kb-publish --kb kb/<id> --rollback-to ID point back to an older build and register all of it again
    kb-publish --kb kb/<id> --prune --keep N move the builds nothing needs into builds/_trash/
        [--project DIR] [--receipt FILE]     (project: the data project, default ``kb/..``; receipt: the JSON summary)

**The pointer** ``kb/<id>/current`` is a relative symlink ``builds/<build_id>``. A switch makes ``current.tmp`` and
renames it over ``current`` - one ``os.replace``, so a reader of the link never sees it missing or dangling (09d-03).
Before anything moves, the target is *verified* (K5): ``build_receipt.json`` PASS for this build, ``MANIFEST.sha256``
present and equal to the tree, ``check_receipt.json`` valid, ``PASS`` and naming exactly this build, and the whole
tree read-only (``.readers/`` excepted). A directory without a build receipt - which ``kb-check`` would only WARN
about - is refused. Refusals change nothing: exit 20 when the check receipt says FAIL (and nothing else is wrong), 30
otherwise.

**The log** ``kb/<id>/PUBLISH.log`` is append-only JSON lines ``{seq, target_build_id, prev_build_id, action, status, at,
by, detail?}`` (contract ``publish_log``); ``seq`` is strictly increasing. One *publication* of a build is its latest
pointer row (``switch`` or ``rollback``, ``done``) and the rows of the four registrations after it - ``mcp_json``,
``registry``, ``agent``, ``approve`` - the *epoch* of the build. An action is **open** when its latest row in the
epoch is missing or ``failed``; ``done`` and ``skipped_newer`` settle it. A build with an open action is **pending**.
"Newer" between two builds is the order of their latest pointer rows' ``seq`` - never of the build ids (09d-18). The
pointer row is appended after the switch; a crash between the two is repaired by the next ``--build``/``--replay``
of the same build, which records the switch after the fact.

**Registrations** (the seam ``ACTIONS``; WI-11b brings the hosts' probes and the richer upsert): ``mcp_json`` in
``<project>/.mcp.json`` (``<kb>-klode`` -> the launcher ``mcp --kb <kb>``, ``env.KLODE_KIT_BUILD`` = the build id, the
marker); ``registry`` an entry ``[[kb]]`` in ``$KLODE_REGISTRY`` (default: ``registry.toml`` in the ``.klode`` directory of the home directory) with ``path`` =
``<kb>/current/library.toml`` and ``build_id``; ``agent`` the build's ``agent/*.md`` into ``<project>/.claude/agents/``
(its ``<!-- klode-kit build ID -->`` line is the marker); ``approve`` the server name in
``.claude/settings.local.json`` ``enabledMcpjsonServers``. Each is read-modify-write, keeps what else is there, goes
through a temporary file and one ``os.replace``, and is skipped (``skipped_newer``) when its marker already names a
build published *later*. A registration that cannot be read or written fails its action only: the pointer stays (the
library is self-contained and can be used directly), the run ends with exit 21 and ``--replay`` repeats just that
action.

**Replay** refuses (exit 22) when ``current`` is not the build; it writes nothing then. **Rollback** verifies its target
like a publish, switches (row ``rollback``) and does *all* the registrations again. All four commands that write take
the publish lock - an ``flock`` on the ``kb/<id>/`` directory itself (no file) - and a second one gets exit 40.

**Prune** keeps ``current``, its rollback target (the previous build of its latest pointer row), every pending build,
the ``N`` newest builds and every build with a live reader. A reader lease is a file ``<pid>-<nonce>`` whose pid is
alive (a name without a pid counts as alive); the MCP wrapper writes it to ``kb/<id>/.readers/<build_id>/`` - outside
the sealed build, whose tree (top directory included) is read-only and which a running reader therefore cannot write
into (WI-11a). The older place ``builds/<id>/.readers/`` is still read, so a lease written there is honoured the same
way. Leases of dead pids are removed first, in both places. The others are *moved* to
``builds/_trash/`` (nothing is deleted; a build keeps its bytes and its read-only seal), each with a ``prune`` row; a
build that got a live reader while it was being moved is moved back.

Exit codes: 0 ok, 2 usage, 20 the check receipt says FAIL, 21 a registration failed (``post_publish_failed``), 22
stale replay, 30 the target is not a verified build or the log is unreadable, 40 the lock is held.
stdout: one JSON line ``klode-kit.publish/1`` (also written to ``--receipt``).

Crash points: ``publish.lock``, ``publish.tmp_link``, ``publish.switched``, ``publish.logged`` (after each row),
``publish.mkdir`` (after each directory made), ``publish.action_tmp`` / ``publish.action_written`` (around the one
``os.replace`` of a registration), ``publish.receipt_tmp`` / ``publish.receipt_written``.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import stat
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Iterator, Mapping, Sequence, TextIO

from klode_kit import __version__
from klode_kit.build.run_build import CHECK_RECEIPT_NAME, MANIFEST_NAME, verify_manifest
from klode_kit.contract.schema import issues
from klode_kit.pipeline import EXIT_CODE, Reason, default_launcher, pid_alive
from klode_kit.receipts import dumps
from klode_kit.seams import crashpoint

SCHEMA = "klode-kit.publish/1"
LOG_NAME = "PUBLISH.log"
BUILD_RE = re.compile(r"^b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
MARKER_RE = re.compile(r"<!--\s*klode-kit build (b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8})\s*-->")
ACTIONS = ("mcp_json", "registry", "agent", "approve")
POINTER_ACTIONS = ("switch", "rollback")
SETTLED = ("done", "skipped_newer")
USAGE_EXIT = EXIT_CODE[Reason.usage]
DEFAULT_KEEP = 3


class LogError(Exception):
    """``PUBLISH.log`` cannot be trusted (a torn line, a row that breaks the contract, ``seq`` not increasing)."""


class LockBusy(Exception):
    pass


class ActionError(Exception):
    """A registration cannot be read or written; its action fails, nothing else does."""


# --------------------------------------------------------------------------- the log


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_log(kb: str | os.PathLike) -> list[dict]:
    """The rows of ``PUBLISH.log`` (``[]`` without one); ``LogError`` for anything but whole, valid rows in order."""
    path = Path(kb) / LOG_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise LogError(f"{path}: cannot be read: {exc}") from exc
    if text and not text.endswith("\n"):
        raise LogError(f"{path}: the last line is torn (no newline)")
    rows: list[dict] = []
    for n, line in enumerate(text.splitlines(), start=1):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise LogError(f"{path}:{n}: not JSON: {exc}") from exc
        problems = issues("publish_log", row)
        if problems:
            raise LogError(f"{path}:{n}: {problems[0].path or '<row>'}: {problems[0].message}")
        if rows and row["seq"] <= rows[-1]["seq"]:
            raise LogError(f"{path}:{n}: seq {row['seq']} does not follow {rows[-1]['seq']}")
        rows.append(row)
    return rows


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def append_row(kb: str | os.PathLike, target: str, action: str, status: str, *, prev: str | None = None,
               detail: str | None = None) -> dict:
    """Append one row (the only write the log ever gets): one ``write`` to an ``O_APPEND`` fd, fsynced."""
    kb = Path(kb)
    rows = read_log(kb)
    row: dict[str, Any] = {"seq": rows[-1]["seq"] + 1 if rows else 1, "target_build_id": target,
                           "prev_build_id": prev, "action": action, "status": status, "at": _now(),
                           "by": f"klode-kit/{__version__}"}
    if detail:
        row["detail"] = detail[:500]
    problems = issues("publish_log", row)
    if problems:
        raise ValueError(f"publish log row breaks its contract: {problems[0].path}: {problems[0].message}")
    data = (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(kb / LOG_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(kb)
    crashpoint("publish.logged")
    return row


# --------------------------------------------------------------------------- the bookkeeping (pure)


def _pointer_rows(rows: Sequence[Mapping]) -> list[Mapping]:
    return [r for r in rows if r["action"] in POINTER_ACTIONS and r["status"] == "done"]


def seq_of(rows: Sequence[Mapping], build_id: str) -> int | None:
    """The ``seq`` of the latest publication (``done`` pointer row) of ``build_id``, ``None`` if it has none."""
    seqs = [r["seq"] for r in _pointer_rows(rows) if r["target_build_id"] == build_id]
    return max(seqs) if seqs else None


def _latest_pointer(rows: Sequence[Mapping]) -> Mapping | None:
    pointers = _pointer_rows(rows)
    return max(pointers, key=lambda r: r["seq"]) if pointers else None


def pending_actions(rows: Sequence[Mapping], build_id: str) -> list[str]:
    """The registrations of ``build_id``'s latest publication that are still open, in the order they run."""
    start = seq_of(rows, build_id)
    if start is None:
        return []
    latest: dict[str, tuple[int, str]] = {}
    for r in rows:
        if r["target_build_id"] == build_id and r["action"] in ACTIONS and r["seq"] > start:
            if r["action"] not in latest or r["seq"] > latest[r["action"]][0]:
                latest[r["action"]] = (r["seq"], r["status"])
    return [a for a in ACTIONS if a not in latest or latest[a][1] not in SETTLED]


def pending_builds(rows: Sequence[Mapping]) -> set[str]:
    return {b for b in {r["target_build_id"] for r in _pointer_rows(rows)} if pending_actions(rows, b)}


def protected_builds(rows: Sequence[Mapping], builds: Collection[str], current: str | None,
                     readers: Collection[str], keep: int) -> dict[str, str]:
    """``{build_id: reason}`` of the builds a prune must not move; the first reason of a build is the one named
    (``current``, ``rollback_target``, ``pending``, ``reader``, ``recent``)."""
    present = set(builds)
    out: dict[str, str] = {}

    def add(build: str | None, why: str) -> None:
        if build in present and build not in out:
            out[build] = why

    if current is not None:
        add(current, "current")
        latest = max((r for r in _pointer_rows(rows) if r["target_build_id"] == current), key=lambda r: r["seq"],
                     default=None)
        if latest is not None:
            add(latest["prev_build_id"], "rollback_target")
    for b in sorted(pending_builds(rows)):
        add(b, "pending")
    for b in sorted(readers):
        add(b, "reader")
    if keep > 0:
        for b in sorted(present)[-keep:]:
            add(b, "recent")
    return out


# --------------------------------------------------------------------------- the pointer, the builds, the leases


def current_build(kb: str | os.PathLike) -> str | None:
    """The build id ``current`` points to (``None``: no link, or one that is not ours)."""
    try:
        target = os.readlink(Path(kb) / "current")
    except OSError:
        return None
    name = os.path.basename(target.rstrip("/"))
    return name if BUILD_RE.fullmatch(name) else None


def switch_pointer(kb: str | os.PathLike, build_id: str) -> None:
    """Point ``current`` to ``builds/<build_id>`` with one ``os.replace`` of a freshly made link over it."""
    kb = Path(kb)
    if not (kb / "builds" / build_id).is_dir():
        raise FileNotFoundError(f"no such build: builds/{build_id}")
    tmp = kb / "current.tmp"
    if os.path.lexists(tmp):  # what a crash between the symlink and the replace leaves
        os.unlink(tmp)
    os.symlink(f"builds/{build_id}", tmp)
    crashpoint("publish.tmp_link")
    os.replace(tmp, kb / "current")
    crashpoint("publish.switched")
    _fsync_dir(kb)


def list_builds(kb: str | os.PathLike) -> list[str]:
    root = Path(kb) / "builds"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if BUILD_RE.fullmatch(p.name) and p.is_dir() and not p.is_symlink())


@dataclass(frozen=True)
class Lease:
    path: Path
    pid: int | None
    alive: bool


def leases(build_dir: str | os.PathLike) -> list[Lease]:
    """The reader leases of a build; one whose name has no pid cannot be proven dead and counts as alive."""
    return _scan_leases(Path(build_dir) / READERS_DIR)


READERS_DIR = ".readers"


def reader_lease_dir(kb: str | os.PathLike, build_id: str) -> Path:
    """Where a reader of ``build_id`` writes its lease: ``kb/<id>/.readers/<build_id>/`` (outside the sealed build)."""
    return Path(kb) / READERS_DIR / build_id


def _scan_leases(root: Path) -> list[Lease]:
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        m = re.match(r"^(\d+)-", p.name)
        pid = int(m.group(1)) if m else None
        out.append(Lease(p, pid, True if pid is None else pid_alive(pid)))
    return out


def build_leases(kb: str | os.PathLike, build_id: str, build_dir: str | os.PathLike | None = None) -> list[Lease]:
    """Every reader lease of a build: the ones in ``kb/<id>/.readers/<build_id>/`` and, as before, the ones inside
    the build (``build_dir`` defaults to ``builds/<build_id>``; a build that was just moved passes its new place)."""
    inside = Path(build_dir) if build_dir is not None else Path(kb) / "builds" / build_id
    return _scan_leases(reader_lease_dir(kb, build_id)) + leases(inside)


def _clean_dead_leases(kb: Path, builds: Sequence[str]) -> int:
    """Remove the leases of dead pids (a lease is a runtime marker, not data), in both places; returns how many.
    A lease directory left empty goes with them."""
    n = 0

    def drop(lease: Lease) -> None:
        nonlocal n
        if not lease.alive:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(lease.path)
                n += 1

    for b in builds:
        for lease in leases(kb / "builds" / b):
            drop(lease)
    root = kb / READERS_DIR
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                for lease in _scan_leases(d):
                    drop(lease)
                with contextlib.suppress(OSError):  # not empty (a live reader) or already gone
                    os.rmdir(d)
    return n


def _rename_build(src: Path, dest: Path) -> None:
    """Rename a build directory; a sealed (read-only) directory must be writable for the rename to a new parent, so
    it is opened for that one call and sealed again."""
    mode = os.stat(src).st_mode
    try:
        os.rename(src, dest)
        return
    except PermissionError:
        os.chmod(src, mode | stat.S_IWUSR)
    try:
        os.rename(src, dest)
    except BaseException:
        os.chmod(src, mode)
        raise
    os.chmod(dest, mode)


def move_to_trash(kb: str | os.PathLike, build_id: str) -> Path:
    """Move ``builds/<id>`` to ``builds/_trash/<id>`` (a numeric suffix when the name is taken); returns the new path."""
    kb = Path(kb)
    trash = kb / "builds" / "_trash"
    trash.mkdir(exist_ok=True)
    dest, n = trash / build_id, 0
    while os.path.lexists(dest):
        n += 1
        dest = trash / f"{build_id}.{n}"
    _rename_build(kb / "builds" / build_id, dest)
    return dest


# --------------------------------------------------------------------------- the lock


@contextlib.contextmanager
def publish_lock(kb: Path) -> Iterator[None]:
    """An exclusive ``flock`` on the ``kb/<id>/`` directory (no file, released with the fd, even by a kill)."""
    fd = os.open(kb, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise LockBusy(f"another publish, replay, rollback or prune holds the lock of {kb}") from None
    try:
        crashpoint("publish.lock")
        yield
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- verifying a target


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _writable(build: Path) -> list[str]:
    bad = []
    for dirpath, dirs, files in os.walk(build):
        if ".readers" in dirs and Path(dirpath) == build:
            dirs.remove(".readers")
        for name in [""] + dirs + files:
            p = Path(dirpath, name) if name else Path(dirpath)
            st = os.lstat(p)
            if not stat.S_ISLNK(st.st_mode) and st.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                bad.append(p.relative_to(build).as_posix() or ".")
    return bad


def _verify(kb: Path, build_id: str) -> tuple[list[str], bool]:
    """``(problems, only_the_check_failed)`` - ``([], False)`` when ``builds/<id>`` is a verified build."""
    build = kb / "builds" / build_id
    if not BUILD_RE.fullmatch(build_id) or build.is_symlink() or not build.is_dir():
        return [f"no such build: builds/{build_id}"], False
    problems: list[str] = []
    receipt = _json(build / "build_receipt.json")
    if not isinstance(receipt, dict):
        problems.append("build_receipt.json: missing or unreadable (not a build of the 13 steps)")
    elif receipt.get("status") != "PASS" or receipt.get("build_id") != build_id:
        problems.append(f"build_receipt.json: status {receipt.get('status')!r} for {receipt.get('build_id')!r}, "
                        f"not a PASS build of {build_id}")
    if not (build / MANIFEST_NAME).is_file():
        problems.append(f"{MANIFEST_NAME}: missing")
    else:
        problems += [f"{MANIFEST_NAME}: {p}" for p in verify_manifest(build)[:5]]
    check_failed = False
    check = _json(build / CHECK_RECEIPT_NAME)
    if check is None:
        problems.append(f"{CHECK_RECEIPT_NAME}: missing or unreadable (the build was not checked)")
    elif issues("check_receipt", check):
        problems.append(f"{CHECK_RECEIPT_NAME}: not a valid check receipt")
    else:
        if check["build_id"] != build_id:
            problems.append(f"{CHECK_RECEIPT_NAME}: names {check['build_id']}, not {build_id}")
        if check["status"] == "FAIL":
            problems.append(f"{CHECK_RECEIPT_NAME}: status FAIL")
            check_failed = True
        elif check["status"] != "PASS":
            problems.append(f"{CHECK_RECEIPT_NAME}: status {check['status']}, not PASS")
    writable = _writable(build)
    if writable:
        shown = ", ".join(writable[:3]) + (f" and {len(writable) - 3} more" if len(writable) > 3 else "")
        problems.append(f"writable: {shown} (a published build is read-only all the way down)")
    return problems, check_failed and len(problems) == 1


def verify_target(kb: str | os.PathLike, build_id: str) -> list[str]:
    """What is wrong with ``builds/<build_id>`` as a publish target (``[]``: a verified build)."""
    return _verify(Path(kb), build_id)[0]


# --------------------------------------------------------------------------- atomic writes, the registrations


def _ensure_dir(directory: Path) -> None:
    missing = []
    cur = directory
    while not cur.is_dir():
        if os.path.lexists(cur):
            raise ActionError(f"{cur} exists and is not a directory")
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            if not d.is_dir():
                raise ActionError(f"{d} exists and is not a directory") from None
        crashpoint("publish.mkdir")


def _atomic(path: Path, data: bytes, tmp_point: str = "publish.action_tmp",
            done_point: str = "publish.action_written") -> None:
    _ensure_dir(path.parent)
    tmp = path.parent / f".{path.name}.klode-kit.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        crashpoint(tmp_point)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    crashpoint(done_point)
    _fsync_dir(path.parent)


@dataclass(frozen=True)
class Target:
    kb: Path
    project: Path
    build_id: str
    env: Mapping[str, str]

    @property
    def server(self) -> str:
        return f"{self.kb.name}-klode"

    @property
    def build(self) -> Path:
        return self.kb / "builds" / self.build_id


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ActionError(f"{path.name} cannot be read as JSON ({exc}); left as it is") from exc
    if not isinstance(doc, dict):
        raise ActionError(f"{path.name} is not a JSON object; left as it is")
    return doc


def _dump_json(doc: Mapping) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _marker_of(value: Any) -> str | None:
    return value if isinstance(value, str) and BUILD_RE.fullmatch(value) else None


def _launcher(t: Target) -> str:
    return t.env.get("KLODE_KIT_BIN") or str(default_launcher())


# mcp_json ---------------------------------------------------------------------------------------------------


def _mcp_marker(t: Target) -> str | None:
    doc = _read_json_object(t.project / ".mcp.json")
    servers = doc.get("mcpServers", {})
    entry = servers.get(t.server) if isinstance(servers, dict) else None
    env = entry.get("env") if isinstance(entry, dict) else None
    return _marker_of(env.get("KLODE_KIT_BUILD")) if isinstance(env, dict) else None


def _mcp_apply(t: Target) -> str:
    path = t.project / ".mcp.json"
    doc = _read_json_object(path)
    servers = doc.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ActionError(".mcp.json: mcpServers is not an object; left as it is")
    old = servers.get(t.server) if isinstance(servers.get(t.server), dict) else {}
    old_env = old.get("env") if isinstance(old.get("env"), dict) else {}
    servers[t.server] = {**old, "command": _launcher(t), "args": ["mcp", "--kb", os.path.abspath(t.kb)],
                         "env": {**old_env, "KLODE_KIT_BUILD": t.build_id}}
    _atomic(path, _dump_json(doc))
    return f"{t.server} -> build {t.build_id}"


# registry ---------------------------------------------------------------------------------------------------


def _registry_path(t: Target) -> Path:
    explicit = t.env.get("KLODE_REGISTRY")
    return Path(explicit) if explicit else Path(t.env.get("HOME") or os.path.expanduser("~")) / ".klode" / "registry.toml"


def _kb_blocks(lines: Sequence[str]) -> list[tuple[int, int, dict]]:
    """``(first line, end line, parsed table)`` of every ``[[kb]]`` block of a registry file."""
    blocks = []
    starts = [i for i, ln in enumerate(lines) if ln.strip() == "[[kb]]"]
    for i in starts:
        end = next((j for j in range(i + 1, len(lines)) if lines[j].lstrip().startswith("[")), len(lines))
        table = tomllib.loads("".join(lines[i:end])).get("kb", [{}])[0]
        blocks.append((i, end, table))
    return blocks


def _registry_read(t: Target) -> tuple[Path, str, list[str]]:
    path = _registry_path(t)
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ActionError(f"{path.name} cannot be read as TOML ({exc}); left as it is") from exc
    return path, text, text.splitlines(keepends=True)


def _registry_marker(t: Target) -> str | None:
    _, _, lines = _registry_read(t)
    for _, _, table in _kb_blocks(lines):
        if table.get("id") == t.kb.name:
            return _marker_of(table.get("build_id"))
    return None


def _registry_apply(t: Target) -> str:
    path, text, lines = _registry_read(t)
    block = (f"[[kb]]\nid = {json.dumps(t.kb.name)}\npath = {json.dumps(os.path.abspath(t.kb / 'current' / 'library.toml'))}\n"
             f"build_id = {json.dumps(t.build_id)}\n")
    for start, end, table in _kb_blocks(lines):
        if table.get("id") == t.kb.name:
            tail = "\n" if end - start > 1 and lines[end - 1].strip() == "" else ""
            new = "".join(lines[:start]) + block + tail + "".join(lines[end:])
            break
    else:
        new = text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text.strip() else "") + block
    _atomic(path, new.encode("utf-8"))
    return f"{t.kb.name} -> build {t.build_id}"


# agent ------------------------------------------------------------------------------------------------------


def _agent_files(t: Target) -> list[Path]:
    files = sorted((t.build / "agent").glob("*.md")) if (t.build / "agent").is_dir() else []
    if not files:
        raise ActionError(f"builds/{t.build_id}/agent holds no agent file")
    return files


def _agent_marker(t: Target) -> str | None:
    dest = t.project / ".claude" / "agents" / _agent_files(t)[0].name
    if not dest.is_file():
        return None
    head = "".join(dest.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[:40])
    m = MARKER_RE.search(head)
    return m.group(1) if m else None


def _agent_apply(t: Target) -> str:
    names = []
    for src in _agent_files(t):
        _atomic(t.project / ".claude" / "agents" / src.name, src.read_bytes())
        names.append(src.name)
    return ", ".join(names)


# approve ----------------------------------------------------------------------------------------------------


def _approve_apply(t: Target) -> str:
    path = t.project / ".claude" / "settings.local.json"
    doc = _read_json_object(path)
    enabled = doc.setdefault("enabledMcpjsonServers", [])
    if not isinstance(enabled, list):
        raise ActionError("settings.local.json: enabledMcpjsonServers is not a list; left as it is")
    if t.server in enabled:
        return f"{t.server} already enabled"
    enabled.append(t.server)
    _atomic(path, _dump_json(doc))
    return f"{t.server} enabled"


ACTION_IMPL: dict[str, tuple[Callable[[Target], str | None] | None, Callable[[Target], str]]] = {
    "mcp_json": (_mcp_marker, _mcp_apply),
    "registry": (_registry_marker, _registry_apply),
    "agent": (_agent_marker, _agent_apply),
    "approve": (None, _approve_apply),
}


# --------------------------------------------------------------------------- one publication


def _run_action(name: str, t: Target, rows: list[dict]) -> dict:
    """Run one registration and append its row; ``rows`` (the in-memory log) grows with it."""
    marker_fn, apply_fn = ACTION_IMPL[name]
    status, detail = "done", ""
    try:
        marker = marker_fn(t) if marker_fn is not None else None
        mine, theirs = seq_of(rows, t.build_id), seq_of(rows, marker) if marker else None
        if marker and marker != t.build_id and mine is not None and theirs is not None and theirs > mine:
            status, detail = "skipped_newer", f"the registration names build {marker}, published later"
        else:
            detail = apply_fn(t) or ""
    except (ActionError, OSError) as exc:
        status, detail = "failed", str(exc)
    rows.append(append_row(t.kb, t.build_id, name, status, prev=None, detail=detail))
    return {"action": name, "status": status, "detail": detail}


def _body(mode: str, kb: Path, build_id: str | None, reason: Reason, **extra: Any) -> dict:
    return {"schema": SCHEMA, "mode": mode, "kb_id": kb.name, "build_id": build_id, "exit_code": EXIT_CODE[reason],
            "reason": reason.value, **extra}


def _project_of(kb: Path, project: str | os.PathLike | None) -> Path | None:
    if project is not None:
        return Path(project)
    return kb.parent.parent if kb.parent.name == "kb" else None


def _complete(kb: Path, t: Target, rows: list[dict], *, mode: str, force_pointer_row: str | None,
              record_missing: bool = True) -> tuple[int, dict]:
    """The pointer is on ``t.build_id`` (or is to be moved there): record it, then do the open registrations.
    ``record_missing``: a pointer already on the build whose latest pointer row names another build is a switch that
    died before its row - a ``--build`` records it after the fact (a replay never writes pointer rows)."""
    done: list[dict] = []
    cur = current_build(kb)
    latest = _latest_pointer(rows)
    prev: str | None
    if cur != t.build_id:
        prev = cur
        switch_pointer(kb, t.build_id)
        action = force_pointer_row or "switch"
        rows.append(append_row(kb, t.build_id, action, "done", prev=prev))
        done.append({"action": action, "status": "done", "detail": ""})
    elif force_pointer_row is not None:
        prev = latest["prev_build_id"] if latest is not None else None
        rows.append(append_row(kb, t.build_id, force_pointer_row, "done", prev=prev, detail="already current"))
        done.append({"action": force_pointer_row, "status": "done", "detail": "already current"})
    elif record_missing and (latest is None or latest["target_build_id"] != t.build_id):
        prev = latest["target_build_id"] if latest is not None else None
        rows.append(append_row(kb, t.build_id, "switch", "done", prev=prev,
                               detail="the pointer was already on the build; recorded after the fact"))
        done.append({"action": "switch", "status": "done", "detail": "recorded after the fact"})
    else:
        own = max((r for r in _pointer_rows(rows) if r["target_build_id"] == t.build_id), key=lambda r: r["seq"],
                  default=None)
        prev = own["prev_build_id"] if own is not None else None
    for name in pending_actions(rows, t.build_id):
        done.append(_run_action(name, t, rows))
    failed = [a for a in done if a["status"] == "failed"]
    reason = Reason.post_publish_failed if failed else Reason.ok
    return EXIT_CODE[reason], _body(mode, kb, t.build_id, reason, prev_build_id=prev, actions=done)


def _refuse(kb: Path, mode: str, build_id: str, problems: list[str], check_failed: bool) -> tuple[int, dict]:
    reason = Reason.content_gate if check_failed else Reason.contract_violation
    return EXIT_CODE[reason], _body(mode, kb, build_id, reason, problems=problems)


def _locked_run(kb: Path, mode: str, build_id: str | None, fn: Callable[[], tuple[int, dict]]) -> tuple[int, dict]:
    try:
        with publish_lock(kb):
            return fn()
    except LockBusy as exc:
        return EXIT_CODE[Reason.lock_busy], _body(mode, kb, build_id, Reason.lock_busy, detail=str(exc))
    except LogError as exc:
        return EXIT_CODE[Reason.contract_violation], _body(mode, kb, build_id, Reason.contract_violation,
                                                           problems=[str(exc)])


def _write_receipt(path: str | os.PathLike | None, body: Mapping) -> None:
    if path is not None:
        _atomic(Path(path), dumps(dict(body)), "publish.receipt_tmp", "publish.receipt_written")


def publish(kb: str | os.PathLike, build_id: str, *, project: str | os.PathLike | None = None,
            env: Mapping[str, str] | None = None, receipt: str | os.PathLike | None = None) -> tuple[int, dict]:
    """Point ``current`` to the verified build and register it; ``(exit code, summary)``."""
    kb = Path(kb)
    target = Target(kb, _project_of(kb, project) or kb.parent.parent, build_id, dict(os.environ if env is None else env))

    def go() -> tuple[int, dict]:
        problems, check_failed = _verify(kb, build_id)
        if problems:
            return _refuse(kb, "publish", build_id, problems, check_failed)
        code, body = _complete(kb, target, read_log(kb), mode="publish", force_pointer_row=None)
        _write_receipt(receipt, body)
        return code, body

    return _locked_run(kb, "publish", build_id, go)


def replay(kb: str | os.PathLike, build_id: str, *, project: str | os.PathLike | None = None,
           env: Mapping[str, str] | None = None, receipt: str | os.PathLike | None = None) -> tuple[int, dict]:
    """Do the open registrations of the build ``current`` points to; exit 22 (nothing written) when it points elsewhere."""
    kb = Path(kb)
    target = Target(kb, _project_of(kb, project) or kb.parent.parent, build_id, dict(os.environ if env is None else env))

    def go() -> tuple[int, dict]:
        cur = current_build(kb)
        if cur != build_id:
            return EXIT_CODE[Reason.stale_replay], _body("replay", kb, build_id, Reason.stale_replay,
                                                         detail=f"current is {cur or 'not set'}, not {build_id}")
        problems, check_failed = _verify(kb, build_id)
        if problems:
            return _refuse(kb, "replay", build_id, problems, check_failed)
        rows = read_log(kb)
        if seq_of(rows, build_id) is None:
            return _refuse(kb, "replay", build_id, [f"{LOG_NAME}: no publication of {build_id} is recorded; "
                                                    f"publish it with --build"], False)
        code, body = _complete(kb, target, rows, mode="replay", force_pointer_row=None, record_missing=False)
        _write_receipt(receipt, body)
        return code, body

    return _locked_run(kb, "replay", build_id, go)


def rollback(kb: str | os.PathLike, build_id: str, *, project: str | os.PathLike | None = None,
             env: Mapping[str, str] | None = None, receipt: str | os.PathLike | None = None) -> tuple[int, dict]:
    """Point back to an older verified build (row ``rollback``) and register all of it again."""
    kb = Path(kb)
    target = Target(kb, _project_of(kb, project) or kb.parent.parent, build_id, dict(os.environ if env is None else env))

    def go() -> tuple[int, dict]:
        problems, check_failed = _verify(kb, build_id)
        if problems:
            return _refuse(kb, "rollback", build_id, problems, check_failed)
        code, body = _complete(kb, target, read_log(kb), mode="rollback", force_pointer_row="rollback")
        _write_receipt(receipt, body)
        return code, body

    return _locked_run(kb, "rollback", build_id, go)


def prune(kb: str | os.PathLike, keep: int, *, env: Mapping[str, str] | None = None,
          receipt: str | os.PathLike | None = None) -> tuple[int, dict]:
    """Move the builds that are not protected into ``builds/_trash/``; ``(exit code, summary)``."""
    kb = Path(kb)

    def go() -> tuple[int, dict]:
        rows = read_log(kb)
        builds = list_builds(kb)
        cleaned = _clean_dead_leases(kb, builds)
        readers = {b for b in builds if any(l.alive for l in build_leases(kb, b))}
        prot = protected_builds(rows, builds, current_build(kb), readers, keep)
        moved, returned = [], []
        for b in builds:
            if b in prot:
                continue
            dest = move_to_trash(kb, b)
            if any(l.alive for l in build_leases(kb, b, dest)):  # a reader wrote its lease between the check and the move
                _rename_build(dest, kb / "builds" / b)
                returned.append(b)
                continue
            append_row(kb, b, "prune", "done", prev=None, detail=f"moved to builds/_trash/{dest.name}")
            moved.append(b)
        body = _body("prune", kb, None, Reason.ok, keep=keep, moved=moved, returned=returned, protected=prot,
                     cleaned_leases=cleaned)
        _write_receipt(receipt, body)
        return 0, body

    return _locked_run(kb, "prune", None, go)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit kb-publish", description="publish protocol of a library")
    parser.add_argument("--kb", type=Path, required=True, help="library root kb/<id> (holds builds/, current, PUBLISH.log)")
    parser.add_argument("--project", type=Path, default=None, help="data project (default: the parent of kb/)")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--build", metavar="ID", help="publish this checked build")
    modes.add_argument("--replay", metavar="ID", help="do the open registrations of the current build")
    modes.add_argument("--rollback-to", metavar="ID", help="point back to this older build")
    modes.add_argument("--prune", action="store_true", help="move unneeded builds to builds/_trash/")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="prune: newest N builds to keep (default 3)")
    parser.add_argument("--receipt", type=Path, default=None, help="also write the JSON summary to this file")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    out: TextIO = sys.stderr

    def usage(message: str) -> int:
        print(f"kb-publish: {message}", file=out)
        print(json.dumps({"schema": SCHEMA, "exit_code": USAGE_EXIT, "reason": "usage", "detail": message},
                         sort_keys=True), file=sys.stdout)
        return USAGE_EXIT

    kb = args.kb
    if not kb.is_dir():
        return usage(f"--kb is not a directory: {kb}")
    if args.keep < 0:
        return usage("--keep must be 0 or more")
    wanted = args.build or args.replay or args.rollback_to
    if not args.prune and not BUILD_RE.fullmatch(wanted or ""):
        return usage(f"not a build id: {wanted!r}")
    project = _project_of(kb, args.project)
    if project is None and not args.prune:
        return usage("--project is needed: the library is not below a kb/ directory")
    if args.prune:
        code, body = prune(kb, args.keep, receipt=args.receipt)
    elif args.build:
        code, body = publish(kb, args.build, project=project, receipt=args.receipt)
    elif args.replay:
        code, body = replay(kb, args.replay, project=project, receipt=args.receipt)
    else:
        code, body = rollback(kb, args.rollback_to, project=project, receipt=args.receipt)
    for problem in body.get("problems", []):
        print(f"kb-publish: refused: {problem}", file=out)
    if body.get("detail"):
        print(f"kb-publish: {body['detail']}", file=out)
    for action in body.get("actions", []):
        print(f"kb-publish: {action['status']:<13} {action['action']}"
              + (f"  {action['detail']}" if action.get("detail") else ""), file=out)
    print(json.dumps(body, sort_keys=True, ensure_ascii=False), file=sys.stdout)
    return code


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit kb-publish``
    sys.exit(main())
