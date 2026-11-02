"""WI-34b (migration finding): libraries made by an older klode have no ``[library].id``; adopting one takes the old
directory's name as its id (recorded as ``old_id``), instead of refusing. A present but blank id is still refused."""

from __future__ import annotations

import pytest

from klode_kit.build import adopt

TOML = """[library]
dir = "library"
cards = "cards"
shelves = ["guides", "web"]

[frameworks]
enabled = false
"""

pytestmark = pytest.mark.unit


def _old(tmp_path, toml):
    root = tmp_path / "jpo-api-docs"
    (root / "library" / "guides").mkdir(parents=True)
    (root / "library" / "web").mkdir(parents=True)
    (root / "library.toml").write_text(toml, encoding="utf-8")
    return root


def test_missing_id_falls_back_to_the_directory_name(tmp_path):
    old = adopt.read_old(_old(tmp_path, TOML))
    assert old.kb_id == "jpo-api-docs"
    assert list(old.shelves) == ["guides", "web"]


def test_blank_id_is_still_refused(tmp_path):
    with pytest.raises(adopt.AdoptError, match="id"):
        adopt.read_old(_old(tmp_path, TOML.replace("[library]\n", '[library]\nid = "  "\n')))
