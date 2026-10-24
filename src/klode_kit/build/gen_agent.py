"""The ``<id>-klode-expert`` agent for the data project: cite, don't recall (WI-02d; vendored).

Vendored from the user's klode-build skill ``scripts/gen_agent.py`` (read-only source, 2026-10-24).

    klode-kit gen-agent KB --out F [--template generic|records_zh] [--lang-source en|zh|ja] [--lang-answer en|zh|ja]
                               [--source-kind book|records|document] [--cite-format quote|grep] [--build-id ID]
                               [--mcp NAME] [--kb-root TEXT] [--spec F]
    exit 0 written; 2 usage; 20 no_examples (the desk has no tuning question, or no desk.json); 30 library.toml
    without [library] id/name/description, an id that cannot name an agent, or an unreadable desk spec

The agent goes to the data project's ``.claude/agents/`` (02 §2.1: plugin subagents ignore ``mcpServers``, docs
https://code.claude.com/docs/en/sub-agents - so the expert is never shipped in the plugin). In a versioned build it
is written to ``builds/<build_id>/agent/`` and copied by publish (02 r3.2).

Kept from the original (03 A6): the tool list (the four klode-mcp tools of the library's server plus Read / Grep /
Glob; with the desk on, ``list_lenses / diagnose / consult_dimension`` and the dimension table), the card table
(id, shelf, title, the Thin first line without its grep anchor), and the discipline: cite, don't recall; two words
or more -> ``full=true``; ``level="content"`` returns no text; nothing found -> say so; no rewriting, no writes.

Changed:

- **3.4** (06 r1; 02 r2.1): the language, source kind and citation format are parameters instead of the hard-coded
  "卡片是中文，原文是英文 ... 引文一律英文" that misleads a Chinese records library. ``--template records_zh``
  (``templates/agent_records_zh.md``) is the medical template; ``generic`` (``templates/agent_generic.md``) is the
  original. Defaults follow the library: a library with an ``items`` or ``records`` shelf is ``records`` /
  ``records_zh`` / zh -> zh / ``grep``; any other is ``book`` / ``generic`` / en -> zh / ``quote`` (the original).
- **R4-03** (06 r4; docs sub-agents "an opening ``---`` that isn't the file's first line" makes the file plain
  documentation): line 1 is ``---``; the build marker ``<!-- klode-kit build <id> -->`` (02 r3.3 publish log) is the
  line right after the closing ``---``. Without ``--build-id`` it reads ``unversioned``.
- **A6**: the description's ``<example>`` blocks are the desk's tuning questions (``src/desk.json`` dimensions'
  ``tests``, in order, at most three) instead of a placeholder for a human to fill; none -> exit 20 ``no_examples``
  (02 r5), nothing written. The root shown is ``~/...`` under the home directory (or ``--kb-root`` verbatim), never
  a path relative to one machine's project folder.
- Citations name klode 0.5.0's real CLI check ``klode verify <card> <phrase>`` (``FOUND`` / ``NOT-FOUND``; the
  MCP ``verify_quote`` says ``VERIFIED`` / ``NOT FOUND``).
- ``library.toml`` is read with ``tomllib`` (the original regex took the first ``id =`` of any table and crashed
  when one was missing); the frontmatter is YAML-safe (the description is one JSON string, which is a YAML
  double-quoted scalar) and the agent name obeys the sub-agents rules (no leading ``-``, no ``:``, <= 256 chars).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

EXIT_USAGE, EXIT_NO_EXAMPLES, EXIT_CONTRACT = 2, 20, 30
LANGS = {"en": "英文", "zh": "中文", "ja": "日文"}
SOURCE_KINDS = {"book": "原著", "records": "单据记录", "document": "文档"}
CITE_FORMATS = ("quote", "grep")
TEMPLATES = {"generic": "agent_generic.md", "records_zh": "agent_records_zh.md"}
RECORD_SHELVES = {"items", "records"}
MAX_EXAMPLES = 3
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BUILD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._]*(?:-[A-Za-z0-9._]+)*$")  # no "--": it would end the comment
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
GREP_RE = re.compile(r"\(grep:[^)]*\)")

DESCRIPTIONS = {
    "generic": ("{lib_name} 的专家顾问（klode 知识库 {kid}）。{desc}。凡是要引这些{kind}的原话、问「书上怎么说 / 作者怎么定义 / "
                "这个案例讲了什么」、或要写引用原文的材料时，派这个 agent。它只从 {kid} 知识库引用，每句引文先 verify_quote "
                "再给，不凭记忆。"),
    "records_zh": ("{lib_name} 的查询助手（klode 知识库 {kid}）。{desc}。凡是要查这些{kind}里的数值、单位、日期、药名或剂量，"
                   "或要引用记录原文时，派这个 agent。它只从 {kid} 知识库引用，每条先 verify_quote 再给，查不到就说查不到，"
                   "不凭记忆、不编值。"),
}


class GenAgentError(Exception):
    """A refusal with its exit code and machine-readable reason."""

    def __init__(self, code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.code, self.reason = code, reason


# --------------------------------------------------------------------------- inputs


def _library(kb: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads((kb / "library.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GenAgentError(EXIT_CONTRACT, "library_toml_incomplete", f"library.toml unreadable: {exc}") from exc
    lib = data.get("library") if isinstance(data.get("library"), dict) else {}
    missing = [k for k in ("id", "name", "description") if not isinstance(lib.get(k), str) or not lib.get(k)]
    if missing:
        raise GenAgentError(EXIT_CONTRACT, "library_toml_incomplete",
                            f"[library] lacks {', '.join(missing)} (init_kb fills them)")
    return data


def examples(spec_path: Path) -> list[str]:
    """The desk's tuning questions in order (dimensions, then their ``tests``), at most ``MAX_EXAMPLES``."""
    if not spec_path.is_file():
        raise GenAgentError(EXIT_NO_EXAMPLES, "no_examples", f"no desk spec at {spec_path.name}; no example question")
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        dims = spec["dimensions"]
        questions = [t for d in dims for t in d.get("tests", [])]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GenAgentError(EXIT_CONTRACT, "spec_invalid", f"desk spec unreadable: {exc}") from exc
    if not all(isinstance(q, str) and q.strip() for q in questions):
        raise GenAgentError(EXIT_CONTRACT, "spec_invalid", "desk spec: every tests item must be a non-empty string")
    if not questions:
        raise GenAgentError(EXIT_NO_EXAMPLES, "no_examples", "the desk has no tuning question (tests are empty)")
    return questions[:MAX_EXAMPLES]


def _field(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}:\s*(\S+)", text, flags=re.M)
    return m.group(1) if m else None


def card_table(cards_dir: Path) -> str:
    rows = []
    for p in sorted(cards_dir.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        t = p.read_text(encoding="utf-8")
        cid = _field(t, "id") or p.stem
        shelf = _field(t, "shelf") or "?"
        title_m = re.search(r"^# (.+)$", t, flags=re.M)
        thin = re.search(r"^## Thin\n(.+?)$", t, flags=re.M | re.S)
        hint = GREP_RE.sub("", thin.group(1).split("\n")[0]).strip()[:70] if thin else ""
        rows.append(f"  | `{cid}` | {shelf} | {title_m.group(1) if title_m else cid} | {hint} |")
    return "\n".join(["  | 卡 id | 书架 | 标题 | 一句话 |", "  |---|---|---|---|"] + rows)


def _dimensions(kb: Path, data: dict[str, Any]) -> list[tuple[str, str]]:
    fw = data.get("frameworks") if isinstance(data.get("frameworks"), dict) else {}
    if fw.get("enabled") is not True:
        return []
    lib = data["library"]
    syn = kb / str(lib.get("dir", "library")) / str(fw.get("dir", "frameworks")) / str(fw.get("syntheses", "_syntheses"))
    dims = []
    for p in sorted(syn.glob("*.md")) if syn.is_dir() else []:
        if p.name.startswith("_"):
            continue
        m = re.search(r"^title:\s*(.*)$", p.read_text(encoding="utf-8"), flags=re.M)
        dims.append((p.stem, m.group(1).strip() if m else p.stem))
    return dims


def _desk_section(dims: list[tuple[str, str]]) -> str:
    if not dims:
        return ""
    rows = "\n".join(f"  | `{d}` | {t} |" for d, t in dims)
    return f'''
## 1b. 书桌（frameworks）：问「怎么做」先走这里

本库开了书桌，{len(dims)} 个维度，每个是跨卡蒸馏的「机制 / 动作 / 可选项」，锚点全部来自卡片：

  | 维度 | 标题 |
  |---|---|
{rows}

- 用户问的是**怎么做 / 该怎么选 / 为什么这样**这类问题时，先 `diagnose(symptom="<用户原话>")` 路由到维度，再 `consult_dimension(dimension="<维度>")` 取 Craft；
  引用其中的锚点原句前仍要对来源卡 `verify_quote`。
- 问的是**某处怎么说**这类定位问题，直接走卡片（§3）。
- `list_lenses` 看全部维度。MCP 参数名：`diagnose` 用 `symptom`，`consult_dimension` 用 `dimension`。
'''


def _lang_section(src: str, ans: str, kind: str) -> str:
    S, A, K = LANGS[src], LANGS[ans], SOURCE_KINDS[kind]
    tip = "- 最省事：从卡片 Full 的 `(grep: …)` 锚点里直接取原文片段——它们已全部通过 `klode check --strict`。"
    if src == ans:
        return "\n".join([
            f"## 2. 卡片和原文都是{S}", "",
            f"- 卡片的 Thin/Full 是{A}说明，源 `.txt` 是逐字的{K}。`verify_quote` 搜的是**源文本**：只有源里逐字存在的片段才会 "
            "VERIFIED，卡片里的概括句、改写过的句子不行。",
            tip,
            f"- 回答用{A}，引文一律{S}原句，逐字照抄。"])
    return "\n".join([
        f"## 2. 卡片是{A}，原文是{S}", "",
        f"- 卡片的 Thin/Full 是{A}说明；源 `.txt` 是{S}的{K}。`verify_quote` 搜的是**源文本**，拿{A}去 verify 永远 NOT FOUND。",
        tip,
        f"- 回答用{A}，引文一律{S}原句，需要时在引文下给{A}直译，原文在先。"])


def _verify_step() -> str:
    return ("**验证后再引**：`verify_quote(id, phrase)` → `VERIFIED` 可引；`NOT FOUND` 不许引，换短语；`AMBIGUOUS` 加长到唯一"
            "再验。用户要自己复核时，在库根目录（`library.toml` 所在处）运行 `klode verify <卡 id> \"<片段>\"`：klode 0.5.0 "
            "回 `FOUND` 即可引，回 `NOT-FOUND` 即不可引。")


def _cite_step(cite: str, src: str, ans: str) -> str:
    S, A = LANGS[src], LANGS[ans]
    if cite == "grep":
        return (f"**回答格式**：先一句{A}结论；再逐条列出，每条末尾带逐字锚点 `(grep: <原文逐字片段>)` 和卡 id，例如 "
                "`- 2026-09-18 一次 (grep: <原文逐字片段>) — 卡 <id>`。锚点里的片段必须是第 4 步验证过的原文，不改一个字。")
    return (f"**回答格式**：先一句{A}结论；再「出处」清单，每条 = {S}原句（引用块）+ 卡 id。说明走正文，原件走引用块，"
            "一眼可分。")


def _kb_root(kb: Path, override: str | None) -> str:
    if override:
        return override.rstrip("/") or "/"
    home = Path.home().resolve()
    return f"~/{kb.relative_to(home).as_posix()}" if kb.is_relative_to(home) else kb.as_posix()


# --------------------------------------------------------------------------- render


def render(kb: Path, *, template: str | None = None, lang_source: str | None = None, lang_answer: str | None = None,
           source_kind: str | None = None, cite_format: str | None = None, build_id: str | None = None,
           mcp: str | None = None, kb_root: str | None = None, spec: Path | None = None) -> str:
    """The agent file's text. Raises ``GenAgentError`` (exit code + reason) on a refusal; ``ValueError`` on a
    parameter outside its closed set."""
    kb = kb.resolve()
    data = _library(kb)
    lib = data["library"]
    kid = lib["id"]
    name = f"{kid}-klode-expert"
    if not NAME_RE.match(kid) or len(name) > 256:
        raise GenAgentError(EXIT_CONTRACT, "bad_agent_name",
                            f"library id {kid!r} cannot name an agent (no leading '-', no ':', <= 256 chars)")
    shelves = lib.get("shelves") if isinstance(lib.get("shelves"), list) else []
    records = bool(RECORD_SHELVES & set(map(str, shelves)))
    source_kind = source_kind or ("records" if records else "book")
    template = template or ("records_zh" if source_kind == "records" else "generic")
    lang_source = lang_source or ("zh" if template == "records_zh" else "en")
    lang_answer = lang_answer or "zh"
    cite_format = cite_format or ("grep" if source_kind == "records" else "quote")
    build_id = build_id or "unversioned"
    mcp = mcp or f"{kid}-klode"
    if template not in TEMPLATES or lang_source not in LANGS or lang_answer not in LANGS \
            or source_kind not in SOURCE_KINDS or cite_format not in CITE_FORMATS:
        raise ValueError("parameter outside its closed set")
    if not BUILD_ID_RE.match(build_id):
        raise ValueError(f"build id {build_id!r}: letters, digits, '.', '_' and single '-' only")
    if not NAME_RE.match(mcp):
        raise ValueError(f"MCP server name {mcp!r}")

    questions = examples(spec or kb / "src" / "desk.json")
    dims = _dimensions(kb, data)
    tools = [f"mcp__{mcp}__{t}" for t in ("search_sources", "zoom_card", "verify_quote", "list_kbs")]
    if dims:
        tools += [f"mcp__{mcp}__{t}" for t in ("list_lenses", "diagnose", "consult_dimension")]
    kind = SOURCE_KINDS[source_kind]
    desc = DESCRIPTIONS[template].format(lib_name=lib["name"], kid=kid, desc=lib["description"].rstrip("。."),
                                         kind=kind)
    desc += "".join(f"\n\n<example>\nuser: {json.dumps(q, ensure_ascii=False)}\nassistant: \"派 {name}：它会 "
                    "search_sources 定位卡片、zoom_card 取要点、verify_quote 后逐字引原文。\"\n</example>"
                    for q in questions)
    cards_dir = kb / str(lib.get("dir", "library")) / str(lib.get("cards", "cards"))
    values = {
        "name": name, "description": json.dumps(desc, ensure_ascii=False), "tools": ", ".join(tools + ["Read", "Grep", "Glob"]),
        "mcp": mcp, "build_id": build_id, "lib_name": lib["name"], "kid": kid, "kb_root": _kb_root(kb, kb_root),
        "source_kind": kind, "card_table": card_table(cards_dir), "desk_section": _desk_section(dims),
        "lang_section": _lang_section(lang_source, lang_answer, source_kind), "verify_step": _verify_step(),
        "cite_step": _cite_step(cite_format, lang_source, lang_answer),
    }
    text = (resources.files("klode_kit") / "templates" / TEMPLATES[template]).read_text(encoding="utf-8")
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)  # one pass: filled text is never scanned again


def _write(out: Path, text: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit gen-agent", description=__doc__.split("\n")[0])
    ap.add_argument("kb", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="agent file (data project .claude/agents/<id>-klode-expert.md)")
    ap.add_argument("--template", choices=sorted(TEMPLATES))
    ap.add_argument("--lang-source", choices=sorted(LANGS))
    ap.add_argument("--lang-answer", choices=sorted(LANGS))
    ap.add_argument("--source-kind", choices=sorted(SOURCE_KINDS))
    ap.add_argument("--cite-format", choices=CITE_FORMATS)
    ap.add_argument("--build-id", default=None)
    ap.add_argument("--mcp", default=None, help="MCP server name (default <id>-klode)")
    ap.add_argument("--kb-root", default=None, help="root shown to the agent (default ~/... or the absolute path)")
    ap.add_argument("--spec", type=Path, default=None, help="desk spec (default KB/src/desk.json)")
    a = ap.parse_args(argv)
    try:
        text = render(a.kb, template=a.template, lang_source=a.lang_source, lang_answer=a.lang_answer,
                      source_kind=a.source_kind, cite_format=a.cite_format, build_id=a.build_id, mcp=a.mcp,
                      kb_root=a.kb_root, spec=a.spec)
    except GenAgentError as exc:
        print(f"klode-kit gen-agent: {exc.reason}: {exc}", file=sys.stderr)
        return exc.code
    except ValueError as exc:
        print(f"klode-kit gen-agent: usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    _write(a.out, text)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
