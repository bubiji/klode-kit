"""``python -m klode_kit`` / ``bin/klode-kit``: subcommand registry.

Subcommands are registered as ``"<group> <name>": "<module>:<function>"`` and imported
lazily, so a broken optional module cannot break unrelated commands.
"""

from __future__ import annotations

import importlib
import sys

from klode_kit import __version__
from klode_kit.config import ConfigError
from klode_kit.seams import startup

USAGE_EXIT = 2

COMMANDS: dict[str, tuple[str, str]] = {
    "dev lane": ("klode_kit.devtools.run_lane:main", "run one test lane and write work/lanes/<lane>.json"),
    "dev privacy-scan": ("klode_kit.devtools.privacy_scan:main", "scan the repository for personal data"),
    "dev coverage-gate": ("klode_kit.devtools.coverage_gate:main", "judge a coverage.py JSON report"),
    "config": ("klode_kit.config:main", "config show [--project DIR]: effective configuration as JSON"),
    "hook isolation-guard": ("klode_kit.ocr.isolation_guard:main", "PreToolUse hook: deny file reads outside cwd"),
    "hook privacy-guard": ("klode_kit.ocr.privacy_guard:main",
                           "PreToolUse hook (hooks/hooks.json): claude/codex via Bash only with registered images"),
    "ocr send": ("klode_kit.ocr.send:main", "ocr send --route R --project P --receipt-dir D --isolation-receipt F "
                 "PATH SHA: send one registered image (exit 60/61 on refusal)"),
    "check-cards": ("klode_kit.build.check_cards:main",
                    "check-cards (CARD ... | --all) [--kb DIR] [--json] [--policy P]: gate 1 (format + verbatim "
                    "anchors; policy from the source's shelf)"),
    "write-card": ("klode_kit.build.write_card:main",
                   "write-card KB [ID ...] [--force] [--receipt F]: write cards_content into library/cards below "
                   "the scaffold marker; gate 1 first, FAIL writes nothing (--force: humans only, step fails)"),
    "check-published": ("klode_kit.build.check_published:main",
                        "check-published (CARD ... | --all) [--kb DIR] [--receipt F] [--json]: gate 1 again on "
                        "the published cards below the marker, plus zoom / stale / forced write receipt"),
    "records-to-l1": ("klode_kit.build.records_to_l1:main",
                      "records-to-l1 KB [--json]: deterministic cards_content for the items / records shelves of a "
                      "records_to_txt export (Thin latest + trend, Full one whole-line anchor per line; aliases >= "
                      "10 from deterministic sources or stop)"),
    "split-chapters": ("klode_kit.build.split_by_manifest:main",
                       "split-chapters KB (--whole F --scan [--head-re T] | --manifest M [--apply]): cut a whole "
                       "text into one source per chapter at manifest line numbers (pieces put back == whole)"),
    "skeleton": ("klode_kit.build.skeleton:main", "skeleton KB ID [CHARS]: print a source's section titles and leads"),
    "fix-anchors": ("klode_kit.build.fix_anchors:main",
                    "fix-anchors KB [ID ...]: rewrite NOT FOUND card anchors to the source's own spelling"),
    "desk-build": ("klode_kit.build.desk_build:main",
                   "desk-build KB (--apply | --prompts | --status | --verify [--holdout F] [--refreeze --reason R]): "
                   "the desk - frameworks on, _diagnostics.md from src/desk.json, worker prompts, tuning and frozen "
                   "holdout scored apart (gate: holdout >= 5 questions and >= 4/5)"),
    "check-synthesis": ("klode_kit.build.check_synthesis:main",
                        "check-synthesis KB [DIM ...]: desk synthesis gate S1-S6 (anchors only copied from cards)"),
    "eval-compare": ("klode_kit.build.eval_compare:main",
                     "eval-compare --gold G --kb NAME=TOML [...] [--desk-gold D]: Hit@1/Hit@k/MRR over libraries; "
                     "desk routing via klode --json diagnose"),
    "cue-suggest": ("klode_kit.build.cue_suggest:main",
                    "cue-suggest KB [--receipt F] [--write]: L1 loop - cues from card aliases for missed tuning "
                    "questions (never reads the holdout)"),
    "gen-agent": ("klode_kit.build.gen_agent:main",
                  "gen-agent KB --out F [--template T] [--lang-source L] [--lang-answer L] [--source-kind K] "
                  "[--cite-format C] [--build-id ID] [--mcp NAME] [--kb-root TEXT] [--spec F]: the <id>-klode-expert "
                  "agent for the data project (examples from desk.json tests; none -> exit 20 no_examples)"),
    "smoke-mcp": ("klode_kit.build.smoke_mcp:main",
                  "smoke-mcp KB --card ID --real PHRASE [--query Q] [--desk SYMPTOM=DIM] [--timeout S]: run the "
                  "real klode-mcp (initialize, tools/list, search, zoom, verify real / fake); exit 0 all PASS"),
    "kb-check": ("klode_kit.kb_check:main", "kb-check --kb DIR --entry E: check receipt with the plugin block"),
    "dev spike-pipeline": ("klode_kit.devtools.spike_pipeline:main", "WI-00d2 stage stand-in for the workflow shell"),
    "kb-ocr": ("klode_kit.ocr.run_batch:main", "kb-ocr --batch DIR|PDF [--project DIR] [--routes a,b] [--redo]: OCR an image/PDF inbox"),
    "kb-audit": ("klode_kit.audit.stage:main", "kb-audit --batch BATCH_ID [--project DIR]: align, vote and gate the "
                 "batch's latest runs into audit revision a<N>"),
    "confirm-page": ("klode_kit.audit.confirm_page:main", "confirm-page --batch BATCH_ID [--project DIR] [--rev N]: "
                     "write the local confirm page work/<batch>/audit/a<N>/confirm.html"),
    "views": ("klode_kit.audit.views:main", "views [--project DIR]: rebuild the read-only views records/<date>-<type>.md "
              "and verified/<date>-<type>.md (symlinks to revision bodies)"),
}


def usage() -> str:
    lines = ["usage: klode-kit [--version] <command> [args...]", "", "commands:"]
    lines += [f"  {name:<20} {doc}" for name, (_, doc) in sorted(COMMANDS.items())]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    startup()
    if argv[:1] == ["--version"]:
        print(__version__)
        return 0
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(), file=sys.stdout if argv else sys.stderr)
        return 0 if argv else USAGE_EXIT
    for words in (2, 1):
        key = " ".join(argv[:words])
        if len(argv) >= words and key in COMMANDS:
            target = COMMANDS[key][0]
            module, func = target.split(":")
            try:
                return int(getattr(importlib.import_module(module), func)(argv[words:]) or 0)
            except ConfigError as exc:  # any command: configuration problems are exit 40 (r2.6)
                print(f"klode-kit: configuration error: {exc}", file=sys.stderr)
                return exc.exit_code
    print(f"klode-kit: unknown command: {' '.join(argv[:2])}\n\n{usage()}", file=sys.stderr)
    return USAGE_EXIT


if __name__ == "__main__":
    sys.exit(main())
