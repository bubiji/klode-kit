"""WI-02a edges of the vendored build tools (written after the 02a-* specification went green; guard paths for
the criticalPath branch thresholds of ``check_cards`` / ``fix_anchors``). Not part of the recorded red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from klode_kit.build import check_cards as C
from klode_kit.build import fix_anchors as F

pytestmark = pytest.mark.unit

ALIASES = "aliases: [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10]\n"
OBS = ["肌酐 88 umol/L 2026-07-01 ⟨r-aaaaaaaaaaaa⟩", "肌酐 91 umol/L 2026-08-01 ⟨r-bbbbbbbbbbbb⟩"]


def kb_with(root: Path, shelf: str, source: str, content: str, cid: str = "x") -> Path:
    kb = root / "kb"
    (kb / "library" / shelf).mkdir(parents=True, exist_ok=True)
    (kb / "cards_content").mkdir(exist_ok=True)
    (kb / "library" / shelf / f"{cid}.txt").write_text(source, encoding="utf-8")
    (kb / "cards_content" / f"{cid}.md").write_text(content, encoding="utf-8")
    return kb


def test_find_source(tmp_path):
    kb = kb_with(tmp_path, "items", OBS[0] + "\n", ALIASES)
    assert C.find_source(kb, "x") == kb / "library" / "items" / "x.txt"
    assert C.find_source(kb, "y") is None


def test_deterministic_empty_and_unfound_anchors_do_not_cover(tmp_path):
    """An empty anchor or one not in the source covers no line: coverage counts only resolved anchors."""
    full = f"- a (grep: `{OBS[0]}`)\n- b (grep: ``)\n- c (grep: `肌酐 99 umol/L 2026-09-01`)\n"
    content = ALIASES + "\n## Thin\nt (grep: `" + OBS[0] + "`)\n\n## Full\n### s\n" + full
    kb = kb_with(tmp_path, "items", "\n".join(OBS) + "\n", content)
    r = C.check_card(kb, "x")
    assert r["coverage"] == {"covered": 1, "lines": 2}
    cov = [p for p in r["problems"] if p.startswith("COV")]
    assert len(cov) == 1 and "1/2" in cov[0] and "[2]" in cov[0]
    assert r["pass"] is False


def test_fix_one_card_missing_with_source(tmp_path):
    kb = kb_with(tmp_path, "book", "some text\n", ALIASES)
    (kb / "cards_content" / "x.md").unlink()
    assert F.fix_one(kb, "x") == (0, ["x: cards_content 文件不存在"])


def test_split_shelf_name_with_path_refused(tmp_path, capsys):
    import json

    from klode_kit.build import split_by_manifest as S

    kb = tmp_path / "kb"
    (kb / "library" / "book").mkdir(parents=True)
    (kb / "library" / "book" / "_w.txt").write_text("a\nb\n", encoding="utf-8")
    mf = kb / "m.json"
    mf.write_text(json.dumps({"shelf": "../out", "whole": "library/book/_w.txt",
                              "pieces": [{"id": "p", "title": "P", "start_line": 1}]}), encoding="utf-8")
    assert S.split(kb, mf, True) == 1
    assert "书架名" in capsys.readouterr().err
    assert not (tmp_path / "out").exists() and not (kb / "_raw").exists()
