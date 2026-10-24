"""WI-02a: ``build/skeleton`` (vendored as is, 03 §6.1) and the no-hardcoded-path guard (matrix 02a-09, G10).

skeleton specification (unchanged heuristic of the user's klode-build ``skeleton.py``): a section title is a line
of at most 12 words, not ending in ``.!?,;:``, after an empty line (or first), whose next non-empty line within 5
lines is a paragraph of >= 120 characters. ``sections(kb, cid, chars=400)`` returns
``{"source", "lines", "words", "sections": [{"title", "line", "lead"}]}`` (``lead`` = first ``chars`` characters of
that paragraph); a missing source raises ``FileNotFoundError``. CLI ``klode-kit skeleton <kb> <id> [chars]``:
exit 0, 1 missing source, 2 usage. Read only. No law: a fixed heuristic over one text (examples, S1/S2).

02a-09: no file under ``src/`` or a ``prompts/`` / ``skills/`` / ``agents/`` / ``workflows/`` directory names a
private location: ``/Users/``, ``/home/<user>``, ``~/.local/bin``, ``.klode/registry``, ``Documents/Claude``,
``Claude/Projects``, ``.claude/skills``, the private project directories ``003/`` ``189/`` ``003_`` ``189_``
``16_d``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.build import skeleton as K

pytestmark = pytest.mark.unit

LONG = ("The descaling routine starts with a cold kettle, a measured vinegar solution and a timer set for "
        "fifteen minutes before the first rinse.")
CHAPTER = "\n".join([
    "Chapter 2: Descaling",       # 1  title candidate, next para short -> no
    "",
    "Short intro line.",          # 3
    "",
    "Preparing the Solution",     # 5  -> section
    "",
    LONG,                         # 7
    "",
    "Why it works.",              # 9  ends with '.', not a title
    "",
    LONG + " Again.",             # 11
    "Directly After Text",        # 12 previous line not empty -> no
    LONG,
    "",
    "A heading that has far too many words to be any kind of heading at all here",  # 15 > 12 words
    "",
    LONG,
    "",
    "Rinsing",                    # 19 -> section
    "",
    "",
    LONG,
]) + "\n"


@pytest.fixture
def kb(tmp_path):
    root = tmp_path / "kb"
    (root / "library" / "manual").mkdir(parents=True)
    (root / "library" / "manual" / "ak-ch02.txt").write_text(CHAPTER, encoding="utf-8")
    return root


def test_sections(kb):
    res = K.sections(kb, "ak-ch02", chars=40)
    assert res["source"] == "library/manual/ak-ch02.txt"
    assert res["lines"] == len(CHAPTER.splitlines())
    assert res["words"] == sum(len(ln.split()) for ln in CHAPTER.splitlines())
    assert [(s["title"], s["line"]) for s in res["sections"]] == [("Preparing the Solution", 5), ("Rinsing", 19)]
    assert res["sections"][0]["lead"] == LONG[:40]


def test_missing_source(kb):
    with pytest.raises(FileNotFoundError):
        K.sections(kb, "nope")


def test_cli(kb, capsys):
    assert K.main([str(kb), "ak-ch02", "30"]) == 0
    out = capsys.readouterr().out
    assert "【Preparing the Solution】(L5)" in out and "【Rinsing】(L19)" in out
    assert LONG[:30] + "\n" in out
    assert K.main([str(kb), "nope"]) == 1
    assert K.main([str(kb)]) == 2
    assert K.main([str(kb), "ak-ch02", "many"]) == 2


# --------------------------------------------------------------------------- 02a-09

PRIVATE = re.compile(
    r"/Users/|/home/[a-z]|~/\.local/bin|\.local/bin/klode|\.klode/registry|Documents/Claude|Claude/Projects"
    r"|\.claude/skills|(?<![\w.])(?:003|189)[/_]|\b16_d\b"
)
SCAN_ROOTS = ["src", "prompts", "skills", "agents", "workflows"]


def _files():
    for root in SCAN_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path


def test_no_hardcoded_paths():
    """02a-09 (L1/S3)."""
    hits = []
    files = list(_files())
    assert any(p.name == "split_by_manifest.py" for p in files)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PRIVATE.search(line):
                hits.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:100]}")
    assert hits == []


@pytest.mark.parametrize("line", ["see 189/kb/jtbd-klode", "~/.local/bin/klode", "/Users/someone/x",
                                  "~/.klode/registry.toml", "003_d_project", "~/.claude/skills/klode-build"])
def test_private_pattern_catches(line):
    assert PRIVATE.search(line)


@pytest.mark.parametrize("line", ["the 189 project's cer.py", "0.003 mm", "id 1189_x", "v1.89/"])
def test_private_pattern_spares(line):
    assert not PRIVATE.search(line)
