"""Release manifest (WI-15b, R3-08): the one definition of ``release_sha``.

``release_sha`` = sha256 over the concatenation of ``sha256(relpath + "\\0" + content)`` hex digests of the run
files, taken in ``sorted(relpath)`` order. The run files are the resources that ship and run:

    templates/** scripts/install.sh config/** prompts/** schemas/** hooks/** workflows/** agents/** skills/**
    bin/** src/** plugin.json pyproject.toml uv.lock

Where the repository keeps a resource decides the pattern: ``templates/``, ``config/``, ``prompts/`` and
``schemas/`` live inside the package (``src/klode_kit/<name>/``, so ``src/**`` covers them as well; WI-00b2 moved
``config`` there); a top-level directory of the same name would count too. ``plugin.json`` is
``.claude-plugin/plugin.json``. Byte-code caches, ``.DS_Store`` and ``*.egg-info`` are build/OS artefacts, not run
files. ``RELEASE.sha256`` itself and everything else (docs, tests, ``work/``) is outside the list.

Used by ``kb_check`` (the host receipt's ``root_sha``), ``run_lane`` (``env_fingerprint.code_tree_sha``) and
``release_check`` - one definition, so a receipt made for another tree can never be mistaken for this one.

``RELEASE.sha256`` format: first line ``release_sha <64 hex>``, then one line per run file
``<sha256(relpath NUL content)>  <relpath>`` in sorted order.

CLI: ``python -m klode_kit.devtools.release_manifest [--root DIR] (--write | --check | --print-sha)``.
Exit: 0 ok; 20 a glob matches no file (``--write``/``--check``) or the file is missing/stale (``--check``); 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

MANIFEST_NAME = "RELEASE.sha256"
CHECK_EXIT = 20
USAGE_EXIT = 2

# glob name (as written in R3-08) -> patterns, each either an exact file or ``<dir>/**``
GLOBS: dict[str, tuple[str, ...]] = {
    "templates/**": ("templates/**", "src/klode_kit/templates/**"),
    "scripts/install.sh": ("scripts/install.sh",),
    "config/**": ("config/**", "src/klode_kit/config/**"),
    "prompts/**": ("prompts/**", "src/klode_kit/prompts/**"),
    "schemas/**": ("schemas/**", "src/klode_kit/schemas/**"),
    "hooks/**": ("hooks/**",),
    "workflows/**": ("workflows/**",),
    "agents/**": ("agents/**",),
    "skills/**": ("skills/**",),
    "bin/**": ("bin/**",),
    "src/**": ("src/**",),
    "plugin.json": (".claude-plugin/plugin.json",),
    "pyproject.toml": ("pyproject.toml",),
    "uv.lock": ("uv.lock",),
}

_LINE = re.compile(r"^([0-9a-f]{64})  (\S.*)$")
_HEAD = re.compile(r"^release_sha ([0-9a-f]{64})$")


class UncoveredGlobs(Exception):
    """A glob of the release definition matches no file."""

    def __init__(self, names: list[str]):
        self.names = names
        super().__init__("release globs matching no file: " + ", ".join(names))


def _is_artefact(rel: Path) -> bool:
    return (any(p == "__pycache__" or p.endswith(".egg-info") for p in rel.parts)
            or rel.suffix == ".pyc" or rel.name == ".DS_Store")


def matches(rel: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            if rel.startswith(pat[:-2]):
                return True
        elif rel == pat:
            return True
    return False


def _roots() -> list[str]:
    seen: list[str] = []
    for patterns in GLOBS.values():
        for pat in patterns:
            top = pat.split("/", 1)[0]
            if top not in seen:
                seen.append(top)
    return seen


def run_files(root: Path) -> list[str]:
    """Sorted relative POSIX paths of the run files below ``root`` (files only, artefacts skipped)."""
    root = Path(root)
    found: set[str] = set()
    for top in _roots():
        base = root / top
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = [p for p in base.rglob("*") if p.is_file()]
        else:
            continue
        for path in candidates:
            rel = path.relative_to(root)
            if not _is_artefact(rel) and matches(rel.as_posix(), (p for ps in GLOBS.values() for p in ps)):
                found.add(rel.as_posix())
    return sorted(found)


def entries(root: Path) -> list[tuple[str, str]]:
    root = Path(root)
    return [(rel, hashlib.sha256(rel.encode("utf-8") + b"\0" + (root / rel).read_bytes()).hexdigest())
            for rel in run_files(root)]


def _sha_of(items: list[tuple[str, str]]) -> str:
    total = hashlib.sha256()
    for _rel, digest in items:
        total.update(digest.encode("ascii"))
    return total.hexdigest()


def release_sha(root: Path) -> str:
    return _sha_of(entries(root))


def uncovered(root: Path, rels: list[str] | None = None) -> list[str]:
    rels = run_files(root) if rels is None else rels
    return [name for name, patterns in GLOBS.items() if not any(matches(r, patterns) for r in rels)]


def render(root: Path) -> str:
    items = entries(root)
    lines = [f"release_sha {_sha_of(items)}"] + [f"{digest}  {rel}" for rel, digest in items]
    return "\n".join(lines) + "\n"


def parse(text: str) -> tuple[str, list[str]]:
    """``(release_sha, run-file list)``; ValueError for anything that is not a well-formed RELEASE.sha256."""
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty RELEASE.sha256")
    head = _HEAD.match(lines[0])
    if not head:
        raise ValueError("first line must be 'release_sha <64 hex>'")
    items: list[tuple[str, str]] = []
    for n, line in enumerate(lines[1:], 2):
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"line {n}: expected '<64 hex>  <relpath>'")
        items.append((m.group(2), m.group(1)))
    if [r for r, _ in items] != sorted(r for r, _ in items):
        raise ValueError("entries are not sorted by relpath")
    if _sha_of(items) != head.group(1):
        raise ValueError("release_sha does not match the entries")
    return head.group(1), [r for r, _ in items]


def write(root: Path) -> Path:
    """Write ``<root>/RELEASE.sha256`` atomically; refuse (nothing written) when a glob matches no file."""
    root = Path(root)
    missing = uncovered(root)
    if missing:
        raise UncoveredGlobs(missing)
    text = render(root)
    target = root / MANIFEST_NAME
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".RELEASE.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev release-manifest", description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write RELEASE.sha256 (default)")
    mode.add_argument("--check", action="store_true", help="exit 20 unless RELEASE.sha256 is current and every glob is covered")
    mode.add_argument("--print-sha", action="store_true", help="print the release sha only")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    root = args.root
    if not root.is_dir():
        print(f"release_manifest: not a directory: {root}", file=sys.stderr)
        return USAGE_EXIT
    if args.print_sha:
        print(release_sha(root))
        return 0
    if args.check:
        missing = uncovered(root)
        if missing:
            print("release_manifest: globs matching no file: " + ", ".join(missing), file=sys.stderr)
            return CHECK_EXIT
        target = root / MANIFEST_NAME
        if not target.is_file():
            print(f"release_manifest: {MANIFEST_NAME} is missing (run with --write)", file=sys.stderr)
            return CHECK_EXIT
        if target.read_text(encoding="utf-8") != render(root):
            print(f"release_manifest: {MANIFEST_NAME} is stale (stale: the run files changed; run with --write)", file=sys.stderr)
            return CHECK_EXIT
        print(f"release_manifest: current ({release_sha(root)})")
        return 0
    try:
        path = write(root)
    except UncoveredGlobs as exc:
        print(f"release_manifest: {exc}", file=sys.stderr)
        return CHECK_EXIT
    print(f"release_manifest: wrote {path.name} ({release_sha(root)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
