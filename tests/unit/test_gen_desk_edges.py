"""WI-03b edges: ``build/gen_desk`` input guards, the >8 medical merge, ambiguity filtering, library id.

Same contract as ``test_gen_desk.py`` (whose module docstring is the specification); these cases were added after
that file's red was recorded, to cover the guards the matrix rows do not name. No mock.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from _support.desk_fx import AURORA_SRC
from klode_kit.build import gen_desk as G

pytestmark = pytest.mark.unit

_spec = importlib.util.spec_from_file_location("_gd", Path(__file__).with_name("test_gen_desk.py"))
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)


def test_template_file_guards(tmp_path, capsys):
    kb = T.book_kb(tmp_path, 2)
    assert G.main([str(kb), "--holdout-templates", str(tmp_path / "missing.jsonl")]) == 30
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n{not json\n", encoding="utf-8")
    assert G.main([str(kb), "--holdout-templates", str(bad)]) == 30
    two = tmp_path / "two.jsonl"
    two.write_text('{"q": "{a} 和 {a}？"}\n', encoding="utf-8")
    assert G.main([str(kb), "--holdout-templates", str(two)]) == 30
    assert capsys.readouterr().err.count("bad_template") == 3
    T._nothing_written(kb)


def test_bad_manifest_and_items_index_exit30(tmp_path):
    kb = T.book_kb(tmp_path / "m", 2)
    (kb / "src" / "chapters-book.json").write_text('{"pieces": [{"title": "no id"}]}', encoding="utf-8")
    with pytest.raises(G.DeskGenError) as exc:
        G.gen_desk(kb)
    assert (exc.value.code, exc.value.reason) == (30, "bad_manifest")
    kb = T.records_kb(tmp_path / "i", ["hemoglobin"])
    (kb / "src" / "items_index.json").write_text("[]", encoding="utf-8")
    with pytest.raises(G.DeskGenError) as exc:
        G.gen_desk(kb)
    assert (exc.value.code, exc.value.reason) == (30, "bad_items_index")
    kb = T.records_kb(tmp_path / "e", ["hemoglobin"], records=False)
    (kb / "src" / "items_index.json").write_text("{}", encoding="utf-8")
    with pytest.raises(G.DeskGenError) as exc:
        G.gen_desk(kb)
    assert (exc.value.code, exc.value.reason) == (30, "no_source")


def test_medical_more_than_8_dimensions_merged(tmp_path):
    cats = [f"类别{k}" for k in range(10)]
    table = tmp_path / "items.yaml"
    table.write_text("".join(f"it-{k}:\n  category: {c}\n  aliases: [指标{k}甲, IT{k}X]\n" for k, c in enumerate(cats)),
                     encoding="utf-8")
    slugs = [f"it-{k}" for k in range(10)]
    kb = T.records_kb(tmp_path, slugs, aliases={s: [f"指标{k}甲", f"IT{k}X", s] for k, s in enumerate(slugs)})
    res = G.gen_desk(kb, items=table)
    desk = T.read_desk(kb)
    assert len(desk["dimensions"]) == G.MAX_DIMENSIONS
    assert sorted(c for d in desk["dimensions"] for c in d["cards"]) == sorted(slugs + [r for r, _ in T.RECS])
    for h in res["holdout"]:
        assert T.cue_dims(desk, h["q"]) == {h["gold"]}
    T.assert_holdout_law(kb, res)


def test_ambiguous_questions_dropped(tmp_path):
    """Chapter 2's only cue holds chapter 1's cue: its questions would route to both, so none is taken."""
    kb = T.book_kb(tmp_path, 2, aliases={1: ["shell"] + T.chapter_aliases(1)[:4], 2: ["copper shell"]})
    res = G.gen_desk(kb)
    desk = T.read_desk(kb)
    golds = [h["gold"] for h in res["holdout"]]
    d1, d2 = T.dims_of(desk)
    assert d2 not in golds and golds.count(d1) >= 5
    for h in res["holdout"]:
        assert T.cue_dims(desk, h["q"]) == {h["gold"]}


def test_library_id_recorded_and_holdout_min_from_project(tmp_path):
    kb = T.book_kb(tmp_path, 2)
    toml = (kb / "library.toml").read_text(encoding="utf-8")
    (kb / "library.toml").write_text(toml.replace("[library]\n", '[library]\nid = "demo-klode"\n'), encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "klode-kit.toml").write_text("[desk]\nholdout_min = 7\n", encoding="utf-8")
    res = G.gen_desk(kb, project=project)
    assert T.read_desk(kb)["kb"] == "demo-klode"
    assert len(res["holdout"]) == 7


def test_explicit_desk_with_foreign_holdout_exit30(tmp_path, capsys):
    kb = T.aurora_kb(tmp_path)
    (kb / "src").mkdir()
    (kb / "src" / "holdout.jsonl").write_text('{"q": "Q?", "gold": "no-such-dimension"}\n', encoding="utf-8")
    given_desk = tmp_path / "desk.json"
    given_desk.write_bytes((AURORA_SRC / "desk.json").read_bytes())
    assert G.main([str(kb), "--desk", str(given_desk)]) == 30
    assert "bad_holdout" in capsys.readouterr().err
    assert not (kb / "src" / "desk.json").exists()


def test_broken_items_table_is_configuration_error(tmp_path, capsys):
    kb = T.records_kb(tmp_path, ["hemoglobin"])
    bad = tmp_path / "items.yaml"
    bad.write_text("Not A Slug:\n", encoding="utf-8")
    assert G.main([str(kb), "--items", str(bad)]) == 40
    assert "configuration error" in capsys.readouterr().err
    T._nothing_written(kb)


def test_api_mode_guards(tmp_path):
    kb = T.book_kb(tmp_path, 2)
    with pytest.raises(ValueError):
        G.gen_desk(kb, mode="nonsense")
    with pytest.raises(ValueError):
        G.gen_desk(kb, mode="explicit")
    res = G.gen_desk(kb, mode="generic", holdout_min=5)
    assert res["mode"] == "generic"
    assert json.loads((kb / "src" / "desk.json").read_text(encoding="utf-8"))["generated_by"]["mode"] == "generic"


def test_unreadable_library_toml_means_no_kb_field(tmp_path):
    kb = T.book_kb(tmp_path, 2)
    (kb / "library.toml").write_text("[library\nbroken", encoding="utf-8")
    G.gen_desk(kb)
    assert "kb" not in T.read_desk(kb)
