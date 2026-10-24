"""WI-01b edges (added after the red of ``test_records_to_l1.py``): anchors that need ``#N`` or cannot be written,
inconsistent exports, alias-table errors and the CLI refusal path. Real export, real gate 1, no mock."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support import export_fx as X
from klode_kit.build import check_cards as C
from klode_kit.build import records_to_l1 as L
from klode_kit.build import records_to_txt as R
from klode_kit.config import ConfigError

pytestmark = pytest.mark.unit

A1 = "aaaaaaaaaaaa/a1"
REC_A, REC_B = "r-aaaaaaaaaaaa", "r-bbbbbbbbbbbb"


def build_of(tmp_path: Path, records: dict[str, tuple[str, list[dict]]]) -> Path:
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    for rec, (date, rows) in records.items():
        fields = {f"{rec}/date": date}
        for n, row in enumerate(rows):
            fields[f"{rec}/labs[{n}].item"] = row["item"]
            fields[f"{rec}/labs[{n}].value"] = row["value"]
            if row.get("unit"):
                fields[f"{rec}/labs[{n}].unit"] = row["unit"]
        X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}},
                     effective=1, date=date)
    build = tmp_path / "build"
    R.export(project, build, kb_id="edges")
    return build


def test_earlier_substring_line_gets_nth_and_passes(tmp_path):
    """A record line that also occurs inside an EARLIER line (``蛋白 1 ...`` after ``血红蛋白 1 ...``) is anchored
    with its own occurrence ``#2``; gate 1 COV still finds both lines once (fx-chain records print no date line)."""
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "血红蛋白", "value": "1"},
                                                       {"item": "蛋白", "value": "1"}])})
    card = L.generate(build, min_aliases=0)["cards"][REC_A]
    assert f"(grep: `蛋白 1 2026-09-18 ⟨{REC_A}⟩` #2)" in card
    (build / "cards_content").mkdir()
    (build / "cards_content" / f"{REC_A}.md").write_text(card, encoding="utf-8")
    res = C.check_card(build, REC_A, min_aliases=0)
    assert res["pass"] and res["coverage"] == {"covered": 2, "lines": 2}, res["problems"]


def test_later_superstring_line_cannot_be_anchored(tmp_path):
    """The same two rows in the other order: the first line's text also occurs inside a later line, so no anchor
    can name it unambiguously -> ``L1Error`` (never a card gate 1 would call AMBIGUOUS)."""
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "蛋白", "value": "1"},
                                                       {"item": "血红蛋白", "value": "1"}])})
    with pytest.raises(L.L1Error, match="later line"):
        L.generate(build, min_aliases=0)


def test_backtick_line_refused(tmp_path):
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6`2"}])})
    with pytest.raises(L.L1Error, match="backtick"):
        L.generate(build, min_aliases=0)


def test_inconsistent_exports(tmp_path):
    """Export map row past the source's end, a source the map does not know, an items index naming another item,
    an unreadable map, an empty source: each is ``L1Error``."""
    good = build_of(tmp_path / "g", {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6.2"}])})
    emap = good / "src" / "export_map.jsonl"
    rows = [json.loads(r) for r in emap.read_text(encoding="utf-8").splitlines()]

    def variant(name: str, edit) -> Path:
        b = tmp_path / name
        import shutil

        shutil.copytree(good, b)
        edit(b)
        return b

    def extra_row(b: Path) -> None:
        row = dict(rows[0], line_no=99)
        with (b / "src" / "export_map.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def foreign_source(b: Path) -> None:
        (b / "library" / "items" / "rbc.txt").write_text("红细胞 4 2026-09-18 ⟨r-cccccccccccc⟩\n", encoding="utf-8")

    def ghost_map(b: Path) -> None:
        (b / "library" / "items" / "wbc.txt").rename(b / "library" / "items" / "wbc.bak")

    def index_other(b: Path) -> None:
        (b / "src" / "items_index.json").write_text('{"rbc": []}\n', encoding="utf-8")

    def broken_map(b: Path) -> None:
        (b / "src" / "export_map.jsonl").write_text("{not json\n", encoding="utf-8")

    def empty_source(b: Path) -> None:
        (b / "library" / "items" / "zzz.txt").write_text("\n", encoding="utf-8")

    for name, edit in (("extra", extra_row), ("foreign", foreign_source), ("ghost", ghost_map),
                       ("index", index_other), ("broken", broken_map), ("empty", empty_source)):
        with pytest.raises(L.L1Error):
            L.generate(variant(name, edit), min_aliases=0)


def test_alias_table_errors(tmp_path):
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6.2"}])})
    cases = {"missing": None, "toml": "[items.wbc\n", "names": '[items.wbc]\nnames = "x"\n',
             "unit_list": '[units]\n"g/L" = "x"\n', "unit_bad": '[units]\n"g/L" = ["a,b"]\n'}
    for name, text in cases.items():
        path = tmp_path / f"{name}.toml"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError):
            L.generate(build, table=path, min_aliases=0)


def test_custom_table_feeds_aliases(tmp_path):
    """A project table's names and unit spellings become aliases (after the items.yaml ones); ``%`` never does."""
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6.2", "unit": "%"},
                                                       {"item": "血小板", "value": "210", "unit": "X/L"}])})
    table = tmp_path / "t.toml"
    table.write_text('[items.wbc]\nnames = ["合成名甲"]\n[units]\n"X/L" = ["合成单位乙"]\n', encoding="utf-8")
    out = L.generate(build, table=table, min_aliases=0)["aliases"]
    assert out["wbc"] == ["白细胞", "白细胞计数", "WBC", "合成名甲"]
    assert out["platelet"] == ["血小板", "血小板计数", "PLT", "platelet", "X/L", "合成单位乙"]


def test_main_refused_exit_1(tmp_path, capsys):
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6.2", "unit": "10^9/L"},
                                                       {"item": "血红蛋白", "value": "128", "unit": "g/L"}]),
                                REC_B: ("2026-09-19", [{"item": "白细胞", "value": "6.4", "unit": "10^9/L"}])})
    assert L.main([str(build)]) == 0
    assert "written" in capsys.readouterr().out
    card = build / "cards_content" / "wbc.md"
    card.write_text("changed\n", encoding="utf-8")
    assert L.main([str(build)]) == 1
    assert "refusing" in capsys.readouterr().err
    assert card.read_text(encoding="utf-8") == "changed\n"


def test_fx_manualfix_sample_tablet_passes_gate(tmp_path):
    """User decision 2026-10-24 (deterministic F1 floor 1): fx-manualfix through the real stages and export -
    the 示例片 item card and its record card are generated with only their deterministic aliases (no padding) and
    gate 1 PASSES every card; an authored card still needs ``cards.min_aliases`` (10)."""
    project = tmp_path / "proj"
    project.mkdir()
    X.pipeline(project, X.FIXTURES / "fx-manualfix")
    build = tmp_path / "build"
    R.export(project, build, kb_id="fx-manualfix")
    res = L.write(build)
    assert "sample-tablet" in res["written"]
    aliases = L.generate(build)["aliases"]
    assert aliases["sample-tablet"] == ["示例片", "sample-tablet"]
    for cid in res["written"]:
        r = C.check_card(build, cid)
        assert r["pass"] and r["policy"] == "deterministic", (cid, r["problems"])

    book = build / "library" / "book"
    book.mkdir()
    (book / "ch01.txt").write_text("".join(f"Line number {n} of the synthetic chapter text.\n" for n in range(6)),
                                   encoding="utf-8")
    full = "".join(f"- point {n} (grep: `Line number {n} of the synthetic`)\n" for n in range(6))
    (build / "cards_content" / "ch01.md").write_text(
        f"aliases: [a, b]\n\n# Ch\n\n## Thin\nx (grep: `Line number 0 of the synthetic`)\n\n## Full\n### P\n{full}",
        encoding="utf-8")
    r = C.check_card(build, "ch01")
    assert r["policy"] == "authored" and [p for p in r["problems"] if p.startswith("F1")] == [
        "F1 aliases 只有 2 个，需 ≥ 10"]


def test_deterministic_f1_floor_is_one(tmp_path):
    """check_cards F1 on a deterministic shelf: 1 alias PASSES, an empty list FAILS with the floor named; the
    ``min_aliases`` argument (the authored floor) does not raise it."""
    build = build_of(tmp_path, {REC_A: ("2026-09-18", [{"item": "白细胞", "value": "6.2"}])})
    line = f"白细胞 6.2 2026-09-18 ⟨{REC_A}⟩"
    body = f"\n# W\n\n## Thin\nx (grep: `{line}`)\n\n## Full\n### P\n- p (grep: `{line}`)\n"
    (build / "cards_content").mkdir()
    card = build / "cards_content" / "wbc.md"
    card.write_text("aliases: [白细胞]\n" + body, encoding="utf-8")
    assert C.MIN_ALIASES_DETERMINISTIC == 1
    assert C.check_card(build, "wbc", min_aliases=10, min_anchors_full=5)["pass"]
    card.write_text("aliases: []\n" + body, encoding="utf-8")
    r = C.check_card(build, "wbc")
    assert r["pass"] is False and "F1 aliases 只有 0 个，需 ≥ 1" in r["problems"]
