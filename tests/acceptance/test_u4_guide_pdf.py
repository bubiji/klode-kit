"""U4 (04 section 6): a guideline PDF into a library - ``kb-ocr guide-2026.pdf`` then ``kb-build --kb kb/guide-klode``.

What the user sees (04): ``receipt.json`` says ``text_layer: true, routes_skipped: [vision,mlx,claude,codex]``;
``build_receipt.json`` has all 13 steps PASS; ``klode -c ... check --strict`` prints ``OK: 0 errors, 0 warning(s)``.

Matrix U4-01, U4-02 (lane cli_integration; poppler and klode 0.5.0 are real, a missing tool FAILS). ``fx-text-pdf`` is the
guide (three pages, a text layer on each). The book path of ``build`` takes the text ``kb-ocr`` made of the PDF
(``work/<batch>/text/<doc>_whole.txt``) as its ``--source``; the three card writers are the replayed ``claude`` (a
real model call is G2), the cards' anchors are lines cut out of the PDF's own text, the desk workers' syntheses are
copied from the cards (the same way the book build tests do it). What is proved is the user path: the PDF's text, not
a model, decides what the library holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import desk_fx as DX
from _support import kb_ocr as K
from _support.write_card_fx import content as card_content

pytestmark = pytest.mark.cli_integration

GUIDE = K.FIXTURES / "fx-text-pdf" / "guide-2026.pdf"
MIXED = K.FIXTURES / "fx-mixed-pdf" / "mixed.pdf"
HEAD = re.compile(r"^第[一二三四五六七八九十]+章 ")
ROUTES = ["vision", "mlx", "claude", "codex"]
# words the chapters really use (every cue and alias is a phrase of the guide); the three dimensions are the chapters
CHAPTER_TERMS = {
    "ch01": ["示例流程", "登记阶段", "识别阶段", "核对阶段", "建库阶段", "单据类型", "多路引擎", "本地确认页", "合成文本",
             "已经确认的记录"],
    "ch02": ["多数票", "推荐值", "自动确认", "不确定条目", "确认页", "缺少某一路", "争议", "人工输入", "区域证据", "平票"],
    "ch03": ["时间序列文本", "每次观测", "引文", "回路", "发布时", "指针", "旧版本", "回滚", "检查阶段", "卡片"],
}
DIMENSIONS = [("process", "ch01", ["示例流程", "登记", "识别"], "示例流程分为哪几个阶段？"),
              ("rules", "ch02", ["多数票", "平票", "人工输入"], "什么情况下记录可以自动确认？"),
              ("build-check", "ch03", ["时间序列", "引文", "回滚"], "建库后怎样检查和回滚？")]
HOLDOUT = [("登记阶段记录什么？", "process"), ("识别阶段由谁读取页面文字？", "process"),
           ("平票的单元格怎么处理？", "rules"), ("人工输入的值要附带什么？", "rules"),
           ("旧版本在发布后怎么办，回滚怎么做？", "build-check")]


def _chapters(whole: str) -> dict[str, list[str]]:
    """``{ch01: [lines of the chapter], ...}`` of the whole text, heads excluded."""
    out: dict[str, list[str]] = {}
    current = None
    for line in whole.splitlines():
        if HEAD.match(line):
            current = f"ch{len(out) + 1:02d}"
            out[current] = []
        elif current and line.strip():
            out[current].append(line)
    return out


def _anchors(lines: list[str]) -> list[str]:
    """16-character pieces cut from the lines - verbatim, each inside one line (a quotation must not cross a break;
    a quotation shorter than 15 characters is not a card anchor)."""
    pieces = []
    for line in lines:
        for start in range(0, len(line) - 15, 16):
            pieces.append(line[start:start + 16])
    return pieces


def guide_inputs(tmp: Path, whole: Path) -> tuple[list[str], dict[str, str]]:
    """``--source/--manifest/--desk/--syntheses`` and the replayed card writers for the guide text."""
    text = whole.read_text(encoding="utf-8")
    chapters = _chapters(text)
    assert sorted(chapters) == ["ch01", "ch02", "ch03"]
    # ``klode ingest`` joins the PDF's hard-wrapped lines into one line per paragraph: the manifest counts those lines
    reflowed: list[str] = []
    for line in text.splitlines():
        if HEAD.match(line) or not reflowed or HEAD.match(reflowed[-1]):
            reflowed.append(line)
        elif line.strip():
            reflowed[-1] += " " + line
    pieces = [{"id": f"ch{n:02d}", "title": ln.strip(), "start_line": i}
              for n, (i, ln) in enumerate(((i, ln) for i, ln in enumerate(reflowed, 1) if HEAD.match(ln)), start=1)]
    assert [p["id"] for p in pieces] == list(chapters)
    pieces[0]["start_line"] = 1
    manifest = tmp / "chapters-book.json"
    manifest.write_text(json.dumps({"shelf": "book", "whole": "library/book/_whole-book.txt", "citation": "示例指南 2026",
                                    "pieces": pieces}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    anchors = {cid: _anchors(lines) for cid, lines in chapters.items()}
    for cid, group in anchors.items():
        assert len(group) >= 5 and all(len(a) >= 15 and a in text for a in group)
    desk = tmp / "desk" / "desk.json"
    desk.parent.mkdir()
    desk.write_text(json.dumps({"threshold": 0.8, "dimensions": [
        {"dimension": dim, "title": dim, "question": q, "cards": [cid], "cues": cues, "tests": [q]}
        for dim, cid, cues, q in DIMENSIONS]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (desk.parent / "holdout.jsonl").write_text("".join(json.dumps({"q": q, "gold": g}, ensure_ascii=False) + "\n"
                                                       for q, g in HOLDOUT), encoding="utf-8")
    syn = tmp / "syntheses"
    syn.mkdir()
    for dim, cid, _cues, _q in DIMENSIONS:
        (syn / f"{dim}.md").write_text(DX.synthesis(dim, [cid], anchors[cid][:4]), encoding="utf-8")
    by_card = {}
    for cid, group in anchors.items():
        card = f"cards_content/{cid}.md"
        receipt = {"card": card, "status": "written", "kind": "authored", "anchors_full": len(group),
                   "aliases": len(CHAPTER_TERMS[cid]), "rounds": 1}
        by_card[card] = [{"write_files": {card: card_content(group, CHAPTER_TERMS[cid])},
                          "stdout": json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                                "total_cost_usd": 0.01, "structured_output": receipt,
                                                "permission_denials": []}), "exit_code": 0}]
    scenario = tmp / "writer-scenario.json"
    scenario.write_text(json.dumps({"by_card": by_card}, ensure_ascii=False), encoding="utf-8")
    return ["--source", str(whole), "--shelf", "book", "--manifest", str(manifest), "--desk", str(desk),
            "--syntheses", str(syn)], {"KLODE_KIT_FAKE_SCENARIO": str(scenario),
                                       "KLODE_KIT_FAKE_LOG": str(tmp / "claude-calls.jsonl")}


def test_u4_guide_pdf(tmp_path):
    """U4-01 (L3 S1)."""
    project = tmp_path / "project"
    project.mkdir()
    log = tmp_path / "engine-calls.jsonl"
    empty = tmp_path / "no-engine"
    empty.mkdir()
    res = K.run_kb_ocr(project, GUIDE, K.kb_ocr_env(empty, log), launcher=True)
    assert res.returncode == 0, res.stderr.decode()
    receipt = K.receipt(project)
    assert receipt["text_layer"] is True and receipt["routes_skipped"] == ROUTES
    assert K.log_calls(log) == []  # not one route was asked
    batch = K.only_batch(project)
    (whole,) = sorted((batch / "text").glob("*_whole.txt"))

    args, writer_env = guide_inputs(tmp_path, whole)
    built = B.build(project, tmp_path, "--kb", "kb/guide-klode", *args, extra_env=writer_env)
    assert built.returncode == 0, built.stdout.decode()[-1200:] + built.stderr.decode()[-1200:]
    (bd,) = B.builds(project)
    rec = B.receipt(bd)
    assert rec["kb_id"] == "guide-klode" and rec["status"] == "PASS"
    assert [s["step"] for s in rec["steps"]] == [
        "export", "init_kb", "ingest", "split_manifest", "klode_build", "dispatch_writers", "gate1", "write_card",
        "check_strict", "desk_apply", "desk_verify", "gen_agent", "self_check"]
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * 13
    # the library holds the PDF's own text, chapter by chapter
    pieces = [(bd / "library" / "book" / f"{cid}.txt").read_text(encoding="utf-8") for cid in ("ch01", "ch02", "ch03")]
    ingested = (bd / "_raw" / "_whole-book.txt").read_text(encoding="utf-8")
    assert "".join(pieces) == ingested  # the three pieces put back are the whole, as ``klode ingest`` made it
    # ``klode ingest`` joins the hard-wrapped lines of the PDF text with a space (a CJK word cut by a line break gets
    # one: "登 记" - noted in impl-deviations); apart from white space it is exactly the PDF's text
    assert "".join(ingested.split()) == "".join(whole.read_text(encoding="utf-8").split())
    assert "第二章 核对规则" in pieces[1] and "第三章 建库与检查" in pieces[2]
    # klode itself says so, run by hand on the finished tree
    from _support.accept_fx import klode

    strict = klode(bd, "check", "--strict")
    assert strict.returncode == 0
    assert strict.stdout.strip().splitlines()[-1] == "OK: 0 errors, 0 warning(s)", strict.stdout
    # the writers were asked for three cards and nothing else left the machine
    assert (tmp_path / "claude-calls.jsonl").read_text(encoding="utf-8").count('"tool": "claude"') == 3


def test_u4_mixed_pdf_not_skipped(tmp_path):
    """U4-02 (L3 S2): a PDF that is only partly text is not skipped as a whole: ``text_layer`` is ``partial``, no route
    is skipped for the batch, and only the page with a text layer needs no route."""
    project = tmp_path / "project"
    project.mkdir()
    log = tmp_path / "calls.jsonl"
    replay = tmp_path / "replay"
    replay.mkdir()
    res = K.run_kb_ocr(project, MIXED, K.kb_ocr_env(replay, log), launcher=True)
    assert res.returncode == 0, res.stderr.decode()
    receipt = K.receipt(project)
    scenario = json.loads((K.FIXTURES / "fx-mixed-pdf" / "scenario.json").read_text(encoding="utf-8"))
    assert receipt["text_layer"] == "partial" and receipt["routes_skipped"] == []
    assert [p["text_class"] for p in receipt["pages"]] == [p["class"] for p in scenario["pages"]]
    skipped_pages = {p["key"] for p in receipt["pages"] if all(r["status"] == "skipped" for r in p["routes"].values())}
    assert skipped_pages == {p["key"] for p in receipt["pages"] if p["text_class"] == "text"}
    asked = {(c["tool"], c["page"]) for c in K.log_calls(log)}
    assert asked == {(r, p["key"]) for r in ROUTES for p in receipt["pages"] if p["text_class"] != "text"}
