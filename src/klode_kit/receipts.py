"""Validated, atomic receipt writes (WI-00b1).

``write(path, data, schema)``:

1. validates ``data`` against the contract ``schema`` and serialises it - an illegal receipt
   raises ``ReceiptInvalid`` before anything touches the disk (no file, no ``.tmp``, no dir);
2. creates missing parent directories one by one, ``crashpoint("receipts.mkdir")`` after each;
3. writes a temporary file in the target directory (``O_CREAT | O_EXCL``, written with
   ``os.write`` and fsynced), then ``crashpoint("receipts.before_replace")``;
4. ``os.replace`` onto the target, then fsyncs the directory.

A crash at any point leaves the target with its complete old content or the complete new one.
Every step between two crash points makes at most one durable write (matrix section 0.3,
00s-16 ``receipts`` <-> 00b1-11), which is why the temporary file is filled through its file
descriptor instead of being reopened.

CLI: ``python -m klode_kit.receipts write --schema <name> <path>`` reads the JSON receipt from
stdin. Exit 0 written; 30 contract violation or unparseable input (nothing written); 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from klode_kit.contract.schema import NAMES, ContractError, Issue, issues
from klode_kit.seams import crashpoint, startup

CONTRACT_EXIT = 30
USAGE_EXIT = 2
CRASHPOINTS = ("receipts.mkdir", "receipts.before_replace")


class ReceiptInvalid(ContractError):
    """The receipt violates its contract (or is not representable as JSON); nothing was written."""


def dumps(data: Any) -> bytes:
    """Canonical serialisation: UTF-8, sorted keys, 2-space indent, trailing newline, no NaN/Infinity."""
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _checked(data: Any, schema: str) -> bytes:
    problems = issues(schema, data)  # UnknownSchema propagates
    if problems:
        raise ReceiptInvalid(schema, problems)
    try:
        return dumps(data)
    except (ValueError, TypeError) as exc:
        raise ReceiptInvalid(schema, [Issue("", f"not representable as JSON: {exc}", "json")]) from exc


def _ensure_dir(directory: Path) -> None:
    missing = []
    cur = directory
    while not cur.is_dir():
        missing.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            if not d.is_dir():
                raise
        crashpoint("receipts.mkdir")


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write(path: str | os.PathLike, data: Any, schema: str) -> Path:
    """Validate ``data`` against ``schema`` and atomically replace ``path`` with it."""
    target = Path(path)
    payload = _checked(data, schema)
    _ensure_dir(target.parent)
    tmp = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        crashpoint("receipts.before_replace")
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise
    _fsync_dir(target.parent)
    return target


def read(path: str | os.PathLike, schema: str) -> Any:
    """Load ``path`` and check it against ``schema``; ``ReceiptInvalid`` if it does not conform."""
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptInvalid(schema, [Issue("", f"not JSON: {exc}", "json")]) from exc
    problems = issues(schema, data)
    if problems:
        raise ReceiptInvalid(schema, problems)
    return data


def main(argv: list[str] | None = None) -> int:
    startup()
    parser = argparse.ArgumentParser(prog="python -m klode_kit.receipts", description="Validated atomic receipt write.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="read a JSON receipt from stdin and write it atomically")
    w.add_argument("--schema", required=True, choices=NAMES)
    w.add_argument("path", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"klode-kit receipts: input is not JSON: {exc}", file=sys.stderr)
        return CONTRACT_EXIT
    try:
        write(args.path, data, args.schema)
    except ReceiptInvalid as exc:
        print(f"klode-kit receipts: {args.schema} contract violated, nothing written:", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue.path or '<root>'}: {issue.message}", file=sys.stderr)
        return CONTRACT_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
