"""WI-02b: ``build/check_published`` - gate 1 again, on what was actually published (matrix 02b-05; 03 B6).

Specification (02 r2.1 row 1.4; plan WI-02b):

- For each published card ``library/cards/<id>.md`` (``CARD ...`` or ``--all``; ``INDEX.md`` is not a card) the
  part **below the scaffold marker**, together with the frontmatter ``aliases:``, is judged by the same rules as
  gate 1 (F1-F3, A1-A3, MIN_FULL / COV with the policy from the source's shelf). This catches what
  ``klode check --strict`` lets through (03 B6): folded punctuation such as ``--`` for ``—`` (``FOLDED-ONLY``)
  and stub cards that were never written.
- Extra codes: ``MARKER`` (no scaffold marker), ``ZOOM`` (frontmatter ``zoom`` is not ``full``), ``RECEIPT``
  (no usable write receipt entry for the card), ``STALE`` (the card's bytes differ from the ``card_sha256`` the
  write receipt recorded - it was changed after it was written), ``FORCED`` (the receipt says the card was
  written with ``--force``).
- Receipt: ``<kb>/write_receipt.json`` unless ``--receipt``.
- CLI ``klode-kit check-published (CARD ... | --all) [--kb DIR] [--receipt F] [--json] [--project DIR]``; exit 0
  all pass, 1 any problem (also: ``--all`` with no card), 2 usage. ``--json`` follows ``JSON_SCHEMA``.

No law of its own beyond gate 1's (covered in test_check_cards): this unit is a judge over a snapshot; the
cases below are failure modes (S3) and the agreement with gate 1 on a clean write (S1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from klode_kit.build import check_published as P
from klode_kit.build import write_card as W
from _support.write_card_fx import A1, A2, CH1, CH2, FOLDED, add_card, content

pytestmark = pytest.mark.unit


@pytest.fixture
def kb(tmp_path) -> Path:
    kb = tmp_path / "kb"
    add_card(kb, "ch01", CH1, A1)
    add_card(kb, "ch02", CH2, A2)
    assert W.main([str(kb)]) == 0
    return kb


def codes(doc: dict, cid: str) -> set[str]:
    card = next(c for c in doc["cards"] if c["id"] == cid)
    return {p["code"] for p in card["problems"]}


def run_json(capsys, *argv: str) -> tuple[int, dict]:
    rc = P.main([*argv, "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert list(Draft202012Validator(P.JSON_SCHEMA).iter_errors(doc)) == []
    return rc, doc


def test_clean_publish_passes(kb, capsys):
    rc, doc = run_json(capsys, "--all", "--kb", str(kb))
    assert rc == 0 and doc["pass"] is True
    assert [(c["id"], c["pass"], c["policy"]) for c in doc["cards"]] == [("ch01", True, "authored"),
                                                                         ("ch02", True, "authored")]
    assert doc["cards"][0]["card"] == "library/cards/ch01.md"


# --------------------------------------------------------------------------- 02b-05  stale receipt


def test_stale_receipt_detected(kb, capsys):
    p = kb / "library" / "cards" / "ch01.md"
    p.write_text(p.read_text(encoding="utf-8").replace("Summary", "A summary"), encoding="utf-8")  # still gate-clean
    rc, doc = run_json(capsys, "--all", "--kb", str(kb))
    assert rc == 1
    assert codes(doc, "ch01") == {"STALE"}
    assert codes(doc, "ch02") == set()


def test_folded_anchor_published_by_hand(kb, capsys):
    """B6: an anchor with ``--`` edited into the published card is caught (A2), and the edit makes it STALE."""
    p = kb / "library" / "cards" / "ch01.md"
    p.write_text(p.read_text(encoding="utf-8").replace(A1[0], FOLDED), encoding="utf-8")
    rc = P.main(["ch01", "--kb", str(kb)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "A2 NOT FOUND" in out and FOLDED[:40] in out and "STALE" in out


def test_forced_card_fails(kb, capsys):
    (kb / "cards_content" / "ch01.md").write_text(content(A1 + [FOLDED]), encoding="utf-8")
    assert W.main([str(kb), "ch01", "--force"]) == 1
    capsys.readouterr()
    rc, doc = run_json(capsys, "--all", "--kb", str(kb))
    assert rc == 1
    assert codes(doc, "ch01") == {"FORCED", "A2"}
    assert codes(doc, "ch02") == set()


def test_stub_card_fails(tmp_path, capsys):
    kb = tmp_path / "kb"
    add_card(kb, "ch01", CH1, None)  # scaffold only, never written
    rc, doc = run_json(capsys, "--all", "--kb", str(kb))
    assert rc == 1
    assert {"ZOOM", "F1", "F2", "F3", "RECEIPT"} <= codes(doc, "ch01")


def test_missing_marker(kb, capsys):
    p = kb / "library" / "cards" / "ch02.md"
    p.write_text(p.read_text(encoding="utf-8").replace(W.MARKER, ""), encoding="utf-8")
    rc, doc = run_json(capsys, "ch02", "--kb", str(kb))
    assert rc == 1 and "MARKER" in codes(doc, "ch02")


def test_missing_or_broken_receipt(kb, capsys, tmp_path):
    rc, doc = run_json(capsys, "--all", "--kb", str(kb), "--receipt", str(tmp_path / "none.json"))
    assert rc == 1 and codes(doc, "ch01") == codes(doc, "ch02") == {"RECEIPT"}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc, doc = run_json(capsys, "--all", "--kb", str(kb), "--receipt", str(bad))
    assert rc == 1 and codes(doc, "ch01") == {"RECEIPT"}
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema": "something/1", "cards": {}}), encoding="utf-8")
    rc, doc = run_json(capsys, "--all", "--kb", str(kb), "--receipt", str(wrong))
    assert rc == 1 and codes(doc, "ch02") == {"RECEIPT"}


def test_card_not_in_ledger(kb, capsys):
    add_card(kb, "ch03", CH1.replace("Chapter 1", "Chapter 3"), A1)
    # a hand copy of a good published card, never written by write_card
    src = (kb / "library" / "cards" / "ch01.md").read_text(encoding="utf-8")
    (kb / "library" / "cards" / "ch03.md").write_text(src.replace("id: ch01", "id: ch03"), encoding="utf-8")
    rc, doc = run_json(capsys, "ch03", "--kb", str(kb))
    assert rc == 1 and codes(doc, "ch03") == {"RECEIPT"}


def test_missing_card_and_source(kb, capsys):
    rc, doc = run_json(capsys, "ch09", "--kb", str(kb))
    assert rc == 1 and codes(doc, "ch09") == {"MISSING"}
    (kb / "library" / "book" / "ch02.txt").rename(kb / "ch02.txt.moved")
    rc, doc = run_json(capsys, "ch02", "--kb", str(kb))
    assert rc == 1 and "MISSING" in codes(doc, "ch02")


def test_text_output(kb, capsys):
    assert P.main(["--all", "--kb", str(kb)]) == 0
    out = capsys.readouterr().out
    assert "PASS  ch01" in out and "PASS  ch02" in out


def test_all_without_cards(tmp_path, capsys):
    kb = tmp_path / "kb"
    (kb / "library" / "cards").mkdir(parents=True)
    (kb / "library" / "cards" / "INDEX.md").write_text("# index\n", encoding="utf-8")
    assert P.main(["--all", "--kb", str(kb)]) == 1


@pytest.mark.parametrize("argv", [[], ["ch01", "--all"], ["../x"]])
def test_usage(argv):
    assert P.main(argv) == 2


# --------------------------------------------------------------------------- added during green


def test_api_defaults_thresholds_from_config(kb):
    doc = P.check_published(kb)
    assert doc["pass"] is True and [c["id"] for c in doc["cards"]] == ["ch01", "ch02"]


def test_judge_rejects_unknown_policy(kb):
    from klode_kit.build import check_cards as C
    with pytest.raises(ValueError):
        C.judge(kb, "ch01", "", "weak", min_aliases=10, min_anchors_full=5)
