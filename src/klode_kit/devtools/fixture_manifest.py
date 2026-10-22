"""MANIFEST for the synthetic fixtures tree: ``python -m klode_kit.devtools.fixture_manifest``.

Format (read by ``privacy_scan`` and the cloud sender's sha allowlist)::

    {"schema": "klode-kit.fixtures-manifest/1",
     "files": [{"path": "<relative to the fixtures root>", "sha256": "...", "bytes": N}, ...]}

Every file under the root is listed (generators and README included), sorted by path,
except ``MANIFEST.json`` itself, ``__pycache__/`` contents and ``.DS_Store``.

Subcommands:
- ``verify [--root R] [--json]``: exit 0 when the tree and MANIFEST agree both ways and
  every sha matches; otherwise exit 1 and list each problem as
  ``missing_file`` / ``not_in_manifest`` / ``sha_mismatch`` (one per path).
- ``write [--root R]``: rewrite MANIFEST.json from the tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "klode-kit.fixtures-manifest/1"
MANIFEST_NAME = "MANIFEST.json"
SKIP_PARTS = {"__pycache__"}
SKIP_NAMES = {".DS_Store"}
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def tree_files(root: Path) -> list[str]:
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_PARTS for part in rel.parts) or path.name in SKIP_NAMES:
            continue
        if path.is_file() and rel.as_posix() != MANIFEST_NAME:
            out.append(rel.as_posix())
    return out


def entries_for(files: dict[str, bytes]) -> dict:
    """MANIFEST document for an in-memory tree ``{relpath: content}``."""
    rows = [
        {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for rel, data in sorted(files.items())
        if rel != MANIFEST_NAME
    ]
    return {"schema": SCHEMA, "files": rows}


def dumps(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def build(root: Path) -> dict:
    return entries_for({rel: (root / rel).read_bytes() for rel in tree_files(root)})


def verify(root: Path) -> list[dict]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return [{"path": MANIFEST_NAME, "problem": "missing_file"}]
    listed = {e["path"]: e["sha256"] for e in json.loads(manifest_path.read_text(encoding="utf-8"))["files"]}
    on_disk = set(tree_files(root))
    problems = []
    for rel in sorted(on_disk | set(listed)):
        if rel not in listed:
            problems.append({"path": rel, "problem": "not_in_manifest"})
        elif rel not in on_disk:
            problems.append({"path": rel, "problem": "missing_file"})
        elif hashlib.sha256((root / rel).read_bytes()).hexdigest() != listed[rel]:
            problems.append({"path": rel, "problem": "sha_mismatch"})
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev fixture-manifest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p_verify.add_argument("--json", action="store_true")
    p_write = sub.add_parser("write")
    p_write.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)

    if args.cmd == "write":
        (args.root / MANIFEST_NAME).write_bytes(dumps(build(args.root)))
        return 0
    problems = verify(args.root)
    if args.json:
        print(json.dumps({"problems": problems}, ensure_ascii=False))
    else:
        for p in problems:
            print(f"{p['path']}: {p['problem']}")
        print(f"fixture_manifest: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
