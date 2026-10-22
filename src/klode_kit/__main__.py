"""``python -m klode_kit`` / ``bin/klode-kit``: subcommand registry.

Subcommands are registered as ``"<group> <name>": "<module>:<function>"`` and imported
lazily, so a broken optional module cannot break unrelated commands.
"""

from __future__ import annotations

import importlib
import sys

from klode_kit import __version__
from klode_kit.seams import startup

USAGE_EXIT = 2

COMMANDS: dict[str, tuple[str, str]] = {
    "dev lane": ("klode_kit.devtools.run_lane:main", "run one test lane and write work/lanes/<lane>.json"),
    "dev privacy-scan": ("klode_kit.devtools.privacy_scan:main", "scan the repository for personal data"),
    "dev coverage-gate": ("klode_kit.devtools.coverage_gate:main", "judge a coverage.py JSON report"),
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
            return int(getattr(importlib.import_module(module), func)(argv[words:]) or 0)
    print(f"klode-kit: unknown command: {' '.join(argv[:2])}\n\n{usage()}", file=sys.stderr)
    return USAGE_EXIT


if __name__ == "__main__":
    sys.exit(main())
