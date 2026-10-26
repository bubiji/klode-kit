"""The file-writing layer of the registrations (WI-11b; extracted from ``publish.py``, WI-09d).

- ``ensure_dir`` / ``atomic_write``: a file is written through a temporary file in its own directory and one
  ``os.replace`` (fsync before, directory fsync after); a failure leaves the old file and no temporary behind. The crash
  points ``publish.mkdir`` / ``publish.action_tmp`` / ``publish.action_written`` are the ones the publish flow
  enumerates (00s-16, 09d-02) and keep their names.
- the klode registry (``$KLODE_REGISTRY``, default ``registry.toml`` in the ``.klode`` directory of ``HOME``): ``[[kb]]`` entries
  ``id`` / ``path`` / ``build_id``. ``upsert`` is idempotent by id, replaces an entry in place, keeps every other entry
  and every foreign table byte for byte (text-level edit, no TOML rewrite), and serialises concurrent writers with an
  ``flock`` on the registry's directory (no lock file; two processes upserting different ids lose nothing). A file that
  is not TOML is never touched (``ActionError``).

Nothing here chooses a path on its own besides ``registry_path``, which follows ``env`` (``HOME`` of the environment
given, never the process's when one is given): a test points ``HOME`` and ``KLODE_REGISTRY`` into tmp (gate G3).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from klode_kit.seams import crashpoint

REGISTRY_SECTION = "[[kb]]"
BUILD_RE = re.compile(r"^b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


def marker_of(value: Any) -> str | None:
    """``value`` when it is a build id, else None (a marker is only ever a build id)."""
    return value if isinstance(value, str) and BUILD_RE.fullmatch(value) else None


class ActionError(Exception):
    """A registration cannot be read or written; its action fails, nothing else does."""


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def ensure_dir(directory: Path) -> None:
    missing = []
    cur = Path(directory)
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


def atomic_write(path: Path, data: bytes, tmp_point: str = "publish.action_tmp",
                 done_point: str = "publish.action_written") -> None:
    path = Path(path)
    ensure_dir(path.parent)
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


# --------------------------------------------------------------------------- the klode registry


def registry_path(env: Mapping[str, str] | None = None) -> Path:
    """``$KLODE_REGISTRY``, else ``registry.toml`` in ``<HOME>`` / ``.klode`` (``HOME`` from ``env`` when given)."""
    env = os.environ if env is None else env
    explicit = env.get("KLODE_REGISTRY")
    if explicit:
        return Path(explicit)
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".klode" / "registry.toml"


def _kb_blocks(lines: Sequence[str]) -> list[tuple[int, int, dict]]:
    """``(first line, end line, parsed table)`` of every ``[[kb]]`` block of a registry file."""
    blocks = []
    starts = [i for i, ln in enumerate(lines) if ln.strip() == REGISTRY_SECTION]
    for i in starts:
        end = next((j for j in range(i + 1, len(lines)) if lines[j].lstrip().startswith("[")), len(lines))
        table = tomllib.loads("".join(lines[i:end])).get("kb", [{}])[0]
        blocks.append((i, end, table))
    return blocks


def _read(path: Path) -> tuple[str, list[str]]:
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ActionError(f"{path.name} cannot be read as TOML ({exc}); left as it is") from exc
    return text, text.splitlines(keepends=True)


def entries(path: Path) -> list[dict[str, Any]]:
    """The ``[[kb]]`` tables of the registry (``[]`` without a file)."""
    _, lines = _read(Path(path))
    return [table for _, _, table in _kb_blocks(lines)]


def marker(path: Path, kb_id: str) -> str | None:
    """The ``build_id`` the registry names for ``kb_id`` (None: no entry, or no valid build id)."""
    for table in entries(path):
        if table.get("id") == kb_id:
            return marker_of(table.get("build_id"))
    return None


@contextlib.contextmanager
def _exclusive(directory: Path) -> Iterator[None]:
    ensure_dir(directory)
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def upsert(path: Path, kb_id: str, kb_path: str, build_id: str) -> str:
    """Make ``[[kb]] id/path/build_id`` the entry of ``kb_id`` (replace in place, else append); returns the detail."""
    path = Path(path)
    block = (f"[[kb]]\nid = {_q(kb_id)}\npath = {_q(kb_path)}\nbuild_id = {_q(build_id)}\n")
    with _exclusive(path.parent):
        text, lines = _read(path)
        for start, end, table in _kb_blocks(lines):
            if table.get("id") == kb_id:
                tail = "\n" if end - start > 1 and lines[end - 1].strip() == "" else ""
                new = "".join(lines[:start]) + block + tail + "".join(lines[end:])
                break
        else:
            new = text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text.strip() else "") + block
        atomic_write(path, new.encode("utf-8"))
    return f"{kb_id} -> build {build_id}"


def _q(value: str) -> str:
    return json.dumps(value)
