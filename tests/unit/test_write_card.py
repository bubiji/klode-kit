"""WI-02b: ``build/write_card`` - the only writer below the scaffold marker, now gated (matrix 02b-01/02/03/06).

Specification (03 B2, G3; 02 §3.3 B2 and r2.1 row 1.4; plan WI-02b):

- Write contract kept from the user's klode-build ``write_card.py``: ``cards_content/<id>.md`` (first line
  ``aliases: [...]``, then the card body) goes into ``library/cards/<id>.md``: everything above the scaffold
  marker is kept except ``zoom:`` becomes ``zoom: full`` and ``aliases:`` is synced; below the marker is
  ``"\\n\\n" + body.strip() + "\\n"``. A missing scaffold or marker is refused. No ids = every
  ``cards_content/*.md``.
- **B2**: gate 1 (``check_cards.check_card``, policy from the source's shelf) runs on every card first. Any FAIL
  -> exit 1 and **nothing** is written (``library/cards`` bytes unchanged, all-or-nothing).
- ``--force`` is for a human only: it writes despite the gate, and every use records ``forced: true`` and a
  failed step in the receipt (exit 1). The pipeline never passes it.
- Receipt ``<kb>/write_receipt.json`` (``--receipt`` overrides), schema ``klode-kit.write_receipt/1``:
  ``status`` done|failed, ``forced``, ``written``, ``gate`` (per card), ``cards`` = ledger of published cards
  ``{id: {card_sha256, content_sha256, forced}}`` carried forward across runs. No wall-clock fields.

Law (S5): writing is idempotent - a second write of the same content gives the same card bytes and the same
receipt bytes; ``render`` is idempotent over generated aliases and bodies.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.write_card_fx import A1, A2, ALIAS_LIST, CH1, CH2, FOLDED, SCAFFOLD, add_card, body, content, snapshot
from klode_kit.build import write_card as W

pytestmark = pytest.mark.unit


def receipt(kb: Path) -> dict:
    return json.loads((kb / "write_receipt.json").read_text(encoding="utf-8"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def kb(tmp_path) -> Path:
    kb = tmp_path / "kb"
    add_card(kb, "ch01", CH1, A1)
    add_card(kb, "ch02", CH2, A2)
    return kb


# --------------------------------------------------------------------------- 02b-01  B2 reproducer


def test_b2_fail_no_write(kb, capsys):
    """03 §2 step 12: the original wrote a card while gate 1 still said ``A2 NOT FOUND``. Now: exit 1, no bytes."""
    (kb / "cards_content" / "ch01.md").write_text(content(A1 + [FOLDED]), encoding="utf-8")
    before = snapshot(kb)
    assert W.main([str(kb)]) == 1
    assert snapshot(kb) == before  # neither the failing ch01 nor the passing ch02 was written
    out = capsys.readouterr()
    assert "A2 NOT FOUND" in out.out + out.err
    assert FOLDED[:40] in out.out + out.err
    r = receipt(kb)
    assert (r["status"], r["forced"], r["written"]) == ("failed", False, [])
    gate = {g["id"]: g["pass"] for g in r["gate"]}
    assert gate == {"ch01": False, "ch02": True}


def test_b2_single_id_fail_no_write(kb):
    (kb / "cards_content" / "ch02.md").write_text(content(A2, aliases=["a", "b"]), encoding="utf-8")
    before = snapshot(kb)
    assert W.main([str(kb), "ch02"]) == 1
    assert snapshot(kb) == before


def test_policy_from_source_shelf(tmp_path):
    """Gate 1 inside write_card is the real gate: a card on a deterministic shelf is judged by COV."""
    kb = tmp_path / "kb"
    lines = [f"血红蛋白 {v} g/L 2026-0{m}-01 ⟨r-{c * 12}⟩" for v, m, c in ((135, 7, "a"), (128, 8, "b"), (126, 9, "c"))]
    add_card(kb, "hb", "\n".join(lines) + "\n", lines[:2], shelf="items")  # third line has no anchor
    before = snapshot(kb)
    assert W.main([str(kb)]) == 1
    assert snapshot(kb) == before
    assert "COV" in json.dumps(receipt(kb)["gate"], ensure_ascii=False)


# --------------------------------------------------------------------------- 02b-02  success


def test_pass_writes_card(kb, capsys):
    scaffold_head = (kb / "library" / "cards" / "ch01.md").read_text(encoding="utf-8").split(W.MARKER)[0]
    assert W.main([str(kb)]) == 0
    text = (kb / "library" / "cards" / "ch01.md").read_text(encoding="utf-8")
    head, below = text.split(W.MARKER)
    assert below == "\n\n" + body(A1).strip() + "\n"
    assert "\nzoom: full\n" in head
    assert "\naliases: [" + ", ".join(ALIAS_LIST) + "]\n" in head
    expected_head = scaffold_head.replace("zoom: stub", "zoom: full").replace(
        "aliases: []", "aliases: [" + ", ".join(ALIAS_LIST) + "]")
    assert head == expected_head
    assert "wrote ch01" in capsys.readouterr().out
    r = receipt(kb)
    assert r["schema"] == "klode-kit.write_receipt/1"
    assert (r["status"], r["forced"], r["written"]) == ("done", False, ["ch01", "ch02"])
    for cid in ("ch01", "ch02"):
        entry = r["cards"][cid]
        assert entry["card_sha256"] == sha(kb / "library" / "cards" / f"{cid}.md")
        assert entry["content_sha256"] == sha(kb / "cards_content" / f"{cid}.md")
        assert entry["forced"] is False


def test_ledger_carried_forward(kb):
    assert W.main([str(kb), "ch01"]) == 0
    first = receipt(kb)["cards"]["ch01"]
    assert W.main([str(kb), "ch02"]) == 0
    r = receipt(kb)
    assert r["written"] == ["ch02"]
    assert r["cards"]["ch01"] == first and set(r["cards"]) == {"ch01", "ch02"}
    # a refused run keeps the ledger as it was
    (kb / "cards_content" / "ch02.md").write_text(content(A2 + [FOLDED]), encoding="utf-8")
    assert W.main([str(kb), "ch02"]) == 1
    assert receipt(kb)["cards"] == r["cards"]


def test_receipt_path_override(kb, tmp_path):
    out = tmp_path / "elsewhere" / "w.json"
    assert W.main([str(kb), "--receipt", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "done"
    assert not (kb / "write_receipt.json").exists()


# --------------------------------------------------------------------------- 02b-03  --force


def test_forced_receipt_fails(kb):
    (kb / "cards_content" / "ch01.md").write_text(content(A1 + [FOLDED]), encoding="utf-8")
    assert W.main([str(kb), "ch01", "--force"]) == 1
    text = (kb / "library" / "cards" / "ch01.md").read_text(encoding="utf-8")
    assert FOLDED in text.split(W.MARKER)[1]  # a human override does write
    r = receipt(kb)
    assert (r["forced"], r["status"], r["written"]) == (True, "failed", ["ch01"])
    assert r["cards"]["ch01"]["forced"] is True


def test_forced_on_a_passing_card_still_fails(kb):
    assert W.main([str(kb), "--force"]) == 1
    r = receipt(kb)
    assert (r["forced"], r["status"]) == (True, "failed")
    assert all(e["forced"] for e in r["cards"].values())
    # a later clean write clears the per-card mark
    assert W.main([str(kb)]) == 0
    r = receipt(kb)
    assert (r["forced"], r["status"]) == (False, "done")
    assert not any(e["forced"] for e in r["cards"].values())


# --------------------------------------------------------------------------- guards


def test_missing_scaffold_refused(tmp_path):
    kb = tmp_path / "kb"
    add_card(kb, "ch01", CH1, A1)
    add_card(kb, "ch02", CH2, A2, scaffold=False)
    before = snapshot(kb)
    assert W.main([str(kb)]) == 1
    assert snapshot(kb) == before
    assert receipt(kb)["status"] == "failed"


def test_missing_scaffold_refused_even_forced(tmp_path, capsys):
    kb = tmp_path / "kb"
    add_card(kb, "ch02", CH2, A2, scaffold=False)
    assert W.main([str(kb), "--force"]) == 1
    assert not (kb / "library" / "cards" / "ch02.md").exists()
    assert "骨架不存在" in capsys.readouterr().err


def test_missing_marker_refused(kb, capsys):
    p = kb / "library" / "cards" / "ch01.md"
    p.write_text(p.read_text(encoding="utf-8").replace(W.MARKER, "<!-- something else -->"), encoding="utf-8")
    before = snapshot(kb)
    assert W.main([str(kb)]) == 1
    assert snapshot(kb) == before
    assert "标记线" in capsys.readouterr().err


def test_missing_content_file(kb):
    before = snapshot(kb)
    assert W.main([str(kb), "ch09"]) == 1
    assert snapshot(kb) == before


def test_no_cards_content(tmp_path):
    kb = tmp_path / "kb"
    (kb / "cards_content").mkdir(parents=True)
    assert W.main([str(kb)]) == 1


@pytest.mark.parametrize("argv", [[], ["--force"], ["kb", "../evil"]])
def test_usage(argv):
    assert W.main(argv) == 2


# --------------------------------------------------------------------------- 02b-06  idempotence (S5)


def test_write_card_idempotent(kb):
    assert W.main([str(kb)]) == 0
    once = snapshot(kb), (kb / "write_receipt.json").read_bytes()
    assert W.main([str(kb)]) == 0
    assert (snapshot(kb), (kb / "write_receipt.json").read_bytes()) == once


_alias = st.text(alphabet=st.characters(blacklist_characters=",[]\n\r", blacklist_categories=("Cs", "Cc")),
                 min_size=1, max_size=12).map(str.strip).filter(bool)
_body = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"), max_size=200).filter(
    lambda s: W.MARKER not in s)


@settings(max_examples=150, deadline=None)
@given(aliases=st.lists(_alias, max_size=12), text=_body)
def test_render_idempotent(aliases, text):
    scaffold = SCAFFOLD.format(cid="x", shelf="book", sha="0" * 64)
    once = W.render(scaffold, aliases, text)
    assert W.render(once, aliases, text) == once
    head, below = once.split(W.MARKER)
    assert below == "\n\n" + text.strip() + "\n"
    assert "\nzoom: full\n" in head and "\naliases: [" + ", ".join(aliases) + "]\n" in head


# --------------------------------------------------------------------------- added during green (more guards)


def test_render_refuses_without_marker():
    with pytest.raises(ValueError):
        W.render("---\nzoom: stub\n---\nno marker\n", ["a"], "body")


def test_render_alias_backslash_literal():
    """The original passed the aliases to re.sub as a template: ``C:\\new`` became a newline, ``\\1`` crashed."""
    scaffold = SCAFFOLD.format(cid="x", shelf="book", sha="0" * 64)
    out = W.render(scaffold, ["C:\\new", "g\\1"], "b")
    assert "\naliases: [C:\\new, g\\1]\n" in out


def test_content_without_aliases_line_refused(kb):
    (kb / "cards_content" / "ch01.md").write_text(body(A1), encoding="utf-8")
    before = snapshot(kb)
    assert W.main([str(kb), "--force"]) == 1
    assert snapshot(kb) == before
    assert receipt(kb)["refused"][0]["id"] == "ch01"
