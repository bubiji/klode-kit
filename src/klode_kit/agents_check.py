"""Static check of the plugin's ``agents/*.md`` (WI-12; matrix 12-01, 12-02, 12-06).

    klode-kit check-agents [DIR] [--json]       exit 0 clean; 20 any problem; 2 usage (DIR is not a directory)

Facts from the Claude Code documentation (sub-agents page, https://code.claude.com/docs/en/sub-agents):

- **Plugin subagents ignore ``hooks``, ``mcpServers`` and ``permissionMode``** ("For security reasons, plugin subagents
  don't support the hooks, mcpServers, or permissionMode frontmatter fields. These fields are ignored when loading
  agents from a plugin", section "Choose the subagent scope"). An agent that relied on one would silently lose it, so
  a plugin agent that carries one is a failure here (``forbidden_key``). The ``<kb>-klode-expert`` needs an MCP server and
  therefore lives in the data project (``.claude/agents/``), never in the plugin.
- **Unknown frontmatter keys are ignored without a message** ("Claude Code ignores a field it doesn't recognize without
  reporting an error", section "Supported frontmatter fields"; names are camelCase and "must match the table exactly").
  A typo such as ``disallowed_tools`` would drop a restriction unnoticed, so it is a failure (``unknown_key``).
- **Files the host skips without a word** (section "Subagent files Claude Code skips"): no ``name``; an opening ``---``
  that is not the first line; a ``name`` that starts with ``-``, contains ``:`` or is longer than 256 characters; a
  ``name`` without ``description``; YAML that does not parse. ``discoverable`` checks exactly these, for the expert that
  publish copies into the data project (R4-03).

Beyond the documentation, plugin agents are held to the subset the execution bridge can read (``bridge.agents_json``
turns ``agents/*.md`` into ``--agents`` JSON with a flat ``key: value`` reader): one line per key, the name equals the file
name, the two shipped agents exist, and ``kb-verifier`` is read-only: its ``tools`` allowlist must exist (an omitted
``tools`` inherits every tool) and hold nothing but ``Read``, ``Grep``, ``Glob`` and ``StructuredOutput``.

The frontmatter reader is deliberately small (no YAML dependency): a ``key: value`` line per key; blank lines and
``#`` lines are skipped; an indented continuation line is accepted only under a key that is not read by the bridge
(``mcpServers``/``hooks`` blocks of the expert); plain scalars must not contain ``: `` or `` #`` and quoted scalars must be
closed - the things that make YAML refuse a line.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 20, 2

FORBIDDEN_KEYS = ("hooks", "mcpServers", "permissionMode")
# the field table of the sub-agents page (Claude Code 2.1.x docs mirror)
KNOWN_KEYS = frozenset({
    "name", "description", "tools", "disallowedTools", "model", "permissionMode", "maxTurns", "autoCompactWindow",
    "skills", "mcpServers", "hooks", "memory", "background", "omitClaudeMd", "effort", "isolation", "color",
    "initialPrompt", "experimental",
})
FLAT_KEYS = frozenset({"name", "description", "tools", "disallowedTools", "model"})  # read by bridge.agents_json
REQUIRED_KEYS = ("name", "description")
REQUIRED_AGENTS = ("klode-card-writer", "kb-verifier")
READONLY_TOOLS = {"kb-verifier": frozenset({"Read", "Grep", "Glob", "StructuredOutput"})}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?: (.*))?$")
DQ_RE = re.compile(r'^"(?:[^"\\]|\\.)*"$')
SQ_RE = re.compile(r"^'(?:[^']|'')*'$")
LOCAL_PATH_RE = re.compile("|".join(["/Us" + "ers/", "/ho" + "me/", "~/" + r"\.cla" + "ude"]))
MAX_NAME = 256


@dataclass(frozen=True)
class Problem:
    file: str
    code: str
    message: str


def _p(file: str, code: str, message: str) -> Problem:
    return Problem(file, code, message)


def _split(text: str, file: str) -> tuple[list[str], str, list[Problem]]:
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return [], "", [_p(file, "no_frontmatter", "the opening --- is not the first line: Claude Code skips the file "
                                                 "as documentation")]
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        return [], "", [_p(file, "unterminated", "the frontmatter has no closing ---")]
    return lines[1:close], "\n".join(lines[close + 1:]), []


def _scalar_ok(value: str, *, flat: bool) -> bool:
    v = value.strip()
    if not v:
        return True
    if v[0] == '"':
        return bool(DQ_RE.match(v))
    if v[0] == "'":
        return bool(SQ_RE.match(v))
    if v[0] in "[{":
        return not flat and v[-1] == {"[": "]", "{": "}"}[v[0]]
    if v[0] in "|>":
        return not flat
    if v[0] in "&*!%@`#" or v.startswith("- ") or v.startswith("? "):
        return False
    return ": " not in v and " #" not in v and not v.endswith(":")


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def _entries(fm: list[str], file: str) -> tuple[list[tuple[str, str]], list[Problem]]:
    """``(key, raw value)`` per key in order, and the line-level problems."""
    out: list[tuple[str, str]] = []
    problems: list[Problem] = []
    for line in fm:
        if not line.strip() or line.lstrip().startswith("#") and not line[:1].isspace():
            continue
        if line[:1] == "\t":
            problems.append(_p(file, "bad_line", f"tab indentation is not YAML: {line!r}"))
            continue
        if line[:1] == " " or line.startswith("- "):
            owner = out[-1][0] if out else None
            if owner is None or owner in FLAT_KEYS:
                problems.append(_p(file, "bad_line", f"continuation line outside a block value: {line!r}"))
            continue
        m = KEY_RE.match(line.rstrip())
        if not m:
            problems.append(_p(file, "bad_line", f"not a 'key: value' line: {line!r}"))
            continue
        out.append((m.group(1), m.group(2) or ""))
    return out, problems


def _name_problems(name: str, file: str, *, strict: bool) -> list[Problem]:
    if strict:
        ok = bool(NAME_RE.match(name)) and len(name) <= MAX_NAME
    else:
        ok = bool(name) and not name.startswith("-") and ":" not in name and len(name) <= MAX_NAME
    return [] if ok else [_p(file, "bad_name", f"name {name!r}: must not start with '-', contain ':' or exceed "
                                              f"{MAX_NAME} characters" + (" (letters, digits, . _ -)" if strict else ""))]


def _tools(value: str) -> list[str]:
    return [t.strip() for t in _unquote(value).split(",") if t.strip()]


def check_text(text: str, filename: str) -> list[Problem]:
    """Problems of one plugin agent file (``filename`` is its base name)."""
    fm, body, problems = _split(text, filename)
    if problems:
        return problems
    entries, problems = _entries(fm, filename)
    seen: dict[str, str] = {}
    for key, raw in entries:
        if key in seen:
            problems.append(_p(filename, "duplicate_key", f"frontmatter key {key!r} appears twice"))
            continue
        seen[key] = raw
        if key in FORBIDDEN_KEYS:
            problems.append(_p(filename, "forbidden_key", f"{key}: plugin subagents ignore this field (move the agent "
                                                         "to the data project's .claude/agents/ or drop the field)"))
        elif key not in KNOWN_KEYS:
            problems.append(_p(filename, "unknown_key", f"{key!r} is not a Claude Code subagent field (ignored "
                                                       "silently by the host)"))
        if not _scalar_ok(raw, flat=key in FLAT_KEYS):
            problems.append(_p(filename, "bad_scalar", f"{key}: the value is not a valid single-line YAML scalar"))
    for key in REQUIRED_KEYS:
        if not _unquote(seen.get(key, "")).strip():
            problems.append(_p(filename, "missing_key", f"required frontmatter key {key!r} is missing or empty"))
    name = _unquote(seen.get("name", "")).strip()
    stem = filename[:-3] if filename.endswith(".md") else filename
    if name:
        problems += _name_problems(name, filename, strict=True)
        if name != stem:
            problems.append(_p(filename, "name_mismatch", f"name {name!r} differs from the file name {stem!r}"))
    if not body.strip():
        problems.append(_p(filename, "empty_body", "an agent without a system prompt does nothing"))
    allowed = READONLY_TOOLS.get(name or stem)
    if allowed is not None:
        tools = _tools(seen["tools"]) if "tools" in seen else None
        if not tools or not set(tools) <= allowed:
            problems.append(_p(filename, "readonly_violation",
                               f"{name or stem} is read-only: tools must be listed and limited to "
                               f"{', '.join(sorted(allowed))} (got {', '.join(tools) if tools else 'no tools line'})"))
    if LOCAL_PATH_RE.search(text):
        problems.append(_p(filename, "local_path", "a machine-specific home-directory path in a public file"))
    return problems


def discoverable(text: str, filename: str = "agent.md") -> list[Problem]:
    """Would Claude Code load this file as a subagent from ``.claude/agents/``? (the "files it skips" rules)"""
    fm, _, problems = _split(text, filename)
    if problems:
        return problems
    entries, problems = _entries(fm, filename)
    seen = {k: v for k, v in entries}
    for key, raw in entries:
        if not _scalar_ok(raw, flat=False):
            problems.append(_p(filename, "bad_scalar", f"{key}: the value is not valid YAML"))
    name = _unquote(seen.get("name", "")).strip()
    if not name:
        problems.append(_p(filename, "missing_key", "no name: the host treats the file as documentation"))
    else:
        problems += _name_problems(name, filename, strict=False)
        if not _unquote(seen.get("description", "")).strip():
            problems.append(_p(filename, "missing_key", "a name but no description: the host skips the file"))
    return problems


def check(agents_dir: str | Path) -> list[Problem]:
    """Problems of every ``*.md`` in ``agents_dir`` and of the set as a whole (both shipped agents present)."""
    root = Path(agents_dir)
    files = sorted(root.glob("*.md"))
    if not files:
        return [_p(".", "no_agents", f"{root} holds no agent file")]
    problems: list[Problem] = []
    names: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        problems += check_text(text, path.name)
        fm, _, split_problems = _split(text, path.name)
        if not split_problems:
            entries, _ = _entries(fm, path.name)
            names |= {_unquote(v).strip() for k, v in entries if k == "name"}
    problems += [_p(".", "missing_agent", f"agents/{name}.md is required") for name in REQUIRED_AGENTS if name not in names]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit check-agents",
                                     description="Static check of the plugin agents/*.md.")
    parser.add_argument("dir", nargs="?", type=Path, default=PLUGIN_ROOT / "agents", help="agents directory")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON on stdout")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    if not args.dir.is_dir():
        print(f"check-agents: {args.dir} is not a directory", file=sys.stderr)
        return EXIT_USAGE
    problems = check(args.dir)
    status = "FAIL" if problems else "PASS"
    if args.json:
        print(json.dumps({"status": status, "problems": [p.__dict__ for p in problems]}, ensure_ascii=False))
    else:
        for p in problems:
            print(f"{p.file}: {p.code}: {p.message}", file=sys.stderr)
        print(f"check-agents: {status} ({len(problems)} problem(s))")
    return EXIT_FAIL if problems else EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit check-agents``
    sys.exit(main())
