"""WI-00d2 stand-in for gate 1, now a thin shim over ``klode_kit.build.check_cards`` (WI-02a).

``klode-kit check-cards`` runs ``build/check_cards`` directly; this module keeps the WI-00d2 interface used by
recorded tests (``check(kb, card)`` -> problem list) and delegates every rule to the vendored gate.
"""

from __future__ import annotations

import re
from pathlib import Path

from klode_kit.build import check_cards as _gate

ANCHOR_RE = _gate.ANCHOR_RE
CARD_RE = re.compile(r"^cards_content/(?!.*\.\./)[^/]+\.md$")


def check(kb: Path, card: str) -> list[str]:
    """Problems of ``card`` (``cards_content/<id>.md``) under ``kb``; each starts with its rule code."""
    return _gate.check_card(Path(kb), Path(card).stem)["problems"]
