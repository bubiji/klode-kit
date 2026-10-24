"""WI-03a: ``build/init_kb`` - the library skeleton on top of ``klode init`` (matrix 03a-02, 03a-03; 03 G11, G14).

Specification (03 G11/G14; original klode-build SKILL.md step 1; design 02 §3.2 G11/G14):

- ``init_kb(kb, kb_id, *, shelves, name="", description="") -> dict`` makes ``kb`` a library the rest of the
  build can use:
  * ``library.toml``: written from klode 0.5.0's own ``init`` template when absent; when present (e.g. just made
    by ``klode init``) only the missing pieces are added, every existing line kept. Afterwards ``[library]`` has
    ``id`` (a klode slug), ``name``, ``description``; every requested shelf is in ``shelves``; ``[copyright]
    guard = true``.
  * directories ``library/<shelf>`` (each), ``library/cards``, ``cards_content``, ``src``, ``_raw``; a
    ``library/BIBLIOGRAPHY.md`` header when missing.
  * ``.gitignore``: the user's existing lines are kept byte for byte and only appended to: klode's managed block
    (``library/<shelf>/*.txt`` / ``*.pdf``: the sources stay local, as in the original template) and ``_raw/``,
    ``_work/``, ``_trash/`` - each line once.
  * returns ``{"changed": [relative paths written or created]}``; a second run with the same arguments writes
    nothing and returns ``changed == []``.
- Guards: an id that is not a slug, a shelf that is not a plain name, a name/description with a line break are
  ``ValueError`` (CLI exit 2) and nothing is written; an existing toml whose ``id``/``name``/``description``
  differ from the request, or with ``[copyright] guard = false``, is ``InitRefused`` (CLI exit 1) and nothing is
  written (no in-place overwrite of the user's values).

Law: init is idempotent (03a-02, S5).
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import pytest

from klode_kit.build import init_kb as I

pytestmark = pytest.mark.unit

# The library.toml that `klode init --shelf manual` (klode 0.5.0) writes, byte for byte.
KLODE_INIT_TOML = """\
# library.toml — a klode knowledge library.
# One card per source, exposed at four Levels of Zoom (L0 meta / L1 thin / L2 full / L3 content).
# See SPEC.md for the card format and the two disciplines that keep it from rotting.

[library]
dir = "library"          # library root, relative to this file
cards = "cards"          # cards subdir, relative to the library dir
shelves = ["manual"]    # the taxonomy: subdirs of the library dir holding source .txt files

[bibliography]
enabled = true
path = "BIBLIOGRAPHY.md"  # relative to the library dir

[frameworks]              # optional per-dimension distillation layer
enabled = false
dir = "frameworks"
syntheses = "_syntheses"

[copyright]
guard = true              # fail `klode check` if any guarded .txt/.pdf is git-tracked
extra_guard_dirs = []     # extra dirs (relative to the library dir) to also guard

[normalize]
backup_dir = ""           # empty => system temp dir (kept OUTSIDE the tree)
backup_keep = 3
dict_path = "/usr/share/dict/words"
"""
KLODE_INIT_GITIGNORE = """\
# >>> klode managed: the corpus is copyrighted — sources stay local, cards are tracked
library/manual/*.txt
library/manual/*.pdf
# <<< klode managed
"""
DIRS = ("library/cards", "cards_content", "src", "_raw")


def tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode() + b"\0")
        h.update(b"D" if p.is_dir() else hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def klode_inited(root: Path) -> Path:
    kb = root / "kb"
    (kb / "library" / "manual").mkdir(parents=True)
    (kb / "library" / "cards").mkdir()
    (kb / "library.toml").write_text(KLODE_INIT_TOML, encoding="utf-8")
    (kb / ".gitignore").write_text(KLODE_INIT_GITIGNORE, encoding="utf-8")
    return kb


def load(kb: Path) -> dict:
    return tomllib.loads((kb / "library.toml").read_text(encoding="utf-8"))


def ignore_lines(kb: Path) -> list[str]:
    return [ln for ln in (kb / ".gitignore").read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------------------------------------------
# 03a-02 idempotence
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("start", ["empty", "klode_init"])
def test_init_idempotent(tmp_path, start):
    if start == "empty":
        kb = tmp_path / "kb"
    else:
        kb = klode_inited(tmp_path)
    first = I.init_kb(kb, "demo-kb", shelves=["manual"], name="Demo", description="合成演示库")
    sha1 = tree_sha(kb)
    second = I.init_kb(kb, "demo-kb", shelves=["manual"], name="Demo", description="合成演示库")
    assert tree_sha(kb) == sha1
    assert first["changed"] and second["changed"] == []
    lines = ignore_lines(kb)
    assert len(lines) == len(set(lines)), lines


def test_init_fields_dirs_and_gitignore(tmp_path):
    kb = tmp_path / "kb"
    I.init_kb(kb, "demo-kb", shelves=["book", "notes"], name="Demo — 演示", description='A "quoted" 描述')
    cfg = load(kb)
    assert cfg["library"]["id"] == "demo-kb"
    assert cfg["library"]["name"] == "Demo — 演示"
    assert cfg["library"]["description"] == 'A "quoted" 描述'
    assert cfg["library"]["shelves"] == ["book", "notes"]
    assert cfg["copyright"]["guard"] is True
    for d in (*DIRS, "library/book", "library/notes"):
        assert (kb / d).is_dir(), d
    assert (kb / "library" / "BIBLIOGRAPHY.md").read_text(encoding="utf-8").startswith("# Bibliography")
    lines = ignore_lines(kb)
    for want in ("library/book/*.txt", "library/notes/*.txt", "_raw/", "_work/", "_trash/"):
        assert want in lines, (want, lines)


def test_init_patches_klode_toml_keeping_every_line(tmp_path):
    kb = klode_inited(tmp_path)
    I.init_kb(kb, "demo-kb", shelves=["manual"], name="Demo", description="d")
    text = (kb / "library.toml").read_text(encoding="utf-8")
    old = KLODE_INIT_TOML.splitlines()
    new = text.splitlines()
    it = iter(new)
    assert all(any(line == n for n in it) for line in old), "an original line was changed, dropped or reordered"
    cfg = load(kb)
    assert (cfg["library"]["id"], cfg["library"]["name"], cfg["library"]["description"]) == ("demo-kb", "Demo", "d")
    assert cfg["library"]["dir"] == "library" and cfg["library"]["cards"] == "cards"


def test_init_adds_missing_shelf(tmp_path):
    kb = klode_inited(tmp_path)
    I.init_kb(kb, "demo-kb", shelves=["book"], name="Demo", description="d")
    assert load(kb)["library"]["shelves"] == ["manual", "book"]
    assert (kb / "library" / "book").is_dir()
    lines = ignore_lines(kb)
    assert "library/manual/*.txt" in lines and "library/book/*.txt" in lines


# ---------------------------------------------------------------------------------------------------------------
# 03a-03 existing .gitignore kept, only appended to
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("trailing_newline", [True, False])
def test_init_preserves_existing_gitignore(tmp_path, trailing_newline):
    kb = tmp_path / "kb"
    kb.mkdir()
    old = "# my rules\nnode_modules/\n*.log\n_raw/\n" + ("" if trailing_newline else "secret.env")
    (kb / ".gitignore").write_text(old, encoding="utf-8")
    I.init_kb(kb, "demo-kb", shelves=["book"], name="Demo", description="d")
    new = (kb / ".gitignore").read_text(encoding="utf-8")
    prefix = old if trailing_newline else old + "\n"
    assert new.startswith(prefix), new
    added = new[len(prefix):].splitlines()
    assert "_work/" in added and "library/book/*.txt" in added
    assert "_raw/" not in added and "_raw" not in added  # already ignored by the user's own line
    lines = ignore_lines(kb)
    assert len(lines) == len(set(lines)), lines


# ---------------------------------------------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kwargs", [
    {"kb_id": "Demo_KB", "shelves": ["book"]},
    {"kb_id": "demo--kb", "shelves": ["book"]},
    {"kb_id": "demo-kb", "shelves": ["../escape"]},
    {"kb_id": "demo-kb", "shelves": []},
    {"kb_id": "demo-kb", "shelves": ["book"], "name": "two\nlines"},
])
def test_invalid_input_writes_nothing(tmp_path, kwargs):
    kb = klode_inited(tmp_path)
    before = tree_sha(kb)
    kb_id = kwargs.pop("kb_id")
    with pytest.raises(ValueError):
        I.init_kb(kb, kb_id, **kwargs)
    assert tree_sha(kb) == before


@pytest.mark.parametrize("edit", [
    ('dir = "library"', 'id = "other-kb"\ndir = "library"'),
    ('dir = "library"', 'name = "Another"\ndir = "library"'),
    ("guard = true ", "guard = false "),
])
def test_conflicting_toml_refused_unchanged(tmp_path, edit):
    kb = klode_inited(tmp_path)
    toml = kb / "library.toml"
    toml.write_text(KLODE_INIT_TOML.replace(*edit, 1), encoding="utf-8")
    before = tree_sha(kb)
    with pytest.raises(I.InitRefused):
        I.init_kb(kb, "demo-kb", shelves=["manual"], name="Demo", description="d")
    assert tree_sha(kb) == before


def test_cli_exit_codes(tmp_path, capsys):
    kb = tmp_path / "kb"
    assert I.main([str(kb), "--id", "demo-kb", "--shelf", "book", "--name", "Demo", "--description", "d"]) == 0
    assert load(kb)["library"]["id"] == "demo-kb"
    assert I.main([str(kb), "--id", "Bad_Id", "--shelf", "book"]) == 2
    assert I.main([str(kb), "--id", "other-kb", "--shelf", "book", "--name", "Demo", "--description", "d"]) == 1
    assert load(kb)["library"]["id"] == "demo-kb"
    assert I.main([str(kb)]) == 2
