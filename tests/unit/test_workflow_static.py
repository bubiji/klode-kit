"""WI-14 static checks of ``workflows/kb-pipeline.js`` (matrix 14-01, 14-02, and the two-stage branches of 14-03/05).

The Workflow runtime makes ``Date.now()``, ``Math.random()`` and a no-argument ``new Date()`` throw and gives the
script no file or process API (https://code.claude.com/docs/en/workflows , "Behavior and limits" and "Edit a saved
script"), so a script that contains one is broken before it runs. These checks read the script as text, after the
comments are removed and the string literals blanked (a forbidden name in a comment or in prompt prose is not a call;
code inside a template literal's ``${...}`` is still code and is scanned).

Law: none for the script itself (a fixed sequence of calls); the checker is specified by S3 reverse cases - every
forbidden construct injected into the legal script must be reported - so a checker that reports nothing cannot pass.
The executed behaviour (stage sequence under a stubbed ``agent()``) is in tests/cli/test_workflow_node.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _support.flows import REPO

pytestmark = pytest.mark.unit

SCRIPT = REPO / "workflows" / "kb-pipeline.js"


REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")


def strip_comments(src: str) -> str:
    """The script without comments; strings kept (the view for checks that need literal text)."""
    return code_only(src, keep_strings=True)


def code_only(src: str, keep_strings: bool = False) -> str:
    """The script without comments; string and template text blanked (unless ``keep_strings``), ``${...}`` expressions
    kept as code; regex literals are skipped as text."""
    out: list[str] = []
    i, n = 0, len(src)
    stack: list[str] = []  # 'code-brace' markers for ${ } inside templates
    mode = ["code"]
    while i < n:
        c = src[i]
        m = mode[-1]
        if m == "code":
            if src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c == "/":
                prev = "".join(out).rstrip()[-1:] or ";"
                if prev in REGEX_PREV:
                    j, in_class = i + 1, False
                    while j < n and (in_class or src[j] != "/"):
                        if src[j] == "\\":
                            j += 1
                        elif src[j] == "[":
                            in_class = True
                        elif src[j] == "]":
                            in_class = False
                        j += 1
                    out.append(src[i:j + 1] if keep_strings else "/ /")
                    i = j + 1
                    continue
            if c in "'\"":
                mode.append(c)
                out.append(c)
            elif c == "`":
                mode.append("`")
                out.append(c)
            elif c == "{":
                stack.append("{")
                out.append(c)
            elif c == "}":
                if stack and stack[-1] == "${":
                    stack.pop()
                    mode.pop()  # back into the template
                else:
                    if stack:
                        stack.pop()
                    out.append(c)
                    i += 1
                    continue
                out.append(c)
            else:
                out.append(c)
            i += 1
        elif m in ("'", '"'):
            if c == "\\":
                out.append(src[i:i + 2] if keep_strings else "")
                i += 2
                continue
            if c == m:
                mode.pop()
                out.append(c)
            elif keep_strings:
                out.append(c)
            i += 1
        else:  # template literal
            if c == "\\":
                out.append(src[i:i + 2] if keep_strings else "")
                i += 2
                continue
            if c == "`":
                mode.pop()
                out.append(c)
                i += 1
            elif src.startswith("${", i):
                stack.append("${")
                mode.append("code")
                out.append("${")
                i += 2
            else:
                if keep_strings:
                    out.append(c)
                i += 1
    return "".join(out)


FORBIDDEN = {
    "Date.now": re.compile(r"\bDate\s*\.\s*now\b"),
    "new Date": re.compile(r"\bnew\s+Date\b"),
    "Date(": re.compile(r"\bDate\s*\("),
    "Math.random": re.compile(r"\bMath\s*\.\s*random\b"),
    "crypto": re.compile(r"\bcrypto\b"),
    "performance.now": re.compile(r"\bperformance\s*\.\s*now\b"),
    "require": re.compile(r"\brequire\s*\("),
    "import()": re.compile(r"\bimport\s*\("),
    "import from": re.compile(r"^\s*import\s", re.M),
    "child_process": re.compile(r"child_process"),
    "fs": re.compile(r"\bfs\b|node:fs|\breadFile|\bwriteFile|\bexistsSync"),
    "process": re.compile(r"\bprocess\s*[.\[]"),
    "Deno/Bun": re.compile(r"\b(Deno|Bun)\b"),
    "eval": re.compile(r"\beval\s*\(|\bnew\s+Function\b|\bFunction\s*\("),
    "fetch": re.compile(r"\bfetch\s*\(|XMLHttpRequest|WebSocket"),
    "setTimeout": re.compile(r"\bsetTimeout\b|\bsetInterval\b"),
    "--force": re.compile(r"--force"),
}


def violations(src: str) -> list[str]:
    code, text = code_only(src), strip_comments(src)
    return sorted(name for name, rx in FORBIDDEN.items() if rx.search(text if name == "--force" else code))


def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_no_forbidden_api_in_the_script():
    """14-01 (L1 S3): no clock, randomness, module loading, file, process or network API in the executable text."""
    assert violations(script()) == []


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_checker_reports_each_forbidden_construct_when_injected(name):
    """S3 reverse of 14-01: the checker must see every forbidden construct the legal script is spared of."""
    inject = {
        "Date.now": "const t = Date.now()",
        "new Date": "const t = new Date()",
        "Date(": "const t = Date()",
        "Math.random": "const r = Math.random()",
        "crypto": "const r = crypto.randomUUID()",
        "performance.now": "const t = performance.now()",
        "require": "const fs2 = require('x')",
        "import()": "const m = await import('x')",
        "import from": "import x from 'y'",
        "child_process": "const cp = child_process",
        "fs": "const w = writeFile",
        "process": "const e = process.env",
        "Deno/Bun": "const d = Deno",
        "eval": "eval('1')",
        "fetch": "await fetch('http://x')",
        "setTimeout": "setTimeout(() => 1, 5)",
        "--force": "const f = '--force'",
    }[name]
    assert name in violations(script() + "\n" + inject + "\n")


def test_forbidden_names_in_comments_and_prose_are_not_calls_but_template_code_is():
    legal = "// Date.now() Math.random() require('fs')\n/* new Date() */\nconst p = 'Date.now() in prose'\n"
    assert violations(legal) == []
    assert violations("const p = `x ${Date.now()} y`\n") == ["Date.now"]
    assert violations("const p = `a ${ {a: 1}.a } ${'x'} b ${Math.random()}`\n") == ["Math.random"]


def test_meta_is_the_first_statement_and_a_plain_literal():
    """The ``meta`` block must stay a literal or ``/klode-kit:kb-pipeline`` drops out of autocomplete (docs, same page)."""
    src = script()
    code = code_only(src).lstrip()
    assert code.startswith("export const meta = {")
    block = src[src.index("export const meta = {"):]
    block = block[:block.index("\n}\n") + 3]
    assert re.search(r"^\s*name: 'kb-pipeline',$", block, re.M)
    assert re.search(r"^\s*description: '.+',$", block, re.M)
    assert "${" not in block and "..." not in block and "(" not in code_only(block).replace("meta = {", "")


def test_every_agent_call_carries_an_output_schema_and_each_stage_has_one_call_site():
    """14-02 (L1 S2): exactly one ``agent(`` call, in the stage loop, with ``schema``; stage lists have no repeats."""
    code = code_only(script())
    calls = [m.start() for m in re.finditer(r"\bagent\s*\(", code)]
    assert len(calls) == 1
    call = code[calls[0]:]
    assert re.search(r"schema\s*:\s*RESULT", call[: call.index("results.push")])
    assert "pipeline(" not in code and "parallel(" not in code  # no fan-out: one agent per stage, in order

    src = script()
    first = re.search(r"const FIRST_STAGES = \[(.*?)\]", src).group(1)
    resume = re.search(r"const RESUME_STAGES = \[(.*?)\]", src).group(1)
    publish = re.search(r"const PUBLISH_STAGE = '(\w+)'", src).group(1)
    names = lambda s: re.findall(r"'(\w+)'", s)  # noqa: E731
    assert names(first) == ["preflight", "ocr", "audit"]
    assert names(resume) == ["writeback", "build", "check"]
    assert publish == "publish"
    assert len(set(names(first) + names(resume) + [publish])) == 7  # no stage listed twice


def test_result_schema_names_stage_exit_code_and_stdout():
    src = script()
    block = src[src.index("const RESULT"):]
    block = block[: block.index("\n}\n") + 3]
    for key in ("stage", "exit_code", "stdout"):
        assert f"'{key}'" in block
    assert "type: 'integer'" in block


def test_stage_one_ends_at_audit_exit_10_and_resume_is_a_separate_branch():
    """Two-stage branches (14-03/14-05 static half): audit exit 10 is a stop, not a failure; resume picks stage 2."""
    code = strip_comments(script())
    assert re.search(r"input\.resume\s*\?\s*RESUME_STAGES\.slice\(\)\s*:\s*FIRST_STAGES\.slice\(\)", code)
    assert re.search(r"stage\s*===\s*'audit'\s*&&\s*!input\.resume\s*&&\s*r\s*&&\s*r\.exit_code\s*===\s*10", code)
    assert "awaiting_confirm" in code


def test_publish_is_a_manual_gate_never_implied_by_resume():
    """User rule (deviations WI-09d / WI-13a): publish only when args.publish === true, and only in stage 2."""
    code = code_only(script())
    pushes = re.findall(r"if \(([^)]*)\)\s*stages\.push\(PUBLISH_STAGE\)", code)
    assert len(pushes) == 1
    assert "input.resume" in pushes[0] and "input.publish === true" in pushes[0]
    assert code.count("PUBLISH_STAGE") == 2 and "'publish'" not in code.split("const PUBLISH_STAGE")[1].split("\n", 1)[1]


def test_agents_only_run_the_plugin_launcher_with_the_pipeline_command():
    """The one command each agent runs is ``${CLAUDE_PLUGIN_ROOT}/bin/klode-kit pipeline ...`` (no python, no shell tricks)."""
    src = script()
    assert "const LAUNCHER = '${CLAUDE_PLUGIN_ROOT}/bin/klode-kit'" in src
    raw = re.findall(r"LAUNCHER \+ ' pipeline --batch", src)
    assert len(raw) == 1
    built = src[src.index("function commandFor"):src.index("const results")]
    literals = " ".join(re.findall(r"'((?:[^'\\]|\\.)*)'", built))  # the text the command is made of
    for banned in ("python", "bash -c", "sh -c", "&&", "|", "$(", ">", ";", "rm "):
        assert banned not in literals, banned


def test_arguments_are_guarded_against_quote_and_newline_injection():
    src = script()
    assert "/['\\n\\r\\0]/.test(value)" in src
    for guard in ("args.project", "args.inbox", "args.routes", "args.type", "args.choices"):
        assert guard in src
