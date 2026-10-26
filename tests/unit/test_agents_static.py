"""WI-12 (unit): static checks of the plugin agents ``agents/*.md`` (matrix 12-01, 12-02, 12-06).

Claude Code facts the assertions rest on (docs mirror ``sub-agents.md``; official page
https://code.claude.com/docs/en/sub-agents):

- "plugin subagents don't support the ``hooks``, ``mcpServers``, or ``permissionMode`` frontmatter fields. These fields
  are ignored when loading agents from a plugin" (#choose-the-subagent-scope) - so an agent that needs one of them is
  broken without any error, and the plugin's agents must not use them;
- frontmatter field names are camelCase and "must match the table exactly: Claude Code ignores a field it doesn't
  recognize without reporting an error" (#supported-frontmatter-fields) - a typo such as ``disallowed_tools`` silently
  drops a restriction, so unknown keys are a failure here;
- "Claude Code skips a file ... without reporting it in the session" when there is no ``name``, when the opening
  ``---`` is not the file's first line, when the ``name`` starts with ``-``, contains ``:`` or is longer than 256
  characters, when there is a ``name`` but no ``description``, or when the YAML does not parse
  (#subagent-files-claude-code-skips) - the generated expert goes to the data project's ``.claude/agents/`` and must
  survive exactly these rules (R4-03).

Law (matrix 12): none for the static text itself; the only law of WI-12, "the verifier never changes a status", is
12-03 (``tests/contract/test_verifier_replay.py``). Here the injected-key property is an S4 sweep: whatever forbidden
key is added at whatever place of the frontmatter, the check fails, and removing it passes again.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit import agents_check as AC
from klode_kit import bridge
from klode_kit.__main__ import COMMANDS

pytestmark = pytest.mark.unit

AGENTS = REPO / "agents"
WRITER, VERIFIER = "klode-card-writer", "kb-verifier"
FORBIDDEN = ("hooks", "mcpServers", "permissionMode")
# the field table of the sub-agents page, written down here independently of the implementation
DOCUMENTED = {"name", "description", "tools", "disallowedTools", "model", "permissionMode", "maxTurns", "autoCompactWindow",
              "skills", "mcpServers", "hooks", "memory", "background", "omitClaudeMd", "effort", "isolation", "color",
              "initialPrompt", "experimental"}
WRITE_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit", "MultiEdit"}


def raw_frontmatter(path: Path) -> tuple[list[str], str]:
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---", f"{path.name}: line 1 must open the frontmatter"
    close = lines.index("---", 1)
    return lines[1:close], "\n".join(lines[close + 1:])


def keys_of(fm: list[str]) -> list[str]:
    return [m.group(1) for ln in fm if (m := re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*:", ln))]


def value_of(fm: list[str], key: str) -> str:
    (line,) = [ln for ln in fm if re.match(rf"^{key}\s*:", ln)]
    return line.split(":", 1)[1].strip()


def tools_of(path: Path, key: str = "tools") -> list[str]:
    fm, _ = raw_frontmatter(path)
    return [t.strip() for t in value_of(fm, key).split(",") if t.strip()]


def copy_agents(tmp_path: Path) -> Path:
    dst = tmp_path / "agents"
    shutil.copytree(AGENTS, dst)
    return dst


# --------------------------------------------------------------------------- 12-01


@pytest.mark.parametrize("name", [WRITER, VERIFIER])
def test_frontmatter_static(name):
    """12-01 (L1, S2): both plugin agents have legal frontmatter and none of the three ignored fields."""
    path = AGENTS / f"{name}.md"
    assert path.is_file(), f"agents/{name}.md is missing"
    fm, body = raw_frontmatter(path)
    keys = keys_of(fm)
    assert not set(FORBIDDEN) & set(keys), keys
    assert set(keys) <= DOCUMENTED, sorted(set(keys) - DOCUMENTED)
    assert len(keys) == len(set(keys)), "duplicate frontmatter key"
    assert value_of(fm, "name") == name
    assert value_of(fm, "description")
    assert body.strip(), "an agent without a system prompt does nothing"
    # no machine-specific path: the public repository must work on any machine
    assert not re.search(r"/Users/|/home/|~/\.claude|\.klode/", path.read_text(encoding="utf-8"))
    assert AC.check_text(path.read_text(encoding="utf-8"), path.name) == []


def test_repository_agents_pass_the_checker_and_the_cli():
    assert AC.check(AGENTS) == []
    assert AC.main([str(AGENTS)]) == 0


def test_check_agents_is_a_registered_command():
    assert COMMANDS["check-agents"][0] == "klode_kit.agents_check:main"


@pytest.mark.parametrize("name", [WRITER, VERIFIER])
def test_the_bridge_reads_the_agent_files(name):
    """The CLI bridge turns agents/*.md into ``--agents`` JSON with a flat reader: every shipped file must survive it."""
    agents = bridge.agents_json(AGENTS)
    assert set(agents) == {WRITER, VERIFIER}
    assert json.loads(json.dumps(agents)) == agents
    assert agents[name]["prompt"].strip()
    assert agents[name]["description"].strip()


def test_writer_definition():
    """The writer keeps the discipline of the user's original (one card, cite don't recall, no git) in the form the
    bridge's argv needs (Edit under cards_content/, the launcher for the checker, StructuredOutput for the receipt)."""
    tools = tools_of(AGENTS / f"{WRITER}.md")
    assert {"Read", "Grep", "Glob", "Edit", "Bash", "StructuredOutput"} <= set(tools), tools
    assert "Write" not in tools, "path rules are consulted against Edit, not Write (bridge docstring)"
    body = (AGENTS / f"{WRITER}.md").read_text(encoding="utf-8")
    for must in ("cards_content/", "check-cards", "git", "(grep:", "逐字"):
        assert must in body, must
    assert "library/" in body and "src/" in body, "must say what it never edits"


# --------------------------------------------------------------------------- 12-06


def test_verifier_tools_readonly():
    """12-06 (L1, S3): the verifier's ``tools`` allowlist exists and holds no tool that can write or run commands."""
    path = AGENTS / f"{VERIFIER}.md"
    tools = tools_of(path)
    assert tools, "no tools line: the agent would inherit every tool"
    assert not WRITE_TOOLS & set(tools), tools
    assert set(tools) <= {"Read", "Grep", "Glob", "StructuredOutput"}, tools
    denied = set(tools_of(path, "disallowedTools"))
    assert {"Edit", "Write", "Bash"} <= denied, denied
    body = path.read_text(encoding="utf-8")
    assert "advice" in body and "status" in body
    assert AC.check_text(body, path.name) == []


@pytest.mark.parametrize("bad_tools", ["Read, Bash", "Read, Grep, Write", "Edit", "Read, Glob, NotebookEdit", None])
def test_checker_rejects_a_verifier_that_can_write(bad_tools):
    text = (AGENTS / f"{VERIFIER}.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    out = []
    for ln in lines:
        if re.match(r"^tools\s*:", ln):
            if bad_tools is None:
                continue
            ln = f"tools: {bad_tools}"
        out.append(ln)
    problems = AC.check_text("\n".join(out), f"{VERIFIER}.md")
    assert [p.code for p in problems] == ["readonly_violation"], problems


# --------------------------------------------------------------------------- 12-02


def inject(text: str, line: str, at: int | None = None) -> str:
    lines = text.split("\n")
    close = lines.index("---", 1)
    pos = close if at is None else 1 + at % max(close - 1, 1)
    lines.insert(pos, line)
    return "\n".join(lines)


@pytest.mark.parametrize("name", [WRITER, VERIFIER])
@pytest.mark.parametrize("key,value", [("hooks", ""), ("mcpServers", "foo"), ("permissionMode", "dontAsk")])
def test_frontmatter_forbidden_keys_rejected(tmp_path, name, key, value):
    """12-02 (L1, S3): an injected ``hooks:`` (or the other two ignored fields) in a tmp copy FAILs the static check."""
    agents = copy_agents(tmp_path)
    target = agents / f"{name}.md"
    target.write_text(inject(target.read_text(encoding="utf-8"), f"{key}: {value}".rstrip()), encoding="utf-8")
    problems = AC.check(agents)
    assert [(p.file, p.code) for p in problems] == [(target.name, "forbidden_key")], problems
    assert key in problems[0].message
    assert AC.main([str(agents)]) == 20
    assert AC.main([str(agents), "--json"]) == 20
    assert AC.main([str(AGENTS)]) == 0  # the original is untouched


@given(key=st.sampled_from(FORBIDDEN), at=st.integers(min_value=0, max_value=50),
       value=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd")), max_size=12),
       name=st.sampled_from([WRITER, VERIFIER]))
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_forbidden_key_property(key, at, value, name):
    """S4: a forbidden key anywhere in the frontmatter is always found; the unmodified text is clean."""
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    bad = inject(text, f"{key}: {value}".rstrip(), at)
    assert [p.code for p in AC.check_text(bad, f"{name}.md")] == ["forbidden_key"]
    assert AC.check_text(text, f"{name}.md") == []


def test_forbidden_word_in_the_body_is_not_a_frontmatter_key():
    """The prose may talk about hooks; only the frontmatter keys count (a body line 'hooks: x' is just text)."""
    text = (AGENTS / f"{VERIFIER}.md").read_text(encoding="utf-8") + "\nhooks: not a key, this is body text\n"
    assert AC.check_text(text, f"{VERIFIER}.md") == []


@pytest.mark.parametrize("mutate,code", [
    (lambda t: "\n" + t, "no_frontmatter"),                      # opening --- not on line 1: skipped as documentation
    (lambda t: t.replace("---\n", "", 1), "no_frontmatter"),
    (lambda t: t.replace("\n---\n", "\n"), "unterminated"),
    (lambda t: re.sub(r"^name:.*\n", "", t, flags=re.M), "missing_key"),
    (lambda t: re.sub(r"^description:.*\n", "", t, flags=re.M), "missing_key"),
    (lambda t: re.sub(r"^name:.*$", "name: some:thing", t, count=1, flags=re.M), "bad_name"),
    (lambda t: re.sub(r"^name:.*$", "name: -lead", t, count=1, flags=re.M), "bad_name"),
    (lambda t: re.sub(r"^name:.*$", "name: other-name", t, count=1, flags=re.M), "name_mismatch"),
    (lambda t: inject(t, "disallowed_tools: Write"), "unknown_key"),          # silently ignored by Claude Code
    (lambda t: inject(t, "Tools: Read"), "unknown_key"),                        # camelCase must match exactly
    (lambda t: inject(t, "color: green\ncolor: blue"), "duplicate_key"),
    (lambda t: inject(t, "this line has no colon"), "bad_line"),
    (lambda t: inject(t, "note: has: colon-space unquoted"), "bad_scalar"),    # not valid YAML
    (lambda t: inject(t, "note: text # comment? no: a ' quote"), "bad_scalar"),
    (lambda t: inject(t, 'note: "unterminated'), "bad_scalar"),
    (lambda t: inject(t, "\tcolor: green"), "bad_line"),                        # tab indentation
    (lambda t: t.split("\n---\n", 1)[0] + "\n---\n", "empty_body"),
])
def test_checker_rejects_a_damaged_agent(mutate, code):
    text = mutate((AGENTS / f"{WRITER}.md").read_text(encoding="utf-8"))
    problems = AC.check_text(text, f"{WRITER}.md")
    assert code in {p.code for p in problems}, problems


def test_directory_level_problems(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    assert [p.code for p in AC.check(empty)] == ["no_agents"]
    assert AC.main([str(empty)]) == 20
    missing = copy_agents(tmp_path / "x")
    (missing / f"{VERIFIER}.md").unlink()
    assert [(p.code, VERIFIER in p.message) for p in AC.check(missing)] == [("missing_agent", True)]
    assert AC.main([str(tmp_path / "does-not-exist")]) == 2
    assert AC.main(["a", "b"]) == 2


def test_json_output_lists_the_problems(tmp_path, capsys):
    agents = copy_agents(tmp_path)
    target = agents / f"{WRITER}.md"
    target.write_text(inject(target.read_text(encoding="utf-8"), "hooks:"), encoding="utf-8")
    assert AC.main([str(agents), "--json"]) == 20
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "FAIL"
    assert doc["problems"] == [{"file": target.name, "code": "forbidden_key",
                                "message": doc["problems"][0]["message"]}]
    assert AC.main([str(AGENTS), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "PASS", "problems": []}


# --------------------------------------------------------------------------- the generated expert (R4-03, 12-05 static half)


def _gen_agent_helpers():
    spec = importlib.util.spec_from_file_location("kk_test_gen_agent", REPO / "tests" / "unit" / "test_gen_agent.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("shelves,frameworks", [(("items", "records"), False), (("book",), True)])
def test_generated_expert_is_discoverable(tmp_path, shelves, frameworks):
    """12-05 static half (L2, S2): the expert ``gen-agent`` writes for the data project passes Claude Code's own
    loading rules (not skipped as documentation) - the real host listing is ``tests/host/test_expert_discovery.py``."""
    helpers = _gen_agent_helpers()
    kb = helpers.make_kb(tmp_path, shelves=shelves, frameworks=frameworks)
    out = tmp_path / "proj" / ".claude" / "agents" / "med-klode-expert.md"
    assert helpers.gen(kb, out, "--build-id", "b-1") == 0
    text = out.read_text(encoding="utf-8")
    assert AC.discoverable(text, out.name) == []
    assert text.split("\n")[0] == "---"
    # the expert is where mcpServers is allowed: project scope, not the plugin (so the plugin check must refuse it)
    assert "mcpServers" in text
    assert [p.code for p in AC.check_text(text, out.name)] == ["forbidden_key"]


@pytest.mark.parametrize("mutate,code", [
    (lambda t: "\n" + t, "no_frontmatter"),
    (lambda t: "<!-- first -->\n" + t, "no_frontmatter"),
    (lambda t: re.sub(r"^name:.*\n", "", t, count=1, flags=re.M), "missing_key"),
    (lambda t: re.sub(r"^description:.*\n", "", t, count=1, flags=re.M), "missing_key"),
    (lambda t: re.sub(r"^name:.*$", "name: a:b", t, count=1, flags=re.M), "bad_name"),
    (lambda t: re.sub(r"^name:.*$", "name: -x", t, count=1, flags=re.M), "bad_name"),
    (lambda t: re.sub(r"^name:.*$", "name: " + "x" * 257, t, count=1, flags=re.M), "bad_name"),
])
def test_discoverable_rejects_what_the_host_skips(tmp_path, mutate, code):
    helpers = _gen_agent_helpers()
    kb = helpers.make_kb(tmp_path)
    out = tmp_path / "e.md"
    assert helpers.gen(kb, out) == 0
    problems = AC.discoverable(mutate(out.read_text(encoding="utf-8")), "e.md")
    assert code in {p.code for p in problems}, problems
