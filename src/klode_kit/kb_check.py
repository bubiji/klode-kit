"""Minimal ``/klode-kit:kb-check`` entry (WI-00d2 spike; the build checks themselves are WI-13a).

``klode-kit kb-check --kb <kb_root> --entry <entry>``  (``kb-check --doctor [args]`` runs ``klode-kit doctor``):

- ``<kb_root>/current`` must be a symlink to an existing ``builds/<build_id>`` - otherwise exit 30 (the kb is not
  published; r2.6 contract/stale input), nothing written;
- writes ``builds/<build_id>/check_receipt.json`` (schema ``check_receipt``, validated, atomic) and prints it.
  Only the ``plugin`` block is real here (R2-11): ``{name, version}`` from ``.claude-plugin/plugin.json`` of the
  plugin root, ``entry`` as passed by the caller (the SKILL.md passes ``skill:/klode-kit:kb-check``), ``root_sha``.
  The build checks are reported as one ``WARN`` "not run in the WI-00d2 spike" and the MCP states as not run, so
  ``u5_ready`` is false.

``root_sha`` (R3-08 release sha): over the run files - ``.claude-plugin/plugin.json``, ``skills/**``,
``agents/**``, ``workflows/**``, ``hooks/**``, ``bin/**``, ``src/**``, ``prompts/**``, ``schemas/**``,
``config/**``, ``pyproject.toml``, ``uv.lock`` - ``sha256`` of the concatenated
``sha256(relpath + "\\0" + content)`` hex digests in ``sorted(relpath)`` order. ``__pycache__`` directories,
``*.pyc`` and ``.DS_Store`` are build/OS artefacts, not run files, and are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from klode_kit import receipts
from klode_kit.receipts import dumps

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
RUN_FILES = (".claude-plugin/plugin.json", "pyproject.toml", "uv.lock")
RUN_DIRS = ("skills", "agents", "workflows", "hooks", "bin", "src", "prompts", "schemas", "config")
EXIT_UNPUBLISHED = 30


def _is_artefact(rel: Path) -> bool:
    return "__pycache__" in rel.parts or rel.suffix == ".pyc" or rel.name == ".DS_Store"


def run_files(root: Path) -> list[str]:
    root = Path(root)
    rels = [r for r in RUN_FILES if (root / r).is_file()]
    for d in RUN_DIRS:
        base = root / d
        if base.is_dir():
            for path in base.rglob("*"):
                rel = path.relative_to(root)
                if path.is_file() and not _is_artefact(rel):
                    rels.append(rel.as_posix())
    return sorted(rels)


def root_sha(root: Path) -> str:
    total = hashlib.sha256()
    for rel in run_files(root):
        entry = hashlib.sha256(rel.encode("utf-8") + b"\0" + (Path(root) / rel).read_bytes()).hexdigest()
        total.update(entry.encode("ascii"))
    return total.hexdigest()


def plugin_block(root: Path, entry: str) -> dict:
    manifest = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {"name": manifest["name"], "version": manifest["version"], "entry": entry, "root_sha": root_sha(root)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--doctor" in argv:  # `/kb-check --doctor` (design r2.5): the doctor with the remaining arguments
        from klode_kit.check import doctor

        return doctor.main([a for a in argv if a != "--doctor"])
    parser = argparse.ArgumentParser(prog="klode-kit kb-check", description="check receipt with the plugin block")
    parser.add_argument("--kb", type=Path, required=True)
    parser.add_argument("--entry", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    kb = args.kb.resolve()
    current = kb / "current"
    build_dir = Path(os.path.realpath(current)) if current.is_symlink() else None
    if build_dir is None or not build_dir.is_dir() or build_dir.parent != kb / "builds":
        print(f"kb-check: {kb} is not published (no valid current -> builds/<build_id>)", file=sys.stderr)
        return EXIT_UNPUBLISHED
    receipt = {
        "schema": "klode-kit.check_receipt/1",
        "build_id": build_dir.name,
        "kb_id": kb.name,
        "status": "WARN",
        "at": _now(),
        "checks": [{"name": "build_checks", "status": "WARN", "detail": "not run in the WI-00d2 spike (WI-13a)"}],
        "mcp": {"mcp_config_written": False, "mcp_direct_smoke": False, "mcp_host_visible": "not_run"},
        "u5_ready": False,
        "plugin": plugin_block(PLUGIN_ROOT, args.entry),
    }
    try:
        receipts.write(build_dir / "check_receipt.json", receipt, "check_receipt")
    except receipts.ReceiptInvalid as exc:
        print(f"kb-check: {build_dir.name} is not a published build: {exc}", file=sys.stderr)
        return EXIT_UNPUBLISHED
    sys.stdout.write(dumps(receipt).decode("utf-8"))
    return 0
