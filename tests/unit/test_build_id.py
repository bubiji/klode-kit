"""WI-09b: the build id (matrix 09b-08, L1 S4; design 02 r3.2 "键分层": ``b-<UTC yyyymmddTHHMMSSZ>-<sha256(inputs)[:8]>``).

Law: with the clock fixed, ``build_id`` changes if and only if the inputs change (a monotone, deterministic key of
the inputs: key order and the clock never leak into the hash part); a changed clock changes only the timestamp part.
Inputs are the components design r3.2 lists - ``sorted(rec_id:rev)``, the item table, the desk / holdout, the config
version, the plugin and klode versions - plus the ones that also change the products (mode, source, manifest,
syntheses, de-identification mode, effective configuration).
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.build import run_build as RB

pytestmark = pytest.mark.unit

ID_RE = re.compile(r"^b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
NOW = datetime(2026, 10, 24, 12, 30, 45, tzinfo=timezone.utc)
SHA = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
REC = st.builds(lambda n, r: f"r-{n:012x}:{r}", st.integers(0, 2**48 - 1), st.integers(1, 9))
INPUTS = st.fixed_dictionaries({
    "mode": st.sampled_from(["records", "book"]),
    "records": st.lists(REC, max_size=6, unique=True).map(sorted),
    "items_table": SHA,
    "desk": st.one_of(st.none(), SHA),
    "holdout": st.one_of(st.none(), SHA),
    "source": st.one_of(st.none(), SHA),
    "manifest": st.one_of(st.none(), SHA),
    "syntheses": st.lists(SHA, max_size=3),
    "config_sha256": SHA,
    "config_version": st.integers(1, 5),
    "plugin_version": st.sampled_from(["0.1.0", "0.2.0", "0+unknown"]),
    "klode_version": st.sampled_from(["0.5.0", "0.5.1", "0.6.0"]),
    "deident_mode": st.sampled_from(["pseudonym", "dateshift", None]),
})


def suffix(build_id: str) -> str:
    return build_id.rsplit("-", 1)[1]


def test_shape_and_clock_part():
    bid = RB.make_build_id({"records": ["r-000000000001:1"]}, NOW)
    assert ID_RE.match(bid)
    assert bid.startswith("b-20261024T123045Z-")
    # the schema of the build receipt accepts exactly this shape
    from klode_kit.contract.schema import issues

    assert issues("build_receipt", {"schema": "klode-kit.build_receipt/1", "build_id": bid, "kb_id": "k",
                                    "status": "FAIL", "steps": [{"step": s, "status": "NOT_RUN"} for s in RB.STEPS],
                                    "audit_bindings": {}, "excluded": []}) == []


@settings(max_examples=150, deadline=None)
@given(INPUTS, INPUTS)
def test_id_equal_iff_inputs_equal(a, b):
    ida, idb = RB.make_build_id(a, NOW), RB.make_build_id(b, NOW)
    assert (ida == idb) == (a == b)
    assert (RB.inputs_hash(a) == RB.inputs_hash(b)) == (a == b)


@settings(max_examples=100, deadline=None)
@given(INPUTS, st.data())
def test_any_single_component_change_changes_the_id(inputs, data):
    key = data.draw(st.sampled_from(sorted(inputs)))
    changed = copy.deepcopy(inputs)
    value = changed[key]
    if isinstance(value, list):
        changed[key] = [*value, "x" * 64 if key == "syntheses" else "r-ffffffffffff:9"]
    elif isinstance(value, int):
        changed[key] = value + 1
    elif value is None:
        changed[key] = "a" * 64 if key != "deident_mode" else "pseudonym"
    else:
        changed[key] = value + "x"
    assert RB.make_build_id(changed, NOW) != RB.make_build_id(inputs, NOW)


@settings(max_examples=100, deadline=None)
@given(INPUTS)
def test_key_order_and_clock_do_not_touch_the_hash_part(inputs):
    shuffled = dict(reversed(list(inputs.items())))
    later = datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    first = RB.make_build_id(inputs, NOW)
    assert RB.make_build_id(shuffled, NOW) == first
    other = RB.make_build_id(inputs, later)
    assert suffix(other) == suffix(first) and other != first


def test_inputs_hash_is_a_sha256_hex_and_the_id_suffix_is_its_prefix():
    inputs = {"records": ["r-0000000000aa:2"], "mode": "records"}
    digest = RB.inputs_hash(inputs)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert suffix(RB.make_build_id(inputs, NOW)) == digest[:8]
