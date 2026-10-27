"""WI-30: the documents tell the truth (matrix 30-01 ... 30-04).

Documents under test: ``docs/*.md`` (top level) and ``README.md``.

* 30-01 every command, option, path, slash command, lane marker and relative link a document names exists
  (the checker is itself tested with planted bad references, so it cannot go green by extracting nothing);
* 30-02 no private project number / path / personal data;
* 30-03 ``README.md`` is the new version (user decision 2026-10-27: README_v2 overwrote it) and no ``README_v2.md`` is left beside it;
* 30-04 ``docs/目录结构.md`` has a section for every top-level directory.

Written to run from ``_staging/wi30/`` and, unchanged, from ``tests/docs/``.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(REPO / "src"))

from klode_kit.__main__ import COMMANDS  # noqa: E402

pytestmark = pytest.mark.unit

NEW_DOCS = ["docs/目录结构.md", "docs/install.md", "docs/med-intake-backup.md", "README.md"]


# Point-in-time documents that describe things as they were planned / before the plugin existed (the user's
# earlier scripts, planned layout). They are scanned for private data (30-02) but their references are history,
# not claims about the current tree, so they are not held to 30-01.
HISTORICAL_DOCS = {"docs/交接_2026-10-22.md", "docs/概要计划_v2.md"}


def all_docs() -> list[str]:
    found = {p.relative_to(REPO).as_posix() for p in (REPO / "docs").glob("*.md")}
    found.add("README.md")
    return sorted(found | set(NEW_DOCS))


def read(rel: str) -> str:
    path = REPO / rel
    assert path.is_file(), f"{rel} does not exist"
    return path.read_text(encoding="utf-8")


# ---- the extractor ----------------------------------------------------------------------------------------

# Directories and files that belong to the DATA PROJECT (or are created at run time), not to the repository
# checkout. A document may name them; docs/目录结构.md must explain every directory in RUNTIME_DIRS.
RUNTIME_DIRS = {
    "work", "kb", "records", "confirm", "verified", "_inbox", "_masked", "_mask_review", "_private", "_trash",
    "_staging", "builds", "library", "cards_content", "current", "inbox", "archive", "guide-2026.pdf",
}
RUNTIME_FILES = {
    ".mcp.json", "settings.local.json", "confirm.html", "choices.json", "receipt.json", "med_intake.json",
    "registry.toml", "PUBLISH.log", "desk.json", "mutation.json", "klode-kit-install.json", "check_receipt.json",
    "build_receipt.json", "advice.json", "export_map.jsonl", "MANIFEST.sha256", "library.toml",
    "settings.json", "advice.json", "holdout.jsonl", "fixes.jsonl", "audit.md",
}
DATA_PROJECT_CLAUDE_PATHS = (".claude/settings", ".claude/agents/", ".claude/skills/")
EXTENSION_MENTIONS = {".md", ".svg", ".json", ".js", ".py", ".wf.json", ".toml", ".sh", ".html"}  # a suffix, not a file
BUILTIN_SLASH = {"config", "workflows", "reload-plugins", "reload-skills", "plugin", "help", "agents", "mcp", "doctor"}
EXTERNAL_BINARIES = {"uv", "git", "rsync"}
SH_LANGS = {"sh", "bash", "zsh", "console", "shell"}
PATH_HEADS = ("scripts", "bin", "src", "docs", "skills", "tests", "hooks", "agents", "workflows", ".claude",
              ".claude-plugin", ".github", "_staging")
FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def extract(text: str) -> list[tuple[int, str, str]]:
    """(line, kind, text): kind ``cmd`` for a line of a sh fence, ``inline`` for a backticked span."""
    out: list[tuple[int, str, str]] = []
    fence: str | None = None
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = None if fence is not None else stripped[3:].strip().lower() or "plain"
            continue
        if fence is not None:
            if fence in SH_LANGS and stripped and not stripped.startswith("#"):
                out.append((no, "cmd", stripped.removeprefix("$ ").strip()))
            continue
        for m in re.finditer(r"`([^`\n]+)`", line):
            out.append((no, "inline", m.group(1).strip()))
    return out


@functools.cache
def skills() -> frozenset[str]:
    return frozenset(p.parent.name for p in (REPO / "skills").glob("*/SKILL.md")) | {"kb-pipeline"}


@functools.cache
def markers() -> frozenset[str]:
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return frozenset(m.split(":")[0].strip() for m in cfg["tool"]["pytest"]["ini_options"]["markers"])


@functools.cache
def help_text(key: str) -> str:
    """`bin/klode-kit <key> --help` plus the registry one-liner (some commands print no argparse help)."""
    ran = subprocess.run([str(REPO / "bin" / "klode-kit"), *key.split(), "--help"], capture_output=True, text=True,
                         timeout=60, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    return ran.stdout + ran.stderr + COMMANDS[key][1]


def split_command(s: str) -> list[str]:
    s = s.replace("${CLAUDE_PLUGIN_ROOT}/", "")
    toks = s.split()
    while toks and re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*", toks[0]):  # leading VAR=value
        toks.pop(0)
    return toks


def command_problems(s: str) -> tuple[list[str], int]:
    """Problems of one command string and how many things were checked."""
    toks = split_command(s)
    problems: list[str] = []
    checked = 0
    if not toks:
        return problems, checked
    head = toks[0]
    if "(" not in head and (head == "klode-kit" or head.endswith("bin/klode-kit")):
        if head != "klode-kit":
            checked += 1
            if not (REPO / head).exists() and not head.startswith(("<", "/", ".")) and head != "bin/klode-kit":
                problems.append(f"launcher path missing: {head}")
        rest = toks[1:]
        if not rest or rest[0].startswith(("-", "<")):
            return problems, checked  # `klode-kit --version`, `klode-kit <命令>` (placeholder)
        key = f"{rest[0]} {rest[1]}" if len(rest) > 1 and f"{rest[0]} {rest[1]}" in COMMANDS else rest[0]
        checked += 1
        if key not in COMMANDS:
            problems.append(f"unknown klode-kit command: {rest[0]!r}")
            return problems, checked
        hay = help_text(key)
        for flag in FLAG_RE.findall(" ".join(rest[len(key.split()):])):
            checked += 1
            if flag not in hay and flag not in ("--help",):
                problems.append(f"`klode-kit {key}` has no option {flag}")
    elif head in EXTERNAL_BINARIES:
        checked += 1
        if shutil.which(head) is None:
            problems.append(f"external command not found: {head}")
    elif head.endswith(".sh") and "/" in head:
        checked += 1
        script = REPO / head
        if not script.is_file():
            problems.append(f"script missing: {head}")
        else:
            body = script.read_text(encoding="utf-8")
            for flag in FLAG_RE.findall(" ".join(toks[1:])):
                checked += 1
                if flag not in body:
                    problems.append(f"{head} has no option {flag}")
    elif head.endswith("/python") and toks[1:2] == ["-m"] and len(toks) > 2:
        checked += 1
        mod = toks[2]
        if mod.startswith("klode_kit"):
            base = REPO / "src" / Path(*mod.split("."))
            if not (base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()):
                problems.append(f"python module missing: {mod}")
        for i, tok in enumerate(toks):
            if tok == "-m" and i > 2 and i + 1 < len(toks):  # pytest -m <marker expression>
                checked += 1
                expr = toks[i + 1].strip("'\"")
                for word in re.findall(r"[a-z_]+", expr):
                    if word not in markers() and word not in ("not", "and", "or"):
                        problems.append(f"unknown pytest marker: {word}")
    return problems, checked


@functools.cache
def checkout_basenames() -> frozenset[str]:
    skip = {".git", ".venv", "work", "_trash", "__pycache__", ".hypothesis", ".pytest_cache", "node_modules"}
    names: set[str] = set()
    for _root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip]
        names.update(files)
    return frozenset(names)


def path_problems(token: str) -> tuple[list[str], int]:
    tok = token.replace("${CLAUDE_PLUGIN_ROOT}/", "").strip("\"',;:()[]").rstrip(".")
    if not tok or "://" in tok or tok.startswith(("~", "$", "<", "/", "-", ".venv", "..")) or "|" in tok or " " in tok:
        return [], 0
    tok = tok.removeprefix("./")
    if tok.startswith(DATA_PROJECT_CLAUDE_PATHS):  # settings / agents of the DATA project, not of this checkout
        return [], 0
    if tok in EXTENSION_MENTIONS or ("/" not in tok and re.search(r"[<>*{}]", tok)):
        return [], 0
    if "/" not in tok:
        if re.search(r"\.(md|json|toml|py|sh|js|yml|yaml|lock|sha256|html|jsonl)$", tok) and tok not in RUNTIME_FILES:
            return ([] if tok in checkout_basenames() else [f"no file named {tok} anywhere in the checkout"]), 1
        return [], 0
    first = tok.split("/")[0]
    if first in RUNTIME_DIRS:
        return [], 0
    if first not in PATH_HEADS and not (REPO / first).exists():
        return [], 0  # not a repository path (e.g. `lab/rx`, `a/b` prose)
    cut = re.search(r"[<*{\[…]", tok)
    if cut:
        tok = tok[: cut.start()]
        tok = tok[: tok.rfind("/") + 1] if "/" in tok else tok
    if not tok:
        return [], 0
    return ([] if (REPO / tok).exists() else [f"path missing: {tok}"]), 1


def slash_problems(s: str) -> tuple[list[str], int]:
    m = re.fullmatch(r"/(?:klode-kit:)?([A-Za-z][\w-]*)(?:\s.*)?", s)
    if not m or "*" in s:
        return [], 0
    name = m.group(1)
    ok = name in skills() or name in BUILTIN_SLASH
    return ([] if ok else [f"unknown slash command: /{name}"]), 1


def link_problems(text: str, rel: str) -> tuple[list[str], int]:
    problems, checked = [], 0
    base = (REPO / rel).parent
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        checked += 1
        if not (base / target.split("#")[0]).exists():
            problems.append(f"broken link: {target}")
    return problems, checked


def problems_in(text: str, rel: str = "README.md") -> tuple[list[str], int]:
    problems: list[str] = []
    checked = 0
    for no, kind, s in extract(text):
        found: list[tuple[list[str], int]] = []
        if kind == "cmd" or re.match(r"(\S+/)?klode-kit\b|[\w./-]*python -m |uv |git |rsync |[\w./-]*install\.sh", s):
            found.append(command_problems(s))
        if kind == "inline":
            found.append(slash_problems(s))
        for tok in s.split():
            found.append(path_problems(tok))
        for probs, n in found:
            checked += n
            problems += [f"{rel}:{no}: {p}" for p in probs]
    probs, n = link_problems(text, rel)
    return problems + [f"{rel}: {p}" for p in probs], checked + n


# ---- 30-00 the documents exist ----------------------------------------------------------------------------

@pytest.mark.parametrize("rel", NEW_DOCS)
def test_new_document_exists_and_is_not_a_stub(rel):
    text = read(rel)
    assert len(text.strip()) > 1500, f"{rel} is too short to document anything"
    assert text.lstrip().startswith("#")


# ---- 30-01 every command and path named exists ------------------------------------------------------------

@pytest.mark.parametrize("rel", [d for d in all_docs() if d not in HISTORICAL_DOCS])
def test_every_command_path_and_link_named_in_a_document_exists(rel):
    problems, checked = problems_in(read(rel), rel)
    assert problems == []
    if rel in NEW_DOCS:
        assert checked >= 8, f"{rel}: only {checked} references were checked, the extractor saw too little"


def test_checker_reports_planted_bad_references():
    text = "\n".join([
        "`bin/klode-kit no-such-command --x`",
        "`bin/klode-kit med-intake --no-such-flag`",
        "`src/klode_kit/does_not_exist.py`",
        "`scripts/nope.sh --dev`",
        "`/klode-kit:no-such-skill`",
        "[gone](docs/nowhere.md)",
        "```sh",
        ".venv/bin/python -m pytest -m no_such_marker",
        ".venv/bin/python -m klode_kit.devtools.not_there",
        "scripts/install.sh --no-such-option",
        "```",
    ])
    problems, _ = problems_in(text)
    joined = "\n".join(problems)
    for needle in ("unknown klode-kit command: 'no-such-command'", "no option --no-such-flag",
                   "path missing: src/klode_kit/does_not_exist.py", "path missing: scripts/nope.sh",
                   "unknown slash command: /no-such-skill", "broken link: docs/nowhere.md",
                   "unknown pytest marker: no_such_marker", "python module missing: klode_kit.devtools.not_there",
                   "scripts/install.sh has no option --no-such-option"):
        assert needle in joined, (needle, joined)
    assert len(problems) == 9


def test_checker_accepts_real_references():
    text = "\n".join([
        "`bin/klode-kit med-intake --inbox _inbox/2026-10-22/ --type lab`",
        "`${CLAUDE_PLUGIN_ROOT}/bin/klode-kit dev privacy-scan`",
        "`scripts/install.sh --no-ocr --dev`",
        "`/klode-kit:kb-check` and `/kb-check` and `/workflows`",
        "`src/klode_kit/audit/writeback.py` `kb/<id>/builds/<build_id>/` `skills/*/SKILL.md`",
        "```sh",
        ".venv/bin/python -m pytest -m unit --disable-socket",
        ".venv/bin/python -m klode_kit.devtools.run_lane unit --not-release",
        "```",
    ])
    problems, checked = problems_in(text)
    assert problems == []
    assert checked >= 12


# ---- required content (the specification of what each document must say) ---------------------------------

def test_install_doc_states_prerequisites_and_cites_the_official_docs():
    text = read("docs/install.md")
    for needle in ("macOS", "uv", "3.12", "klode==0.5.0", "PyPI", "ocr-mac", "mlx", "scripts/install.sh", "--no-ocr",
                   "--with-mlx", "--dev", "--keep-extra", "_trash", "klode-kit-install.json", "Bash(", "/kb-check",
                   "/klode-kit:kb-check", "信任", "付费", "Workflow", "doctor", "settings.local.json"):
        assert needle in text, f"docs/install.md does not mention {needle!r}"
    for url in ("https://code.claude.com/docs/en/workflows", "https://code.claude.com/docs/en/permissions",
                "https://code.claude.com/docs/en/skills", "https://code.claude.com/docs/en/security"):
        assert url in text, f"docs/install.md does not cite {url}"
    assert "rm -rf" not in text


def test_install_doc_permission_templates_use_the_documented_wildcard_shape():
    rules = re.findall(r"Bash\([^)\n]*\)", read("docs/install.md"))
    assert rules, "no Bash(...) permission rule template"
    for rule in rules:
        inner = rule[5:-1]
        assert not re.match(r"\*", inner) or "bin/klode-kit" in inner, rule
        if "klode-kit" in inner:
            assert inner.endswith(" *") or inner.endswith(":*") or inner.endswith("klode-kit"), rule


def test_backup_doc_matches_the_recorded_decision():
    text = read("docs/med-intake-backup.md")
    for needle in ("git init --bare", "外置盘", "rsync", "--checksum", "本地 git", "不实现", "work/", "_private/"):
        assert needle in text, f"docs/med-intake-backup.md does not mention {needle!r}"
    assert not re.search(r"(github|gitlab|icloud|dropbox)", text, re.I) or "不" in text  # no cloud remote advice


def test_readme_v2_covers_the_agreed_sections():
    text = read("README.md")
    for needle in ("四件套", "skill", "agent", "workflow", "快速开始", "G2", "KLODE_KIT_GATE_ACK", "release-check",
                   "mutation-gate", "MIT", "医疗", "docs/install.md", "docs/目录结构.md", "docs/med-intake-backup.md",
                   "unit", "contract_replay", "cli_integration", "privacy-scan", "手动闸门"):
        assert needle in text, f"README.md does not mention {needle!r}"
    for n in range(1, 9):
        assert f"U{n}" in text, f"README.md has no use case U{n}"
    assert "macOS" in text


def test_directory_doc_explains_the_pieces():
    text = read("docs/目录结构.md")
    for needle in ("skills/", "agents/", "workflows/", "scripts/", "bin/", "hooks/", ".claude-plugin/", "src/klode_kit/",
                   "tests/unit/", "tests/contract/", "tests/cli/", "tests/model/", "tests/host/", "tests/replay/",
                   "tests/docs/", "tests/fixtures/", "tests/recordings/", "work/", "kb/", "records/", "confirm/",
                   "_trash/", "_staging/", "_masked/", "_mask_review/", "_private/", "docs/pipeline/", "docs/wua/"):
        assert needle in text, f"docs/目录结构.md does not explain {needle!r}"
    for sub in sorted(p.name for p in (REPO / "src" / "klode_kit").iterdir()
                      if p.is_dir() and not p.name.startswith("_")):
        assert f"src/klode_kit/{sub}/" in text, f"docs/目录结构.md misses the sub-package {sub}"
    for name in sorted(skills() - {"kb-pipeline"}):
        assert f"skills/{name}/" in text, f"docs/目录结构.md misses skills/{name}/"
    for agent in sorted(p.name for p in (REPO / "agents").glob("*.md")):
        assert f"agents/{agent}" in text, f"docs/目录结构.md misses agents/{agent}"


# ---- 30-02 nothing private --------------------------------------------------------------------------------

PRIVATE_PATTERNS = {
    "absolute home path": re.compile(r"/Users/|/home/[a-z]|C:\\Users"),
    "home-relative documents path": re.compile(r"~/Documents"),
    "local user name": re.compile(r"deanlee|DeanLee", re.I),
    "private project number": re.compile(r"(?<![A-Za-z0-9])00\d[-_][A-Za-z]|_P[0-9](?![0-9A-Za-z])"),
    "private project name": re.compile(r"family-medical|scan-kb|rational-unified|002_d_"),
    "e-mail address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
}


@pytest.mark.parametrize("rel", all_docs())
def test_no_private_project_ids_paths_or_personal_data(rel):
    text = read(rel)
    hits = [(why, m.group(0)) for why, rx in PRIVATE_PATTERNS.items() for m in rx.finditer(text)]
    assert hits == []


def test_private_patterns_catch_planted_values():
    planted = {
        "absolute home path": "/Users/someone/x", "home-relative documents path": "~/Documents/Claude",
        "local user name": "owner deanlee", "private project number": "see 002_d_notes",
        "private project name": "family-medical", "e-mail address": "a.b@example.com",
    }
    for why, sample in planted.items():
        assert PRIVATE_PATTERNS[why].search(sample), why
    assert not PRIVATE_PATTERNS["private project number"].search("batch page p002 and WI-13b")


def test_repository_privacy_scan_is_clean_on_the_documents():
    present = [rel for rel in all_docs() if (REPO / rel).is_file()]
    ran = subprocess.run([str(REPO / "bin" / "klode-kit"), "dev", "privacy-scan", "--root", str(REPO), *present],
                         capture_output=True, text=True, timeout=120, env={"PATH": "/usr/bin:/bin"})
    assert ran.returncode == 0, ran.stdout + ran.stderr


# ---- 30-03 README.md is the new version (user decision 2026-10-27) -----------------------------------------

def test_readme_is_the_new_version_and_no_v2_copy_is_left():
    assert not (REPO / "README_v2.md").exists(), "README_v2.md overwrote README.md; no second copy should remain"
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "LICENSE" in text and all(f"U{n}" in text for n in range(1, 9))


# ---- 30-04 every top-level directory is explained ------------------------------------------------------------

def tracked_top_dirs() -> set[str]:
    ran = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, timeout=30)
    assert ran.returncode == 0
    names = {f.split(b"/")[0].decode() for f in ran.stdout.split(b"\0") if b"/" in f}
    return {n.strip('"') for n in names}


def test_directory_doc_covers_every_top_level_directory():
    text = read("docs/目录结构.md")
    required = tracked_top_dirs() | {"work", "_trash", "_staging", ".venv"}
    assert {"src", "tests", "docs", "skills", "agents", "workflows", "scripts", "bin", "hooks", ".claude-plugin"} <= required
    missing = sorted(d for d in required if f"`{d}/`" not in text)
    assert missing == [], f"docs/目录结构.md has no entry for: {missing}"


def test_directory_doc_explains_every_runtime_directory_other_documents_may_name():
    text = read("docs/目录结构.md")
    named = {"work", "kb", "records", "confirm", "verified", "_inbox", "_masked", "_mask_review", "_private", "_trash",
             "_staging", "builds", "library", "cards_content", "current"}
    assert named <= RUNTIME_DIRS
    missing = sorted(d for d in named if f"{d}/" not in text)
    assert missing == []
