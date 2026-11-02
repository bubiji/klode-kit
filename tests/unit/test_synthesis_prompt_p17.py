"""P17: workers copy the synthesis template's headings verbatim into Craft, so a heading may not carry an engine term
that check-synthesis S6 rejects (a real desk worker hit S6 on the 动作 heading)."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROMPT = Path(__file__).resolve().parents[2] / "src/klode_kit/prompts/synthesis.md"


def test_template_headings_carry_no_engine_terms():
    heads = [l for l in PROMPT.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("#")]
    bad = [h for h in heads for term in ("gate", "scorer", "RT-", "ε-test") if term in h]
    assert bad == []
