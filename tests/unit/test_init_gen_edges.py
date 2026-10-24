"""WI-03a edges: guard and fallback paths of ``init_kb`` and ``gen_manifest`` (adds to matrix 03a-02 .. 03a-06).

Specification as in ``test_init_kb.py`` / ``test_gen_manifest.py``; here the less common inputs:

- ``init_kb`` on a toml without ``[library]`` / ``shelves`` / ``guard`` adds them; a toml it cannot patch safely
  (invalid TOML, ``shelves`` not a list or a multi-line array that needs a new shelf, dotted ``library.*`` keys)
  is ``InitRefused`` with nothing written; a symlinked ``library.toml`` is refused.
- ``gen_manifest`` refuses (nothing written): a broken ``library.toml``, a whole outside the library, an
  unreadable / malformed explicit manifest, a malformed piece, a bad shelf or prefix, a non-UTF-8 whole, an empty
  whole, a different ``_raw/<whole>`` on ``apply``; a broken ``--head-re`` file is exit 40; leading blank lines
  join the first chapter.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from klode_kit.build import gen_manifest as G
from klode_kit.build import init_kb as I

pytestmark = pytest.mark.unit

SHELF = "book"
WHOLE = f"library/{SHELF}/_whole-{SHELF}.txt"
TWO = "Front\n\nChapter 1: Copper\n\nbody one\n\nChapter 2: Valves\n\nbody two\n"


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "D")
            for p in sorted(root.rglob("*"))}


# ---------------------------------------------------------------------------------------------------------------
# init_kb
# ---------------------------------------------------------------------------------------------------------------
def test_toml_without_library_or_copyright_gets_them(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "library.toml").write_text("[bibliography]\nenabled = true", encoding="utf-8")
    I.init_kb(kb, "demo", shelves=["book"], name="N", description="D")
    cfg = tomllib.loads((kb / "library.toml").read_text(encoding="utf-8"))
    assert cfg["library"]["id"] == "demo" and cfg["library"]["shelves"] == ["book"]
    assert cfg["copyright"]["guard"] is True and cfg["bibliography"]["enabled"] is True


def test_library_without_shelves_and_copyright_without_guard(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "library.toml").write_text('[library]\ndir = "library"\n\n[copyright]\nextra_guard_dirs = []\n',
                                     encoding="utf-8")
    I.init_kb(kb, "demo", shelves=["book"])
    cfg = tomllib.loads((kb / "library.toml").read_text(encoding="utf-8"))
    assert cfg["library"]["shelves"] == ["book"] and cfg["copyright"]["guard"] is True
    assert cfg["library"]["name"] == "" and cfg["library"]["description"] == ""


@pytest.mark.parametrize("toml", [
    "[library\n",                                                    # not TOML
    '[library]\nshelves = "book"\n',                                 # shelves not a list
    '[library]\nshelves = [\n  "manual",\n]\n',                      # multi-line array, new shelf needed
    'library.dir = "library"\n',                                     # dotted keys: no [library] table line
])
def test_unpatchable_toml_refused_unchanged(tmp_path, toml):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "library.toml").write_text(toml, encoding="utf-8")
    before = tree(kb)
    with pytest.raises(I.InitRefused):
        I.init_kb(kb, "demo", shelves=["book"])
    assert tree(kb) == before


def test_symlinked_toml_refused(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    elsewhere = tmp_path / "elsewhere.toml"
    elsewhere.write_text("", encoding="utf-8")
    (kb / "library.toml").symlink_to(elsewhere)
    with pytest.raises(I.InitRefused):
        I.init_kb(kb, "demo", shelves=["book"])
    assert elsewhere.read_text(encoding="utf-8") == ""


def test_cli_second_run_reports_nothing_to_do(tmp_path, capsys):
    kb = tmp_path / "kb"
    args = [str(kb), "--id", "demo", "--shelf", "book"]
    assert I.main(args) == 0
    capsys.readouterr()
    assert I.main(args) == 0
    assert "未改动" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------------------
# gen_manifest
# ---------------------------------------------------------------------------------------------------------------
def make_kb(root: Path, data: bytes = TWO.encode()) -> Path:
    kb = root / "kb"
    (kb / "library" / SHELF).mkdir(parents=True)
    (kb / WHOLE).write_bytes(data)
    return kb


def explicit(root: Path, obj) -> Path:
    p = root / "m.json"
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return p


@pytest.mark.parametrize("case", [
    "broken_toml", "outside", "unreadable", "not_object", "piece_not_dict", "piece_bad_id", "dup_ids",
    "bad_shelf", "bad_prefix", "not_utf8", "empty_whole", "raw_conflict", "no_pieces",
])
def test_gen_manifest_refusals_write_nothing(tmp_path, case):
    kb = make_kb(tmp_path, b"" if case == "empty_whole" else b"\xff\xfe bad" if case == "not_utf8" else TWO.encode())
    kw: dict = {"shelf": SHELF, "prefix": "demo-book"}
    piece = {"id": "demo-a", "title": "A", "start_line": 1}
    if case == "broken_toml":
        (kb / "library.toml").write_text("[library\n", encoding="utf-8")
        kw.pop("prefix")
    elif case == "outside":
        kw["whole"] = "../outside.txt"
        (tmp_path / "outside.txt").write_text(TWO, encoding="utf-8")
    elif case == "unreadable":
        kw["manifest"] = explicit(tmp_path, "{not json")
    elif case == "not_object":
        kw["manifest"] = explicit(tmp_path, [1, 2])
    elif case == "piece_not_dict":
        kw["manifest"] = explicit(tmp_path, {"shelf": SHELF, "whole": WHOLE, "pieces": [piece, "x"]})
    elif case == "piece_bad_id":
        kw["manifest"] = explicit(tmp_path, {"shelf": SHELF, "whole": WHOLE,
                                             "pieces": [{**piece, "id": "../evil"}]})
    elif case == "dup_ids":
        kw["manifest"] = explicit(tmp_path, {"shelf": SHELF, "whole": WHOLE,
                                             "pieces": [piece, {**piece, "start_line": 3}]})
    elif case == "no_pieces":
        kw["manifest"] = explicit(tmp_path, {"shelf": SHELF, "whole": WHOLE, "pieces": []})
    elif case == "bad_shelf":
        kw["shelf"] = "a/b"
    elif case == "bad_prefix":
        kw["prefix"] = "../x"
    elif case == "raw_conflict":
        (kb / "_raw").mkdir()
        (kb / "_raw" / f"_whole-{SHELF}.txt").write_text("another whole\n", encoding="utf-8")
        kw["apply"] = True
    before = tree(kb)
    with pytest.raises(G.ManifestError):
        G.gen_manifest(kb, **kw)
    assert tree(kb) == before


def test_shelf_or_manifest_required(tmp_path):
    kb = make_kb(tmp_path)
    with pytest.raises(ValueError):
        G.gen_manifest(kb)


def test_broken_head_re_is_exit_40(tmp_path):
    kb = make_kb(tmp_path)
    bad = tmp_path / "head_re.toml"
    bad.write_text("patterns = ['(']\n", encoding="utf-8")
    assert G.main([str(kb), "--shelf", SHELF, "--head-re", str(bad)]) == 40
    assert not (kb / "src").exists()


def test_custom_head_re_used(tmp_path):
    kb = make_kb(tmp_path, b"intro\n== A\nx\n== B\ny\n")
    pats = tmp_path / "head_re.toml"
    pats.write_text("patterns = ['^== ']\n", encoding="utf-8")
    res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book", head_re=pats)
    assert [p["start_line"] for p in res["manifest"]["pieces"]] == [1, 2, 4]


def test_leading_blank_lines_join_first_chapter(tmp_path):
    kb = make_kb(tmp_path, b"\n\nChapter 1: Copper\nbody\nChapter 2: Valves\nbody\n")
    res = G.gen_manifest(kb, shelf=SHELF, prefix="demo-book")
    pieces = res["manifest"]["pieces"]
    assert [p["start_line"] for p in pieces] == [1, 5]
    assert pieces[0]["id"] == "demo-book-ch01-copper" and pieces[0]["title"] == "Chapter 1: Copper"


def test_explicit_manifest_in_place_is_not_rewritten(tmp_path):
    kb = make_kb(tmp_path)
    (kb / "src").mkdir()
    out = kb / "src" / f"chapters-{SHELF}.json"
    raw = json.dumps({"shelf": SHELF, "whole": WHOLE, "pieces": [{"id": "demo-a", "title": "A", "start_line": 1},
                                                                  {"id": "demo-b", "title": "B", "start_line": 3}]})
    out.write_text(raw, encoding="utf-8")
    res = G.gen_manifest(kb, manifest=Path("src") / out.name, apply=True)
    assert res["mode"] == "explicit" and out.read_text(encoding="utf-8") == raw
    assert sorted(p.name for p in (kb / "library" / SHELF).glob("*.txt")) == ["demo-a.txt", "demo-b.txt"]


def test_citation_from_library_name(tmp_path):
    kb = make_kb(tmp_path)
    (kb / "library.toml").write_text('[library]\nid = "demo"\nname = "Synthetic Book"\n', encoding="utf-8")
    assert G.gen_manifest(kb, shelf=SHELF)["manifest"]["citation"] == "Synthetic Book"
