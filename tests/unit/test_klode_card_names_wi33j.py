"""WI-33j (real-data pilot P16): every card / file stem is a name klode can resolve in a synthesis ``cards:`` list.

klode 0.5.0 ``check.py`` reads those names with ``[A-Za-z0-9][A-Za-z0-9-]{2,}``; a 1-2 character stem (``r``) or one
with ``.`` / ``_`` is never resolved -> a citation-rot warning. Spec change WI-33j: ``items.file_stem`` keeps a slug
only when it fully matches that rule, otherwise ``u-<10 hex>``. Second cause: ``check_strict`` ran before the desk
syntheses existed; ``desk_verify`` now runs ``klode check --strict`` again (warnings fail). Synthetic data only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from _support import export_fx as X
from klode_kit import items as items_mod
from klode_kit.build import gen_desk, records_to_l1 as L1, run_build as RB
from klode_kit.build import records_to_txt as R
from klode_kit.pipeline import Reason

pytestmark = pytest.mark.unit

KLODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,}")
HASHED = re.compile(r"u-[0-9a-f]{10}")
A1 = "aaaaaaaaaaaa/a1"


@pytest.mark.parametrize("slug", ["r", "b", "d", "ab", "x.y", "a_b", "abc.def", "ab_", "-ab"[1:], "肌酸激酶"])
def test_unresolvable_names_become_hashed_stems(slug):
    assert HASHED.fullmatch(items_mod.file_stem(slug))


@pytest.mark.parametrize("slug", ["abc", "alt", "hemoglobin", "a-b", "r-aaaaaaaaaaaa", "u-0123456789", "b12"])
def test_resolvable_names_stay(slug):
    assert items_mod.file_stem(slug) == slug


@settings(max_examples=120, deadline=None)
@given(st.text(min_size=1, max_size=12).filter(lambda t: t.strip() and any(c.isalnum() for c in t)))
def test_every_stem_is_a_klode_card_name_and_stems_are_idempotent(label):
    try:
        slug = items_mod.slugify(label)
    except ValueError:
        return
    stem = items_mod.file_stem(slug)
    assert KLODE_NAME.fullmatch(stem)
    assert items_mod.file_stem(stem) == stem
    if KLODE_NAME.fullmatch(slug):
        assert stem == slug


def test_every_card_stem_the_build_writes_is_a_klode_card_name(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    labels = ("R", "b", "d", "x.y", "ALT", "肌酸激酶")
    for rec, date in (("r-" + "a" * 12, "2026-03-01"), ("r-" + "b" * 12, "2026-04-01")):
        fields = {f"{rec}/date": date}
        for n, label in enumerate(labels):
            fields[f"{rec}/labs[{n}].item"] = label
            fields[f"{rec}/labs[{n}].value"] = str(10 + n)
            fields[f"{rec}/labs[{n}].unit"] = "U/L"
        X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                     date=date)
    build = tmp_path / "b"
    R.export(project, build, kb_id="k")
    out = L1.generate(build)
    L1.write(build)
    stems = {p.stem for sub in ("items", "records") for p in (build / "library" / sub).glob("*.txt")}
    assert len(stems) >= 8
    plan, _ = gen_desk._records_plan(build, None)
    cards = {c for d in plan for c in d["cards"]}
    for name in stems | set(out["cards"]) | cards:
        assert KLODE_NAME.fullmatch(name), name
    assert cards == stems


def _fake_build(monkeypatch, strict: tuple[int, str, str], verify_rc: int = 0) -> RB.Build:
    b = RB.Build.__new__(RB.Build)
    b.dir = Path("/nonexistent-build")
    b.project = Path("/nonexistent-project")
    seen: list[tuple] = []
    b.kit = lambda *a: (seen.append(("kit", a)), (verify_rc, "", ""))[1]
    b.klode_run = lambda *a: (seen.append(("klode", a)), strict)[1]
    b.seen = seen
    return b


def test_desk_verify_also_runs_strict_and_passes_on_clean(monkeypatch):
    b = _fake_build(monkeypatch, (0, "checked\nOK: 0 errors, 0 warning(s)\n", ""))
    res = b.step_desk_verify()
    assert res.reason is Reason.ok
    assert ("klode", ("check", "--strict")) in b.seen
    assert "0 errors, 0 warning(s)" in res.detail


def test_desk_verify_fails_on_strict_warnings_and_keeps_the_report(monkeypatch):
    text = "[F citation-rot?] frameworks/_syntheses/other-items.md: grep `r 25` not found\n7 errors? no: 0 errors, 7 warning(s)\n"
    b = _fake_build(monkeypatch, (0, text, ""))
    res = b.step_desk_verify()
    assert res.reason is Reason.content_gate
    assert "7 warning(s)" in res.detail and "strict" in res.detail
    assert "citation-rot" in (res.report or "")


def test_desk_verify_failure_of_desk_build_is_reported_before_strict(monkeypatch):
    b = _fake_build(monkeypatch, (0, "OK: 0 errors, 0 warning(s)\n", ""), verify_rc=20)
    res = b.step_desk_verify()
    assert res.reason is not Reason.ok
    assert not any(k == "klode" for k, _ in b.seen)
