"""WI-03a: ``build/gen_manifest`` - the chapter manifest generator (matrix 03a-04 .. 03a-06; design r2.4, r3.4).

Specification (design 02 r2.4 ``gen_manifest`` row; 03 §6.1 "正文第 2 次出现"; original klode-build SKILL.md
steps 3-5; WI-02a ``split_by_manifest``):

- ``heads(text, patterns=None) -> [(line_no, head)]``: the ``split_by_manifest`` scan candidates (packaged
  ``head_re.toml`` incl. Chinese heads) with the table of contents removed: when the same head occurs more than
  once (compared without TOC dot leaders / page numbers), its first occurrence is the TOC entry and is dropped,
  the body occurrence kept. A head whose piece would hold nothing but the head itself (a Part title page directly
  followed by the next head) is merged into the next piece: the merged head keeps the earlier line number and is
  titled ``"<earlier> / <next>"``.
- ``gen_manifest(kb, *, shelf, whole=None, manifest=None, prefix=None, citation=None, head_re=None, apply=False)``
  writes ``src/chapters-<shelf>.json`` (the ``split_by_manifest`` format) and returns ``{"mode", "path",
  "manifest"}``:
  * ``whole`` defaults to ``library/<shelf>/_whole-<shelf>.txt`` (relative to ``kb``);
  * fewer than 2 heads -> ``mode == "single"``: one piece from line 1 (the whole as one source, the run goes on);
  * 2 or more heads -> ``mode == "auto"``: one piece per head, plus a front piece from line 1 when non-blank text
    comes before the first head;
  * an explicit ``manifest`` (U7) -> ``mode == "explicit"``: its pieces are used as given, no scan;
  * piece ids are plain names (``split_by_manifest.NAME_RE``), unique, ``<prefix>-chNN[-slug]``;
  * before anything is written the pieces are cut and put back: they must equal the whole byte for byte, the
    first piece starts at line 1 and none is empty - otherwise ``ManifestError`` (CLI exit 1) and nothing at all
    is written (no manifest, no piece);
  * ``apply=True`` then splits (``split_by_manifest.split(..., apply=True)``): one ``library/<shelf>/<id>.txt``
    per piece, the whole moved into ``_raw/``.
  * an existing ``src/chapters-<shelf>.json`` that differs from the result is never overwritten
    (``ManifestError``); the user passes it as ``manifest`` instead. Equal -> nothing written.

Law: the generated manifest is a partition - the written pieces put back == the whole (S5); generation is
deterministic.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.build import gen_manifest as G
from klode_kit.build import split_by_manifest as S

pytestmark = pytest.mark.unit

SHELF = "book"
WHOLE = f"library/{SHELF}/_whole-{SHELF}.txt"


def make_kb(root: Path, text: str) -> Path:
    kb = root / "kb"
    (kb / "library" / SHELF).mkdir(parents=True)
    (kb / "src").mkdir()
    (kb / "_raw").mkdir()
    (kb / WHOLE).write_bytes(text.encode("utf-8"))
    return kb


def body(n: int, tag: str) -> list[str]:
    return [f"{tag} paragraph {i} with enough words to read like prose." for i in range(n)]


def chapter_text(n_heads: int, front: bool = True) -> tuple[str, list[int]]:
    lines: list[str] = []
    if front:
        lines += ["A Synthetic Handbook", ""]
    starts = []
    for k in range(1, n_heads + 1):
        starts.append(len(lines) + 1)
        lines += [f"Chapter {k}: Part Number {k}", ""] + body(3, f"c{k}") + [""]
    if n_heads == 0:
        lines += body(5, "plain")
    return "\n".join(lines) + "\n", starts


def piece_files(kb: Path) -> list[Path]:
    return sorted(p for p in (kb / "library" / SHELF).glob("*.txt") if not p.name.startswith("_whole"))


def written_tree(kb: Path) -> dict[str, bytes]:
    return {p.relative_to(kb).as_posix(): p.read_bytes() for p in sorted(kb.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------------------------------------------
# 03a-04 fewer than 2 heads -> the whole as a single source
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n_heads", [0, 1, 2])
def test_lt2_heads_single_source(tmp_path, n_heads):
    text, starts = chapter_text(n_heads)
    kb = make_kb(tmp_path, text)
    res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book", apply=True)
    pieces = res["manifest"]["pieces"]
    on_disk = json.loads((kb / "src" / f"chapters-{SHELF}.json").read_text(encoding="utf-8"))
    assert on_disk == res["manifest"]
    if n_heads < 2:
        assert res["mode"] == "single"
        assert [p["start_line"] for p in pieces] == [1]
        files = piece_files(kb)
        assert len(files) == 1 and files[0].read_bytes() == text.encode("utf-8")
    else:
        assert res["mode"] == "auto"
        assert [p["start_line"] for p in pieces] == [1, *starts]
        assert len(piece_files(kb)) == 3
    assert (kb / "_raw" / f"_whole-{SHELF}.txt").read_bytes() == text.encode("utf-8")


def test_two_heads_without_front_matter(tmp_path):
    text, starts = chapter_text(2, front=False)
    kb = make_kb(tmp_path, text)
    res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book")
    assert starts[0] == 1
    assert [p["start_line"] for p in res["manifest"]["pieces"]] == starts
    assert piece_files(kb) == []  # no --apply: manifest only


# ---------------------------------------------------------------------------------------------------------------
# 03a-05 a manifest that does not put back aborts before any write
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("starts", [
    [2, 10],          # the first piece does not start at line 1: the leading lines would be lost
    [1, 10, 10],      # an empty piece
    [1, 10, 999],     # past the end of the whole
])
def test_split_selfcheck_fail_aborts(tmp_path, starts):
    text, _ = chapter_text(3)
    kb = make_kb(tmp_path, text)
    bad = {"shelf": SHELF, "whole": WHOLE, "citation": "c",
           "pieces": [{"id": f"demo-book-ch{i:02d}", "title": f"t{i}", "start_line": s} for i, s in enumerate(starts)]}
    mf = tmp_path / "bad.json"
    mf.write_text(json.dumps(bad), encoding="utf-8")
    before = written_tree(kb)
    with pytest.raises(G.ManifestError):
        G.gen_manifest(kb, shelf=SHELF, manifest=mf, apply=True)
    assert written_tree(kb) == before
    assert G.main([str(kb), "--shelf", SHELF, "--manifest", str(mf), "--apply"]) == 1
    assert written_tree(kb) == before


# ---------------------------------------------------------------------------------------------------------------
# 03a-06 an explicit manifest (U7) wins over the scan
# ---------------------------------------------------------------------------------------------------------------
def test_explicit_manifest_overrides(tmp_path):
    text, starts = chapter_text(3)
    kb = make_kb(tmp_path, text)
    lines = S.lines_of(text)
    mine = {"shelf": SHELF, "whole": WHOLE, "citation": "Synthetic Handbook",
            "pieces": [{"id": "demo-book-a", "title": "A", "start_line": 1},
                       {"id": "demo-book-b", "title": "B", "start_line": 5}]}
    assert 5 not in starts
    mf = tmp_path / "mine.json"
    mf.write_text(json.dumps(mine), encoding="utf-8")
    res = G.gen_manifest(kb, shelf=SHELF, manifest=mf, apply=True)
    assert res["mode"] == "explicit"
    assert [(p["id"], p["start_line"]) for p in res["manifest"]["pieces"]] == [("demo-book-a", 1), ("demo-book-b", 5)]
    files = piece_files(kb)
    assert [f.stem for f in files] == ["demo-book-a", "demo-book-b"]
    assert files[0].read_text(encoding="utf-8") == "".join(lines[:4])
    assert files[1].read_text(encoding="utf-8") == "".join(lines[4:])


def test_explicit_manifest_shelf_mismatch_refused(tmp_path):
    text, _ = chapter_text(2)
    kb = make_kb(tmp_path, text)
    mf = tmp_path / "other.json"
    mf.write_text(json.dumps({"shelf": "other", "whole": WHOLE,
                              "pieces": [{"id": "x-a", "title": "A", "start_line": 1}]}), encoding="utf-8")
    before = written_tree(kb)
    with pytest.raises(G.ManifestError):
        G.gen_manifest(kb, shelf=SHELF, manifest=mf)
    assert written_tree(kb) == before


# ---------------------------------------------------------------------------------------------------------------
# heads: TOC duplicates, Chinese heads, Part title pages
# ---------------------------------------------------------------------------------------------------------------
def test_toc_heads_take_body_occurrence(tmp_path):
    lines = ["The Handbook", "", "Contents", "Chapter 1: Copper ........ 3", "Chapter 2: Valves\t\t9", ""]
    s1 = len(lines) + 1
    lines += ["Chapter 1: Copper", ""] + body(4, "a") + [""]
    s2 = len(lines) + 1
    lines += ["Chapter 2: Valves", ""] + body(4, "b")
    text = "\n".join(lines) + "\n"
    assert [n for n, _ in G.heads(text)] == [s1, s2]
    kb = make_kb(tmp_path, text)
    res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book")
    assert [p["start_line"] for p in res["manifest"]["pieces"]] == [1, s1, s2]


def test_chinese_heads(tmp_path):
    lines = ["合成手册", ""]
    s1 = len(lines) + 1
    lines += ["第一章 铜壳", ""] + ["这是第一章的正文段落，内容为合成文本。"] * 3 + [""]
    s2 = len(lines) + 1
    lines += ["第二章 哨阀", ""] + ["这是第二章的正文段落，内容为合成文本。"] * 3
    text = "\n".join(lines) + "\n"
    kb = make_kb(tmp_path, text)
    res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book", apply=True)
    pieces = res["manifest"]["pieces"]
    assert [p["start_line"] for p in pieces] == [1, s1, s2]
    assert [p["title"] for p in pieces[1:]] == ["第一章 铜壳", "第二章 哨阀"]
    assert all(S.NAME_RE.match(p["id"]) for p in pieces)
    assert b"".join(f.read_bytes() for f in sorted(piece_files(kb), key=lambda f: [p["id"] for p in pieces].index(f.stem))) \
        == text.encode("utf-8")


def test_part_title_page_merges_into_next_chapter():
    lines = ["Front", "", "Part One", "", "Chapter 1: Start", ""] + body(3, "a") + ["", "Chapter 2: Next", ""] + body(3, "b")
    text = "\n".join(lines) + "\n"
    assert [h for _, h in G.heads(text)] == ["Part One / Chapter 1: Start", "Chapter 2: Next"]
    assert [n for n, _ in G.heads(text)] == [3, lines.index("Chapter 2: Next") + 1]


# ---------------------------------------------------------------------------------------------------------------
# existing manifest, missing whole, CLI
# ---------------------------------------------------------------------------------------------------------------
def test_existing_different_manifest_not_overwritten(tmp_path):
    text, _ = chapter_text(2)
    kb = make_kb(tmp_path, text)
    out = kb / "src" / f"chapters-{SHELF}.json"
    out.write_text('{"hand": "edited"}\n', encoding="utf-8")
    before = written_tree(kb)
    with pytest.raises(G.ManifestError):
        G.gen_manifest(kb, shelf=SHELF, prefix="demo-book")
    assert written_tree(kb) == before


def test_rerun_same_result_writes_nothing(tmp_path):
    text, _ = chapter_text(2)
    kb = make_kb(tmp_path, text)
    first = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book")
    out = kb / "src" / f"chapters-{SHELF}.json"
    mtime = out.stat().st_mtime_ns
    snap = written_tree(kb)
    second = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book")
    assert second["manifest"] == first["manifest"]
    assert written_tree(kb) == snap and out.stat().st_mtime_ns == mtime


def test_missing_whole_is_error(tmp_path):
    kb = tmp_path / "kb"
    (kb / "src").mkdir(parents=True)
    with pytest.raises(G.ManifestError):
        G.gen_manifest(kb, shelf=SHELF)
    assert not (kb / "src" / f"chapters-{SHELF}.json").exists()


def test_prefix_defaults_to_library_id(tmp_path):
    text, _ = chapter_text(2)
    kb = make_kb(tmp_path, text)
    (kb / "library.toml").write_text('[library]\nid = "demo-kb"\nshelves = ["book"]\n', encoding="utf-8")
    res = G.gen_manifest(kb, shelf=SHELF)
    assert all(p["id"].startswith("demo-kb-book-") for p in res["manifest"]["pieces"])


def test_cli_scan_to_split(tmp_path, capsys):
    text, starts = chapter_text(2)
    kb = make_kb(tmp_path, text)
    assert G.main([str(kb), "--shelf", SHELF, "--prefix", "demo-book", "--apply"]) == 0
    assert len(piece_files(kb)) == 3
    assert G.main([str(kb)]) == 2


# ---------------------------------------------------------------------------------------------------------------
# law: partition + determinism over generated books
# ---------------------------------------------------------------------------------------------------------------
LINE = st.one_of(
    st.just(""),
    st.text(alphabet="abcdefghij klmnop.,'", min_size=1, max_size=40).map(lambda s: s.strip() or "x"),
    st.integers(1, 30).map(lambda k: f"Chapter {k}: Title {k}"),
    st.sampled_from(["第一章 铜壳", "第二章 哨阀", "一、检验报告", "Part Two", "Notes"]),
)


@settings(max_examples=60, deadline=None)
@given(lines=st.lists(LINE, min_size=1, max_size=40), crlf=st.booleans(), final_nl=st.booleans())
def test_generated_manifest_is_a_partition(lines, crlf, final_nl):
    nl = "\r\n" if crlf else "\n"
    text = nl.join(lines) + (nl if final_nl else "")
    if not text.strip():
        text = "x" + text
    with tempfile.TemporaryDirectory() as d:
        kb = make_kb(Path(d), text)
        res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book", apply=True)
        pieces = res["manifest"]["pieces"]
        ids = [p["id"] for p in pieces]
        assert len(ids) == len(set(ids)) and all(S.NAME_RE.match(i) for i in ids)
        assert pieces[0]["start_line"] == 1
        files = {f.stem: f.read_bytes() for f in piece_files(kb)}
        assert sorted(files) == sorted(ids)
        assert b"".join(files[i] for i in ids) == text.encode("utf-8")
        kb2 = Path(d) / "again"
        shutil.copytree(kb, kb2)
        (kb2 / "src" / f"chapters-{SHELF}.json").rename(kb2 / "first.json")
        shutil.move(str(kb2 / "_raw" / f"_whole-{SHELF}.txt"), str(kb2 / WHOLE))
        for f in piece_files(kb2):
            f.unlink()
        assert G.gen_manifest(kb2, shelf=SHELF, prefix="demo-book")["manifest"] == res["manifest"]
