"""``/klode-kit:kb-check`` - the whole-library check of one build directory (WI-10a; design 02 r2.6 / r3.2, R2-11).

``klode-kit kb-check --kb <kb_root> --entry <entry> [--build <id|dir>] [--project DIR] [--host-state S]``
(``kb-check --doctor [args]`` runs ``klode-kit doctor``). The build checked is ``--build`` or the newest
``<kb_root>/builds/<build_id>``: **check reads the build directory, never ``current``**, and a build is judged
before anything points to it (K5). Nothing is written outside that build: ``check_receipt.json`` (schema
``check_receipt``, atomic; the read-only root is opened for the one replace and sealed again; the file is not part
of ``MANIFEST.sha256``, like the manifest itself) and the same JSON on stdout.

Criteria, in the order of the receipt (a line each, FAIL details start with the way back ``L1:`` / ``L2:`` / ``L3:``
or the exit code ``40:``, classification as in ``klode_kit.loops``):

- ``build_receipt`` the 13 steps PASS and the receipt names this directory; ``manifest`` the tree equals its
  ``MANIFEST.sha256`` (exit 30 otherwise); ``write_receipt`` not forced, status done (03 B2);
- ``cards`` .. ``card_provenance`` gate 1 again on every published card (``check_published``, 03 B6): ``zoom`` all
  ``full`` (a stub FAILs, L2); ``aliases`` authored >= 10 / deterministic >= 1 (L2); ``anchors`` authored Full >= 5,
  deterministic coverage 100 percent, records shelf included (L2); ``anchor_text`` every anchor verbatim in its source
  (folded punctuation FAILs, L3); ``card_provenance`` marker, source, write receipt entry, not stale, not forced (L2);
- ``strict`` ``klode check --strict`` 0 errors, 0 warnings (citation-rot only: L3, else L2);
- ``desk`` syntheses and ``_diagnostics.md``; ``holdout`` the frozen holdout, >= ``desk.holdout_min`` questions and
  >= ``desk.holdout_pass`` hit (L1) - judged again here, the tuning set only reported;
- ``smoke_mcp`` the six checks of ``smoke_mcp.run`` against the real ``klode-mcp`` (L2).

A build that failed (or whose tree changed) is judged by the first three lines only. A directory without
``build_receipt.json`` is not a build: one WARN line, exit 0, ``u5_ready`` false (the WI-00d2 spike's behaviour).
``klode`` / ``klode-mcp`` missing: exit 40. Exit: 0 pass (warnings allowed), 20 content gate, 30 no such build /
contract (also a changed tree), 40 environment, 50 no progress (the same problems as the previous build's check).

``plugin`` block (R2-11): ``{name, version}`` from ``.claude-plugin/plugin.json``, ``entry`` as passed (the SKILL.md
passes ``skill:/klode-kit:kb-check``), ``root_sha`` (R3-08 release sha).

``root_sha``: the release sha of ``klode_kit.devtools.release_manifest`` (WI-15b: the single definition - the run files
of R3-08, ``sha256`` of the concatenated ``sha256(relpath + "\\0" + content)`` hex digests in ``sorted(relpath)`` order;
byte-code caches, ``.DS_Store`` and ``*.egg-info`` are not run files).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from klode_kit import config as config_mod
from klode_kit import loops, receipts
from klode_kit.build import check_cards, check_published, desk_build, run_build, smoke_mcp, write_card
from klode_kit.contract import schema as contract
from klode_kit.devtools import release_manifest
from klode_kit.pipeline import write_receipt_verdict
from klode_kit.receipts import dumps
from klode_kit.seams import crashpoint

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def run_files(root: Path) -> list[str]:
    return release_manifest.run_files(Path(root))


def root_sha(root: Path) -> str:
    """The release sha (WI-15b: one definition, ``devtools.release_manifest``)."""
    return release_manifest.release_sha(Path(root))


def plugin_block(root: Path, entry: str) -> dict:
    manifest = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {"name": manifest["name"], "version": manifest["version"], "entry": entry, "root_sha": root_sha(root)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- WI-10a: the build checks

CHECK_RECEIPT = "check_receipt.json"
BUILD_ID_RE = re.compile(r"^b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
HOST_STATES = ("connected", "pending", "failed", "not_run")
STRICT_OK = "0 errors, 0 warning(s)"
STRICT_LINE = re.compile(r"^\s*(?:ERROR|WARN\w*)\s+\[(?P<tag>\w+)\s+(?P<code>[^\]]+?)\]\s+(?P<path>\S+?):")
ROT_CODES = ("citation-rot", "citation-rot?")
EXIT_CONTENT, EXIT_CONTRACT, EXIT_ENV, EXIT_NO_PROGRESS = 20, 30, 40, 50
SHOWN = 12  # problems named in one detail line

_L2_CODES = ("ZOOM", "F1", "F2", "F3", "MIN_FULL", "COV", "MARKER", "RECEIPT", "STALE", "FORCED", "MISSING", "SRC",
             "POLICY")
_L3_CODES = ("A1", "A2", "A3", *ROT_CODES)
TARGETS: dict[str, str | int] = {
    **{code: "L2" for code in _L2_CODES},
    **{code: "L3" for code in _L3_CODES},
    "HOLDOUT": "L1",
    "NO_KLODE": EXIT_ENV,
    "NO_KLODE_MCP": EXIT_ENV,
    "NO_PROGRESS": EXIT_NO_PROGRESS,
}
# which check reports which card-judge codes (zoom / aliases / anchors / anchor_text / card_provenance)
CARD_GROUPS = (
    ("zoom", ("ZOOM",)),
    ("aliases", ("F1",)),
    ("anchors", ("F2", "F3", "MIN_FULL", "COV")),
    ("anchor_text", ("A1", "A2", "A3")),
    ("card_provenance", ("MARKER", "MISSING", "SRC", "POLICY", "RECEIPT", "STALE", "FORCED")),
)


def target_of(code: str) -> str | int:
    """Where a failed criterion goes back to: ``"L1"`` / ``"L2"`` / ``"L3"`` (the repair loops of WI-09c) or the exit
    code 40 (environment) / 50 (no progress). ``KeyError`` for a code nobody mapped."""
    return TARGETS[code]


@dataclass(frozen=True)
class Check:
    """One line of the receipt. ``loop`` is the way back (also the ``"Ln: "`` prefix of ``detail``); ``exit`` forces
    the run's exit code (40 environment, 30 contract)."""

    name: str
    status: str
    detail: str = ""
    loop: str | None = None
    exit: int | None = None

    def as_dict(self) -> dict:
        out = {"name": self.name, "status": self.status}
        if self.detail:
            out["detail"] = self.detail
        return out


def _field(check: Any, key: str) -> Any:
    return check.get(key) if isinstance(check, dict) else getattr(check, key, None)


def failure_signature(checks: Iterable[Any]) -> str:
    """sha256 over the sorted ``(name, detail)`` of the FAIL lines - the same problems give the same signature."""
    rows = sorted([_field(c, "name"), _field(c, "detail") or ""] for c in checks if _field(c, "status") == "FAIL")
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def exit_code_for(checks: Iterable[Check], *, previous_signature: str | None = None) -> int:
    """0 when nothing FAILs (warnings do not count); else the forced code (40 before 50 before 30), 50 when the
    problems are exactly those of the previous build's check (no progress), else 20 (content gate)."""
    checks = list(checks)
    fails = [c for c in checks if c.status == "FAIL"]
    if not fails:
        return 0
    forced = {c.exit for c in fails if c.exit}
    for code in (EXIT_ENV, EXIT_NO_PROGRESS, EXIT_CONTRACT):
        if code in forced:
            return code
    if forced:
        return min(forced)
    if previous_signature is not None and failure_signature(checks) == previous_signature:
        return EXIT_NO_PROGRESS
    return EXIT_CONTENT


def _fail(name: str, loop: str | None, text: str, exit: int | None = None) -> Check:
    prefix = f"{loop}: " if loop else (f"{exit}: " if exit else "")
    return Check(name, "FAIL", prefix + text, loop, exit)


def _named(items: list[str]) -> str:
    more = f" (+{len(items) - SHOWN} more)" if len(items) > SHOWN else ""
    return "; ".join(items[:SHOWN]) + more


def card_checks(build_dir: Any, *, min_aliases: int = 10, min_anchors_full: int = 5) -> list[Check]:
    """The whole-library criteria over ``library/cards`` of a build (design r2.6 / r3, 03 B6): the judge is
    ``check_published`` (gate 1 again on what was published), reported as named lines so each failure has a way back."""
    build = Path(build_dir)
    doc = check_published.check_published(build, min_aliases=min_aliases, min_anchors_full=min_anchors_full)
    cards = doc["cards"]
    if not cards:
        return [_fail("cards", "L2", "no published card in library/cards")]
    out = [Check("cards", "PASS", f"{len(cards)} cards")]
    for name, codes in CARD_GROUPS:
        items = []
        for card in cards:
            for problem in card["problems"]:
                code, _, message = problem.partition(" ")
                if code in codes:
                    items.append(f"{card['id']} {code}" + (f" ({message[:100]})" if message else ""))
        loops_ = {TARGETS[c] for c in codes}
        out.append(_fail(name, sorted(loops_)[0], _named(items)) if items else Check(name, "PASS"))
    return out


def assemble(build_id: str, kb_id: str, checks: Iterable[Check], *, plugin: dict, host: str = "not_run",
             probe_build_id: str | None = None, at: str | None = None) -> dict:
    """The ``check_receipt`` document: the status is the worst line (a host that is not connected is a warning, never
    a failure); ``u5_ready`` needs a connected host, a probe that read this build and no FAIL (r4.2)."""
    checks = list(checks)
    lines = [c.as_dict() for c in checks]
    if host in ("pending", "failed"):
        hint = ("run claude once in the data project directory to accept the trust dialog" if host == "pending"
                else "the host reports the MCP server as failed")
        lines.append({"name": "mcp_host_visible", "status": "WARN", "detail": f"{host}: {hint}"})
    statuses = {line["status"] for line in lines}
    status = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    smoke = any(c.name == "smoke_mcp" and c.status == "PASS" for c in checks)
    ready = host == "connected" and probe_build_id == build_id and status != "FAIL"
    doc: dict[str, Any] = {
        "schema": "klode-kit.check_receipt/1", "build_id": build_id, "kb_id": kb_id, "status": status,
        "at": at or _now(), "checks": lines,
        "mcp": {"mcp_config_written": False, "mcp_direct_smoke": smoke, "mcp_host_visible": host},
        "u5_ready": ready, "plugin": plugin,
    }
    if ready:
        doc["probe"] = {"build_id": build_id}
    return doc


# --------------------------------------------------------------------------- the checks that run tools


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_receipt_check(build: Path) -> Check:
    doc = _json(build / "build_receipt.json")
    if not isinstance(doc, dict):
        return _fail("build_receipt", None, "build_receipt.json is missing or not JSON", EXIT_CONTRACT)
    problems = contract.issues("build_receipt", doc)
    if problems:
        return _fail("build_receipt", None, f"build_receipt.json violates its contract: {problems[0]}", EXIT_CONTRACT)
    if doc.get("build_id") != build.name:
        return _fail("build_receipt", None, f"build_receipt says {doc.get('build_id')}, the directory is {build.name}",
                     EXIT_CONTRACT)
    if doc.get("status") == "PASS":
        return Check("build_receipt", "PASS", f"{len(doc['steps'])} steps PASS")
    step, detail, code = loops.failed_step(build)
    problems_ = loops.classify(build, loopable_only=False)
    loop = next((p["loop"] for p in problems_ if p.get("loop")), "L2") if code == EXIT_CONTENT else None
    forced = None if code == EXIT_CONTENT else code
    return _fail("build_receipt", loop, f"the build failed at step {step} (exit {code}): {detail}"[:400], forced)


def manifest_check(build: Path) -> Check:
    problems = run_build.verify_manifest(build)
    if not problems:
        return Check("manifest", "PASS", "MANIFEST.sha256 matches the tree")
    return _fail("manifest", None, "the tree no longer matches MANIFEST.sha256: " + _named(problems), EXIT_CONTRACT)


def write_receipt_check(build: Path) -> Check:
    doc = _json(build / write_card.RECEIPT_NAME)
    if not isinstance(doc, dict):
        return _fail("write_receipt", "L2", "no readable write_receipt.json (the cards were not written by write-card)")
    if write_receipt_verdict(doc) == "PASS":
        return Check("write_receipt", "PASS", f"{len(doc.get('written', []))} cards written, not forced")
    return _fail("write_receipt", "L2", f"write receipt: status {doc.get('status')}, forced {doc.get('forced')}")


def strict_check(build: Path, klode: str | None) -> Check:
    if not klode:
        return _fail("strict", None, "klode not found on PATH (install klode 0.5.0)", target_of("NO_KLODE"))
    proc = subprocess.run([klode, "-c", str(build / "library.toml"), "check", "--strict"], capture_output=True,
                          text=True, timeout=300)
    lines = proc.stdout.strip().splitlines()
    last = lines[-1] if lines else ""
    if proc.returncode == 0 and STRICT_OK in last:
        return Check("strict", "PASS", STRICT_OK)
    items, rot = [], True
    for line in lines:
        m = STRICT_LINE.match(line)
        if m:
            code = m.group("code").strip()
            rot = rot and code in ROT_CODES
            items.append(f"{m.group('path')} {code}")
    if not items:
        items, rot = [f"klode check --strict exit {proc.returncode}: {last}"[:200]], False
    return _fail("strict", "L3" if rot else "L2", _named(items))


def desk_checks(build: Path, cfg: Any, klode: str | None) -> list[Check]:
    if not klode:
        text = "klode not found on PATH (install klode 0.5.0)"
        return [_fail("desk", None, text, target_of("NO_KLODE")), _fail("holdout", None, text, target_of("NO_KLODE"))]
    try:
        spec = desk_build.load(build)
    except desk_build.DeskError as exc:
        return [_fail("desk", "L2", str(exc)), _fail("holdout", "L1", f"no desk to score: {exc}")]
    with tempfile.TemporaryDirectory(prefix="kb-check-desk-") as tmp, contextlib.redirect_stdout(io.StringIO()):
        try:
            desk_build.verify(build, spec, receipt=Path(tmp) / "desk_receipt.json", holdout_min=cfg["desk.holdout_min"],
                              pass_ratio=cfg["desk.holdout_pass"])
        except desk_build.DeskError as exc:
            env = exc.code if exc.code == EXIT_ENV else None
            return [_fail("desk", None if env else "L2", str(exc), env), _fail("holdout", None if env else "L1", str(exc), env)]
        except OSError as exc:  # a build without its frozen holdout cannot be frozen now (the tree is read-only)
            return [_fail("desk", "L2", f"desk verify could not run: {exc}"), _fail("holdout", "L1", f"{exc}")]
        rec = _json(Path(tmp) / "desk_receipt.json")
    if not isinstance(rec, dict):
        return [_fail("desk", "L2", "desk verify wrote no receipt"), _fail("holdout", "L1", "desk verify wrote no receipt")]
    problems = [n for n, ok in (("synthesis", rec["synthesis"]["pass"]), ("diagnostics", rec["diagnostics"]["current"])) if not ok]
    desk = _fail("desk", "L2", "desk " + ", ".join(problems) + " not passing") if problems else Check(
        "desk", "PASS", "syntheses pass, _diagnostics.md current")
    h = rec["holdout"]
    if desk_build.holdout_gate(h["hit"], h["total"], holdout_min=cfg["desk.holdout_min"], pass_ratio=cfg["desk.holdout_pass"]):
        holdout = Check("holdout", "PASS", f"{h['hit']}/{h['total']}")
    else:
        holdout = _fail("holdout", "L1", f"HOLDOUT {h['hit']}/{h['total']} (needs >= {cfg['desk.holdout_min']} questions "
                        f"and >= {cfg['desk.holdout_pass']:.2f} hit)")
    return [desk, holdout]


def smoke_target(build: Path) -> tuple[str | None, str]:
    for path in sorted((build / "library" / "cards").glob("*.md")):
        if path.stem == "INDEX":
            continue
        found = check_cards.ANCHOR_RE.findall(path.read_text(encoding="utf-8"))
        if found:
            return path.stem, found[0][0] if isinstance(found[0], tuple) else found[0]
    return None, ""


def smoke_check(build: Path, klode_mcp: str | None) -> Check:
    if not klode_mcp:
        return _fail("smoke_mcp", None, "klode-mcp not found on PATH (install klode 0.5.0)", target_of("NO_KLODE_MCP"))
    card, phrase = smoke_target(build)
    if card is None:
        return _fail("smoke_mcp", "L2", "no card with an anchor to smoke-test")
    try:
        results = smoke_mcp.run(build, card, phrase, exe=klode_mcp)
    except (OSError, subprocess.SubprocessError) as exc:
        return _fail("smoke_mcp", "L2", f"klode-mcp could not be run: {exc}")
    failed = [r.label for r in results if not r.ok]
    if failed:
        return _fail("smoke_mcp", "L2", f"{card}: " + _named(failed))
    return Check("smoke_mcp", "PASS", f"{len(results)} checks on {card}")


# --------------------------------------------------------------------------- one run


def builds_of(kb: Path) -> list[Path]:
    root = kb / "builds"
    return sorted(p for p in root.iterdir() if p.is_dir() and BUILD_ID_RE.match(p.name)) if root.is_dir() else []


def previous_signature(kb: Path, build: Path) -> str | None:
    """Signature of the FAIL lines of the newest older build's check receipt (None when it has none / passed)."""
    for older in reversed([b for b in builds_of(kb) if b.name < build.name]):
        doc = _json(older / CHECK_RECEIPT)
        if isinstance(doc, dict) and isinstance(doc.get("checks"), list):
            return failure_signature(doc["checks"]) if doc.get("status") == "FAIL" else None
    return None


def run_checks(build: Path, *, cfg: Any, klode: str | None, klode_mcp: str | None) -> list[Check]:
    """All criteria of one build directory, in the fixed order of the receipt."""
    head = [build_receipt_check(build), manifest_check(build), write_receipt_check(build)]
    if head[0].status == "FAIL" or head[1].status == "FAIL":  # not a sound build: nothing more to judge
        return head
    cards = card_checks(build, min_aliases=cfg["cards.min_aliases"], min_anchors_full=cfg["cards.min_anchors_full"])
    if cards[0].status == "FAIL":
        return head + cards
    return head + cards + [strict_check(build, klode), *desk_checks(build, cfg, klode), smoke_check(build, klode_mcp)]


def _write_receipt(build: Path, doc: dict) -> None:
    """Into the (read-only) build: the root is made writable for the one atomic replace, then sealed again. The sealed
    mode is the current one *without* its write bits - a run that was killed between the two steps leaves the root
    writable, and the next run must not take that for the mode to restore (publish refuses a writable build, K5)."""
    sealed = build.stat().st_mode & ~0o222
    os.chmod(build, sealed | stat.S_IWUSR)
    crashpoint("check.unsealed")
    try:
        receipts.write(build / CHECK_RECEIPT, doc, "check_receipt")
        crashpoint("check.written")
    finally:
        os.chmod(build, sealed)
    target = build / CHECK_RECEIPT
    os.chmod(target, target.stat().st_mode & ~0o222)


def run_check(build_dir: Any, *, entry: str, project: Any = None, host: str = "not_run",
              probe_build_id: str | None = None, write: bool = True) -> tuple[int, dict]:
    """Check one build directory; returns ``(exit code, receipt)`` and writes ``<build>/check_receipt.json``."""
    build = Path(build_dir).resolve()
    kb = build.parent.parent
    cfg = config_mod.load(project)
    plugin = plugin_block(PLUGIN_ROOT, entry)
    if (build / "build_receipt.json").is_file():
        checks = run_checks(build, cfg=cfg, klode=shutil.which("klode"), klode_mcp=shutil.which("klode-mcp"))
        code = exit_code_for(checks, previous_signature=previous_signature(kb, build))
    else:  # not a build of the 13 steps (the WI-00d2 spike's bare directory): nothing can be judged, nothing passes
        checks = [Check("build_checks", "WARN", "no build_receipt.json in this directory: build checks not run")]
        code = 0
    doc = assemble(build.name, kb.name, checks, plugin=plugin, host=host, probe_build_id=probe_build_id)
    if write:
        _write_receipt(build, doc)
    return code, doc


def pick_build(kb: Path, wanted: str | None) -> Path | None:
    if wanted:
        path = Path(wanted)
        path = path if path.is_absolute() or path.exists() else kb / "builds" / wanted
        return path.resolve() if path.is_dir() and path.resolve().parent == (kb / "builds").resolve() else None
    found = builds_of(kb)
    return found[-1] if found else None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--doctor" in argv:  # `/kb-check --doctor` (design r2.5): the doctor with the remaining arguments
        from klode_kit.check import doctor

        return doctor.main([a for a in argv if a != "--doctor"])
    parser = argparse.ArgumentParser(prog="klode-kit kb-check", description="whole-library check of a build directory")
    parser.add_argument("--kb", type=Path, required=True, help="kb root (kb/<id>) holding builds/")
    parser.add_argument("--entry", required=True, help="how it was invoked, recorded as plugin.entry")
    parser.add_argument("--build", default=None, help="build id or directory (default: the newest under builds/)")
    parser.add_argument("--project", type=Path, default=None, help="data project with klode-kit.toml overrides")
    parser.add_argument("--host-state", choices=HOST_STATES, default="not_run",
                        help="the host's view of the MCP server (r4.2, set by the publish step)")
    parser.add_argument("--probe-build-id", default=None, help="build_id the direct probe read (r4.2)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    kb = args.kb.resolve()
    build = pick_build(kb, args.build)
    if build is None:
        print(f"kb-check: no build to check in {kb / 'builds'}" + (f" ({args.build})" if args.build else ""),
              file=sys.stderr)
        return EXIT_CONTRACT
    project = args.project
    if project is None and kb.parent.name == "kb" and (kb.parent.parent / "klode-kit.toml").is_file():
        project = kb.parent.parent
    try:
        code, doc = run_check(build, entry=args.entry, project=project, host=args.host_state,
                              probe_build_id=args.probe_build_id)
    except (receipts.ReceiptInvalid, OSError, config_mod.ConfigError) as exc:
        print(f"kb-check: {build.name}: {exc}", file=sys.stderr)
        return EXIT_CONTRACT
    for line in doc["checks"]:
        print(f"kb-check: {line['status']:<4} {line['name']}" + (f"  {line['detail']}" if line.get("detail") else ""),
              file=sys.stderr)
    sys.stdout.write(dumps(doc).decode("utf-8"))
    return code
