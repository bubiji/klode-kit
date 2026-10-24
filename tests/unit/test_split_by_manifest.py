"""WI-02a: ``build/split_by_manifest`` - the vendored chapter splitter (matrix 02a-01 .. 02a-04; 03 A1, B5, G6).

Specification (03 §2 steps 3-5c, §3 A1, §5 B5; 02 §3.1 A1, §3.3 B5):

- ``split(kb, manifest, apply) -> int``: cut ``manifest.whole`` at the 1-based ``start_line`` of each piece (lines
  end at ``\n`` only, as ``grep -n`` counts them; the whole is read and written without newline translation); the
  first piece starts at line 1; every piece runs to the line before the next one. The pieces put back together
  must equal the whole **byte for byte** or nothing is written (exit 1). ``--apply`` writes
  ``library/<shelf>/<id>.txt``, moves the whole into ``_raw/`` and adds one BIBLIOGRAPHY row per piece.
- B5: a second ``--apply`` (the whole put back, e.g. the doctor flow re-cutting a lost source) adds no row that
  is already there - BIBLIOGRAPHY after two applies equals BIBLIOGRAPHY after one.
- ``HEAD_RE`` lives in configuration (``klode_kit/config/head_re.toml``, ``patterns = [...]``); the packaged
  patterns keep the original English heads and add the Chinese ones (「第一章」「一、」「检验报告」, 03 step 3b found
  0 candidates). ``candidates(text, patterns)`` returns ``[(line_no, line)]``; ``load_head_patterns(path)``
  reads a custom file (``--head-re`` on the CLI); a broken file is ``ConfigError`` (exit 40).

Law: split is a partition - concatenation of the pieces == the original bytes (02a-01, S5); apply is idempotent
on BIBLIOGRAPHY (02a-04, S5).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit.build import split_by_manifest as S
from klode_kit.config import ConfigError

pytestmark = pytest.mark.unit

AURORA = REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt"
WHOLE = "library/book/_whole-book.txt"
PIECES = [
    {"id": "front", "title": "Front Matter", "start_line": 1},
    {"id": "ch01", "title": "Chapter 1: The Copper Shell", "start_line": 3},
    {"id": "ch02", "title": "Chapter 2: The Whistle Valve", "start_line": 17},
    {"id": "ch03", "title": "Chapter 3: Night Market Care", "start_line": 31},
]


def make_kb(root: Path, whole: bytes, pieces=PIECES, citation: str = "Aurora Kettle handbook (synthetic)") -> Path:
    kb = root / "kb"
    (kb / "library" / "book").mkdir(parents=True)
    (kb / "src").mkdir()
    (kb / WHOLE).write_bytes(whole)
    manifest = {"shelf": "book", "whole": WHOLE, "citation": citation, "pieces": pieces}
    (kb / "src" / "chapters-book.json").write_text(json.dumps(manifest), encoding="utf-8")
    return kb


def manifest_path(kb: Path) -> Path:
    return kb / "src" / "chapters-book.json"


# --------------------------------------------------------------------------- 02a-01 partition law


LINE_CHARS = st.characters(codec="utf-8", exclude_characters="\n\r", exclude_categories=("Cs",))
LINE = st.text(LINE_CHARS, max_size=30)
HEAD = st.sampled_from(["Chapter 1: Start", "第一章 总则", "一、检验报告", "检验报告", "PART TWO", "12 Notes"])
EOL = st.sampled_from(["\n", "\r\n", "\r", " "])


@st.composite
def book_and_cuts(draw):
    n = draw(st.integers(min_value=1, max_value=25))
    lines = [draw(st.one_of(LINE, HEAD)) + draw(EOL) for _ in range(n)]
    if draw(st.booleans()):  # last line without a line break
        lines[-1] = lines[-1].rstrip("\r\n ")
    text = "".join(lines)
    assume(text)
    n_lines = len(text.split("\n")) if not text.endswith("\n") else text.count("\n")
    starts = draw(st.sets(st.integers(min_value=2, max_value=max(2, n_lines)), max_size=6))
    starts = sorted(s for s in starts if s <= n_lines)
    return text, [1, *starts]


@settings(max_examples=150, deadline=None)
@given(data=book_and_cuts())
def test_split_concat_equals_original(data):
    """02a-01 (L1/S5): for any text (CR, CRLF, U+2028 included) and any valid cut list, the written pieces read
    back as bytes and concatenated equal the original whole byte for byte."""
    text, starts = data
    original = text.encode("utf-8")
    pieces = [{"id": f"p{k:02d}", "title": f"P{k}", "start_line": s} for k, s in enumerate(starts)]
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp), original, pieces)
        assert S.split(kb, manifest_path(kb), True) == 0
        back = b"".join((kb / "library" / "book" / f"{p['id']}.txt").read_bytes() for p in pieces)
        assert back == original
        assert (kb / "_raw" / "_whole-book.txt").read_bytes() == original


def test_aurora_split_dry_run_then_apply(tmp_path, capsys):
    """03 steps 4-5: the dry run writes nothing; apply writes 4 pieces, moves the whole, adds 4 rows."""
    kb = make_kb(tmp_path, AURORA.read_bytes())
    assert S.split(kb, manifest_path(kb), False) == 0
    out = capsys.readouterr().out
    assert "4 片拼回 == 原文" in out
    assert sorted(p.name for p in (kb / "library" / "book").iterdir()) == ["_whole-book.txt"]
    assert not (kb / "library" / "BIBLIOGRAPHY.md").exists()

    assert S.split(kb, manifest_path(kb), True) == 0
    shelf = sorted(p.name for p in (kb / "library" / "book").iterdir())
    assert shelf == ["ch01.txt", "ch02.txt", "ch03.txt", "front.txt"]
    assert (kb / "library" / "book" / "ch02.txt").read_text(encoding="utf-8").startswith("Chapter 2: The Whistle Valve\n")
    assert not (kb / WHOLE).exists()
    assert (kb / "_raw" / "_whole-book.txt").read_bytes() == AURORA.read_bytes()
    bib = (kb / "library" / "BIBLIOGRAPHY.md").read_text(encoding="utf-8")
    assert bib.startswith("# Bibliography\n\n| source | file | notes |\n|---|---|---|\n")
    for p in PIECES:
        assert bib.count(f"`library/book/{p['id']}.txt`") == 1


# --------------------------------------------------------------------------- 02a-04 B5


def test_b5_reapply_no_dup(tmp_path):
    """02a-04 (L2/S5): fx-book-aurora, ``--apply`` twice (the whole copied back in between): BIBLIOGRAPHY after the
    second apply is byte-identical to after the first, every piece row exactly once (03 B5: 3 rows became 7)."""
    kb = make_kb(tmp_path, AURORA.read_bytes())
    assert S.split(kb, manifest_path(kb), True) == 0
    bib = kb / "library" / "BIBLIOGRAPHY.md"
    first = bib.read_bytes()
    shutil.copyfile(AURORA, kb / WHOLE)
    assert S.split(kb, manifest_path(kb), True) == 0
    assert bib.read_bytes() == first
    text = bib.read_text(encoding="utf-8")
    assert [text.count(f"`library/book/{p['id']}.txt`") for p in PIECES] == [1, 1, 1, 1]


def test_b5_existing_bibliography_keeps_other_rows(tmp_path):
    """klode init writes its own BIBLIOGRAPHY header; rows of other shelves stay; only missing rows are added."""
    kb = make_kb(tmp_path, AURORA.read_bytes())
    bib = kb / "library" / "BIBLIOGRAPHY.md"
    head = "# Bibliography\n\n| source | file | notes |\n|---|---|---|\n| Other | `library/papers/x.txt` |  |\n"
    head += "| Aurora — Chapter 1 | `library/book/ch01.txt` | hand-written |\n"
    bib.write_text(head, encoding="utf-8")
    assert S.split(kb, manifest_path(kb), True) == 0
    text = bib.read_text(encoding="utf-8")
    assert text.startswith(head)
    assert [text.count(f"`library/book/{p['id']}.txt`") for p in PIECES] == [1, 1, 1, 1]
    assert text.count("`library/papers/x.txt`") == 1


# --------------------------------------------------------------------------- guards (03 steps 4b, 5c)


@pytest.mark.parametrize("pieces, needle", [
    pytest.param([{**PIECES[0], "start_line": 2}, *PIECES[1:]], "第 1 行", id="first-not-1"),
    pytest.param([*PIECES, {"id": "ch01", "title": "dup", "start_line": 40}], "重复 id", id="dup-id"),
    pytest.param([*PIECES[:3], {**PIECES[3], "start_line": 17}], "不小于", id="same-start"),
    pytest.param([*PIECES[:3], {**PIECES[3], "start_line": 99}], "不小于", id="past-end"),
    pytest.param([*PIECES[:3], {**PIECES[3], "id": "../escape"}], "id", id="id-path-traversal"),
])
def test_bad_manifest_refused_nothing_written(tmp_path, capsys, pieces, needle):
    kb = make_kb(tmp_path, AURORA.read_bytes(), pieces)
    assert S.split(kb, manifest_path(kb), True) == 1
    assert needle in capsys.readouterr().err
    assert sorted(p.name for p in (kb / "library" / "book").iterdir()) == ["_whole-book.txt"]
    assert not (kb / "_raw").exists()
    assert not (kb / "library" / "escape.txt").exists()


def test_whole_missing_refused(tmp_path, capsys):
    """03 step 5c: the whole already moved away -> exit 1 (``找不到整本源文件``)."""
    kb = make_kb(tmp_path, AURORA.read_bytes())
    (kb / WHOLE).unlink()
    assert S.split(kb, manifest_path(kb), True) == 1
    assert "找不到整本源文件" in capsys.readouterr().err


def test_raw_copy_differs_refused(tmp_path, capsys):
    """Never overwrite a different whole already kept in ``_raw/``: exit 1, no piece written."""
    kb = make_kb(tmp_path, AURORA.read_bytes())
    (kb / "_raw").mkdir()
    (kb / "_raw" / "_whole-book.txt").write_text("an older, different whole\n", encoding="utf-8")
    assert S.split(kb, manifest_path(kb), True) == 1
    assert "_raw" in capsys.readouterr().err
    assert (kb / "_raw" / "_whole-book.txt").read_text(encoding="utf-8") == "an older, different whole\n"
    assert sorted(p.name for p in (kb / "library" / "book").iterdir()) == ["_whole-book.txt"]


# --------------------------------------------------------------------------- 02a-02 / 02a-03 HEAD_RE config

CN_BOOK = "\n".join([
    "前言", "第一章 总则", "这是正文的一段，讲的是总则。", "一、检验报告", "血常规的说明文字。", "检验报告",
    "第十二章 附录", "二、用药记录", "普通的一行字", "Chapter 3: Night Market Care", "Part Two",
]) + "\n"


def test_cn_heads():
    """02a-02 (L1/S2): the packaged patterns find 「第一章」「一、」「检验报告」 (and still the English heads)."""
    found = dict(S.candidates(CN_BOOK, S.load_head_patterns()))
    want = {2: "第一章 总则", 4: "一、检验报告", 6: "检验报告", 7: "第十二章 附录", 8: "二、用药记录",
            10: "Chapter 3: Night Market Care", 11: "Part Two"}
    assert {n: found[n] for n in want if n in found} == want
    assert not {3, 5, 9} & set(found)  # body lines are not heads


def test_english_heads_kept_from_original():
    """The original HEAD_RE alternatives are all in the packaged file (behaviour of 03 step 3 on fx-book-aurora)."""
    text = AURORA.read_text(encoding="utf-8")
    assert [n for n, _ in S.candidates(text, S.load_head_patterns())] == [3, 17, 31]
    for line in ["Chapter 9", "CHAPTER 9", "Introduction", "Acknowledgments", "About the Author", "1. The start",
                 "12: Roots", "Index", "Final Thoughts", "Epilogue"]:
        assert S.candidates(line + "\n", S.load_head_patterns()) == [(1, line)], line
    long_head = "Chapter " + "x" * 80
    assert S.candidates(long_head + "\n", S.load_head_patterns()) == []


def test_head_re_from_config(tmp_path):
    """02a-03 (L1/S2): a custom ``head_re.toml`` replaces the packaged patterns."""
    cfg = tmp_path / "head_re.toml"
    cfg.write_text("patterns = ['^§\\s*\\d+', '^Annex [A-Z]$']\n", encoding="utf-8")
    text = "§1 Scope\nChapter 1: x\n§ 22 Terms\nAnnex B\nAnnex b\n第一章 总则\n"
    assert S.candidates(text, S.load_head_patterns(cfg)) == [(1, "§1 Scope"), (3, "§ 22 Terms"), (4, "Annex B")]


@pytest.mark.parametrize("content", ["patterns = []\n", "patterns = 'x'\n", "nothing = ['x']\n",
                                     "patterns = ['(unclosed']\n", "patterns = [1]\n", "not toml [\n"],
                         ids=["empty", "not-list", "missing", "bad-regex", "not-str", "bad-toml"])
def test_head_re_config_invalid(tmp_path, content):
    cfg = tmp_path / "head_re.toml"
    cfg.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        S.load_head_patterns(cfg)
    assert exc.value.exit_code == 40


def test_scan_cli_custom_head_re(tmp_path, capsys):
    kb = make_kb(tmp_path, CN_BOOK.encode("utf-8"))
    assert S.main([str(kb), "--whole", WHOLE, "--scan"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "2\t第一章 总则" in lines and "4\t一、检验报告" in lines and "6\t检验报告" in lines
    cfg = tmp_path / "h.toml"
    cfg.write_text("patterns = ['^普通']\n", encoding="utf-8")
    assert S.main([str(kb), "--whole", WHOLE, "--scan", "--head-re", str(cfg)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [ln for ln in out if not ln.startswith("#")] == ["9\t普通的一行字"]
    cfg.write_text("patterns = ['(']\n", encoding="utf-8")
    assert S.main([str(kb), "--whole", WHOLE, "--scan", "--head-re", str(cfg)]) == 40


def test_cli_usage_and_apply(tmp_path, capsys):
    kb = make_kb(tmp_path, AURORA.read_bytes())
    assert S.main([str(kb), "--scan"]) == 2
    assert S.main([str(kb)]) == 2
    assert S.main([str(kb), "--manifest", "src/chapters-book.json"]) == 0
    assert (kb / WHOLE).exists()
    assert S.main([str(kb), "--manifest", str(manifest_path(kb)), "--apply"]) == 0
    assert (kb / "library" / "book" / "ch03.txt").exists()
    capsys.readouterr()
