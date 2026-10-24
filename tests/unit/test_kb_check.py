"""WI-10a: the ``/kb-check`` judging table over a build directory (matrix 10a-04, 10a-05, 10a-06, 10a-10).

No law: the kernel is a table of criteria (a unit with a law only in ``assemble``'s status/``u5_ready`` fold, S4 below).

- 10a-05 / 10a-06: the per-card criteria are the ones of ``check_published`` (zoom, aliases >= 10 authored / >= 1
  deterministic, anchors >= 5 authored / coverage 100 percent deterministic) reported as named checks.
- 10a-04: every failure maps back to one of the loops L1 / L2 / L3 (the classification of ``loops.classify``) or to
  exit 40 (environment) / 50 (no progress).
- 10a-10: a host state that is not ``connected`` is a warning, never a failure; ``u5_ready`` is false then.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.write_card_fx import A1, ALIAS_LIST, CH1, add_card, content
from klode_kit.build import check_published
from klode_kit.build import write_card as W
from klode_kit.check import kb_check as K
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.unit

BUILD = "b-20261025T000000Z-0123abcd"
PLUGIN = {"name": "klode-kit", "version": "0.1.0", "entry": "skill:/klode-kit:kb-check", "root_sha": "0" * 64}
ITEM_SRC = "血红蛋白 128 g/L 2026-07-18 ⟨r-aaaaaaaaaaaa⟩\n血红蛋白 135 g/L 2026-09-18 ⟨r-bbbbbbbbbbbb⟩\n"
ITEM_LINES = ITEM_SRC.splitlines()


def _publish(tmp_path, aliases, anchors, force: bool = True) -> object:
    """An authored build with one card, published through write-card (``--force``: gate 1 may fail on purpose)."""
    kb = tmp_path / "build"
    add_card(kb, "ch01", CH1, None)
    (kb / "cards_content" / "ch01.md").write_text(content(anchors, aliases), encoding="utf-8")
    assert W.main([str(kb), *(["--force"] if force else [])]) in (0, 1)
    return kb


def _by_name(kb) -> dict:
    return {c.name: c for c in K.card_checks(kb)}


@pytest.mark.parametrize(("n", "expected"), [(9, "FAIL"), (10, "PASS")])
def test_aliases_boundary(tmp_path, n, expected):
    """10a-05 (S2): an authored card with 9 aliases FAILs, with 10 PASSes; the failure goes back to L2 (the writer)."""
    checks = _by_name(_publish(tmp_path, ALIAS_LIST[:n] + [f"x{i}" for i in range(n - len(ALIAS_LIST))], A1))
    assert checks["aliases"].status == expected
    if expected == "FAIL":
        assert checks["aliases"].loop == "L2" and checks["aliases"].detail.startswith("L2:")
        assert "ch01" in checks["aliases"].detail


@pytest.mark.parametrize(("n", "expected"), [(0, "FAIL"), (1, "PASS")])
def test_deterministic_aliases_floor_is_one(tmp_path, n, expected):
    """WI-01b decision: a deterministic (items / records) card needs >= 1 alias, not 10."""
    kb = tmp_path / "build"
    add_card(kb, "hb", ITEM_SRC, None, shelf="items")
    full = "".join(f"- 观测 (grep: `{ln}`)\n" for ln in ITEM_LINES)
    (kb / "cards_content" / "hb.md").write_text(
        "aliases: [" + ", ".join(["血红蛋白"][:n]) + f"]\n\n## Thin\n共 2 次 (grep: `{ITEM_LINES[1]}`)\n\n## Full\n### 逐次\n{full}",
        encoding="utf-8")
    assert W.main([str(kb), "--force"]) in (0, 1)
    assert _by_name(kb)["aliases"].status == expected


@pytest.mark.parametrize(("k", "expected"), [(4, "FAIL"), (5, "PASS")])
def test_authored_anchors_boundary(tmp_path, k, expected):
    """10a-06 (S2): an authored card with 4 Full anchors FAILs, with 5 PASSes (L2: the card is written again)."""
    checks = _by_name(_publish(tmp_path, ALIAS_LIST, A1[:k]))
    assert checks["anchors"].status == expected
    if expected == "FAIL":
        assert checks["anchors"].loop == "L2" and "MIN_FULL" in checks["anchors"].detail


@pytest.mark.parametrize(("drop", "expected"), [(False, "PASS"), (True, "FAIL")])
def test_deterministic_anchor_coverage(tmp_path, drop, expected):
    """A deterministic card covers every source line with exactly one Full anchor (a records-shelf card too)."""
    for shelf in ("items", "records"):
        kb = tmp_path / shelf / "build"
        add_card(kb, "hb", ITEM_SRC, None, shelf=shelf)
        lines = ITEM_LINES[:1] if drop else ITEM_LINES
        full = "".join(f"- 观测 (grep: `{ln}`)\n" for ln in lines)
        (kb / "cards_content" / "hb.md").write_text(
            f"aliases: [血红蛋白]\n\n## Thin\n共 2 次 (grep: `{ITEM_LINES[1]}`)\n\n## Full\n### 逐次\n{full}", encoding="utf-8")
        assert W.main([str(kb), "--force"]) in (0, 1)
        checks = _by_name(kb)
        assert checks["anchors"].status == expected, shelf
        if drop:
            assert "COV" in checks["anchors"].detail and checks["anchors"].loop == "L2"


def test_stub_card_fails_zoom(tmp_path):
    """A card still a stub (``zoom: stub``, never written) FAILs the zoom check, back to L2 (03 B6)."""
    kb = _publish(tmp_path, ALIAS_LIST, A1)
    assert _by_name(kb)["zoom"].status == "PASS"
    card = kb / "library" / "cards" / "ch01.md"
    card.write_text(card.read_text(encoding="utf-8").replace("zoom: full", "zoom: stub"), encoding="utf-8")
    zoom = _by_name(kb)["zoom"]
    assert zoom.status == "FAIL" and zoom.loop == "L2" and "ch01" in zoom.detail


def test_folded_anchor_fails_anchor_text(tmp_path):
    """An anchor that is not verbatim in the source (punctuation folded, ``--`` for ``—``) FAILs, back to L3."""
    anchors = list(A1)
    anchors[0] = "copper shell -- hammered thin and then annealed twice"
    checks = _by_name(_publish(tmp_path, ALIAS_LIST, anchors))
    assert checks["anchor_text"].status == "FAIL" and checks["anchor_text"].loop == "L3"
    assert checks["anchor_text"].detail.startswith("L3:") and "ch01" in checks["anchor_text"].detail


def test_clean_cards_pass_every_card_check(tmp_path):
    checks = K.card_checks(_publish(tmp_path, ALIAS_LIST, A1, force=False))
    assert {c.name for c in checks} >= {"cards", "zoom", "aliases", "anchors", "anchor_text", "card_provenance"}
    assert [c.status for c in checks] == ["PASS"] * len(checks)


def test_no_cards_is_a_failure(tmp_path):
    (tmp_path / "build" / "library" / "cards").mkdir(parents=True)
    checks = {c.name: c for c in K.card_checks(tmp_path / "build")}
    assert checks["cards"].status == "FAIL" and checks["cards"].loop == "L2"


# --------------------------------------------------------------------------- 10a-04 mapping


@pytest.mark.parametrize(("code", "target"), [
    ("ZOOM", "L2"), ("F1", "L2"), ("F2", "L2"), ("F3", "L2"), ("MIN_FULL", "L2"), ("COV", "L2"), ("MARKER", "L2"),
    ("RECEIPT", "L2"), ("STALE", "L2"), ("FORCED", "L2"), ("MISSING", "L2"), ("SRC", "L2"), ("POLICY", "L2"),
    ("A1", "L3"), ("A2", "L3"), ("A3", "L3"), ("citation-rot", "L3"),
    ("HOLDOUT", "L1"),
    ("NO_KLODE", 40), ("NO_KLODE_MCP", 40),
    ("NO_PROGRESS", 50),
])
def test_loop_mapping(code, target):
    """10a-04 (S2): each failure goes back to L1, L2 or L3, or ends the run with exit 40 / 50."""
    assert K.target_of(code) == target


def test_every_check_published_code_maps_to_a_loop():
    """No code the card judge can emit is left without a way back (total mapping)."""
    for code in check_published.CODES:
        assert K.target_of(code) in ("L2", "L3"), code


def test_unknown_code_is_not_silently_mapped():
    with pytest.raises(KeyError):
        K.target_of("NO-SUCH-CODE")


def _fail(name="zoom", loop="L2", detail="L2: ch01", exit=None):
    return K.Check(name, "FAIL", detail, loop, exit)


def test_exit_codes():
    ok = K.Check("zoom", "PASS")
    warn = K.Check("mcp_host_visible", "WARN", "pending")
    assert K.exit_code_for([ok, warn]) == 0
    assert K.exit_code_for([ok, _fail()]) == 20
    assert K.exit_code_for([ok, _fail(exit=40)]) == 40  # klode / klode-mcp missing
    assert K.exit_code_for([_fail(), _fail("strict", exit=40)]) == 40  # environment beats content
    assert K.exit_code_for([_fail(exit=30)]) == 30  # a build that no longer matches its MANIFEST
    same = K.failure_signature([_fail()])
    assert K.exit_code_for([ok, _fail()], previous_signature=same) == 50  # same problems as the last build
    other = K.failure_signature([_fail(detail="L2: ch02")])
    assert K.exit_code_for([ok, _fail()], previous_signature=other) == 20
    assert K.exit_code_for([ok], previous_signature=same) == 0


def test_failure_signature_ignores_passes_and_order():
    a, b = _fail("zoom"), _fail("aliases", detail="L2: ch02")
    ok = K.Check("strict", "PASS", "0 errors")
    assert K.failure_signature([a, b, ok]) == K.failure_signature([b, a])
    assert K.failure_signature([a]) != K.failure_signature([a, b])


# --------------------------------------------------------------------------- 10a-10 and the fold


def _doc(checks, **kw):
    doc = K.assemble(BUILD, "fx-labs-3x", checks, plugin=PLUGIN, at="2026-10-25T00:00:00Z", **kw)
    assert contract.issues("check_receipt", doc) == []
    return doc


def test_u5_ready_false_on_pending():
    """10a-10 (S3): host pending -> a warning line, ``u5_ready`` false, status WARN, nothing failed."""
    doc = _doc([K.Check("strict", "PASS", "0 errors"), K.Check("smoke_mcp", "PASS", "6 checks")], host="pending")
    assert doc["u5_ready"] is False and doc["status"] == "WARN"
    assert doc["mcp"] == {"mcp_config_written": False, "mcp_direct_smoke": True, "mcp_host_visible": "pending"}
    host = [c for c in doc["checks"] if c["name"] == "mcp_host_visible"]
    assert len(host) == 1 and host[0]["status"] == "WARN"


def test_u5_ready_needs_connected_and_matching_probe():
    checks = [K.Check("strict", "PASS"), K.Check("smoke_mcp", "PASS")]
    ready = _doc(checks, host="connected", probe_build_id=BUILD)
    assert ready["u5_ready"] is True and ready["status"] == "PASS" and ready["probe"] == {"build_id": BUILD}
    assert _doc(checks, host="connected", probe_build_id="b-20250101T000000Z-ffffffff")["u5_ready"] is False
    assert _doc(checks, host="connected")["u5_ready"] is False
    assert _doc(checks + [_fail()], host="connected", probe_build_id=BUILD)["u5_ready"] is False


def test_not_run_host_adds_no_line_and_keeps_pass():
    doc = _doc([K.Check("strict", "PASS")])
    assert doc["status"] == "PASS" and doc["mcp"]["mcp_host_visible"] == "not_run" and doc["u5_ready"] is False
    assert [c["name"] for c in doc["checks"]] == ["strict"]


def test_checks_in_receipt_carry_only_schema_fields():
    doc = _doc([_fail(), K.Check("strict", "PASS", "0 errors")])
    assert doc["status"] == "FAIL" and doc["checks"][0] == {"name": "zoom", "status": "FAIL", "detail": "L2: ch01"}
    assert doc["build_id"] == BUILD and doc["kb_id"] == "fx-labs-3x" and doc["plugin"] == PLUGIN


@settings(max_examples=80, deadline=None)
@given(statuses=st.lists(st.sampled_from(["PASS", "WARN", "FAIL"]), min_size=1, max_size=8),
       host=st.sampled_from(["connected", "pending", "failed", "not_run"]), probe=st.booleans())
def test_status_is_the_worst_line_and_u5_ready_implies_no_failure(statuses, host, probe):
    """S4: the receipt status is the worst check status (a non-connected host counts as WARN); ``u5_ready``
    implies a connected host, a matching probe and no FAIL."""
    checks = [K.Check(f"c{i}", s, "L2: x" if s == "FAIL" else "") for i, s in enumerate(statuses)]
    doc = _doc(checks, host=host, probe_build_id=BUILD if probe else None)
    worst = "FAIL" if "FAIL" in statuses else "WARN" if ("WARN" in statuses or host in ("pending", "failed")) else "PASS"
    assert doc["status"] == worst
    if doc["u5_ready"]:
        assert host == "connected" and probe and "FAIL" not in statuses
