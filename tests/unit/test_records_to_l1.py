"""WI-01b: ``records_to_l1`` - deterministic cards for the ``library/items`` and ``library/records`` shelves
(plan WI-01b; matrix 01b-01..06; design 02 r2.2-C 卡 / 索引, r3.4 R2-02; impl-deviations WI-02a -> 01b F1).

Contract under test:

- ``generate(build, *, min_aliases=None, table=None) -> {"cards": {id: text}, "aliases": {id: [alias]}}`` reads a
  WI-01a export (``library/{items,records}/*.txt``, ``src/export_map.jsonl``, ``src/items_index.json``) and gives
  one card per source, same id as the source (``cards_content/<id>.md`` content, the form ``write-card`` takes).
- Card front matter (everything before the first ``## ``): first line ``aliases: [...]``, then
  ``kind: deterministic``. ``## Thin``: the latest value plus the trend in words, no clinical judgement;
  ``## Full``: one ``###`` section, one bullet per source line, each with exactly one whole-line anchor.
- Aliases come only from deterministic sources (items.yaml, ``config/card_aliases.toml``, units written in the
  source and their listed spellings, rec_id and date spellings), never padded. Fewer than ``min_aliases`` (default
  ``check_cards.MIN_ALIASES_DETERMINISTIC`` = 1, user decision 2026-10-24) on any card -> ``AliasShortage`` naming
  every short card; nothing is written.
- ``write(build)`` writes ``cards_content/<id>.md``; a rerun is a no-op; a differing existing card is refused
  before anything is written. Rows for the deterministic sources are appended to ``library/BIBLIOGRAPHY.md``
  once (only when the file exists, i.e. after ``klode init``).

Fixtures: the real stages on ``fx-labs-1x`` / ``fx-labs-3x`` (``export_fx.pipeline``) then the real WI-01a
export, or records written by hand in the writeback's format. Gate 1 is the real ``check_cards``. No mock.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit import items as items_mod
from klode_kit.build import check_cards as C
from klode_kit.build import records_to_l1 as L
from klode_kit.build import records_to_txt as R
from klode_kit.config import ConfigError

pytestmark = pytest.mark.unit

LABS1 = X.FIXTURES / "fx-labs-1x"
LABS3 = X.FIXTURES / "fx-labs-3x"
A1 = "aaaaaaaaaaaa/a1"
REC_A, REC_B, REC_C = (f"r-{c * 12}" for c in "abc")
ANCHOR = re.compile(r"\(grep:\s*`([^`]*)`(?:\s*#(\d+))?\)")
TABLE = Path(L.__file__).resolve().parents[1] / "config" / "card_aliases.toml"
CLINICAL = ("正常", "异常", "偏高", "偏低", "升高", "降低", "建议", "诊断")


# --------------------------------------------------------------------------- helpers


def truth(fx: Path) -> dict:
    return json.loads((fx / "truth.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    """``{fixture name: build}``: the WI-01a export of each fixture through the real stages, made once."""
    out: dict[str, Path] = {}
    for fx in (LABS1, LABS3):
        root = tmp_path_factory.mktemp(f"l1-{fx.name}")
        project = root / "proj"
        project.mkdir()
        X.pipeline(project, fx)
        R.export(project, root / "build", kb_id=fx.name)
        out[fx.name] = root / "build"
    return out


@pytest.fixture
def exported(exports):
    """``exported(dest, fx)``: a fresh copy of the export of ``fx`` at ``dest/build``."""
    def copy(dest: Path, fx: Path) -> Path:
        build = dest / "build"
        shutil.copytree(exports[fx.name], build)
        return build
    return copy


def from_records(tmp_path: Path, records: dict[str, tuple[str, list[dict]]]) -> Path:
    """Export hand-written verified records ``{rec: (date, [row])}``; rows ``{item, value, unit?, flag?}``."""
    project = tmp_path / "proj"
    project.mkdir()
    for rec, (date, rows) in records.items():
        fields = {f"{rec}/date": date}
        for n, row in enumerate(rows):
            fields[f"{rec}/labs[{n}].item"] = row["item"]
            fields[f"{rec}/labs[{n}].value"] = row["value"]
            if row.get("unit"):
                fields[f"{rec}/labs[{n}].unit"] = row["unit"]
            if row.get("flag"):
                fields[f"{rec}/labs[{n}].flag"] = row["flag"]
        X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}},
                     effective=1, date=date)
    build = tmp_path / "build"
    R.export(project, build, kb_id="hand")
    return build


def sources(build: Path) -> dict[str, Path]:
    return {p.stem: p for shelf in ("items", "records") for p in sorted((build / "library" / shelf).glob("*.txt"))}


def section(card: str, name: str) -> str:
    m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", card, flags=re.S | re.M)
    assert m, f"no ## {name} section"
    return m.group(1)


def front(card: str) -> list[str]:
    return card.split("\n## ", 1)[0].splitlines()


def aliases_of(card: str) -> list[str]:
    m = re.match(r"^aliases: \[(.*?)\]\n", card)
    assert m, "first line is not aliases: [...]"
    return [a.strip() for a in m.group(1).split(",") if a.strip()]


def nth_line(text: str, q: str, nth: int) -> int | None:
    """0-based line index of the ``nth`` occurrence of ``q`` in ``text`` (independent of check_cards)."""
    pos = -1
    for _ in range(nth):
        pos = text.find(q, pos + 1)
        if pos < 0:
            return None
    return text.count("\n", 0, pos)


def gate(build: Path, cid: str, **kw) -> dict:
    return C.check_card(build, cid, **kw)


def key(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


# --------------------------------------------------------------------------- 01b-01


def test_front_matter_kind_deterministic(tmp_path, exported):
    """01b-01 (S1): fx-labs-1x: one card per source (6 items + 1 record), same id as the source; each card's
    front matter is ``aliases: [...]`` then ``kind: deterministic``, and gate 1 reads the kind back."""
    build = exported(tmp_path, LABS1)
    L.write(build)
    srcs = sources(build)
    cards = {p.stem: p.read_text(encoding="utf-8") for p in (build / "cards_content").glob("*.md")}
    assert set(cards) == set(srcs) and len(srcs) == 7
    for cid, card in cards.items():
        head = front(card)
        assert head[0].startswith("aliases: [")
        assert head[1] == "kind: deterministic"
        assert gate(build, cid)["kind_declared"] == "deterministic"


# --------------------------------------------------------------------------- 01b-02

ITEM_LABELS = ("血红蛋白", "HGB", "白细胞", "WBC", "红细胞", "血小板", "PLT", "中性粒细胞百分比", "淋巴细胞%")
row_st = st.fixed_dictionaries({
    "item": st.sampled_from(ITEM_LABELS),
    "value": st.sampled_from(("128", "128.0", "6.2", "15", "0.5", "<0.5", "阴性", "1e3")),
    "unit": st.sampled_from(("", "g/L", "10^9/L", "%")),
    "flag": st.sampled_from(("", "H", "L", "↑"))})
records_st = st.lists(st.tuples(st.dates(min_value=datetime.date(2020, 1, 1), max_value=datetime.date(2030, 12, 31)),
                                st.lists(row_st, min_size=1, max_size=5)), min_size=1, max_size=5)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(records_st)
def test_coverage_equals_rows(records):
    """01b-02 (S4): over generated observation sequences (repeated items, values, dates; the same row twice in a
    record, which WI-01a anchors ``#2``), every generated card's Full anchors map one-to-one onto the source's
    lines: as many anchors as lines, each anchor hits exactly one line, no line twice - and gate 1's COV agrees.
    Aliases are not under test here (``min_aliases=0``)."""
    with tempfile.TemporaryDirectory() as td:
        recs = {f"r-{n:012x}": (d.isoformat(), rows) for n, (d, rows) in enumerate(records, start=1)}
        build = from_records(Path(td), recs)
        out = L.generate(build, min_aliases=0)
        srcs = sources(build)
        assert set(out["cards"]) == set(srcs)
        (build / "cards_content").mkdir()
        for cid, card in out["cards"].items():
            text = srcs[cid].read_text(encoding="utf-8")
            lines = [i for i, ln in enumerate(text.split("\n")) if ln.strip()]
            hits = [nth_line(text, q, int(n or 1)) for q, n in ANCHOR.findall(section(card, "Full"))]
            assert len(hits) == len(lines)
            assert sorted(hits) == lines  # each anchor on one line, every line once, no repeats
            for q, n in ANCHOR.findall(card):
                assert text.count(q) >= int(n or 1) and (n or text.count(q) == 1)
            (build / "cards_content" / f"{cid}.md").write_text(card, encoding="utf-8")
            res = gate(build, cid, min_aliases=0)
            assert res["coverage"] == {"covered": len(lines), "lines": len(lines)}
            assert res["pass"], res["problems"]


# --------------------------------------------------------------------------- 01b-03


def test_thin_latest_and_trend(tmp_path, exported):
    """01b-03 (S1): fx-labs-3x: each item card's Thin names the latest date's value with its whole line as the
    anchor and states the trend against the previous observation in words (all three rise: 128->135,
    205->221, 6.4->7.1); no clinical judgement words."""
    build = exported(tmp_path, LABS3)
    cards = L.generate(build)["cards"]
    recs = sorted(truth(LABS3)["records"], key=lambda r: r["date"])
    last, prev = recs[-1], recs[-2]
    for item, slug in (("血红蛋白", "hemoglobin"), ("血小板", "platelet"), ("白细胞", "wbc")):
        row = next(r for r in last["rows"] if r["item"] == item)
        before = next(r for r in prev["rows"] if r["item"] == item)
        thin = section(cards[slug], "Thin")
        line = f"{item} {row['value']} {row['unit']} {last['date']} ⟨{last['rec_id']}⟩"
        assert ANCHOR.findall(thin) == [(line, "")]
        assert last["date"] in thin and f"{row['value']} {row['unit']}" in thin
        assert before["value"] in thin and prev["date"] in thin
        assert "上升" in thin and "下降" not in thin and "持平" not in thin
        assert not any(w in thin for w in CLINICAL)


@pytest.mark.parametrize("values,word", [(("7.1", "5.8"), "下降"), (("128", "128.0"), "持平"),
                                         (("阴性", "阳性"), "无法比较"), (("5", "6"), "上升")])
def test_thin_trend_words(tmp_path, values, word):
    """01b-03 (S2, added): the trend word follows the numeric comparison of the latest value with the one
    before it; a non-numeric value is ``无法比较``. The latest value is the later date's, whatever the rec_id
    order."""
    build = from_records(tmp_path, {REC_B: ("2026-01-02", [{"item": "白细胞", "value": values[0]}]),
                                    REC_A: ("2026-03-04", [{"item": "白细胞", "value": values[1]}])})
    thin = section(L.generate(build, min_aliases=0)["cards"]["wbc"], "Thin")
    assert ANCHOR.findall(thin) == [(f"白细胞 {values[1]} 2026-03-04 ⟨{REC_A}⟩", "")]
    others = {"上升", "下降", "持平", "无法比较"} - {word}
    assert word in thin and not any(o in thin for o in others)


def test_thin_units_differ_not_compared(tmp_path):
    """01b-03 (S3, added): numeric values in different units are not compared (``无法比较``)."""
    build = from_records(tmp_path, {REC_A: ("2026-01-02", [{"item": "白细胞", "value": "6", "unit": "10^9/L"}]),
                                    REC_B: ("2026-03-04", [{"item": "白细胞", "value": "6000", "unit": "/uL"}])})
    thin = section(L.generate(build, min_aliases=0)["cards"]["wbc"], "Thin")
    assert "无法比较" in thin and not any(w in thin for w in ("上升", "下降", "持平"))


# --------------------------------------------------------------------------- 01b-04


def test_single_observation(tmp_path, exported):
    """01b-04 (S2): fx-labs-1x: every item card has one observation -> one Full anchor, coverage 1/1, Thin says
    there is one observation and no trend, and gate 1 (all rules, aliases >= 10 included) PASSES."""
    build = exported(tmp_path, LABS1)
    L.write(build)
    (rec,) = truth(LABS1)["records"]
    slugs = {items_mod.default().resolve(r["item"])[0] for r in rec["rows"]}
    assert len(slugs) == 6
    for slug in slugs:
        card = (build / "cards_content" / f"{slug}.md").read_text(encoding="utf-8")
        assert len(ANCHOR.findall(section(card, "Full"))) == 1
        thin = section(card, "Thin")
        assert "仅 1 次观测" in thin and not any(w in thin for w in ("上升", "下降", "持平"))
        res = gate(build, slug)
        assert res["coverage"] == {"covered": 1, "lines": 1}
        assert res["pass"] and res["policy"] == "deterministic", res["problems"]


# --------------------------------------------------------------------------- 01b-05


def test_missing_anchor_fails_check(tmp_path, exported):
    """01b-05 (S3): fx-labs-3x: the generated hemoglobin card PASSES 3/3; with one Full bullet removed gate 1
    FAILS with COV 2/3."""
    build = exported(tmp_path, LABS3)
    L.write(build)
    path = build / "cards_content" / "hemoglobin.md"
    ok = gate(build, "hemoglobin")
    assert ok["pass"] and ok["coverage"] == {"covered": 3, "lines": 3}, ok["problems"]
    card = path.read_text(encoding="utf-8")
    bullets = [ln for ln in section(card, "Full").splitlines() if ln.startswith("- ")]
    assert len(bullets) == 3
    path.write_text(card.replace(bullets[1] + "\n", ""), encoding="utf-8")
    res = gate(build, "hemoglobin")
    assert res["pass"] is False
    assert res["coverage"] == {"covered": 2, "lines": 3}
    assert any(p.startswith("COV ") and "2/3" in p for p in res["problems"])


# --------------------------------------------------------------------------- 01b-06


def test_records_shelf_cards_generated(tmp_path, exported):
    """01b-06 (S1): fx-labs-3x: every records source has a same-named deterministic card covering each of its
    lines (the date line and the three observations) and gate 1 PASSES it under the deterministic policy."""
    build = exported(tmp_path, LABS3)
    L.write(build)
    recs = {r["rec_id"] for r in truth(LABS3)["records"]}
    assert {p.stem for p in (build / "library" / "records").glob("*.txt")} == recs
    for rec in recs:
        card = (build / "cards_content" / f"{rec}.md").read_text(encoding="utf-8")
        assert front(card)[1] == "kind: deterministic"
        src = (build / "library" / "records" / f"{rec}.txt").read_text(encoding="utf-8").splitlines()
        assert [q for q, _ in ANCHOR.findall(section(card, "Full"))] == src and len(src) == 4
        res = gate(build, rec)
        assert res["pass"] and res["policy"] == "deterministic", res["problems"]
        assert res["coverage"] == {"covered": 4, "lines": 4}


# --------------------------------------------------------------------------- F1 aliases (WI-02a -> 01b)


def _allowed_item(slug: str, units: set[str], table: dict) -> set[str]:
    it = next((i for i in items_mod.default().items if i.slug == slug), None)
    names = {slug, *(it.aliases if it else ())} | set(table["items"].get(slug, {}).get("names", []))
    for u in units:
        if re.search(r"[0-9A-Za-z一-鿿]", u):
            names |= {u, *table["units"].get(u, [])}
    return {key(n) for n in names}


def _date_forms(date: str) -> set[str]:
    y, m, d = date.split("-")
    return {date, f"{y}/{m}/{d}", f"{y}.{m}.{d}", f"{y}{m}{d}", f"{y}年{int(m)}月{int(d)}日"}


@pytest.mark.parametrize("fx", [LABS1, LABS3], ids=["fx-labs-1x", "fx-labs-3x"])
def test_aliases_at_least_ten_from_deterministic_sources(tmp_path, exported, fx):
    """F1 (S3, added): every generated card carries >= 10 aliases, no two equal after case / space folding,
    and every alias comes from a deterministic source: an item card's from items.yaml, the slug,
    card_aliases.toml names and the units written in its source (with their listed spellings); a record card's
    from its rec_id, its date's five spellings and the non-unit aliases of its items."""
    table = tomllib.loads(TABLE.read_text(encoding="utf-8"))
    build = exported(tmp_path, fx)
    out = L.generate(build)
    L.write(build)  # added after red: gate 1 below reads cards_content/<id>.md, which generate() does not write
    by_rec ={r["rec_id"]: r for r in truth(fx)["records"]}
    for cid, card in out["cards"].items():
        al = aliases_of(card)
        assert al == out["aliases"][cid]
        assert len(al) >= 10 and len({key(a) for a in al}) == len(al)
        if cid in by_rec:
            rec = by_rec[cid]
            allowed = {key(cid)} | {key(f) for f in _date_forms(rec["date"])}
            for r in rec["rows"]:
                allowed |= _allowed_item(items_mod.default().resolve(r["item"])[0], set(), table)
        else:
            units = {r["unit"] for rec in by_rec.values() for r in rec["rows"]
                     if items_mod.default().resolve(r["item"])[0] == cid}
            allowed = _allowed_item(cid, units, table)
        assert {key(a) for a in al} <= allowed, sorted({key(a) for a in al} - allowed)
        assert gate(build, cid)["pass"]


def test_alias_shortage_stops_and_writes_nothing(tmp_path):
    """F1 (S3, added; revised by user decision 2026-10-24 - deterministic floor 1): a synthetic item with few
    deterministic aliases (示例片: 示例片 + slug) is not padded; below an explicit floor (``min_aliases=10``)
    ``AliasShortage`` names every short card with its count and ``cards_content`` is not created; at the default
    floor the same export is written and gate 1 PASSES both cards."""
    build = from_records(tmp_path, {REC_A: ("2026-09-18", [{"item": "示例片", "value": "10mg"}]),
                                    REC_B: ("2026-09-18", [{"item": "白细胞", "value": "6.2", "unit": "10^9/L"},
                                                           {"item": "血红蛋白", "value": "128", "unit": "g/L"}])})
    with pytest.raises(L.AliasShortage) as exc:
        L.write(build, min_aliases=10)
    assert exc.value.need == 10
    assert exc.value.short == {"sample-tablet": 2, REC_A: 8}
    assert not (build / "cards_content").exists()
    assert L.main([str(build)]) == 0
    assert L.generate(build)["aliases"]["sample-tablet"] == ["示例片", "sample-tablet"]
    assert gate(build, "sample-tablet")["pass"] and gate(build, REC_A)["pass"]


def test_alias_table_guards(tmp_path):
    """F1 (S3, added): a table name that is another item's items.yaml alias, or that would break the aliases
    line, is a configuration error (never silently used)."""
    build = from_records(tmp_path, {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6.2"}])})
    clash = tmp_path / "clash.toml"
    clash.write_text('[items.wbc]\nnames = ["HGB"]\n[units]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        L.generate(build, table=clash, min_aliases=0)
    comma = tmp_path / "comma.toml"
    comma.write_text('[items.wbc]\nnames = ["white, cell"]\n[units]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        L.generate(build, table=comma, min_aliases=0)


# --------------------------------------------------------------------------- rerun / refusal / inputs


def test_write_rerun_idempotent_and_refuses_changed_card(tmp_path, exported):
    """S5 (added): a second ``write`` writes nothing and leaves every byte; with one card changed by hand the
    run is refused (``L1Error``) and no card is touched."""
    build = exported(tmp_path, LABS3)
    first = L.write(build)
    assert sorted(first["written"]) == sorted(sources(build)) and first["unchanged"] == []
    snap = X.tree(build)
    second = L.write(build)
    assert second["written"] == [] and sorted(second["unchanged"]) == sorted(sources(build))
    assert X.tree(build) == snap
    edited = build / "cards_content" / "wbc.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    (build / "cards_content" / "platelet.md").unlink()
    snap = X.tree(build)
    with pytest.raises(L.L1Error):
        L.write(build)
    assert X.tree(build) == snap


def test_generate_deterministic(tmp_path, exported):
    """S5 (added): the same export gives the same cards, byte for byte, in two separate copies."""
    a = exported(tmp_path / "a", LABS3)
    b = exported(tmp_path / "b", LABS3)
    assert L.generate(a) == L.generate(b)


def test_missing_or_inconsistent_export(tmp_path, exported):
    """S3 (added): no export map / items index -> ``L1Error``; a source line the export map does not list (the
    source changed after the export) -> ``L1Error``; nothing is written."""
    empty = tmp_path / "empty"
    (empty / "library" / "items").mkdir(parents=True)
    with pytest.raises(L.L1Error):
        L.generate(empty)
    build = exported(tmp_path, LABS3)
    src = build / "library" / "items" / "wbc.txt"
    src.write_text(src.read_text(encoding="utf-8").replace(" 6.4 ", " 6.5 "), encoding="utf-8")
    with pytest.raises(L.L1Error):
        L.write(build)
    assert not (build / "cards_content").exists()


def test_bibliography_rows_once(tmp_path, exported):
    """Added: with ``library/BIBLIOGRAPHY.md`` present (klode init), one row per deterministic source naming its
    file is appended; a rerun appends nothing."""
    build = exported(tmp_path, LABS3)
    bib = build / "library" / "BIBLIOGRAPHY.md"
    bib.write_text("# Bibliography\n\n| source | file | notes |\n|---|---|---|\n", encoding="utf-8")
    L.write(build)
    text = bib.read_text(encoding="utf-8")
    for cid, path in sources(build).items():
        cell = f"`{path.relative_to(build).as_posix()}`"
        assert sum(1 for ln in text.splitlines() if cell in ln) == 1
    L.write(build)
    assert bib.read_text(encoding="utf-8") == text


def test_main_cli(tmp_path, exported, capsys):
    """CLI (added): ``records-to-l1 KB`` exit 0 and the cards exist; ``--json`` reports the run; no KB is a
    usage error (2)."""
    build = exported(tmp_path, LABS1)
    assert L.main([str(build)]) == 0
    assert len(list((build / "cards_content").glob("*.md"))) == 7
    capsys.readouterr()
    assert L.main([str(build), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["written"] == [] and len(doc["unchanged"]) == 7
    assert L.main([]) == 2
