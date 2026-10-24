"""WI-03b: ``build/gen_desk`` - the desk spec + holdout generator, generic and medical (matrix 03b-01 .. 03b-06).

Specification (design 02 r2.4 ``gen_desk`` row; r3.4 "1 轮 2.4 剩余"; r2.6 ``desk.holdout_min``; WI-02c desk.json
structure - ``dimension / title / question / cards / cues / tests`` and holdout lines ``{"q", "gold"}``; klode-kb
SKILL.md iron law 2 - the holdout is never used to tune cues):

- ``gen_desk(kb, *, desk=None, mode="auto", items=None, holdout_templates=None, project=None) -> {"mode", "desk",
  "holdout", "written", "unchanged"}`` writes ``src/desk.json`` and ``src/holdout.jsonl``; no model is called.
- **generic** (no ``--desk``, no ``src/items_index.json``): dimensions = the pieces of ``src/chapters-*.json``
  (gen_manifest); more than 8 chapters are merged, adjacent ones only, to at most 8 dimensions; 2..8 chapters give
  one dimension each; fewer than 2 give one dimension for the whole. A dimension's ``cards`` are its chapters'
  cards, its ``cues`` the cards' own aliases (first 5 per card), its ``tests`` one tuning question.
- **records** (``src/items_index.json`` present): dimensions come only from what exists - the item cards of
  ``items_index`` grouped by item category (``items.yaml``) and the record cards of ``library/records`` - and the
  holdout questions only from those dimensions, filled with their cards' aliases.
- the holdout has at least ``desk.holdout_min`` (5) questions, all different, even for one chapter or one item;
  when the deterministic sources cannot give that many, ``holdout_insufficient``: exit 20 and nothing written.
- explicit ``--desk F`` (U7): ``src/desk.json`` is F byte for byte.
- cues come from card aliases only, never from the holdout wording; a generated holdout question is never a
  substring of a cue; and the generated files are consumed by ``desk-build --apply`` / its holdout loader as is.
- exit codes (CLI ``klode-kit gen-desk``): 0 written / unchanged; 1 refused (an existing different desk or
  holdout is never overwritten); 2 usage; 20 holdout_insufficient; 30 input contract (no chapters / items, missing
  card, invalid explicit desk or template).

Law: the holdout has >= 5 pairwise distinct questions over generated chapter / item counts (S4); every holdout
question names an existing dimension and is filled from that dimension's cards (S4); the desk spec does not depend
on the holdout templates (S6); a rerun changes nothing (S5).

Fixtures: libraries written in tmp directories (cards with ``aliases:`` front matter, a gen_manifest-shaped
chapters manifest, a WI-01a-shaped ``items_index``); the packaged ``items.yaml``; the real ``desk_build``. No mock.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.desk_fx import ALIASES as AURORA_ALIASES
from _support.desk_fx import AURORA_SRC, CH, TOML, card_text
from klode_kit import __main__ as cli
from klode_kit import items as items_mod
from klode_kit.build import desk_build as D
from klode_kit.build import gen_desk as G

pytestmark = pytest.mark.unit

DIM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PACKAGED = items_mod.default()
SLUGS = [it.slug for it in PACKAGED.items]
RECS = [("r-0000000000a1", "2026-09-18"), ("r-0000000000b2", "2026-10-02")]

_SPAWNED: list[str] = []
_WATCH = {"on": False}


def _audit(event: str, args) -> None:
    if _WATCH["on"] and event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"):
        _SPAWNED.append(event)


sys.addaudithook(_audit)


# --------------------------------------------------------------------------- library builders


def _fold(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


def _dedup(seq: list[str]) -> list[str]:
    out, seen = [], set()
    for a in seq:
        if _fold(a) not in seen:
            seen.add(_fold(a))
            out.append(a)
    return out


def write_card(kb: Path, cid: str, aliases: list[str], shelf: str = "book") -> None:
    p = kb / "library" / "cards" / f"{cid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nid: {cid}\nshelf: {shelf}\nzoom: full\naliases: [{', '.join(aliases)}]\n---\n\n# {cid}\n\n"
                 f"<!-- scaffold: managed by klode — edit Thin/Full below the marker by hand -->\n\n## Thin\nx\n",
                 encoding="utf-8")


def chapter_aliases(k: int, n: int = 10) -> list[str]:
    return [f"c{k:02d}t{j:02d}" for j in range(1, n + 1)]


def book_kb(root: Path, n_chapters: int, *, aliases: dict[int, list[str]] | None = None) -> Path:
    """A book library: ``n_chapters`` pieces in ``src/chapters-book.json`` and one card per piece."""
    kb = root / "kb"
    (kb / "src").mkdir(parents=True)
    (kb / "library" / "book").mkdir(parents=True)
    (kb / "library.toml").write_text(TOML, encoding="utf-8")
    pieces = []
    for k in range(1, n_chapters + 1):
        cid = f"demo-book-ch{k:02d}"
        pieces.append({"id": cid, "title": f"Chapter {k}: Topic Number {k}", "start_line": 1 + 10 * (k - 1)})
        write_card(kb, cid, (aliases or {}).get(k, chapter_aliases(k)))
    manifest = {"shelf": "book", "whole": "library/book/_whole-book.txt", "citation": "demo", "pieces": pieces,
                "generated_by": {"tool": "klode-kit gen-manifest", "mode": "auto", "heads": n_chapters}}
    (kb / "src" / "chapters-book.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return kb


def item_aliases(slug: str) -> list[str]:
    """The aliases records_to_l1 gives an item card from items.yaml: display name, aliases, slug."""
    it = next(i for i in PACKAGED.items if i.slug == slug)
    return _dedup([*it.aliases, slug])


def date_forms(date: str) -> list[str]:
    y, m, d = date.split("-")
    return [date, f"{y}/{m}/{d}", f"{y}.{m}.{d}", f"{y}{m}{d}", f"{y}年{int(m)}月{int(d)}日"]


def records_kb(root: Path, slugs: list[str], *, records: bool = True,
               aliases: dict[str, list[str]] | None = None) -> Path:
    """A medical library: ``items_index`` naming ``slugs`` (both records hold every item), one card per item and
    (with ``records``) one card per record on ``library/records``."""
    kb = root / "kb"
    (kb / "src").mkdir(parents=True)
    (kb / "library" / "items").mkdir(parents=True)
    (kb / "library.toml").write_text(TOML.replace('shelves = ["book"]', 'shelves = ["items", "records"]'),
                                     encoding="utf-8")
    aliases = aliases or {}
    index = {}
    for slug in slugs:
        index[slug] = [{"rec_id": r, "rev": 1, "date": d} for r, d in RECS]
        al = aliases.get(slug) or item_aliases(slug)
        (kb / "library" / "items" / f"{slug}.txt").write_text(
            "".join(f"{al[0]} 1.0 {d} ⟨{r}⟩\n" for r, d in RECS), encoding="utf-8")
        write_card(kb, slug, al, shelf="items")
    (kb / "src" / "items_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True)
                                                 + "\n", encoding="utf-8")
    if records:
        (kb / "library" / "records").mkdir(parents=True)
        for r, d in RECS:
            names = [(aliases.get(s) or item_aliases(s))[0] for s in slugs]
            (kb / "library" / "records" / f"{r}.txt").write_text(
                "".join(f"{n} 1.0 {d} ⟨{r}⟩\n" for n in names), encoding="utf-8")
            write_card(kb, r, _dedup([r, *date_forms(d), *names]), shelf="records")
    return kb


def read_desk(kb: Path) -> dict:
    return json.loads((kb / "src" / "desk.json").read_text(encoding="utf-8"))


def read_holdout(kb: Path) -> list[dict]:
    return [json.loads(x) for x in (kb / "src" / "holdout.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]


def dims_of(desk: dict) -> list[str]:
    return [d["dimension"] for d in desk["dimensions"]]


def cue_dims(desk: dict, q: str) -> set[str]:
    """Dimensions with a cue occurring in ``q`` (lower-case substring - how klode diagnose matches cues)."""
    ql = q.lower()
    return {d["dimension"] for d in desk["dimensions"] if any(c.lower() in ql for c in d["cues"])}


def card_alias_list(kb: Path, cid: str) -> list[str]:
    text = (kb / "library" / "cards" / f"{cid}.md").read_text(encoding="utf-8")
    m = re.search(r"^aliases: \[(.*)\]$", text, flags=re.M)
    assert m
    return [a.strip() for a in m.group(1).split(",") if a.strip()]


def assert_holdout_law(kb: Path, res: dict) -> None:
    """>= 5 distinct questions; the file says the same as the result; every gold is a dimension of the desk."""
    hold = read_holdout(kb)
    qs = [h["q"] for h in hold]
    assert len(qs) >= 5
    assert len(set(qs)) == len(qs)
    assert hold == res["holdout"]
    assert {h["gold"] for h in hold} <= set(dims_of(read_desk(kb)))


# --------------------------------------------------------------------------- 03b-01 / 03b-02  dimensions


@pytest.mark.parametrize("n", [9, 20])
def test_dimension_merge_gt8(tmp_path, n):
    kb = book_kb(tmp_path, n)
    res = G.gen_desk(kb)
    desk = read_desk(kb)
    assert res["mode"] == "generic"
    assert 2 <= len(desk["dimensions"]) <= G.MAX_DIMENSIONS == 8
    # only adjacent chapters merged: the dimensions' cards, in order, are exactly the chapters, in order
    assert [c for d in desk["dimensions"] for c in d["cards"]] == [f"demo-book-ch{k:02d}" for k in range(1, n + 1)]
    assert all(d["cards"] for d in desk["dimensions"])
    assert all(DIM_RE.match(x) for x in dims_of(desk)) and len(set(dims_of(desk))) == len(desk["dimensions"])
    # every dimension is asked about in the holdout (target = max(holdout_min, dimensions))
    assert {h["gold"] for h in read_holdout(kb)} == set(dims_of(desk))
    assert_holdout_law(kb, res)


@pytest.mark.parametrize("n", [2, 5, 8])
def test_dimension_one_per_chapter_up_to_8(tmp_path, n):
    kb = book_kb(tmp_path, n)
    G.gen_desk(kb)
    desk = read_desk(kb)
    assert [d["cards"] for d in desk["dimensions"]] == [[f"demo-book-ch{k:02d}"] for k in range(1, n + 1)]


def test_lt2_one_dimension(tmp_path):
    kb = book_kb(tmp_path, 1)
    res = G.gen_desk(kb)
    desk = read_desk(kb)
    assert len(desk["dimensions"]) == 1
    assert desk["dimensions"][0]["cards"] == ["demo-book-ch01"]
    assert_holdout_law(kb, res)


# --------------------------------------------------------------------------- 03b-03  >= 5 distinct (S4)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(kind=st.sampled_from(["book", "records"]), n_chapters=st.integers(min_value=1, max_value=20),
       items=st.lists(st.sampled_from(SLUGS), min_size=1, max_size=len(SLUGS), unique=True))
def test_holdout_ge5_distinct(kind, n_chapters, items):
    with tempfile.TemporaryDirectory() as tmp:
        kb = book_kb(Path(tmp), n_chapters) if kind == "book" else records_kb(Path(tmp), items)
        res = G.gen_desk(kb)
        assert res["mode"] == ("generic" if kind == "book" else "records")
        assert_holdout_law(kb, res)
        tune = {q for d in read_desk(kb)["dimensions"] for q in d["tests"]}
        assert not tune & {h["q"] for h in res["holdout"]}


# --------------------------------------------------------------------------- 03b-04  holdout_insufficient


def _nothing_written(kb: Path) -> None:
    assert not (kb / "src" / "desk.json").exists()
    assert not (kb / "src" / "holdout.jsonl").exists()


def test_holdout_insufficient_exit20(tmp_path, capsys):
    # (a) medical, items only, one item with one alias; two pad templates: 2 questions after padding < 5
    tpl = tmp_path / "tpl.jsonl"
    tpl.write_text('{"category": "*", "q": "{a} 最近一次是哪天？"}\n{"category": "*", "q": "{a} 一共几次？"}\n',
                   encoding="utf-8")
    kb = records_kb(tmp_path / "a", ["zz-custom"], records=False, aliases={"zz-custom": ["zz-custom"]})
    assert G.main([str(kb), "--holdout-templates", str(tpl)]) == 20
    assert "holdout_insufficient" in capsys.readouterr().err
    _nothing_written(kb)
    with pytest.raises(G.DeskGenError) as exc:
        G.gen_desk(kb, holdout_templates=tpl)
    assert (exc.value.code, exc.value.reason) == (20, "holdout_insufficient")
    _nothing_written(kb)

    # (b) generic, one chapter whose card has no alias usable as a cue (too short, or holding a cue separator)
    kb = book_kb(tmp_path / "b", 1, aliases={1: ["a", "b", "x|y", "c", "d", "e", "f", "g", "h", "i"]})
    assert G.main([str(kb)]) == 20
    assert "holdout_insufficient" in capsys.readouterr().err
    _nothing_written(kb)

    # (c) explicit desk whose only dimension has no cue, no holdout yet: nothing to fill the questions with
    kb = tmp_path / "c" / "kb"
    write_card(kb, "ch01", AURORA_ALIASES["ch01"])
    (kb / "library.toml").write_text(TOML, encoding="utf-8")
    spec = tmp_path / "c" / "desk.json"
    spec.write_text(json.dumps({"threshold": 0.8, "dimensions": [
        {"dimension": "construction", "title": "T", "question": "Q", "cards": ["ch01"], "cues": [],
         "tests": ["t?"]}]}), encoding="utf-8")
    assert G.main([str(kb), "--desk", str(spec)]) == 20
    assert "holdout_insufficient" in capsys.readouterr().err
    _nothing_written(kb)


# --------------------------------------------------------------------------- 03b-05  only existing dimensions


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(items=st.lists(st.sampled_from(SLUGS), min_size=1, max_size=len(SLUGS), unique=True))
def test_medical_holdout_only_existing_dimensions(items):
    with tempfile.TemporaryDirectory() as tmp:
        kb = records_kb(Path(tmp), items)
        res = G.gen_desk(kb)
        desk = read_desk(kb)
        dims = {d["dimension"]: d for d in desk["dimensions"]}
        # the cards of the desk are exactly the existing item and record cards, each in one dimension
        cards = [c for d in desk["dimensions"] for c in d["cards"]]
        assert sorted(cards) == sorted(items + [r for r, _ in RECS])
        # one dimension per category present (+ the records), never one for a category with no item
        cats = {PACKAGED.category(s) for s in items}
        assert len(dims) == len(cats) + 1
        for d in desk["dimensions"]:
            item_cards = [c for c in d["cards"] if c in items]
            assert len({PACKAGED.category(c) for c in item_cards}) <= 1
            # cues come only from the dimension's own cards' aliases
            own = {a for c in d["cards"] for a in card_alias_list(kb, c)}
            assert set(d["cues"]) <= own
        for h in res["holdout"]:
            assert h["gold"] in dims
            # filled from the gold dimension's cards, and from no other dimension's
            assert cue_dims(desk, h["q"]) == {h["gold"]}
        assert_holdout_law(kb, res)


# --------------------------------------------------------------------------- 03b-06  explicit desk


def aurora_kb(root: Path) -> Path:
    kb = root / "kb"
    (kb / "library" / "book").mkdir(parents=True)
    (kb / "library.toml").write_text(TOML, encoding="utf-8")
    for cid in CH:
        p = kb / "library" / "cards" / f"{cid}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(card_text(cid), encoding="utf-8")
    return kb


def test_explicit_desk_overrides(tmp_path):
    kb = aurora_kb(tmp_path)
    given_desk = tmp_path / "my-desk.json"
    raw = (AURORA_SRC / "desk.json").read_bytes()
    given_desk.write_bytes(raw)
    assert G.main([str(kb), "--desk", str(given_desk)]) == 0
    assert (kb / "src" / "desk.json").read_bytes() == raw
    res = G.gen_desk(kb, desk=given_desk)
    assert res["mode"] == "explicit" and res["written"] == []
    # no holdout was there: one is generated from the explicit desk's own dimensions and cues
    assert_holdout_law(kb, res)
    desk = read_desk(kb)
    for h in res["holdout"]:
        assert cue_dims(desk, h["q"]) == {h["gold"]}


def test_explicit_desk_in_place_keeps_existing_holdout(tmp_path):
    """U7 shape: ``--desk src/desk.json`` with ``src/holdout.jsonl`` already there - both left byte-identical."""
    kb = aurora_kb(tmp_path)
    (kb / "src").mkdir()
    desk_raw = (AURORA_SRC / "desk.json").read_bytes()
    hold_raw = (AURORA_SRC / "holdout.jsonl").read_bytes()
    (kb / "src" / "desk.json").write_bytes(desk_raw)
    (kb / "src" / "holdout.jsonl").write_bytes(hold_raw)
    assert G.main([str(kb), "--desk", "src/desk.json"]) == 0
    assert (kb / "src" / "desk.json").read_bytes() == desk_raw
    assert (kb / "src" / "holdout.jsonl").read_bytes() == hold_raw


def test_explicit_desk_invalid_exit30(tmp_path, capsys):
    kb = aurora_kb(tmp_path)
    bad = tmp_path / "bad.json"
    spec = json.loads((AURORA_SRC / "desk.json").read_text(encoding="utf-8"))
    spec["dimensions"][0]["cards"] = ["no-such-card"]
    bad.write_text(json.dumps(spec), encoding="utf-8")
    assert G.main([str(kb), "--desk", str(bad)]) == 30
    assert "no-such-card" in capsys.readouterr().err
    _nothing_written(kb)


# --------------------------------------------------------------------------- cues: card aliases only, no leak


def test_cues_from_card_aliases_no_holdout_leak(tmp_path):
    kb = book_kb(tmp_path, 3)
    res = G.gen_desk(kb)
    desk = read_desk(kb)
    for d in desk["dimensions"]:
        first5 = [a for c in d["cards"] for a in card_alias_list(kb, c)[:5]]
        assert d["cues"] == first5
        assert len(d["tests"]) == 1 and cue_dims(desk, d["tests"][0]) == {d["dimension"]}
    cues = [c for d in desk["dimensions"] for c in d["cues"]]
    for h in res["holdout"]:
        assert not any(h["q"] in c for c in cues), h["q"]
        assert cue_dims(desk, h["q"]) == {h["gold"]}


def test_desk_independent_of_holdout_templates(tmp_path):
    """Metamorphic (S6): other holdout templates change the holdout, never the desk (cues are not tuned on it)."""
    tpl = tmp_path / "tpl.jsonl"
    tpl.write_text("".join(json.dumps({"category": "*", "q": f"完全不同的问法 {k}：{{a}}？"}, ensure_ascii=False)
                           + "\n" for k in range(6)), encoding="utf-8")
    for make in (lambda r: book_kb(r, 4), lambda r: records_kb(r, ["hemoglobin", "alt", "sample-tablet"])):
        kb1, kb2 = make(tmp_path / f"p{id(make)}"), make(tmp_path / f"q{id(make)}")
        r1, r2 = G.gen_desk(kb1), G.gen_desk(kb2, holdout_templates=tpl)
        assert (kb1 / "src" / "desk.json").read_bytes() == (kb2 / "src" / "desk.json").read_bytes()
        assert {h["q"] for h in r1["holdout"]}.isdisjoint({h["q"] for h in r2["holdout"]})
        assert all(h["q"].startswith("完全不同的问法") for h in r2["holdout"])


# --------------------------------------------------------------------------- medical dimension details


def test_medical_unknown_category_ascii_dimension(tmp_path):
    table = tmp_path / "items.yaml"
    table.write_text("hemoglobin:\n  category: 血常规\n  aliases: [血红蛋白, HGB, Hb]\n"
                     "zz-thing:\n  category: 自定类别\n  aliases: [某某指标, ZZT]\n", encoding="utf-8")
    kb = records_kb(tmp_path, ["hemoglobin", "zz-thing"], aliases={"zz-thing": ["某某指标", "ZZT", "zz-thing"]})
    G.gen_desk(kb, items=table)
    desk = read_desk(kb)
    by_card = {c: d for d in desk["dimensions"] for c in d["cards"]}
    assert by_card["hemoglobin"]["dimension"] != by_card["zz-thing"]["dimension"]
    assert by_card["zz-thing"]["title"] == "自定类别"
    assert all(DIM_RE.match(x) for x in dims_of(desk))
    # an item the table does not know joins the uncategorised dimension instead of being dropped
    kb2 = records_kb(tmp_path / "u", ["hemoglobin", "zz-unknown"], aliases={"zz-unknown": ["未知指标", "zz-unknown"]})
    G.gen_desk(kb2)
    assert "zz-unknown" in [c for d in read_desk(kb2)["dimensions"] for c in d["cards"]]


# --------------------------------------------------------------------------- consumed by desk-build as is


@pytest.mark.parametrize("kind", ["book", "records"])
def test_generated_desk_feeds_desk_build(tmp_path, kind, capsys):
    kb = book_kb(tmp_path, 12) if kind == "book" else records_kb(tmp_path, SLUGS)
    res = G.gen_desk(kb)
    spec = D.load(kb)
    items, raw = D.load_holdout(kb / "src" / "holdout.jsonl", spec)
    assert raw is not None and items == res["holdout"] and len(items) >= 5
    assert D.main([str(kb), "--apply"]) == 0
    diag = (kb / "library" / "frameworks" / "_syntheses" / "_diagnostics.md").read_text(encoding="utf-8")
    assert diag == D.render_diagnostics(spec)
    assert [ln.rsplit("|", 2)[1].strip() for ln in diag.splitlines() if ln.startswith("| ") and "---" not in ln
            and "symptom cues" not in ln] == dims_of(spec)


# --------------------------------------------------------------------------- rerun, refusal, guards


def test_rerun_unchanged_and_existing_different_refused(tmp_path, capsys):
    kb = records_kb(tmp_path, ["hemoglobin", "creatinine"])
    first = G.gen_desk(kb)
    assert sorted(first["written"]) == ["src/desk.json", "src/holdout.jsonl"]
    before = {p: (kb / p).read_bytes() for p in first["written"]}
    again = G.gen_desk(kb)
    assert again["written"] == [] and sorted(again["unchanged"]) == sorted(before)
    assert {p: (kb / p).read_bytes() for p in before} == before
    # a holdout edited by hand (it is frozen by desk-build) is never overwritten
    (kb / "src" / "holdout.jsonl").write_text('{"q": "改过的题？", "gold": "records"}\n', encoding="utf-8")
    edited = (kb / "src" / "holdout.jsonl").read_bytes()
    assert G.main([str(kb)]) == 1
    assert "holdout.jsonl" in capsys.readouterr().err
    assert (kb / "src" / "holdout.jsonl").read_bytes() == edited
    assert (kb / "src" / "desk.json").read_bytes() == before["src/desk.json"]


def test_deterministic_across_libraries(tmp_path):
    a, b = book_kb(tmp_path / "a", 11), book_kb(tmp_path / "b", 11)
    G.gen_desk(a)
    G.gen_desk(b)
    for rel in ("src/desk.json", "src/holdout.jsonl"):
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_no_source_or_missing_card_exit30(tmp_path, capsys):
    empty = tmp_path / "empty"
    (empty / "src").mkdir(parents=True)
    (empty / "library.toml").write_text(TOML, encoding="utf-8")
    assert G.main([str(empty)]) == 30
    capsys.readouterr()
    kb = book_kb(tmp_path / "m", 3)
    (kb / "library" / "cards" / "demo-book-ch02.md").rename(tmp_path / "moved-away.md")
    assert G.main([str(kb)]) == 30
    assert "demo-book-ch02" in capsys.readouterr().err
    _nothing_written(kb)


def test_bad_template_exit30(tmp_path, capsys):
    kb = book_kb(tmp_path, 2)
    tpl = tmp_path / "tpl.jsonl"
    tpl.write_text('{"category": "*", "q": "no placeholder here"}\n', encoding="utf-8")
    assert G.main([str(kb), "--holdout-templates", str(tpl)]) == 30
    _nothing_written(kb)


def test_usage_and_registered_command(tmp_path, capsys):
    assert G.main([]) == 2
    assert G.main([str(tmp_path), "--mode", "nonsense"]) == 2
    kb = book_kb(tmp_path, 2)
    assert cli.main(["gen-desk", str(kb)]) == 0
    assert (kb / "src" / "desk.json").is_file()


def test_no_model_or_subprocess_called(tmp_path):
    kb = records_kb(tmp_path, ["hemoglobin", "sample-tablet"])
    _SPAWNED.clear()
    _WATCH["on"] = True
    try:
        res = G.gen_desk(kb)
    finally:
        _WATCH["on"] = False
    assert _SPAWNED == []
    assert len(res["holdout"]) >= 5
