"""WI-13a (unit): static checks of the five plugin skills ``skills/*/SKILL.md`` (matrix 13a-01, 13a-02, 13a-03).

Claude Code facts the assertions rest on (docs mirror ``skills.md`` / ``permissions.md`` / ``errors.md``; official
pages https://code.claude.com/docs/en/skills , https://code.claude.com/docs/en/permissions ,
https://code.claude.com/docs/en/errors ):

- a plugin skill's command is ``/<plugin name>:<skill name>``; the frontmatter ``name`` is the last segment
  (skills, "How a skill gets its command name") - so ``kb-check`` in plugin ``klode-kit`` is ``/klode-kit:kb-check``;
- ``${CLAUDE_PLUGIN_ROOT}`` is "substituted only in plugin skills", in the skill's markdown content and in the Bash
  rules of ``allowed-tools`` (skills, "Available string substitutions"); the rule written here does not depend on it
  (``Bash(*/bin/klode-kit *)``), so the launcher path in the body may change with every plugin version;
- the frontmatter fields are the ones of the skills field table; ``allowed-tools`` accepts a space- or comma-separated
  string; ``description`` + ``when_to_use`` are cut at 1,536 characters in the skill listing, key use first;
- an ``!`command`` line is run when the skill is rendered and is checked against the permission rules; one that no
  rule allows aborts the invocation ("Permission checks on injected commands") - ``allowed-tools`` pre-approves it;
- a Bash allow rule with ``*`` before the subcommand is warned about when it sits in a settings file, managed
  settings or ``--allowedTools`` (errors, "Has a wildcard before the rest of the command"); a skill's frontmatter is
  not one of those places, and the leading ``*`` here only stands for the plugin's install directory.

Law (matrix 13a): none for the text itself - each case pins one rule of the text. The S3 cases are checked in both
directions: the checker functions are fed texts with an injected violation and must catch each one, so a checker that
accepts everything cannot pass.
"""

from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.__main__ import COMMANDS

pytestmark = pytest.mark.unit

SKILLS = ("kb-ocr", "kb-audit", "kb-build", "kb-check", "med-intake")
SKILLS_DIR = REPO / "skills"
LAUNCHER = "${CLAUDE_PLUGIN_ROOT}/bin/klode-kit"
ALLOW = "Bash(*/bin/klode-kit *)"
# the field table of the skills page, written down here independently of the skills
DOCUMENTED = {"name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
              "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent",
              "background", "hooks", "paths", "shell", "metadata", "license", "compatibility"}
# what these five skills may use: nothing that changes the context, the model or the shell
USED = {"name", "description", "when_to_use", "argument-hint", "allowed-tools", "disable-model-invocation"}
DESCRIPTION_CAP = 1536
MAX_BODY_LINES = 260
# 04 section 7: the sentence that triggers each function
TRIGGERS = {
    "med-intake": ("今天的截图入库", "把 _inbox 里的单子收进去"),
    "kb-ocr": ("这几张拍的单子先 OCR", "四路读一下", "这份指南 PDF 建个库", "2025 年那批重新 OCR 一遍"),
    "kb-audit": ("分歧我点完了，写回", "2025 年那批重新 OCR 一遍"),
    "kb-build": ("这本书走 kb-build", "书架书桌一起建", "这份指南 PDF 建个库"),
    "kb-check": ("检查一下库过不过",),
}
# 03 A11: the twelve known pitfalls of the original klode-build skill, each with a word its row must carry
PITFALLS = {
    "K01": "extra_guard_dirs", "K02": "--scan", "K03": "自校验", "K04": "AMBIGUOUS", "K05": "NOT FOUND",
    "K06": "fix-anchors", "K07": "Agent type not found", "K08": "20", "K09": "ingest", "K10": "--stamp",
    "K11": "Source for ''", "K12": "--full",
}
SCRIPT_LANGS = {"bash", "sh", "zsh", "shell", "python", "py", "python3", "js", "javascript", "node", "ts", "perl",
                "ruby", "awk", "powershell", "ps1", "console"}


# --------------------------------------------------------------------------- reading a skill


def read(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def split(text: str) -> tuple[dict[str, str], str]:
    """``(frontmatter, body)``; the frontmatter must open on line 1 and hold ``key: value`` lines only."""
    lines = text.split("\n")
    assert lines[0] == "---", "the opening --- must be line 1"
    end = lines.index("---", 1)
    fm: dict[str, str] = {}
    for ln in lines[1:end]:
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*): (.+)", ln)
        assert m, f"frontmatter line is not 'key: value': {ln!r}"
        assert m.group(1) not in fm, f"duplicate key {m.group(1)}"
        fm[m.group(1)] = m.group(2).strip()
    return fm, "\n".join(lines[end + 1:])


def fenced_blocks(body: str) -> list[tuple[str, list[str]]]:
    """``[(info string, lines)]`` of every fenced code block."""
    out: list[tuple[str, list[str]]] = []
    info: str | None = None
    cur: list[str] = []
    for ln in body.split("\n"):
        if ln.lstrip().startswith("```"):
            if info is None:
                info, cur = ln.lstrip()[3:].strip(), []
            else:
                out.append((info, cur))
                info = None
        elif info is not None:
            cur.append(ln)
    assert info is None, "unclosed code fence"
    return out


def injections(body: str) -> list[str]:
    """The commands of the ``!`...`` lines."""
    return [m.group(1) for m in re.finditer(r"^!`([^`]*)`\s*$", body, flags=re.M)]


META = re.compile(r"\|\||\||&&|;|\$\(|`|<<|\s>\s|\s>>\s|\\$")
BANNED = re.compile(r"\bpython3?\b|\bnode\s+-e\b|\b(?:sh|bash|zsh)\s+-c\b|\bperl\s+-e\b|\bawk\b|\bjq\b|<<\s*['\"]?\w+")


def business_logic(text: str) -> list[str]:
    """Every sign of logic (instead of a call) in a skill: an interpreter, a script block, a command with shell
    plumbing. ``[]`` = the skill only calls the launcher."""
    _, body = split(text)
    found = [f"banned: {m.group(0)}" for m in BANNED.finditer(text)]
    for info, lines in fenced_blocks(body):
        if info.split()[:1] and info.split()[0].lstrip("!").lower() in SCRIPT_LANGS or info.startswith("!"):
            found.append(f"script block: ```{info}")
        for ln in lines:
            if ln.strip() and not ln.startswith(LAUNCHER):
                found.append(f"fenced line is not a launcher call: {ln.strip()[:60]}")
            elif META.search(ln):
                found.append(f"shell plumbing in a fenced line: {ln.strip()[:60]}")
    for cmd in injections(body):
        if not cmd.startswith(LAUNCHER):
            found.append(f"injection is not a launcher call: {cmd[:60]}")
        elif META.search(cmd):
            found.append(f"shell plumbing in an injection: {cmd[:60]}")
    if len(body.split("\n")) > MAX_BODY_LINES:
        found.append(f"body longer than {MAX_BODY_LINES} lines")
    return found


@lru_cache(maxsize=None)
def help_text(command: str) -> str:
    proc = subprocess.run([sys.executable, "-m", "klode_kit", *command.split(), "--help"], capture_output=True,
                          text=True, timeout=120, cwd=str(REPO))
    return proc.stdout + proc.stderr


def calls(body: str) -> list[tuple[str, list[str]]]:
    """``[(command, [tokens after it])]`` for each launcher call (inline code, fenced lines and injections)."""
    out = []
    for m in re.finditer(re.escape(LAUNCHER) + r"((?: [^\s`]+)*)", body):
        toks = m.group(1).split()
        if not toks:
            continue
        two = " ".join(toks[:2])
        out.append((two, toks[2:]) if two in COMMANDS else (toks[0], toks[1:]))
    return out


# --------------------------------------------------------------------------- 13a-01


def test_exactly_the_five_skills():
    assert sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir()) == sorted(SKILLS)
    for name in SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").is_file(), name


@pytest.mark.parametrize("name", SKILLS)
def test_skills_static(name):
    """13a-01 (L1 S2): frontmatter fields, the whitelist, the launcher in the body, triggers in the description."""
    text = read(name)
    fm, body = split(text)
    assert fm["name"] == name, "plugin skill command = /klode-kit:<name>"
    assert set(fm) <= USED and set(fm) <= DOCUMENTED, sorted(set(fm) - USED)
    assert {"name", "description", "argument-hint", "allowed-tools"} <= set(fm)
    # the whitelist is exactly one rule: the launcher, whatever directory the plugin is installed in
    assert fm["allowed-tools"] == ALLOW
    assert len(fm["description"]) + len(fm.get("when_to_use", "")) <= DESCRIPTION_CAP
    for phrase in TRIGGERS[name]:
        assert phrase in fm["description"] + fm.get("when_to_use", ""), (name, phrase)
    # the body calls the launcher by the plugin variable, every time
    assert LAUNCHER in body
    for m in re.finditer(r"bin/klode-kit", body):
        assert body[:m.start()].endswith("${CLAUDE_PLUGIN_ROOT}/"), (name, body[max(0, m.start() - 40):m.end()])


@pytest.mark.parametrize("name", SKILLS)
def test_every_call_names_a_real_command_and_real_options(name):
    """13a-01: a typo in a command or an option would only show when a person follows the skill - check them all."""
    _, body = split(read(name))
    seen = calls(body)
    assert seen, "the skill calls the launcher at least once"
    for cmd, rest in seen:
        assert cmd in COMMANDS, f"{name}: unknown command {cmd!r}"
        help_ = help_text(cmd)
        for tok in rest:
            if tok.startswith("--"):
                flag = tok.split("=")[0]
                assert flag in help_, f"{name}: {cmd} has no option {flag}"


@pytest.mark.parametrize("name", SKILLS)
def test_no_local_paths_or_user_names(name):
    text = read(name)
    for needle in ("/Users/", "/home/", "~/.claude", "~/.klode", "~/.codex", "DeanLee", "$HOME/"):
        assert needle not in text, (name, needle)


def test_kb_check_is_the_host_entry():
    """13a-04's static half: the skill writes the entry the receipt must carry, through the launcher, at render time."""
    fm, body = split(read("kb-check"))
    (cmd,) = injections(body)
    assert cmd.startswith(f"{LAUNCHER} kb-check ")
    assert "$ARGUMENTS" in cmd and "--entry skill:/klode-kit:kb-check" in cmd
    assert fm["argument-hint"].startswith("--kb")


# --------------------------------------------------------------------------- 13a-02


@pytest.mark.parametrize("name", SKILLS)
def test_skills_no_business_logic(name):
    """13a-02 (L1 S3): no interpreter, no script block, no shell plumbing - the skill only calls the launcher."""
    assert business_logic(read(name)) == []


GOOD = f"---\nname: x\ndescription: d\n---\n\nRun it:\n\n```\n{LAUNCHER} kb-check --kb K\n```\n"


@pytest.mark.parametrize("bad", [
    f"```\npython3 -c 'print(1)'\n```\n",
    f"```bash\n{LAUNCHER} kb-check --kb K\n```\n",
    f"```!\n{LAUNCHER} kb-check --kb K\n```\n",
    f"```\n{LAUNCHER} kb-check --kb K | jq .status\n```\n",
    f"```\n{LAUNCHER} kb-check --kb K && echo done\n```\n",
    f"```\n{LAUNCHER} kb-check --kb K; rm -rf x\n```\n",
    f"```\nls work\n```\n",
    f"!`ls work`\n",
    f"!`{LAUNCHER} kb-check --kb K > out.txt`\n",
    f"!`{LAUNCHER} kb-check $(whoami)`\n",
    "Use python -c to compute it.\n",
    "Pipe it through awk.\n",
    "x\n" * (MAX_BODY_LINES + 1),
])
def test_the_checker_catches_each_kind_of_logic(bad):
    """S3 both ways: the same checker flags an injected violation (and passes the clean text)."""
    assert business_logic(GOOD) == []
    injected = GOOD + "\n" + bad
    assert business_logic(injected), bad


def test_no_launcher_line_passes_force():
    for name in SKILLS:
        _, body = split(read(name))
        for cmd, rest in calls(body):
            assert not any(t.startswith("--force") for t in rest), (name, cmd)


# --------------------------------------------------------------------------- 13a-03


def table_rows(body: str, heading: str) -> list[list[str]]:
    """Rows (cells) of the first table under ``## <heading>``."""
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", body, flags=re.M | re.S)
    assert m, f"no section '{heading}'"
    rows = []
    for ln in m.group(1).split("\n"):
        if ln.startswith("|") and not re.fullmatch(r"\|[\s:|-]+\|?", ln):
            rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return rows[1:] if rows else rows  # the header row


def test_known_pitfalls_and_iron_rules():
    """13a-03 (L1 S2): the twelve pitfalls (03 A11) and the two iron rules (03 A8), all of them, each once."""
    _, body = split(read("kb-build"))
    rows = table_rows(body, "已知坑")
    ids = [r[0] for r in rows]
    assert ids == list(PITFALLS), ids
    for row in rows:
        assert len(row) == 4 and all(row), row  # id | 现象 | 原因 | 处理
        assert PITFALLS[row[0]] in " ".join(row[1:]), (row[0], PITFALLS[row[0]])
    iron = re.findall(r"^- \*\*铁律 ([12])\b[^\n]*", body, flags=re.M)
    assert iron == ["1", "2"]
    first = re.search(r"^- \*\*铁律 1.*?(?=^- \*\*铁律 2|\Z)", body, flags=re.M | re.S).group(0)
    second = re.search(r"^- \*\*铁律 2.*?(?=^## |\Z)", body, flags=re.M | re.S).group(0)
    assert "handback" in first and "文件存在" in first and "不碰" in first
    assert "_diagnostics.md" in second and "兜底" in second and "留出集" in second and "线索词" in second
    assert body.count("铁律 1") >= 1 and body.count("铁律 2") >= 1


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_carries_a_pitfall_section(name):
    """The pitfalls are merged into each skill (02 A11 "并入各 SKILL.md"): a section with real rows."""
    _, body = split(read(name))
    rows = table_rows(body, "已知坑")
    assert len(rows) >= 2, name
    for row in rows:
        assert all(row), (name, row)
        for cell in row[:1]:
            if re.fullmatch(r"K\d\d", cell):
                assert cell in PITFALLS, (name, cell)


def test_kb_check_repeats_the_pitfalls_a_check_surfaces():
    _, body = split(read("kb-check"))
    ids = {r[0] for r in table_rows(body, "已知坑")}
    assert {"K04", "K05", "K10", "K11"} <= ids


def test_iron_rule_two_names_the_frozen_holdout():
    """Iron rule 2 is made real by the frozen holdout and --verify; the text must point at both."""
    _, body = split(read("kb-build"))
    assert "holdout.freeze.json" in body and "desk-build" in body and "--verify" in body
    assert "cue-suggest" in body


# --------------------------------------------------------------------------- the stages the user case needs


def test_med_intake_is_two_segments_with_the_folder_entry():
    """U1/U8: first segment ``med-intake`` (exit 10), the person clicks in ``confirm/<alias>/confirm.html``, second
    segment ``pipeline --choices`` (WI-13b decision 4; WI-13c: the folder entry renders the crops)."""
    _, body = split(read("med-intake"))
    assert f"{LAUNCHER} med-intake --inbox" in body
    assert "confirm/<别名>/confirm.html" in body
    assert f"{LAUNCHER} pipeline --batch" in body and "--choices" in body and "--stage writeback" in body
    assert "exit 10" in body or "退出码 10" in body
    assert "awaiting_confirm" in body
    assert "choices.json" in body


def test_kb_audit_reopen_and_optional_verifier():
    _, body = split(read("kb-audit"))
    assert f"{LAUNCHER} kb-audit --reopen" in body
    assert f"{LAUNCHER} kb-advice" in body
    sentence = next(ln for ln in body.split("\n") if "kb-advice" in ln and "明确" in ln or "kb-verifier" in ln and "明确" in ln)
    assert "用户" in sentence and "明确" in sentence
    assert f"{LAUNCHER} views" in body
    assert "--writeback" in body and "choices.json" in body


def test_kb_build_closes_the_book_path_desk_gap():
    """impl-deviations "设计缺口": the book path's desk syntheses come from workers the skill dispatches, in the
    ``--syntheses DIR`` convention; a missing dimension is ``desk_apply`` FAIL, exit 20."""
    _, body = split(read("kb-build"))
    for needle in ("desk-tasks", "--syntheses", "--repair", "check-synthesis", "klode-card-writer", "desk_apply",
                   "exit 20", "repair.json"):
        assert needle in body, needle
    order = [body.index(x) for x in (f"{LAUNCHER} build", f"{LAUNCHER} desk-tasks", f"{LAUNCHER} check-synthesis")]
    assert order == sorted(order)
    assert "--loops" in body and "kb-publish" in body and "--force" not in " ".join(
        ln for ln in body.split("\n") if ln.startswith(LAUNCHER))
