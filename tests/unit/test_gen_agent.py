"""WI-02d: ``build/gen_agent`` - the ``<id>-klode-expert`` agent for the data project (matrix 02d-01..05).

Specification (03 A6; 02 §3.1 A6, r2.1 row 3.4, r2.6 ``no_examples``; 06 R4-03; vendored from the user's
klode-build ``gen_agent.py``):

- ``klode-kit gen-agent KB --out F`` renders one agent file from ``KB/library.toml`` (``[library] id/name/
  description``), the cards under ``library/cards/`` and the examples in ``KB/src/desk.json`` (``--spec``
  overrides). Parameters: ``--template generic|records_zh``, ``--lang-source / --lang-answer`` (``en|zh|ja``,
  shown as 英文 / 中文 / 日文), ``--source-kind book|records|document`` (原著 / 单据记录 / 文档),
  ``--cite-format quote|grep``, ``--build-id``, ``--mcp``, ``--kb-root``.
- **R4-03** (docs: https://code.claude.com/docs/en/sub-agents#subagent-files-claude-code-skips - an opening
  ``---`` that is not the first line makes the file documentation): line 1 is ``---``; the version comment
  ``<!-- klode-kit build <id> -->`` is the line right after the closing ``---``; nothing else precedes it.
- **3.4**: the language section is rendered from the parameters - ``## 2. 卡片是{A}，原文是{S}`` when they differ,
  ``## 2. 卡片和原文都是{S}`` when equal; ``- 回答用{A}，引文一律{S}原句``. The medical template (records shelves)
  never says the source is English.
- **A6**: the description's ``<example>`` blocks carry the desk's tuning questions in order (first = the first
  dimension's first ``tests`` item), at most three; no question at all (or no desk.json) -> exit 20, reason
  ``no_examples``, nothing written.
- Citations: the answer format names the cite format (``quote``: 引用块; ``grep``: ``(grep: <原文逐字片段>)``) and
  the CLI re-check in klode 0.5.0's real form ``klode verify <卡 id> "<片段>"`` -> ``FOUND`` / ``NOT-FOUND``.

No law: template rendering (matrix WI-02d header). The parameter cases are an S2 sweep over the closed sets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from klode_kit.build import gen_agent as G

pytestmark = pytest.mark.unit

ITEM_CARD = """---
id: {cid}
shelf: {shelf}
file: library/{shelf}/{cid}.txt
framework: none
zoom: full
aliases: [{alias}, item, value]
grep_ready: true
---

# {title}

## Content
`library/{shelf}/{cid}.txt` - full text.

<!-- scaffold: managed by klode - edit Thin/Full below the marker by hand -->

## Thin
{thin}

## Full
### Points
- point (grep: `x`)
"""
LANG = {"en": "英文", "zh": "中文", "ja": "日文"}
KIND = {"book": "原著", "records": "单据记录", "document": "文档"}
TESTS_A = ["示例片最近三次的剂量", "血红蛋白最近一次是多少"]
TESTS_B = ["白细胞有没有超出范围"]


def make_kb(root: Path, *, kid: str = "med", shelves: tuple[str, ...] = ("items", "records"),
            tests: list[list[str]] | None = None, desk: bool = True, frameworks: bool = False) -> Path:
    kb = root / "kb"
    (kb / "library" / "cards").mkdir(parents=True)
    shelf_list = ", ".join(f'"{s}"' for s in shelves)
    fw = "true" if frameworks else "false"
    (kb / "library.toml").write_text(
        f'[library]\nid = "{kid}"\nname = "合成记录库"\ndescription = "三次合成化验单"\ndir = "library"\n'
        f'cards = "cards"\nshelves = [{shelf_list}]\n\n[frameworks]\nenabled = {fw}\ndir = "frameworks"\n'
        f'syntheses = "_syntheses"\n', encoding="utf-8")
    cards = kb / "library" / "cards"
    (cards / "INDEX.md").write_text("# index\n", encoding="utf-8")
    (cards / "hemoglobin.md").write_text(ITEM_CARD.format(
        cid="hemoglobin", shelf=shelves[0], alias="血红蛋白", title="Hemoglobin",
        thin="血红蛋白三次结果 (grep: `血红蛋白 135 g/L 2026-09-18`)。"), encoding="utf-8")
    (cards / "sample-tablet.md").write_text(ITEM_CARD.format(
        cid="sample-tablet", shelf=shelves[0], alias="示例片", title="Sample tablet",
        thin="示例片剂量记录。"), encoding="utf-8")
    if desk:
        dims = tests if tests is not None else [TESTS_A, TESTS_B]
        spec = {"threshold": 0.8, "dimensions": [
            {"dimension": f"dim-{k}", "title": f"维度 {k}", "question": "q", "cards": ["hemoglobin"],
             "cues": [f"cue{k}"], "tests": t} for k, t in enumerate(dims)]}
        (kb / "src").mkdir()
        (kb / "src" / "desk.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    if frameworks:
        syn = kb / "library" / "frameworks" / "_syntheses"
        syn.mkdir(parents=True)
        (syn / "_diagnostics.md").write_text("| cues | dim |\n", encoding="utf-8")
        (syn / "dosage-history.md").write_text("---\ndimension: dosage-history\ntitle: 剂量变化\n---\n",
                                               encoding="utf-8")
    return kb


def frontmatter(text: str) -> tuple[list[str], int]:
    """(frontmatter lines, index of the closing ``---``); the file must open with ``---`` on line 1."""
    lines = text.split("\n")
    assert lines[0] == "---"
    close = lines.index("---", 1)
    return lines[1:close], close


def fm_value(fm: list[str], key: str) -> str:
    (line,) = [ln for ln in fm if ln.startswith(f"{key}:")]
    return line[len(key) + 1:].strip()


def description(text: str) -> str:
    fm, _ = frontmatter(text)
    return json.loads(fm_value(fm, "description"))


def examples(text: str) -> list[str]:
    return [json.loads(m) for m in re.findall(r"<example>\nuser: (.*)\n", description(text))]


def section(text: str, head: str) -> str:
    """Body of the ``## <head>`` section up to the next ``## `` heading (the heading line included)."""
    start = next(i for i, ln in enumerate(text.split("\n")) if ln.startswith(f"## {head}"))
    lines = text.split("\n")[start:]
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.startswith("## ")), len(lines))
    return "\n".join(lines[:end])


def gen(kb: Path, out: Path, *args: str) -> int:
    return G.main([str(kb), "--out", str(out), *args])


# --------------------------------------------------------------------------- 02d-01 R4-03


@pytest.mark.parametrize("template", ["records_zh", "generic"])
def test_first_line_frontmatter(tmp_path, template):
    """02d-01 (S3): line 1 is ``---``; the build comment is the first line after the closing ``---`` and appears
    nowhere inside the frontmatter; the frontmatter names the agent and parses (description is one JSON string)."""
    kb = make_kb(tmp_path)
    out = tmp_path / "agents" / "med-klode-expert.md"
    assert gen(kb, out, "--template", template, "--build-id", "b20261024-01") == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm, close = frontmatter(text)
    lines = text.split("\n")
    assert lines[close + 1] == "<!-- klode-kit build b20261024-01 -->"
    assert text.count("<!--") == 1  # the build comment is the only HTML comment
    assert not any("<!--" in ln for ln in fm)
    assert fm_value(fm, "name") == "med-klode-expert"
    assert description(text).startswith("合成记录库")


def test_build_comment_default_and_bad_id(tmp_path, capsys):
    """R4-03 edge: no ``--build-id`` -> ``unversioned``; an id that would break the HTML comment is a usage error
    and nothing is written."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    assert gen(kb, out) == 0
    _, close = frontmatter(out.read_text(encoding="utf-8"))
    assert out.read_text(encoding="utf-8").split("\n")[close + 1] == "<!-- klode-kit build unversioned -->"
    bad = tmp_path / "b.md"
    assert gen(kb, bad, "--build-id", "x--y") == 2
    assert not bad.exists()


# --------------------------------------------------------------------------- 02d-02 / 3.4


def test_zh_template_no_english_claim(tmp_path):
    """02d-02 (S3): the medical template (records shelves, defaults) never claims the source is English nor
    tells the agent that Chinese phrases cannot be verified; it says cards and source are both Chinese."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    assert gen(kb, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "原文是英文" not in text
    assert "英文原句" not in text
    assert "拿中文去 verify" not in text
    assert section(text, "2.").split("\n")[0] == "## 2. 卡片和原文都是中文"
    assert "- 回答用中文，引文一律中文原句" in section(text, "2.")


@pytest.mark.parametrize("src", sorted(LANG))
def test_zh_template_any_source_lang(tmp_path, src):
    """02d-02 boundary: the records_zh template with an explicit source language renders that language and
    never English unless asked for."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    assert gen(kb, out, "--template", "records_zh", "--lang-source", src, "--lang-answer", "zh") == 0
    text = out.read_text(encoding="utf-8")
    assert ("原文是英文" in text) == (src == "en")


# --------------------------------------------------------------------------- 02d-03 parameters

COMBOS = [("en", "zh", "book", "quote"), ("zh", "zh", "records", "grep"), ("ja", "en", "document", "grep"),
          ("zh", "ja", "book", "quote")]


@pytest.mark.parametrize("template", ["generic", "records_zh"])
@pytest.mark.parametrize("src,ans,kind,cite", COMBOS)
def test_params_rendered(tmp_path, template, src, ans, kind, cite):
    """02d-03 (S2): each parameter appears where it belongs - the language heading and answer rule of §2, the
    source kind in §1 and in the description, the cite format in §3 - whichever template."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    assert gen(kb, out, "--template", template, "--lang-source", src, "--lang-answer", ans,
               "--source-kind", kind, "--cite-format", cite) == 0
    text = out.read_text(encoding="utf-8")
    S, A, K = LANG[src], LANG[ans], KIND[kind]
    s2 = section(text, "2.")
    head = f"## 2. 卡片和原文都是{S}" if src == ans else f"## 2. 卡片是{A}，原文是{S}"
    assert s2.split("\n")[0] == head
    assert f"- 回答用{A}，引文一律{S}原句" in s2
    assert f"逐字的{K}" in section(text, "1.")
    assert K in description(text)
    s3 = section(text, "3.")
    if cite == "grep":
        assert "(grep: <原文逐字片段>)" in s3 and "引用块" not in s3
    else:
        assert "引用块" in s3 and "(grep: <原文逐字片段>)" not in s3
    assert 'klode verify <卡 id> "<片段>"' in s3 and "`FOUND`" in s3 and "`NOT-FOUND`" in s3


def test_defaults_follow_shelves(tmp_path):
    """3.4 defaults: a book library (no items/records shelf) keeps the original English-source wording and quote
    format; a records library gets the zh/zh records template with grep citations."""
    book = make_kb(tmp_path / "b", shelves=("manual",))
    out = tmp_path / "book.md"
    assert gen(book, out) == 0
    text = out.read_text(encoding="utf-8")
    assert section(text, "2.").split("\n")[0] == "## 2. 卡片是中文，原文是英文"
    assert "引用块" in section(text, "3.")
    med = make_kb(tmp_path / "m")
    out2 = tmp_path / "med.md"
    assert gen(med, out2) == 0
    assert "(grep: <原文逐字片段>)" in section(out2.read_text(encoding="utf-8"), "3.")


@pytest.mark.parametrize("flag,value", [("--lang-source", "fr"), ("--source-kind", "novel"),
                                        ("--cite-format", "apa"), ("--template", "x")])
def test_unknown_param_is_usage(tmp_path, flag, value):
    """Guard: a value outside the closed sets is a usage error (2); nothing written."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    with pytest.raises(SystemExit) as exc:
        gen(kb, out, flag, value)
    assert exc.value.code == 2
    assert not out.exists()


# --------------------------------------------------------------------------- 02d-04 / 02d-05 examples


def test_example_from_desk_tests(tmp_path):
    """02d-04 (S1): the first example is the first dimension's first tuning question; the examples are the
    desk's questions in order, at most three; no placeholder example is left."""
    kb = make_kb(tmp_path, tests=[TESTS_A, TESTS_B, ["第四题"]])
    out = tmp_path / "a.md"
    assert gen(kb, out) == 0
    text = out.read_text(encoding="utf-8")
    ex = examples(text)
    assert ex[0] == TESTS_A[0]
    assert ex == (TESTS_A + TESTS_B)[:3]
    assert "补一个典型问题" not in text


def test_example_quotes_survive(tmp_path):
    """02d-04 edge: a question with a double quote round-trips through the YAML frontmatter unchanged."""
    q = '他说"最近一次"是哪天'
    kb = make_kb(tmp_path, tests=[[q]])
    out = tmp_path / "a.md"
    assert gen(kb, out) == 0
    assert examples(out.read_text(encoding="utf-8")) == [q]


def test_empty_desk_tests(tmp_path, capsys):
    """02d-05 (S2): every dimension with ``tests: []`` -> exit 20, reason ``no_examples`` on stderr, no file."""
    kb = make_kb(tmp_path, tests=[[], []])
    out = tmp_path / "a.md"
    assert gen(kb, out) == 20
    assert "no_examples" in capsys.readouterr().err
    assert not out.exists()


def test_missing_desk_is_no_examples(tmp_path, capsys):
    """02d-05 boundary: no desk.json at all is the same ``no_examples`` (20); exactly one question -> one
    example (the lower edge that passes)."""
    kb = make_kb(tmp_path / "a", desk=False)
    out = tmp_path / "a.md"
    assert gen(kb, out) == 20
    assert "no_examples" in capsys.readouterr().err
    assert not out.exists()
    one = make_kb(tmp_path / "b", tests=[["唯一一题"]])
    out2 = tmp_path / "b.md"
    assert gen(one, out2) == 0
    assert examples(out2.read_text(encoding="utf-8")) == ["唯一一题"]


# --------------------------------------------------------------------------- kept behaviour and guards


def test_card_table_and_tools(tmp_path):
    """Kept (03 A6): the card table lists every card but INDEX.md with id, shelf, title and the Thin first line
    without its grep anchor; the tools are the four klode-mcp tools of the server plus Read/Grep/Glob."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    assert gen(kb, out, "--mcp", "med-klode") == 0
    text = out.read_text(encoding="utf-8")
    assert "| `hemoglobin` | items | Hemoglobin | 血红蛋白三次结果 。 |" in text
    assert "| `sample-tablet` | items | Sample tablet | 示例片剂量记录。 |" in text
    assert "INDEX" not in section(text, "1.")
    fm, _ = frontmatter(text)
    tools = [t.strip() for t in fm_value(fm, "tools").split(",")]
    assert tools == ["mcp__med-klode__search_sources", "mcp__med-klode__zoom_card", "mcp__med-klode__verify_quote",
                     "mcp__med-klode__list_kbs", "Read", "Grep", "Glob"]
    assert fm[fm.index("mcpServers:") + 1] == "  - med-klode"


def test_desk_tools_when_frameworks_on(tmp_path):
    """Kept: with frameworks enabled and a synthesis, the desk tools and the dimension table are added; off ->
    neither."""
    on = make_kb(tmp_path / "on", frameworks=True)
    out = tmp_path / "on.md"
    assert gen(on, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "mcp__med-klode__diagnose" in text and "| `dosage-history` | 剂量变化 |" in text
    off = make_kb(tmp_path / "off")
    out2 = tmp_path / "off.md"
    assert gen(off, out2) == 0
    assert "diagnose" not in out2.read_text(encoding="utf-8")


def test_kb_root_never_absolute_home(tmp_path, monkeypatch):
    """A6 fix: a library under the home directory is shown as ``~/...``; the absolute home path never appears;
    ``--kb-root`` overrides the shown root verbatim."""
    monkeypatch.setenv("HOME", str(tmp_path))
    kb = make_kb(tmp_path / "data")
    out = tmp_path / "a.md"
    assert gen(kb, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "`~/data/kb/`" in text
    assert str(tmp_path) not in text
    assert gen(kb, out, "--kb-root", "kb/med/current") == 0
    assert "`kb/med/current/`" in out.read_text(encoding="utf-8")


def test_library_toml_incomplete(tmp_path, capsys):
    """Guard: ``[library]`` without ``id`` (``klode init`` output before init_kb) -> exit 30; nothing written."""
    kb = make_kb(tmp_path)
    toml = kb / "library.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('id = "med"\n', ""), encoding="utf-8")
    out = tmp_path / "a.md"
    assert gen(kb, out) == 30
    assert "library_toml_incomplete" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("kid", ["-med", "a:b"])
def test_bad_agent_name(tmp_path, kid, capsys):
    """Guard (docs sub-agents: a name starting with ``-`` or containing ``:`` is skipped) -> exit 30."""
    kb = make_kb(tmp_path, kid=kid)
    out = tmp_path / "a.md"
    assert gen(kb, out) == 30
    assert "bad_agent_name" in capsys.readouterr().err
    assert not out.exists()


def test_render_is_deterministic(tmp_path):
    """Determinism: the same library renders byte-identical text twice (render() equals the written file)."""
    kb = make_kb(tmp_path)
    out = tmp_path / "a.md"
    assert gen(kb, out, "--build-id", "b1") == 0
    assert G.render(kb, build_id="b1") == out.read_text(encoding="utf-8") == G.render(kb, build_id="b1")
