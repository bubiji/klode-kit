"""WI-00c3: field metrics of design 02 r2.7 (matrix 00c3-01 .. 00c3-05).

Contract of ``klode_kit.eval.eval_fields`` (the shapes the tests below rely on):

- ``truth_rows(truth_json)`` -> ``{row_id: {"key", "row", "kind", "item", "value", "unit", "flag"}}`` with
  ``row_id = "<key>/r<row>"``, from ``truth.json`` ``records[].rows`` (absent columns are ``""``).
- A prediction is ``{row_id: {"value", "unit", "flag"}}`` (``None`` = not read); a row id that is absent
  means the row was not found at all.
- Fields are the ``(row_id, col)`` pairs, ``col`` in ``value / unit / flag``, whose truth is non-empty.
  Values compare after ``cer.normalize`` (NFKC + no whitespace; digits are never folded).
- ``field_metrics(truth, pred)`` -> ``{"fields_total", "exact_match_rate", "association_accuracy",
  "missing_rate"}``: exact = read and equal / fields; association = rows whose value, unit and flag all
  equal the truth (an unread flag equals an empty one) / rows; missing = unread fields / fields.
- ``evaluate(truth, routes, *, min_all_agree=20)``: a field is auto-verified when every route read it and all
  readings are equal (the four-way agreement of the run); ``false_auto_pass_rate`` = auto-verified and != truth
  / auto-verified total, ``"n/a"`` when that total is 0. ``merged`` = per field the reading of a strict
  majority of routes (else unread). Verdict ``FAIL`` with reasons from ``no_auto_verified_samples``,
  ``false_auto_pass``, ``all_agree_below_min``, ``exact_match_rate``, ``association_accuracy``,
  ``missing_rate`` (thresholds r2.7 on ``merged``: >= 0.98, >= 0.98, <= 0.02, false auto pass == 0).
- ``parse_rows(lines, truth)``: the rows of one page read from OCR lines, by item name (n-th occurrence for
  repeated items); lab rows give value, unit, flag; rx rows give the value only.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.eval import eval_fields as E

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTES = ("vision", "mlx", "claude", "codex")


def _truth(name: str) -> dict:
    return E.truth_rows(json.loads((FIXTURES / name / "truth.json").read_text(encoding="utf-8")))


def _pred_from_truth(truth: dict) -> dict:
    return {rid: {c: (r[c] or None) for c in ("value", "unit", "flag")} for rid, r in truth.items()}


def _lab_truth(n_rows: int) -> dict:
    """n_rows lab rows with value + unit and no flag -> 2 * n_rows fields."""
    return {f"p001/r{i}": {"key": "p001", "row": i, "kind": "lab", "item": f"项目{i}", "value": str(100 + i),
                           "unit": "g/L", "flag": ""} for i in range(n_rows)}


def test_truth_rows_from_fixture():
    t = _truth("fx-u1-3labs")
    assert len(t) == 18
    assert t["p001/r0"] == {"key": "p001", "row": 0, "kind": "lab", "item": "血红蛋白", "value": "128",
                            "unit": "g/L", "flag": ""}
    assert t["p001/r4"]["flag"] == "H"
    rx = _truth("fx-rx-stamp")
    assert [rx[f"p001/r{i}"]["value"] for i in range(3)] == ["10mg", "5mg", "0.25g"]
    assert {r["kind"] for r in rx.values()} == {"rx"} and all(r["unit"] == "" for r in rx.values())


def test_false_auto_pass_zero_samples_fails():
    """00c3-01 (L1/S3): no auto-verified field -> rate n/a, verdict FAIL, reason no_auto_verified_samples."""
    truth = _lab_truth(12)
    routes = {r: _pred_from_truth(truth) for r in ROUTES}
    routes["codex"] = {}  # one route read nothing: no field has four readings
    rep = E.evaluate(truth, routes)
    assert rep["auto_verified_total"] == 0
    assert rep["false_auto_pass_rate"] == "n/a"
    assert rep["verdict"] == "FAIL"
    assert "no_auto_verified_samples" in rep["reasons"]


def test_metrics_perfect_on_truth():
    """00c3-02 (L1/S1): truth against itself on every route."""
    truth = _truth("fx-u1-3labs")
    rep = E.evaluate(truth, {r: _pred_from_truth(truth) for r in ROUTES})
    for m in [rep["merged"], *rep["routes"].values()]:
        assert m["exact_match_rate"] == 1.0
        assert m["association_accuracy"] == 1.0
        assert m["missing_rate"] == 0.0
        assert m["fields_total"] == 41  # 18 values + 18 units + 5 flags
    assert rep["false_auto_pass_rate"] == 0.0
    assert rep["auto_verified_total"] == 41 and rep["all_agree_fields"] == 41
    assert rep["verdict"] == "PASS" and rep["reasons"] == []


_COLS = st.sampled_from(["g/L", "10^9/L", "%", "mmol/L"])
_ROW = st.fixed_dictionaries({
    "value": st.from_regex(r"[1-9][0-9]{0,2}(\.[0-9])?", fullmatch=True),
    "unit": _COLS,
    "flag": st.sampled_from(["", "H", "L"]),
})


@st.composite
def _truth_and_perturbations(draw):
    rows = draw(st.lists(_ROW, min_size=1, max_size=12))
    truth = {f"p001/r{i}": {"key": "p001", "row": i, "kind": "lab", "item": f"项目{i}", **r} for i, r in enumerate(rows)}
    fields = [(rid, c) for rid, r in truth.items() for c in ("value", "unit", "flag") if r[c]]
    targets = draw(st.lists(st.sampled_from(fields), unique=True, max_size=len(fields)))
    perturb = [(rid, c, draw(st.sampled_from(["wrong", "missing", "drop_row"]))) for rid, c in targets]
    return truth, perturb


def _apply(truth: dict, perturb: list) -> dict:
    pred = _pred_from_truth(truth)
    for rid, col, kind in perturb:
        if rid not in pred:
            continue
        if kind == "drop_row":
            del pred[rid]
        elif kind == "missing":
            pred[rid][col] = None
        else:
            pred[rid][col] = (pred[rid][col] or "") + "x"
    return pred


@settings(max_examples=200, deadline=None)
@given(_truth_and_perturbations())
def test_metrics_on_truth_vs_perturbed(case):
    """00c3-03 (L1/S4): along a growing perturbation set, exact match and association never improve and the
    missing rate never falls; with no perturbation the metrics are perfect."""
    truth, perturb = case
    series = [E.field_metrics(truth, _apply(truth, perturb[:k])) for k in range(len(perturb) + 1)]
    assert series[0]["exact_match_rate"] == 1.0 and series[0]["missing_rate"] == 0.0
    assert series[0]["association_accuracy"] == 1.0
    for a, b in zip(series, series[1:]):
        assert b["exact_match_rate"] <= a["exact_match_rate"]
        assert b["association_accuracy"] <= a["association_accuracy"]
        assert b["missing_rate"] >= a["missing_rate"]
    for m in series:
        assert 0.0 <= m["missing_rate"] <= 1.0 - m["exact_match_rate"] + 1e-12


def test_false_auto_pass_one_wrong_fails():
    """00c3-04 (L1/S2): 20 auto fields, all four routes agree on one wrong value -> 0.05, FAIL (threshold 0)."""
    truth = _lab_truth(10)
    routes = {r: _pred_from_truth(truth) for r in ROUTES}
    for r in ROUTES:
        routes[r]["p001/r3"]["value"] = "1O3"  # the same misreading on every route
    rep = E.evaluate(truth, routes)
    assert rep["auto_verified_total"] == 20
    assert rep["false_auto_pass"] == 1
    assert rep["false_auto_pass_rate"] == pytest.approx(0.05)
    assert rep["verdict"] == "FAIL"
    assert "false_auto_pass" in rep["reasons"]


@pytest.mark.parametrize("agree,verdict", [(19, "FAIL"), (20, "PASS")])
def test_all_agree_fields_19_vs_20(agree, verdict):
    """00c3-05 (L1/S2): the four-way agreement count comes from the run's readings; 19 FAIL, 20 PASS."""
    truth = _lab_truth(10)
    routes = {r: _pred_from_truth(truth) for r in ROUTES}
    if agree == 19:
        routes["codex"]["p001/r9"]["unit"] = "mg/L"  # three routes still agree on the right unit
    rep = E.evaluate(truth, routes)
    assert rep["all_agree_fields"] == agree
    assert rep["verdict"] == verdict
    assert ("all_agree_below_min" in rep["reasons"]) is (agree == 19)
    assert rep["merged"]["exact_match_rate"] == 1.0


def test_threshold_failures_named_on_merged():
    truth = _lab_truth(10)
    routes = {r: _pred_from_truth(truth) for r in ROUTES}
    for r in ("vision", "mlx"):
        routes[r]["p001/r0"]["value"] = None
    routes["claude"]["p001/r0"]["value"] = "999"
    rep = E.evaluate(truth, routes)
    assert rep["merged"]["missing_rate"] == pytest.approx(0.05)
    assert {"exact_match_rate", "association_accuracy", "missing_rate"} <= set(rep["reasons"])
    assert rep["routes"]["codex"]["exact_match_rate"] == 1.0
    assert rep["routes"]["vision"]["missing_rate"] == pytest.approx(0.05)


def test_evaluate_rejects_empty_inputs():
    with pytest.raises(ValueError):
        E.evaluate({}, {r: {} for r in ROUTES})
    with pytest.raises(ValueError):
        E.evaluate(_lab_truth(1), {})


def test_comparison_uses_nfkc_only():
    truth = _lab_truth(1)
    pred = {"p001/r0": {"value": "１００", "unit": "g / L", "flag": None}}
    m = E.field_metrics(truth, pred)
    assert m["exact_match_rate"] == 1.0 and m["association_accuracy"] == 1.0
    pred = {"p001/r0": {"value": "10.0", "unit": "g/L", "flag": "H"}}
    m = E.field_metrics(truth, pred)
    assert m["exact_match_rate"] == 0.5 and m["association_accuracy"] == 0.0


def test_parse_rows_lab_lines():
    truth = _truth("fx-labs-1x")
    lines = [
        "示例医院 检验报告单",
        "项目 结果 单位 提示 参考范围",
        "血红蛋白 128 g/L 115-150",
        "| 白细胞 | 6.2 | 10^9/L | | 3.5-9.5 |",
        "中性粒细胞百分比 78.5 % H 40-75",
    ]
    pred = E.parse_rows(lines, truth)
    assert pred["p001/r0"] == {"value": "128", "unit": "g/L", "flag": None}
    assert pred["p001/r1"] == {"value": "6.2", "unit": "10^9/L", "flag": None}
    assert pred["p001/r4"] == {"value": "78.5", "unit": "%", "flag": "H"}
    assert "p001/r2" not in pred  # 红细胞 not in the lines


def test_parse_rows_repeated_items_and_rx():
    truth = _truth("fx-rx-stamp")
    lines = ["药品 规格 用法", "示例片 10mg 每日1次", "示例片 5mg 每日2次", "示例片 0.25g 必要时"]
    pred = E.parse_rows(lines, truth)
    assert [pred[f"p001/r{i}"]["value"] for i in range(3)] == ["10mg", "5mg", "0.25g"]
    assert all(pred[f"p001/r{i}"]["unit"] is None for i in range(3))
    assert E.field_metrics(truth, pred)["association_accuracy"] == 1.0


def test_parse_rows_item_without_values_is_unread():
    truth = _lab_truth(2)
    pred = E.parse_rows(["项目0", "项目1 101"], truth)
    assert pred["p001/r0"] == {"value": None, "unit": None, "flag": None}
    assert pred["p001/r1"] == {"value": "101", "unit": None, "flag": None}


def test_parse_rows_is_deterministic_and_pure():
    truth = _truth("fx-labs-1x")
    lines = ["血红蛋白 128 g/L 115-150"]
    before = copy.deepcopy(truth)
    assert E.parse_rows(lines, truth) == E.parse_rows(list(lines), truth)
    assert truth == before
