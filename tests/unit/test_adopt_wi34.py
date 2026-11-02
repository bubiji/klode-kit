"""WI-34 (unit): adopting a pre-klode-kit klode library - the pure parts (``klode_kit.build.adopt``) and the usage
refusals of ``klode-kit build --adopt / --cards-from``.

Specification (user goal 2026-11-02: migrate the 11 existing klode libraries; old layout of klode 0.5.0):

- ``read_old(dir)`` reads ``library.toml`` (``[library] id/name/description/dir/cards/shelves``, ``[frameworks]``,
  ``[copyright]``, ``[bibliography]``) and finds, relative to the *old* ``dir`` / ``cards`` names, the sources
  ``<dir>/<shelf>/*.txt``, the cards (``cards_content/<id>.md`` and ``<dir>/<cards>/<id>.md``), ``src/desk.json``, a
  holdout file and ``<dir>/<frameworks.dir>/<frameworks.syntheses>/*.md``. It only reads.
- ``extract_card(text)`` turns a published klode card into the ``cards_content`` format: the aliases of the
  front matter, then the body below the scaffold marker, stripped. A card that was never written (stub zoom / the
  ``_(L1 owed`` placeholder), has no marker or an empty body is ``None``.
- ``patch_toml(text, old)`` carries ``extra_guard_dirs`` and ``[bibliography] enabled`` into the new library.toml;
  the library dir / cards dir names stay klode-kit's, ``[copyright] guard`` stays true.

Law (S5 round trip): ``extract_card(write_card.render(scaffold, aliases, body))`` parses back (``write_card.parse_content``)
to the same aliases and the same stripped body - for any aliases without comma / bracket / newline and any body.

Usage refusals (exit 2, nothing created): ``--adopt`` with ``--source``, an old directory that is missing or has no
library.toml or no shelves, ``--cards-from`` that is not a directory or used on the records path, ``--loops`` with
``--adopt`` or ``--cards-from``.
"""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.build import adopt as A
from klode_kit.build import run_build as RB
from klode_kit.build import write_card as W

pytestmark = pytest.mark.unit

SCAFFOLD = """---
id: c1
shelf: s
file: library/s/c1.txt
framework: none
zoom: stub
aliases: []
grep_ready: true
source_sha256: 0000000000000000000000000000000000000000000000000000000000000000
---

# C1

## Content
`library/s/c1.txt` — full text (git-ignored, grep-ready). Never duplicated here; grep it to verify.

<!-- scaffold: managed by klode — edit Thin/Full below the marker by hand -->

## Thin
_(L1 owed — 1-3 sentences, grep-grounded to the .txt)_

## Full
_(L2 owed — main points outlined, grep-cited)_
"""
BODY = "## Thin\n一句话 (grep: `alpha beta gamma delta`)。\n\n## Full\n- 要点 (grep: `alpha beta gamma delta`)\n"

TOML = """[library]
id = "old"
name = "Old lib"
description = "from the old scripts"
dir = "{dir}"
cards = "{cards}"
shelves = ["a", "b"]

[bibliography]
enabled = {bib}
path = "BIBLIOGRAPHY.md"

[frameworks]
enabled = {fw}
dir = "fw"
syntheses = "syn"

[copyright]
guard = {guard}
extra_guard_dirs = {extra}
"""


def make_old(root: Path, *, dir="library", cards="cards", bib="true", fw="true", guard="true", extra="[]") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "library.toml").write_text(TOML.format(dir=dir, cards=cards, bib=bib, fw=fw, guard=guard, extra=extra),
                                       encoding="utf-8")
    for shelf, name in (("a", "one"), ("a", "two"), ("b", "three")):
        d = root / dir / shelf
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.txt").write_bytes(f"text of {name}\r\nno final newline".encode())
    (root / dir / cards).mkdir(parents=True, exist_ok=True)
    return root


def published(aliases: list[str], body: str = BODY) -> str:
    return W.render(SCAFFOLD, aliases, "\n" + body)


# --------------------------------------------------------------------------- extract_card


def test_extract_card_gives_the_cards_content_format():
    text = published(["Alpha, beta".replace(",", ""), "gamma"])
    assert A.extract_card(text) == "aliases: [Alpha beta, gamma]\n\n" + BODY


def test_extract_card_keeps_the_aliases_text_verbatim():
    # an alias with an apostrophe / CJK / dash survives; the raw bracket text is what write_card will read back
    text = published(["Don't say", "Résumé 简历", "a-b"])
    assert A.extract_card(text).splitlines()[0] == "aliases: [Don't say, Résumé 简历, a-b]"


def test_extract_card_empty_aliases_is_an_empty_list():
    assert A.extract_card(published([])).startswith("aliases: []\n")


@pytest.mark.parametrize("text", [
    SCAFFOLD,                                                   # the klode stub: never written
    SCAFFOLD.replace("zoom: stub", "zoom: full"),               # zoom lies, the placeholder says it is unwritten
    SCAFFOLD.split("<!-- scaffold")[0],                         # no marker
    published(["x"], "   \n\n"),                                # empty body
    "",
])
def test_extract_card_unwritten_or_malformed_is_none(text):
    assert A.extract_card(text) is None


def test_extract_card_marker_text_after_the_dots_is_free():
    text = published(["x"]).replace("— edit Thin/Full below the marker by hand", "(any older wording)")
    assert A.extract_card(text) == "aliases: [x]\n\n" + BODY


ALIAS = st.text(alphabet=st.characters(blacklist_characters=",[]\n\r", blacklist_categories=("Cc", "Cs")),
                min_size=1, max_size=12).map(str.strip).filter(bool)
BODIES = st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=1, max_size=60).map(
    lambda s: "## Thin\n" + s.strip().replace("\n", " ") + "x\n")


@settings(max_examples=60, deadline=None)
@given(st.lists(ALIAS, max_size=6, unique=True), BODIES)
def test_extract_then_parse_round_trips_aliases_and_body(aliases, body):
    """S5: render (what write_card publishes) -> extract_card -> parse_content gives the same aliases and body."""
    parsed = W.parse_content(A.extract_card(published(aliases, body)))
    assert parsed is not None
    assert parsed[0] == aliases
    assert parsed[1].strip() == body.strip()


@settings(max_examples=40, deadline=None)
@given(st.lists(ALIAS, max_size=6, unique=True), BODIES)
def test_republishing_an_extracted_card_is_the_same_card(aliases, body):
    """S5 idempotence: extract -> render onto the same scaffold gives back the published bytes."""
    text = published(aliases, body)
    got_aliases, got_body = W.parse_content(A.extract_card(text))
    assert W.render(SCAFFOLD, got_aliases, got_body) == text


# --------------------------------------------------------------------------- read_old


def test_read_old_settings_and_files(tmp_path):
    old = make_old(tmp_path / "old", fw="false", extra='["_raw"]')
    (old / "cards_content").mkdir()
    (old / "cards_content" / "one.md").write_text("aliases: []\n\n## Thin\nx\n", encoding="utf-8")
    (old / "library" / "cards" / "two.md").write_text(published(["x"]), encoding="utf-8")
    (old / "library" / "cards" / "INDEX.md").write_text("# index\n", encoding="utf-8")
    got = A.read_old(old)
    assert (got.kb_id, got.name, got.description) == ("old", "Old lib", "from the old scripts")
    assert got.shelves == ("a", "b")
    assert got.frameworks_enabled is False and got.guard is True and got.extra_guard_dirs == ["_raw"]
    assert sorted(got.sources) == ["a/one.txt", "a/two.txt", "b/three.txt"]
    assert got.sources["a/one.txt"].read_bytes() == b"text of one\r\nno final newline"
    assert sorted(got.cards_content) == ["one"] and sorted(got.old_cards) == ["two"]  # INDEX is not a card
    assert got.desk is None and got.holdout is None and got.syntheses_dir is None and got.bibliography is None


def test_read_old_follows_the_old_dir_and_cards_names(tmp_path):
    old = make_old(tmp_path / "old", dir="lib", cards="kards")
    (old / "lib" / "kards" / "one.md").write_text(published(["x"]), encoding="utf-8")
    (old / "lib" / "fw" / "syn").mkdir(parents=True)
    (old / "lib" / "fw" / "syn" / "dim-a.md").write_text("x", encoding="utf-8")
    (old / "lib" / "BIBLIOGRAPHY.md").write_text("# Bibliography\n", encoding="utf-8")
    got = A.read_old(old)
    assert sorted(got.sources) == ["a/one.txt", "a/two.txt", "b/three.txt"]
    assert sorted(got.old_cards) == ["one"]
    assert got.syntheses_dir == old / "lib" / "fw" / "syn" and got.bibliography == old / "lib" / "BIBLIOGRAPHY.md"


def test_read_old_finds_desk_and_holdout(tmp_path):
    old = make_old(tmp_path / "old")
    (old / "src").mkdir()
    (old / "src" / "desk.json").write_text("{}", encoding="utf-8")
    (old / "eval").mkdir()
    (old / "eval" / "gold_desk_holdout.jsonl").write_text("", encoding="utf-8")
    got = A.read_old(old)
    assert got.desk == old / "src" / "desk.json" and got.holdout == old / "eval" / "gold_desk_holdout.jsonl"
    (old / "src" / "holdout.jsonl").write_text("", encoding="utf-8")  # the one beside the desk wins
    assert A.read_old(old).holdout == old / "src" / "holdout.jsonl"


def test_read_old_does_not_write(tmp_path):
    old = make_old(tmp_path / "old")
    before = sorted((p.relative_to(old).as_posix(), p.stat().st_mtime_ns) for p in old.rglob("*"))
    A.read_old(old)
    assert sorted((p.relative_to(old).as_posix(), p.stat().st_mtime_ns) for p in old.rglob("*")) == before


@pytest.mark.parametrize("break_it", ["no_toml", "bad_toml", "no_shelves", "no_id", "bad_shelf"])
def test_read_old_refuses_what_is_not_a_library(tmp_path, break_it):
    old = make_old(tmp_path / "old")
    toml = old / "library.toml"
    if break_it == "no_toml":
        toml.rename(tmp_path / "moved.toml")
    elif break_it == "bad_toml":
        toml.write_text("[library\n", encoding="utf-8")
    elif break_it == "no_shelves":
        toml.write_text(toml.read_text(encoding="utf-8").replace('shelves = ["a", "b"]', "shelves = []"),
                        encoding="utf-8")
    elif break_it == "no_id":
        toml.write_text(toml.read_text(encoding="utf-8").replace('id = "old"\n', ""), encoding="utf-8")
    else:
        toml.write_text(toml.read_text(encoding="utf-8").replace('"a", "b"', '"../x", "b"'), encoding="utf-8")
    with pytest.raises(A.AdoptError):
        A.read_old(old)


def test_read_old_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(A.AdoptError):
        A.read_old(tmp_path / "nope")


# --------------------------------------------------------------------------- select (--adopt-shelves, D-45)


def old_with_cards(tmp_path, *, desk_cards=("one", "three"), fw="true") -> A.OldKb:
    old = make_old(tmp_path / "old", fw=fw)
    (old / "cards_content").mkdir()
    for cid in ("one", "two", "three"):
        (old / "cards_content" / f"{cid}.md").write_text(f"aliases: [{cid}]\n\n## Thin\nx\n", encoding="utf-8")
        (old / "library" / "cards" / f"{cid}.md").write_text(published([cid]), encoding="utf-8")
    (old / "src").mkdir()
    (old / "src" / "desk.json").write_text(json.dumps({"threshold": 0.8, "dimensions": [
        {"dimension": "d", "cards": list(desk_cards)}]}), encoding="utf-8")
    (old / "library" / "BIBLIOGRAPHY.md").write_text(
        "# Bibliography\n\n| source | file | notes |\n|---|---|---|\n| One | library/a/one.txt | x |\n"
        "| Three | library/b/three.txt | y |\n", encoding="utf-8")
    return A.read_old(old)


def test_select_keeps_only_the_chosen_shelves_sources_and_cards(tmp_path):
    sel = A.select(old_with_cards(tmp_path), ["b"])
    assert sel.shelves == ("b",) and sel.dropped_shelves == ("a",)
    assert sorted(sel.sources) == ["b/three.txt"]
    assert sorted(sel.cards_content) == ["three"] and sorted(sel.old_cards) == ["three"]


def test_select_keeps_the_old_shelf_order_and_ignores_repeats(tmp_path):
    sel = A.select(old_with_cards(tmp_path), ["b", "a", "b"])
    assert sel.shelves == ("a", "b") and sel.dropped_shelves == ()


def test_select_everything_changes_nothing(tmp_path):
    old = old_with_cards(tmp_path)
    sel = A.select(old, ["a", "b"])
    assert sel == old and A.select(old, None) == old


def test_select_an_unknown_shelf_is_refused(tmp_path):
    with pytest.raises(A.AdoptError, match="nope"):
        A.select(old_with_cards(tmp_path), ["a", "nope"])


def test_select_with_no_shelf_is_refused(tmp_path):
    with pytest.raises(A.AdoptError):
        A.select(old_with_cards(tmp_path), [])


def test_select_drops_the_old_desk_when_it_names_cards_of_other_shelves(tmp_path):
    sel = A.select(old_with_cards(tmp_path, desk_cards=("one", "three")), ["b"])
    assert sel.desk is None and sel.holdout is None and sel.syntheses_dir is None


def test_select_keeps_the_old_desk_when_all_its_cards_are_chosen(tmp_path):
    old = old_with_cards(tmp_path, desk_cards=("one", "two"))
    assert A.select(old, ["a"]).desk == old.desk


def test_select_bibliography_loses_the_rows_of_dropped_shelves(tmp_path):
    old = old_with_cards(tmp_path)
    assert A.bibliography_text(old) == old.bibliography.read_text(encoding="utf-8")  # whole: verbatim
    got = A.bibliography_text(A.select(old, ["b"]))
    assert "library/b/three.txt" in got and "library/a/one.txt" not in got and got.startswith("# Bibliography")


def test_inputs_record_the_selection_and_only_the_selected_files(tmp_path):
    old = old_with_cards(tmp_path)
    full, part = A.inputs(old), A.inputs(A.select(old, ["b"]))
    assert "selected" not in full and part["selected"] == ["b"]
    assert sorted(part["sources"]) == ["b/three.txt"] and sorted(part["cards"]) == ["three"]
    assert full["sources"]["b/three.txt"] == part["sources"]["b/three.txt"]


# --------------------------------------------------------------------------- patch_toml


NEW_TOML = """[library]
id = "new"
name = "new"
description = "d"
dir = "library"
cards = "cards"
shelves = ["a", "b"]

[bibliography]
enabled = true
path = "BIBLIOGRAPHY.md"

[frameworks]
enabled = false
dir = "frameworks"
syntheses = "_syntheses"

[copyright]
guard = true
extra_guard_dirs = []
"""


def test_patch_toml_carries_guard_dirs_and_bibliography_switch(tmp_path):
    old = A.read_old(make_old(tmp_path / "old", bib="false", extra='["_raw", "x y"]', dir="lib", cards="kards"))
    out = tomllib.loads(A.patch_toml(NEW_TOML, old))
    assert out["copyright"] == {"guard": True, "extra_guard_dirs": ["_raw", "x y"]}
    assert out["bibliography"]["enabled"] is False and out["bibliography"]["path"] == "BIBLIOGRAPHY.md"
    assert out["library"]["dir"] == "library" and out["library"]["cards"] == "cards"  # klode-kit's names
    assert out["library"]["id"] == "new" and out["library"]["shelves"] == ["a", "b"]


def test_patch_toml_keeps_guard_on_when_the_old_one_was_off(tmp_path):
    old = A.read_old(make_old(tmp_path / "old", guard="false"))
    assert old.guard is False
    assert tomllib.loads(A.patch_toml(NEW_TOML, old))["copyright"]["guard"] is True


def test_patch_toml_is_idempotent(tmp_path):
    old = A.read_old(make_old(tmp_path / "old", bib="false", extra='["_raw"]'))
    once = A.patch_toml(NEW_TOML, old)
    assert A.patch_toml(once, old) == once


# --------------------------------------------------------------------------- usage refusals


def run_main(project: Path, *argv: str) -> tuple[int, str]:
    import contextlib

    err = io.StringIO()
    out = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        code = RB.main(["--project", str(project), *argv])
    return code, out.getvalue() + err.getvalue()


@pytest.fixture()
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def test_adopt_with_source_is_a_usage_error(tmp_path, project):
    old = make_old(tmp_path / "old")
    src = tmp_path / "b.txt"
    src.write_text("x", encoding="utf-8")
    code, text = run_main(project, "--adopt", str(old), "--source", str(src), "--kb", "kb/demo")
    assert code == 2 and "--adopt" in text and not (project / "kb").exists()


def test_adopt_of_a_missing_directory_is_a_usage_error(tmp_path, project):
    code, text = run_main(project, "--adopt", str(tmp_path / "nope"), "--kb", "kb/demo")
    assert code == 2 and "--adopt" in text and not (project / "kb").exists()


def test_adopt_of_a_directory_without_library_toml_is_a_usage_error(tmp_path, project):
    (tmp_path / "empty").mkdir()
    code, text = run_main(project, "--adopt", str(tmp_path / "empty"), "--kb", "kb/demo")
    assert code == 2 and "library.toml" in text and not (project / "kb").exists()


def test_adopt_of_the_target_library_itself_is_a_usage_error(tmp_path, project):
    old = make_old(project / "kb" / "demo")
    code, text = run_main(project, "--adopt", str(old), "--kb", "kb/demo")
    assert code == 2 and "--adopt" in text and not (old / "builds").exists()


def test_cards_from_must_be_a_directory(tmp_path, project):
    old = make_old(tmp_path / "old")
    code, text = run_main(project, "--adopt", str(old), "--cards-from", str(tmp_path / "nope"), "--kb", "kb/demo")
    assert code == 2 and "--cards-from" in text and not (project / "kb").exists()


def test_cards_from_on_the_records_path_is_a_usage_error(tmp_path, project):
    (tmp_path / "cards").mkdir()
    code, text = run_main(project, "--cards-from", str(tmp_path / "cards"), "--kb", "kb/demo")
    assert code == 2 and "--cards-from" in text and not (project / "kb").exists()


@pytest.mark.parametrize("extra", [["--adopt", "OLD"], ["--cards-from", "CARDS", "--source", "SRC"]])
def test_loops_cannot_be_combined_with_adopt_or_cards_from(tmp_path, project, extra):
    old = make_old(tmp_path / "old")
    (tmp_path / "cards").mkdir()
    (tmp_path / "src.txt").write_text("x", encoding="utf-8")
    argv = [{"OLD": str(old), "CARDS": str(tmp_path / "cards"), "SRC": str(tmp_path / "src.txt")}.get(a, a)
            for a in extra]
    code, text = run_main(project, *argv, "--loops", "--kb", "kb/demo")
    assert code == 2 and "--loops" in text and not (project / "kb").exists()


def test_the_cli_summary_line_of_a_usage_error_is_json(tmp_path, project):
    code, text = run_main(project, "--adopt", str(tmp_path / "nope"), "--kb", "kb/demo")
    last = [ln for ln in text.splitlines() if ln.startswith("{")][-1]
    assert code == 2 and json.loads(last)["reason"] == "usage"


def test_adopt_shelves_without_adopt_is_a_usage_error(tmp_path, project):
    code, text = run_main(project, "--adopt-shelves", "a", "--kb", "kb/demo")
    assert code == 2 and "--adopt-shelves" in text and not (project / "kb").exists()


@pytest.mark.parametrize("value", ["nope", "a,nope", ","])
def test_adopt_shelves_of_a_shelf_the_old_library_lacks_is_a_usage_error(tmp_path, project, value):
    old = make_old(tmp_path / "old")
    code, text = run_main(project, "--adopt", str(old), "--adopt-shelves", value, "--kb", "kb/demo")
    assert code == 2 and "--adopt-shelves" in text and not (project / "kb").exists()
