"""WI-32a: the masking that ``kb-ocr`` now runs keeps the crash-point discipline of 00s-16 (matrix section 0.3):
under the audit hook, every segment between two crash points of a normal ``kb-ocr`` run (all four routes replayed, so
the masking runs) holds at most one durable write, and the mask crash points the run passes are exactly the ones
``mask.CRASHPOINTS`` lists as reachable on a first run.

The enumeration list of the ``run_batch`` flow in ``_support/flows.py`` (00s-16, recorded in WI-04b) is not touched by
this file; ``test_crashpoints_partition_durable_writes[run_batch]`` compares against it by name.
"""

from __future__ import annotations

import pytest

from _support.flows import REAL_FLOWS, audit_env
from klode_kit.audit import mask
from klode_kit.devtools.audit import partition, read_log

pytestmark = pytest.mark.contract_replay

FIRST_RUN_MASK_POINTS = frozenset({"mask.mkdir", "mask.image_written", "mask.review_written",
                                   "mask.manifest_appended", "mask.derived_registered", "mask.manifest_opened",
                                   "mask.before_replace"})


def test_masking_inside_kb_ocr_has_at_most_one_durable_write_per_segment(tmp_path):
    """32a-C5 (L2 S2/S5)."""
    log = tmp_path / "audit.jsonl"
    roots = REAL_FLOWS["run_batch"].runner(tmp_path, audit_env(log))
    p = partition(read_log(log), roots)
    assert p.violations == []
    seen = {n for n in p.crashpoint_names if n.startswith("mask.")}
    assert seen == FIRST_RUN_MASK_POINTS
    assert seen <= set(mask.CRASHPOINTS)
