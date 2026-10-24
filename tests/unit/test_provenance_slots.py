"""WI-00b3b: the provenance validator reads the slot names the real pipeline writes (impl-deviations WI-01a「待修」,
WI-01c「发现」).

Two slot formats name a field (``<rec_id>/<slot>``):

- kb-audit / writeback slots (WI-08a, ``audit.gate.slot``): ``r<row:03d>-<kind>-<k>``, and ``<kind>-<k>`` (the
  ``cell.col`` form when the row is not part of the name); ``kind`` in ``number date unit flag drug``;
- fx-chain slots ``<group>[<n>].<role>`` (roles ``item value unit flag date``) and a bare ``date``.

Public surface (``klode_kit.contract.provenance``):

- ``slot_kind(field_id) -> str | None``: the kind in the pipeline vocabulary - ``number`` (fx-chain ``value``),
  ``date``, ``unit``, ``flag``, ``drug``, ``item``; ``None`` for a slot of neither format;
- ``NUMERIC_KINDS`` (no transform may touch them) = ``{number, unit}``; ``ITEM_KINDS`` (``alias`` may touch them)
  = ``{item, drug}`` (a ``drug-0`` slot is the item of an rx row, ``records_to_txt``);
- ``check_transforms``: a transform naming a field of unknown slot format is ``unknown_slot`` - never skipped.

Law (S4): over every slot ``audit.gate.slot`` can produce, ``slot_kind`` returns its kind; ``check_transforms``
reports exactly the transforms the rule table forbids, each with its own code, for pipeline slots as for fx-chain
slots.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.audit import gate as G
from klode_kit.contract import provenance as P

pytestmark = pytest.mark.unit

REC = "r-0123456789ab"
PIPE_KINDS = ("number", "date", "unit", "flag", "drug")


@pytest.mark.parametrize("slot, kind", [
    ("r005-number-0", "number"), ("r003-date-0", "date"), ("r002-unit-0", "unit"), ("r001-flag-0", "flag"),
    ("r001-drug-0", "drug"), ("r012-number-1", "number"), ("r1000-date-2", "date"),
    ("number-0", "number"), ("date-1", "date"), ("unit-0", "unit"), ("flag-0", "flag"), ("drug-3", "drug"),
    ("labs[0].value", "number"), ("labs[1].unit", "unit"), ("labs[2].item", "item"), ("labs[0].date", "date"),
    ("labs[0].flag", "flag"), ("date", "date"),
])
def test_slot_kind_both_formats(slot, kind):
    """(S2) one slot per kind of each format, both pipeline forms (with and without the row)."""
    assert P.slot_kind(f"{REC}/{slot}") == kind


@pytest.mark.parametrize("slot", [
    "r005-weird-0", "r005-number", "r005-number-x", "number", "labs[0].comment", "labs.value", "labs[0]value",
    "r-5-number-0", "", "value", "r005-number-0-1",
])
def test_slot_kind_unknown_is_none(slot):
    """(S3) a slot of neither format has no kind (``None``), never a guess from its last segment."""
    assert P.slot_kind(f"{REC}/{slot}") is None


def test_kind_sets():
    assert P.NUMERIC_KINDS == frozenset({"number", "unit"})
    assert P.ITEM_KINDS == frozenset({"item", "drug"})
    assert "unknown_slot" in P.CODES


@settings(max_examples=300)
@given(row=st.integers(min_value=0, max_value=99999), kind=st.sampled_from(PIPE_KINDS),
       k=st.integers(min_value=0, max_value=999), rowless=st.booleans())
def test_slot_kind_of_every_gate_slot(row, kind, k, rowless):
    """(S4) every slot the audit gate writes (and its row-less ``cell.col`` form) maps to its own kind."""
    slot = f"{kind}-{k}" if rowless else G.slot(row, kind, k)
    assert P.slot_kind(f"{REC}/{slot}") == kind


def test_unknown_slot_transform_is_reported():
    """(S3) a transform on a field of unknown slot format: exactly one ``unknown_slot``, naming field and index."""
    fid = f"{REC}/r005-weird-0"
    found = P.check_transforms([{"kind": "layout", "from": "a", "to": "b"},
                                {"kind": "alias", "field_id": fid, "from": "x", "to": "y"}], deident_mode="dateshift")
    assert [(v.code, v.field_id, v.rec_id, v.location) for v in found] == [
        ("unknown_slot", fid, REC, "transforms#1")]


SLOTS = ([f"r{r:03d}-{kd}-{k}" for r in (1, 5) for kd in PIPE_KINDS for k in (0, 1)]
         + [f"{kd}-0" for kd in PIPE_KINDS]
         + [f"labs[0].{role}" for role in ("item", "value", "unit", "flag", "date")] + ["date"]
         + ["r005-weird-0", "labs[0].comment"])
KIND_OF = {"value": "number"}


def _expected(t_kind: str, slot: str) -> str | None:
    """The rule table, written from the slot text (not through ``slot_kind``)."""
    if slot in ("r005-weird-0", "labs[0].comment"):
        return "unknown_slot"
    if slot == "date":
        kind = "date"
    elif "." in slot:
        kind = KIND_OF.get(slot.rsplit(".", 1)[1], slot.rsplit(".", 1)[1])
    else:
        kind = slot.split("-")[-2]
    if kind in ("number", "unit"):
        return "transform_on_numeric"
    if t_kind == "alias" and kind not in ("item", "drug"):
        return "alias_not_item"
    if t_kind == "dateshift" and kind != "date":
        return "dateshift_not_date"
    return None


@settings(max_examples=300)
@given(raw=st.lists(st.tuples(st.sampled_from(["alias", "dateshift", "layout"]), st.sampled_from(SLOTS),
                              st.text(max_size=4), st.text(max_size=4)), max_size=8),
       mode=st.sampled_from(["dateshift", "pseudonym"]))
def test_check_transforms_rule_table_on_pipeline_slots(raw, mode):
    """(S4) the reported ``(index, code)`` set equals the rule table over pipeline, row-less and fx-chain slots:
    numeric (number/unit) -> ``transform_on_numeric``; alias off item/drug -> ``alias_not_item``; dateshift off a
    date -> ``dateshift_not_date``; dateshift on a date outside ``dateshift`` mode -> ``dateshift_without_mode``;
    unknown slot -> ``unknown_slot``."""
    transforms = [{"kind": k, "field_id": f"{REC}/{s}", "from": a, "to": b} for k, s, a, b in raw]
    want = set()
    for i, (k, s, _, _) in enumerate(raw):
        code = _expected(k, s)
        if code is None and k == "dateshift" and mode != "dateshift":
            code = "dateshift_without_mode"
        if code is not None:
            want.add((i, code))
    found = P.check_transforms(transforms, deident_mode=mode)
    assert {(int(v.location.rsplit("#", 1)[1]), v.code) for v in found} == want
    assert len(found) == len(want)
