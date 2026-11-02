"""WI-34 (contract_replay): ``klode-kit build --adopt OLD_KB`` and ``--cards-from DIR`` - migrating a pre-klode-kit
klode library into an immutable klode-kit build (user goal 2026-11-02).

Real processes: the project's own commands through the launcher and the real ``klode`` 0.5.0 of PATH; the old
library is built by the real ``klode`` too (``tests/_support/adopt_fx.old_kb``); the fake ``claude`` is installed but
must never be called (the adopt path has no writer, ``--cards-from`` replaces it).

Specification (see ``tests/unit/test_adopt_wi34.py`` for the pure parts):

- all 13 steps, in the fixed order; ``export`` / ``ingest`` / ``split_manifest`` PASS "not applicable: adopted";
  ``dispatch_writers`` reports "N cards adopted from <old>" and calls no model.
- sources: every ``library/<shelf>/*.txt`` of the old library lands byte-identical in the same shelf; the shelves are
  those of the old library.toml; the sha of each is in ``build_inputs.json``.
- cards: ``cards_content/<id>.md`` of the old library byte for byte, else extracted from below the scaffold marker of
  ``library/cards/<id>.md``; the first wins when both exist; ``--cards-from DIR`` wins over both. A card with none of
  them fails ``dispatch_writers`` (exit 20) naming it; later steps are NOT_RUN.
- desk: ``src/desk.json`` + holdout + ``_syntheses/<dim>.md`` of the old library are carried when its frameworks are
  enabled and ``desk-build --verify`` passes as usual; without them (no desk, or frameworks disabled) the desk is
  generated from the cards (gen-desk, one piece per card) and the first build ends exit 20 at ``desk_apply`` with
  ``desk syntheses missing for: <dimensions>``; ``desk-tasks`` + workers + a second build with ``--syntheses`` and
  ``--repair`` finish it, the cards carried over byte for byte.
- the old library is only read: its tree (hashes and mtimes) is the same afterwards; the project is written only
  below ``kb/<id>/builds/<build_id>/``.
- ``--cards-from`` on the book path: cards in DIR are copied instead of dispatching writers (no ``claude`` call); a
  card not in DIR is still written by a writer; the shas are in ``build_inputs.json``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from _support import adopt_fx as AF
from _support import build_fx as B
from _support import desk_fx as DX
from _support.flows import REPO, base_env
from klode_kit.build import run_build as RB
from klode_kit.build.check_synthesis import ANCHOR_RE
from klode_kit.contract.schema import issues

pytestmark = pytest.mark.contract_replay

KB = "kb/demo-adopt"
SHELVES = ("alpha", "beta", "gamma")


@pytest.fixture()
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def one_build(project: Path) -> Path:
    (bd,) = B.builds(project)
    return bd


def inputs(bd: Path) -> dict:
    return json.loads((bd / "build_inputs.json").read_text(encoding="utf-8"))


def kit(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "klode_kit", *args], capture_output=True, text=True, timeout=600,
                          env=base_env(), cwd=str(REPO))


# --------------------------------------------------------------------------- the full path


def test_adopt_with_cards_content_and_desk_passes_all_13_steps(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True)
    before_old = AF.tree_hash(old)
    before_project = B.snapshot(project)
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, extra_env=env)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    rec = B.receipt(bd)
    assert issues("build_receipt", rec) == []
    assert [s["step"] for s in rec["steps"]] == list(RB.STEPS)
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * 13 and rec["status"] == "PASS"
    by = AF.by_step(bd)
    for step in ("export", "ingest", "split_manifest"):
        assert "not applicable: adopted" in by[step]["detail"], step
    assert f"3 cards adopted from {old}" in by["dispatch_writers"]["detail"]
    assert AF.claude_calls(env) == 0
    for cid in AF.CARDS:  # cards: the old cards_content, byte for byte
        assert (bd / "cards_content" / f"{cid}.md").read_bytes() == (old / "cards_content" / f"{cid}.md").read_bytes()
    assert AF.tree_hash(old) == before_old  # the old library was only read
    # the project is written below kb/<id>/builds/<build_id>/ only
    for path in B.changed(before_project, B.snapshot(project)):
        assert path in ("kb", "kb/demo-adopt") or path.startswith("kb/demo-adopt/builds"), path
    assert B.writable_bits(bd) == []


def test_adopt_carries_the_desk_the_holdout_and_the_syntheses(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True)
    assert AF.adopt(project, tmp_path, old, extra_env=AF.fake_env(tmp_path)).returncode == 0
    bd = one_build(project)
    assert (bd / "src" / "desk.json").read_bytes() == (old / "src" / "desk.json").read_bytes()
    assert (bd / "src" / "holdout.jsonl").read_bytes() == (old / "src" / "holdout.jsonl").read_bytes()
    syn_old = old / "library" / "frameworks" / "_syntheses"
    for d in B.DESK_SPEC["dimensions"]:
        name = f"{d['dimension']}.md"
        assert (bd / "library" / "frameworks" / "_syntheses" / name).read_bytes() == (syn_old / name).read_bytes()
    diag = (bd / "library" / "frameworks" / "_syntheses" / "_diagnostics.md").read_text(encoding="utf-8")
    assert "old diagnostics" not in diag  # regenerated by desk-build --apply from the spec
    desk = json.loads((bd / "desk_receipt.json").read_text(encoding="utf-8"))
    assert desk["status"] == "pass" and desk["holdout"]["pass"] is True
    cfg = tomllib.loads((bd / "library.toml").read_text(encoding="utf-8"))
    assert cfg["frameworks"]["enabled"] is True


def test_explicit_syntheses_win_over_the_old_ones(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True)
    syn = B.syntheses(tmp_path / "newsyn")
    marker = "Core question marker from the explicit directory"
    first = B.DESK_SPEC["dimensions"][0]["dimension"]
    text = (syn / f"{first}.md").read_text(encoding="utf-8").replace("**Core question:**", f"**Core question:** {marker}")
    (syn / f"{first}.md").write_text(text, encoding="utf-8")
    assert AF.adopt(project, tmp_path, old, "--syntheses", str(syn), extra_env=AF.fake_env(tmp_path)).returncode == 0
    got = (one_build(project) / "library" / "frameworks" / "_syntheses" / f"{first}.md").read_text(encoding="utf-8")
    assert marker in got


def test_adopt_cards_only_in_library_cards(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="scaffold", desk=True)
    assert list((old / "cards_content").glob("*.md")) == []  # init-kb leaves the empty directory
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, extra_env=env)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    for cid in AF.CARDS:
        # extracted into the cards_content format ...
        assert (bd / "cards_content" / f"{cid}.md").read_text(encoding="utf-8") == AF.card_text(cid)
        # ... and published again as the very card the old library had
        assert (bd / "library" / "cards" / f"{cid}.md").read_bytes() == (old / "library" / "cards" / f"{cid}.md").read_bytes()
    assert f"3 cards adopted from {old}" in AF.by_step(bd)["dispatch_writers"]["detail"]
    assert AF.claude_calls(env) == 0


def test_cards_content_wins_over_the_copy_in_library_cards(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="both", desk=True)
    assert "old copy" in (old / "library" / "cards" / "ch01.md").read_text(encoding="utf-8")
    assert AF.adopt(project, tmp_path, old, extra_env=AF.fake_env(tmp_path)).returncode == 0
    bd = one_build(project)
    assert (bd / "cards_content" / "ch01.md").read_bytes() == (old / "cards_content" / "ch01.md").read_bytes()
    assert "old copy" not in (bd / "library" / "cards" / "ch01.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- settings, shelves, sources


def test_three_shelves_and_the_sources_are_preserved_byte_for_byte(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True)
    assert AF.adopt(project, tmp_path, old, extra_env=AF.fake_env(tmp_path)).returncode == 0
    bd = one_build(project)
    cfg = tomllib.loads((bd / "library.toml").read_text(encoding="utf-8"))
    assert cfg["library"]["shelves"] == list(SHELVES)
    assert (cfg["library"]["id"], cfg["library"]["name"]) == ("demo-adopt", "Old Aurora Library")
    assert cfg["library"]["description"] == "Aurora text, three chapters, built by the old scripts"
    assert (cfg["library"]["dir"], cfg["library"]["cards"]) == ("library", "cards")
    assert cfg["copyright"]["guard"] is True
    for cid in AF.CARDS:
        shelf = AF.shelf_of(SHELVES, cid)
        new = bd / "library" / shelf / f"{cid}.txt"
        assert new.read_bytes() == (old / "library" / shelf / f"{cid}.txt").read_bytes()
        others = [s for s in SHELVES if s != shelf]
        assert all(not (bd / "library" / s / f"{cid}.txt").exists() for s in others)
    # build_inputs records mode, the old id and the sha of every adopted source
    rec = inputs(bd)
    assert rec["mode"] == "adopt" and rec["adopt"]["old_id"] == AF.OLD_ID
    assert rec["adopt"]["sources"] == {f"{AF.shelf_of(SHELVES, c)}/{c}.txt": AF.sha(old / "library" / AF.shelf_of(SHELVES, c) / f"{c}.txt")
                                       for c in AF.CARDS}
    assert set(rec["adopt"]["cards"]) == set(AF.CARDS)
    assert (bd / "library" / "BIBLIOGRAPHY.md").read_bytes() == (old / "library" / "BIBLIOGRAPHY.md").read_bytes()


def test_a_single_shelf_library_is_adopted_too(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", shelves=("book",), desk=True)
    assert AF.adopt(project, tmp_path, old, extra_env=AF.fake_env(tmp_path)).returncode == 0
    bd = one_build(project)
    assert tomllib.loads((bd / "library.toml").read_text(encoding="utf-8"))["library"]["shelves"] == ["book"]
    assert sorted(p.name for p in (bd / "library" / "book").glob("*.txt")) == [f"{c}.txt" for c in AF.CARDS]


def test_build_id_follows_the_adopted_inputs(tmp_path):
    ids = []
    for n, flip in enumerate((False, False, True)):
        proj = tmp_path / f"p{n}"
        proj.mkdir()
        old = AF.old_kb(tmp_path / f"old{n}", desk=True)
        if flip:
            (old / "cards_content" / "ch01.md").write_text(AF.card_text("ch01") + "\n", encoding="utf-8")
        assert AF.adopt(proj, tmp_path, old, extra_env=AF.fake_env(tmp_path)).returncode == 0
        ids.append(one_build(proj).name.rsplit("-", 1)[1])
    assert ids[0] == ids[1] and ids[0] != ids[2]


# --------------------------------------------------------------------------- missing cards


def test_a_card_missing_in_the_old_library_fails_dispatch_naming_it(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="scaffold", drop=("ch02",), desk=True)
    before_old = AF.tree_hash(old)
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, extra_env=env)
    assert res.returncode == 20, res.stderr.decode()
    assert json.loads(res.stdout.decode().splitlines()[-1])["reason"] == "content_gate"
    by = AF.by_step(one_build(project))
    assert by["dispatch_writers"]["status"] == "FAIL"
    assert "ch02" in by["dispatch_writers"]["detail"] and "ch01" not in by["dispatch_writers"]["detail"]
    assert by["gate1"]["status"] == "NOT_RUN" and by["klode_build"]["status"] == "PASS"
    assert AF.claude_calls(env) == 0  # no model call to fill the gap
    assert AF.tree_hash(old) == before_old


def test_cards_from_fills_the_card_the_old_library_lacks(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="content", drop=("ch02",), desk=True)
    assert not (old / "cards_content" / "ch02.md").exists()
    extra = AF.cards_dir(tmp_path / "agent-cards", ["ch02"])
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, "--cards-from", str(extra), extra_env=env)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    assert (bd / "cards_content" / "ch02.md").read_bytes() == (extra / "ch02.md").read_bytes()
    assert (bd / "cards_content" / "ch01.md").read_bytes() == (old / "cards_content" / "ch01.md").read_bytes()
    assert AF.claude_calls(env) == 0
    rec = inputs(bd)
    assert rec["cards_from"] == {"ch02": AF.sha(extra / "ch02.md")}


def test_cards_from_wins_over_the_old_card(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True)
    newer = AF.cards_dir(tmp_path / "agent-cards", ["ch01"], variant="rewritten alias")
    assert AF.adopt(project, tmp_path, old, "--cards-from", str(newer), extra_env=AF.fake_env(tmp_path)).returncode == 0
    bd = one_build(project)
    assert (bd / "cards_content" / "ch01.md").read_bytes() == (newer / "ch01.md").read_bytes()
    detail = AF.by_step(bd)["dispatch_writers"]["detail"]
    assert "2 cards adopted from" in detail and "1 cards carried from" in detail


# --------------------------------------------------------------------------- no desk / frameworks disabled


def _dims_missing(detail: str) -> list[str]:
    return detail.split("desk syntheses missing for: ", 1)[1].split(", ")


def test_no_desk_generates_one_from_the_cards_and_asks_for_syntheses(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=False)
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, extra_env=env)
    assert res.returncode == 20, res.stderr.decode()
    bd = one_build(project)
    by = AF.by_step(bd)
    assert [by[s]["status"] for s in ("dispatch_writers", "gate1", "write_card", "check_strict")] == ["PASS"] * 4
    assert by["desk_apply"]["status"] == "FAIL" and by["desk_verify"]["status"] == "NOT_RUN"
    dims = _dims_missing(by["desk_apply"]["detail"])
    spec = json.loads((bd / "src" / "desk.json").read_text(encoding="utf-8"))
    assert dims == [d["dimension"] for d in spec["dimensions"]]
    assert sorted(c for d in spec["dimensions"] for c in d["cards"]) == list(AF.CARDS)  # every card is on the desk
    assert (bd / "src" / "holdout.jsonl").is_file()
    assert tomllib.loads((bd / "library.toml").read_text(encoding="utf-8"))["frameworks"]["enabled"] is True
    assert AF.claude_calls(env) == 0


def test_frameworks_disabled_ignores_the_old_desk_like_a_library_without_one(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True, frameworks=False)
    assert (old / "src" / "desk.json").is_file()
    res = AF.adopt(project, tmp_path, old, extra_env=AF.fake_env(tmp_path))
    assert res.returncode == 20, res.stderr.decode()
    bd = one_build(project)
    detail = AF.by_step(bd)["desk_apply"]["detail"]
    assert detail.startswith("desk syntheses missing for: ")
    assert (bd / "src" / "desk.json").read_bytes() != (old / "src" / "desk.json").read_bytes()
    assert all(not d.startswith(("construction", "whistle", "safety")) for d in _dims_missing(detail))


def test_no_desk_workers_then_second_build_with_syntheses_and_repair(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="scaffold", desk=False)
    before_old = AF.tree_hash(old)
    env = AF.fake_env(tmp_path)
    first = AF.adopt(project, tmp_path, old, extra_env=env)
    assert first.returncode == 20
    failed = one_build(project)

    syn = tmp_path / "work" / "syntheses"
    planned = kit("desk-tasks", "--build", str(failed), "--out", str(syn), "--project", str(project))
    assert planned.returncode == 0, planned.stderr
    doc = json.loads(planned.stdout)
    spec = json.loads((failed / "src" / "desk.json").read_text(encoding="utf-8"))
    assert [t["dimension"] for t in doc["tasks"]] == [d["dimension"] for d in spec["dimensions"]]
    for t in doc["tasks"]:  # the workers' files: anchors copied from the build's own cards
        anchors: list[str] = []
        for cid in t["cards"]:
            anchors += list(dict.fromkeys(ANCHOR_RE.findall((failed / "library" / "cards" / f"{cid}.md").read_text(encoding="utf-8"))))
        Path(t["output"]).write_text(DX.synthesis(t["dimension"], t["cards"], anchors[:4]), encoding="utf-8")
    assert kit("check-synthesis", str(failed), "--syntheses", str(syn)).returncode == 0

    second = AF.adopt(project, tmp_path, old, "--syntheses", str(syn), "--repair", str(syn / "repair.json"),
                      extra_env=env)
    assert second.returncode == 0, second.stderr.decode()
    (good,) = [b for b in B.builds(project) if b != failed]
    rec = B.receipt(good)
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * 13 and rec["status"] == "PASS"
    for cid in AF.CARDS:  # carried over from the first build, byte for byte
        assert (good / "cards_content" / f"{cid}.md").read_bytes() == (failed / "cards_content" / f"{cid}.md").read_bytes()
    assert "carried over" in AF.by_step(good)["dispatch_writers"]["detail"]
    for t in doc["tasks"]:
        assert (good / "library" / "frameworks" / "_syntheses" / f"{t['dimension']}.md").read_bytes() == Path(t["output"]).read_bytes()
    assert AF.claude_calls(env) == 0
    assert AF.tree_hash(old) == before_old


# --------------------------------------------------------------------------- --cards-from on the book path


def _book(tmp_path: Path, project: Path, *extra: str, env: dict):
    return B.build(project, tmp_path, "--kb", "kb/demo-klode", *B.book_args(tmp_path), *extra, extra_env=env)


def test_cards_from_on_the_book_path_calls_no_writer(tmp_path, project):
    cards = AF.cards_dir(tmp_path / "agent-cards")
    env = {**B.writer_replay(tmp_path)}
    res = _book(tmp_path, project, "--cards-from", str(cards), env=env)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    assert AF.claude_calls(env) == 0
    for cid in AF.CARDS:
        assert (bd / "cards_content" / f"{cid}.md").read_bytes() == (cards / f"{cid}.md").read_bytes()
    detail = AF.by_step(bd)["dispatch_writers"]["detail"]
    assert "3 cards carried from" in detail and str(cards) in detail
    assert inputs(bd)["cards_from"] == {c: AF.sha(cards / f"{c}.md") for c in AF.CARDS}
    assert [s["status"] for s in B.receipt(bd)["steps"]] == ["PASS"] * 13


def test_cards_from_with_some_cards_still_dispatches_the_rest(tmp_path, project):
    cards = AF.cards_dir(tmp_path / "agent-cards", ["ch01", "ch02"])
    env = {**B.writer_replay(tmp_path)}
    res = _book(tmp_path, project, "--cards-from", str(cards), env=env)
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    assert AF.claude_calls(env) == 1  # ch03 only
    assert (bd / "cards_content" / "ch01.md").read_bytes() == (cards / "ch01.md").read_bytes()
    assert (bd / "cards_content" / "ch03.md").read_text(encoding="utf-8") == AF.card_text("ch03")  # the writer's card


def test_cards_from_ignores_files_that_are_not_cards_of_the_library(tmp_path, project):
    cards = AF.cards_dir(tmp_path / "agent-cards")
    (cards / "notes.md").write_text("not a card\n", encoding="utf-8")
    (cards / "ch99.md").write_text("aliases: []\n", encoding="utf-8")
    env = {**B.writer_replay(tmp_path)}
    res = _book(tmp_path, project, "--cards-from", str(cards), env=env)
    assert res.returncode == 0, res.stderr.decode()
    assert sorted(p.stem for p in (one_build(project) / "cards_content").glob("*.md")) == list(AF.CARDS)
    assert AF.claude_calls(env) == 0


# --------------------------------------------------------------------------- --adopt-shelves (user decision D-45)


def test_adopt_shelves_comma_list_carries_only_those_shelves(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="scaffold", desk=True)
    before_old = AF.tree_hash(old)
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, "--adopt-shelves", "alpha,gamma", extra_env=env)
    assert res.returncode == 20, res.stderr.decode()  # the old desk names ch02 (beta): a new one has to be written
    bd = one_build(project)
    by = AF.by_step(bd)
    assert by["dispatch_writers"]["status"] == "PASS" and "2 cards adopted from" in by["dispatch_writers"]["detail"]
    assert by["check_strict"]["status"] == "PASS" and by["desk_apply"]["status"] == "FAIL"
    cfg = tomllib.loads((bd / "library.toml").read_text(encoding="utf-8"))
    assert cfg["library"]["shelves"] == ["alpha", "gamma"]
    assert not (bd / "library" / "beta").exists() and not (bd / "library" / "beta" / "ch02.txt").exists()
    assert sorted(p.stem for p in (bd / "cards_content").glob("*.md")) == ["ch01", "ch03"]
    assert sorted(p.stem for p in (bd / "library" / "cards").glob("*.md") if p.stem != "INDEX") == ["ch01", "ch03"]
    assert (bd / "library" / "alpha" / "ch01.txt").read_bytes() == (old / "library" / "alpha" / "ch01.txt").read_bytes()
    assert (bd / "src" / "desk.json").read_bytes() != (old / "src" / "desk.json").read_bytes()  # not carried
    spec = json.loads((bd / "src" / "desk.json").read_text(encoding="utf-8"))
    assert sorted(c for d in spec["dimensions"] for c in d["cards"]) == ["ch01", "ch03"]
    rec = inputs(bd)
    assert rec["adopt"]["selected"] == ["alpha", "gamma"] and sorted(rec["adopt"]["sources"]) == ["alpha/ch01.txt", "gamma/ch03.txt"]
    bib = (bd / "library" / "BIBLIOGRAPHY.md").read_text(encoding="utf-8")
    assert "ch01.txt" in bib and "ch03.txt" in bib and "ch02.txt" not in bib  # no row for a source that is not here
    assert AF.claude_calls(env) == 0
    assert AF.tree_hash(old) == before_old


def test_adopt_shelves_repeatable_and_a_card_missing_on_another_shelf_is_not_reported(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="scaffold", drop=("ch02",), desk=True)  # ch02 is on beta
    res = AF.adopt(project, tmp_path, old, "--adopt-shelves", "alpha", "--adopt-shelves", "gamma",
                   extra_env=AF.fake_env(tmp_path))
    assert res.returncode == 20, res.stderr.decode()
    by = AF.by_step(one_build(project))
    assert by["dispatch_writers"]["status"] == "PASS" and by["gate1"]["status"] == "PASS"
    assert by["desk_apply"]["status"] == "FAIL" and by["desk_apply"]["detail"].startswith("desk syntheses missing for")


def test_adopt_shelves_naming_a_missing_card_of_a_chosen_shelf_still_fails(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", cards="scaffold", drop=("ch02",), desk=True)
    res = AF.adopt(project, tmp_path, old, "--adopt-shelves", "beta", extra_env=AF.fake_env(tmp_path))
    assert res.returncode == 20
    by = AF.by_step(one_build(project))
    assert by["dispatch_writers"]["status"] == "FAIL" and "ch02" in by["dispatch_writers"]["detail"]


def test_adopt_shelves_naming_every_shelf_is_the_whole_library_with_its_desk(tmp_path, project):
    old = AF.old_kb(tmp_path / "old", desk=True)
    res = AF.adopt(project, tmp_path, old, "--adopt-shelves", "gamma,beta,alpha", extra_env=AF.fake_env(tmp_path))
    assert res.returncode == 0, res.stderr.decode()
    bd = one_build(project)
    assert (bd / "src" / "desk.json").read_bytes() == (old / "src" / "desk.json").read_bytes()
    assert tomllib.loads((bd / "library.toml").read_text(encoding="utf-8"))["library"]["shelves"] == list(SHELVES)
    assert "selected" not in inputs(bd)["adopt"]
