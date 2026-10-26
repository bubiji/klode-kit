"""WI-13b: ``/med-intake`` pure parts, unit lane (matrix 13b-03, 13b-04 and the law of 13b-02).

Law (S5): ``allocate_alias`` never changes where an existing alias points, never hands out a name that is taken by
something else, and gives the same batch the same name every time.
"""

from __future__ import annotations

import itertools
import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit import med_intake as M
from klode_kit.__main__ import COMMANDS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 13b-03 header keyword -> type
@pytest.mark.parametrize("title,expected", [
    ("示例医院 检验报告单", "lab"),
    ("示例医院 化验单", "lab"),
    ("示例医院 门诊处方笺", "rx"),
    ("检 验 报 告", "lab"),        # spaces inside the keyword (OCR spacing)
    ("示例医院 报告单", None),      # no keyword
    ("", None),
])
def test_type_by_header_keyword(title: str, expected: str | None) -> None:
    assert M.header_type({"vision": [title, "姓名：测试"]}) == expected


def test_header_type_is_the_vote_of_the_routes_and_ignores_their_order() -> None:
    lines = {"vision": ["检验报告单"], "mlx": ["检验报告单"], "claude": ["处方"], "codex": ["检验报告"]}
    assert M.header_type(lines) == "lab"
    for perm in itertools.permutations(lines.items()):
        assert M.header_type(dict(perm)) == "lab"


def test_resolve_type_from_headers() -> None:
    assert M.resolve_type(["lab", "lab"], None) == ("lab", "header")
    assert M.resolve_type(["rx"], None) == ("rx", "header")


@pytest.mark.parametrize("pages", [[None], ["lab", None], [], ["lab", "rx"]])
def test_resolve_type_without_a_decision_requires_type(pages) -> None:
    with pytest.raises(M.TypeRequired) as exc:
        M.resolve_type(pages, None)
    assert "--type" in str(exc.value)


# ---------------------------------------------------------------- 13b-04
@pytest.mark.parametrize("pages", [[None], ["lab"], ["lab", "rx"], []])
def test_explicit_type_overrides(pages) -> None:
    assert M.resolve_type(pages, "rx") == ("rx", "explicit")


def test_explicit_type_must_be_known() -> None:
    with pytest.raises(ValueError):
        M.resolve_type(["lab"], "scan")


@given(pages=st.lists(st.sampled_from(["lab", "rx", None]), max_size=6), explicit=st.sampled_from(M.TYPES))
def test_law_explicit_always_wins_and_header_needs_agreement(pages, explicit) -> None:
    assert M.resolve_type(pages, explicit) == (explicit, "explicit")
    try:
        got = M.resolve_type(pages, None)
    except M.TypeRequired:
        assert not pages or None in pages or len(set(pages)) > 1
    else:
        assert got == (pages[0], "header") and set(pages) == {pages[0]}


# ---------------------------------------------------------------- the date of the inbox directory
@pytest.mark.parametrize("path,expected", [
    ("/x/_inbox/2026-10-22", "20261022"),
    ("/x/_inbox/2026-10-22/", "20261022"),
    ("/x/_inbox/20261022", "20261022"),
    ("/x/_inbox/2026-10-22/sub", "20261022"),   # the nearest dated directory above
    ("/x/2026-10-22/_inbox/2026-10-23", "20261023"),
    ("/x/_inbox/2026-13-40", None),             # not a calendar day
    ("/x/_inbox/2026-10-22-extra", None),
    ("/x/_inbox/new", None),
])
def test_alias_date(path: str, expected: str | None) -> None:
    assert M.alias_date(Path(path)) == expected


# ---------------------------------------------------------------- aliases
def _target(path: Path) -> str:
    return str(path.readlink())


def test_allocate_alias_first_free_name(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    assert M.allocate_alias(tmp_path, "20261022", "aaaaaaaaaaaa") == "20261022"
    assert M.allocate_alias(tmp_path, "20261022", "bbbbbbbbbbbb") == "20261022-2"
    assert M.allocate_alias(tmp_path, "20261022", "cccccccccccc") == "20261022-3"
    assert M.allocate_alias(tmp_path, "20261022", "bbbbbbbbbbbb") == "20261022-2"
    assert _target(tmp_path / "work" / "20261022") == "aaaaaaaaaaaa"
    assert _target(tmp_path / "work" / "20261022-2") == "bbbbbbbbbbbb"


def test_allocate_alias_skips_stray_confirm_link_and_real_directories(tmp_path: Path) -> None:
    (tmp_path / "work" / "20261022").mkdir(parents=True)
    (tmp_path / "confirm").mkdir()
    (tmp_path / "confirm" / "20261022-2.html").symlink_to("elsewhere.html")
    assert M.allocate_alias(tmp_path, "20261022", "aaaaaaaaaaaa") == "20261022-3"
    assert (tmp_path / "work" / "20261022").is_dir() and not (tmp_path / "work" / "20261022").is_symlink()


def test_allocate_alias_creates_work_dir_and_relative_link(tmp_path: Path) -> None:
    assert M.allocate_alias(tmp_path, "20261022", "aaaaaaaaaaaa") == "20261022"
    link = tmp_path / "work" / "20261022"
    assert link.is_symlink() and _target(link) == "aaaaaaaaaaaa" and not os.path.isabs(_target(link))


def test_link_confirm_creates_and_repoints_only_when_the_revision_moves(tmp_path: Path) -> None:
    page = M.link_confirm(tmp_path, "20261022", "aaaaaaaaaaaa", 1)
    assert page == tmp_path / "confirm" / "20261022.html"
    assert _target(page) == "../work/aaaaaaaaaaaa/audit/a1/confirm.html"
    ino = os.lstat(page).st_ino
    assert M.link_confirm(tmp_path, "20261022", "aaaaaaaaaaaa", 1) == page
    assert os.lstat(page).st_ino == ino  # untouched
    M.link_confirm(tmp_path, "20261022", "aaaaaaaaaaaa", 2)
    assert _target(page) == "../work/aaaaaaaaaaaa/audit/a2/confirm.html"


def test_link_confirm_refuses_a_name_that_is_not_ours(tmp_path: Path) -> None:
    (tmp_path / "confirm").mkdir()
    (tmp_path / "confirm" / "20261022.html").write_text("a real file", encoding="utf-8")
    with pytest.raises(M.AliasConflict):
        M.link_confirm(tmp_path, "20261022", "aaaaaaaaaaaa", 1)
    assert (tmp_path / "confirm" / "20261022.html").read_text(encoding="utf-8") == "a real file"


_BATCHES = st.text(alphabet="0123456789abcdef", min_size=12, max_size=12)
_DATES = st.sampled_from(["20261022", "20261023", "20270101"])


@settings(max_examples=60, deadline=None)
@given(plan=st.lists(st.tuples(_DATES, _BATCHES), min_size=1, max_size=14))
def test_law_aliases_never_move_and_never_collide(plan) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        seen: dict[tuple[str, str], str] = {}
        links: dict[str, str] = {}
        for date, batch in plan:
            name = M.allocate_alias(project, date, batch)
            assert name == date or name.startswith(f"{date}-")
            assert _target(project / "work" / name) == batch          # the alias is this batch's
            assert seen.setdefault((date, batch), name) == name       # same batch, same name, every time
            for other, target in links.items():                       # nothing allocated earlier moved
                assert _target(project / "work" / other) == target
            links[name] = batch
        for date in {d for d, _ in plan}:  # one name per distinct batch of the day, no name shared by two batches
            names = {n for (d, _), n in seen.items() if d == date}
            assert len(names) == len({b for d, b in plan if d == date})


# ---------------------------------------------------------------- the two lines
def _receipt(pages: int = 3, routes=("vision", "mlx", "claude", "codex"), failed=()) -> dict:
    page_rows = []
    for i in range(pages):
        key = f"p{i + 1:03d}"
        page_rows.append({"key": key, "routes": {r: {"status": "route_failed" if (r, key) in failed else "done"}
                                                   for r in routes}})
    return {"pages": page_rows, "attempts": {r: {"status": "done"} for r in routes}, "issues": []}


def test_ocr_line() -> None:
    assert M.ocr_line(_receipt()) == "ocr: 3 页 × 4 路 OK"
    assert M.ocr_line(_receipt(1, ("vision", "mlx"))) == "ocr: 1 页 × 2 路 OK"
    assert M.ocr_line(_receipt(failed=[("claude", "p002")])) == "ocr: 3 页 × 4 路，1 处失败（claude:p002）"
    assert M.ocr_failures(_receipt(failed=[("codex", "p003"), ("claude", "p001")])) == ["claude:p001", "codex:p003"]


def test_audit_line() -> None:
    assert M.audit_line(5, "20261022") == "audit: 5 条分歧 → confirm/20261022.html"
    assert M.audit_line(1, "20261022-2") == "audit: 1 条分歧 → confirm/20261022-2.html"
    assert M.audit_line(0, None) == "audit: 0 条分歧，无需确认"


def test_command_is_registered() -> None:
    assert COMMANDS["med-intake"][0] == "klode_kit.med_intake:main"
