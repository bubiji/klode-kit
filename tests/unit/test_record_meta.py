"""WI-08d: where a record's ``type`` and ``date`` come from (WI-08c remainder 1; plan WI-08d; design 02 r2.10 /
r4.1 views ``records/<date>-<type>.md``).

The batch receipt of kb-ocr carries no ``record_type`` / ``date``; ``klode_kit.audit.record_meta`` derives them
deterministically from what the routes read on the page:

- ``record_type(lines)``: per route, the first line (reading order) holding a title keyword decides that route's
  type (``处方`` -> ``rx``; ``检验`` / ``化验`` -> ``lab``); across routes the most common type wins, a tie goes to
  the route first in ``gate.ENGINES`` order; no keyword anywhere -> ``None``.
- ``page_date(lines)``: per route, the earliest date (WI-07a ``date`` field, ISO) on a line labelled ``日期``,
  else the earliest date anywhere on the page; across routes the most common date wins, a tie goes to the
  earliest date; no date -> ``None``.
- ``dir_date(source)``: the nearest directory of the inbox path named like a date (``YYYY-MM-DD`` or
  ``YYYYMMDD``, a real calendar day).
- ``derive(batch_dir, page, routes)``: the receipt page's route files (done routes of ``routes``) ->
  ``{record_type, date, date_source}`` where ``date_source`` is ``page`` / ``inbox_dir`` / ``batch`` (the date
  part of ``receipts/ocr.json`` ``started``) / ``None``.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.audit import record_meta as M

pytestmark = pytest.mark.unit

LAB = ["示例医院 检验报告单", "姓名：测试患者甲", "日期：2026-10-21", "项目 结果 单位 提示 参考范围", "血红蛋白 128 g/L 115-150"]
RX = ["示例医院 处方笺", "日期：2026-09-18", "药品 规格 用法", "示例片 10mg 每日一次"]


# --------------------------------------------------------------------------- record_type


def test_type_lab_and_rx_from_title():
    assert M.record_type({"vision": LAB, "mlx": LAB}) == "lab"
    assert M.record_type({"vision": RX}) == "rx"
    assert M.record_type({"vision": ["化验单", "x 1"]}) == "lab"


def test_type_none_without_keyword():
    assert M.record_type({"vision": ["示例医院", "血红蛋白 128 g/L"]}) is None
    assert M.record_type({}) is None
    assert M.record_type({"vision": []}) is None


def test_type_first_keyword_line_of_a_route_decides():
    # the title says rx; a later line mentions 检验 - the route still reads rx
    assert M.record_type({"vision": ["示例医院 处方笺", "附：检验 见另单"]}) == "rx"


def test_type_majority_then_engine_order():
    assert M.record_type({"vision": RX, "mlx": LAB, "claude": LAB}) == "lab"
    # 1:1 -> the route first in gate.ENGINES order (vision before mlx before claude before codex)
    assert M.record_type({"codex": LAB, "mlx": RX}) == "rx"
    assert M.record_type({"claude": LAB, "vision": RX}) == "rx"


def test_type_masked_header_on_cloud_routes():
    """Cloud routes read the masked image (header band black): only the local routes see the title."""
    assert M.record_type({"vision": LAB, "mlx": LAB, "claude": LAB[1:], "codex": LAB[1:]}) == "lab"


# --------------------------------------------------------------------------- page_date


def test_date_labelled_line():
    assert M.page_date({"vision": LAB}) == "2026-10-21"


def test_date_fullwidth_normalised():
    assert M.page_date({"vision": ["日期：２０２６-１０-２１"]}) == "2026-10-21"


def test_date_earliest_labelled_beats_unlabelled():
    lines = ["打印 2026-01-02", "采样日期：2026-10-21", "报告日期：2026-10-23"]
    assert M.page_date({"vision": lines}) == "2026-10-21"


def test_date_unlabelled_fallback_earliest():
    assert M.page_date({"vision": ["2026-10-23 打印", "复查 2026-10-21"]}) == "2026-10-21"


def test_date_none():
    assert M.page_date({"vision": ["示例医院 检验报告单", "血红蛋白 128 g/L"]}) is None
    assert M.page_date({}) is None


def test_date_majority_then_earliest():
    a, b = ["日期：2026-10-21"], ["日期：2026-10-27"]
    assert M.page_date({"vision": b, "mlx": a, "claude": a}) == "2026-10-21"
    assert M.page_date({"vision": b, "mlx": b, "claude": a}) == "2026-10-27"
    assert M.page_date({"vision": b, "mlx": a}) == "2026-10-21"  # 1:1 -> earliest


ROUTES = ("vision", "mlx", "claude", "codex")
DATES = st.dates(min_value=__import__("datetime").date(2000, 1, 1), max_value=__import__("datetime").date(2099, 12, 31))
PAGE_LINES = st.lists(
    st.one_of(
        DATES.map(lambda d: f"日期：{d.isoformat()}"),
        DATES.map(lambda d: f"备注 {d.isoformat()}"),
        st.sampled_from(["示例医院 检验报告单", "示例医院 处方笺", "血红蛋白 128 g/L", "姓名：测试患者甲"]),
    ),
    max_size=6,
)


@settings(max_examples=150, deadline=None)
@given(st.dictionaries(st.sampled_from(ROUTES), PAGE_LINES, max_size=4), st.randoms(use_true_random=False))
def test_meta_independent_of_route_order(lines, rnd):
    """S4: type and date are functions of the set of (route, lines): the order the routes are handed over never
    changes them, and a returned date is one some route read on the page."""
    items = list(lines.items())
    rnd.shuffle(items)
    shuffled = dict(items)
    assert M.record_type(shuffled) == M.record_type(lines)
    assert M.page_date(shuffled) == M.page_date(lines)
    got = M.page_date(lines)
    if got is not None:
        assert any(got in x for v in lines.values() for x in v)


# --------------------------------------------------------------------------- dir_date


@pytest.mark.parametrize(("source", "want"), [
    ("/data/_inbox/2026-09-18/scan1.png", "2026-09-18"),
    ("/data/_inbox/20260918/scan1.png", "2026-09-18"),
    ("/data/2025-01-01/_inbox/2026-09-18/a/scan1.png", "2026-09-18"),  # nearest dated directory
    ("/data/_inbox/2026-13-45/scan1.png", None),  # not a calendar day
    ("/data/_inbox/scan1.png", None),
    ("/data/_inbox/2026-09-18.png", None),  # the file name is not a directory
    (None, None),
])
def test_dir_date(source, want):
    assert M.dir_date(source) == want


# --------------------------------------------------------------------------- derive


def _batch(tmp_path, routes: dict[str, list[str] | None], *, source=None, started=None):
    bdir = tmp_path / "work" / "0123456789ab"
    page = {"key": "p001", "page_id": "d-0123456789ab/p001", "routes": {}}
    if source is not None:
        page["source"] = source
    for r, lines in routes.items():
        if lines is None:
            page["routes"][r] = {"status": "route_missing_page"}
            continue
        rel = f"ocr/{r}/0123456789ab.t1/p001.jsonl"
        (bdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (bdir / rel).write_text("".join(json.dumps({"line_idx": i, "text": t}, ensure_ascii=False) + "\n"
                                        for i, t in enumerate(lines)), encoding="utf-8")
        page["routes"][r] = {"status": "done", "file": rel}
    if started is not None:
        (bdir / "receipts").mkdir(parents=True, exist_ok=True)
        (bdir / "receipts" / "ocr.json").write_text(json.dumps({"started": started}), encoding="utf-8")
    bdir.mkdir(parents=True, exist_ok=True)
    return bdir, page


def test_derive_from_page_lines(tmp_path):
    bdir, page = _batch(tmp_path, {"vision": LAB, "mlx": LAB, "codex": None},
                        source="/x/_inbox/2026-01-05/a.png", started="2026-10-24T01:02:03Z")
    assert M.derive(bdir, page, ROUTES) == {"record_type": "lab", "date": "2026-10-21", "date_source": "page"}


def test_derive_only_named_routes(tmp_path):
    """A route not in ``routes`` (tesseract: geometry only) is not read."""
    bdir, page = _batch(tmp_path, {"vision": RX[2:], "tesseract": LAB})
    assert M.derive(bdir, page, ROUTES) == {"record_type": None, "date": None, "date_source": None}


def test_derive_inbox_dir_then_batch_date(tmp_path):
    no_date = ["示例医院 检验报告单", "血红蛋白 128 g/L"]
    bdir, page = _batch(tmp_path / "a", {"vision": no_date}, source="/x/_inbox/2026-01-05/a.png",
                        started="2026-10-24T01:02:03Z")
    assert M.derive(bdir, page, ROUTES) == {"record_type": "lab", "date": "2026-01-05", "date_source": "inbox_dir"}
    bdir, page = _batch(tmp_path / "b", {"vision": no_date}, source="/x/_inbox/a.png", started="2026-10-24T01:02:03Z")
    assert M.derive(bdir, page, ROUTES) == {"record_type": "lab", "date": "2026-10-24", "date_source": "batch"}
    bdir, page = _batch(tmp_path / "c", {"vision": no_date})
    assert M.derive(bdir, page, ROUTES) == {"record_type": "lab", "date": None, "date_source": None}


def test_derive_bad_batch_receipt_is_no_date(tmp_path):
    bdir, page = _batch(tmp_path, {"vision": ["x"]}, started="not a time")
    assert M.derive(bdir, page, ROUTES)["date"] is None


# --------------------------------------------------------------------------- supplements (added after the red)


def test_derive_missing_route_file_and_blank_lines(tmp_path):
    bdir, page = _batch(tmp_path, {"vision": LAB})
    page["routes"]["mlx"] = {"status": "done", "file": "ocr/mlx/gone.t1/p001.jsonl"}
    f = bdir / page["routes"]["vision"]["file"]
    f.write_text("\n" + f.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert M.derive(bdir, page, ROUTES) == {"record_type": "lab", "date": "2026-10-21", "date_source": "page"}


def test_derive_reads_text_layer_file(tmp_path):
    bdir, page = _batch(tmp_path, {})
    (bdir / "text").mkdir()
    (bdir / "text" / "p001.jsonl").write_text(
        "".join(json.dumps({"line_idx": i, "text": t}, ensure_ascii=False) + "\n" for i, t in enumerate(RX)),
        encoding="utf-8")
    page["text_file"] = "text/p001.jsonl"
    assert M.derive(bdir, page, ROUTES) == {"record_type": "rx", "date": "2026-09-18", "date_source": "page"}
