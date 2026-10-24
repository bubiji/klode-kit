"""WI-02b: write_card's built-in gate and check_published against the real klode 0.5.0 (matrix 02b-04, L3 S3).

03 §2 steps 12-13b with the real tools: ``klode init`` -> ``klode ingest`` (two synthetic manual texts, one with
em dashes) -> ``klode build --stamp`` (two scaffolds). Then through ``bin/klode-kit``:

1. a clean card goes in with ``write-card`` (exit 0) and ``klode check --strict`` says 0 errors;
2. **B2**: the card rewritten with a ``--`` anchor (the source has ``—``) is refused by ``write-card`` (exit 1,
   published bytes unchanged);
3. a human ``write-card --force`` publishes it anyway (exit 1, forced receipt);
4. **B6**: ``klode check --strict`` still reports 0 errors and ``klode verify`` answers ``FOLDED-ONLY`` (exit 0)
   - and so does the never-written stub card - while ``check-published --all`` FAILS, naming the ``--`` anchor
   (A2 NOT FOUND) and FORCED for the forced card and the stub for the other.

A missing ``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from _support.flows import REPO
from klode_kit.build import check_published as P

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
LAMP = """Night Lamp Care Notes

To reset the lamp — hold the dial for five seconds until the ring blinks twice.

The wick holder unscrews counter-clockwise and should be wiped dry after every refill.

Use only clear lamp oil; scented oils leave a sticky film on the glass chimney.

Trim the wick flat with sharp scissors so that the flame burns evenly — never in a point.

Store the lamp upright in a cool cupboard away from direct sunlight and heaters.

Replace the glass chimney if a hairline crack appears near the brass collar.
"""
FAN = """Desk Fan Notes

The fan has three speeds and a timer that switches it off after one, two or four hours.

Unplug the fan before lifting the front grille away from the blade guard.
"""
GOOD = ["To reset the lamp — hold the dial for five seconds",
        "The wick holder unscrews counter-clockwise",
        "Use only clear lamp oil; scented oils leave a sticky film",
        "Trim the wick flat with sharp scissors",
        "Store the lamp upright in a cool cupboard",
        "Replace the glass chimney if a hairline crack appears"]
FOLDED = "To reset the lamp -- hold the dial for five seconds"
ALIASES = "aliases: [lamp, night lamp, wick, chimney, lamp oil, reset, dial, collar, cupboard, scissors]\n"


def _card(anchors: list[str]) -> str:
    full = "".join(f"- point {k} (grep: `{a}`)\n" for k, a in enumerate(anchors))
    return f"{ALIASES}\n# Lamp care\n\n## Thin\nReset by holding the dial (grep: `{anchors[0]}`).\n\n## Full\n### Care\n{full}"


def _klode(kb: Path, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    return subprocess.run([exe, "-c", str(kb / "library.toml"), *args], capture_output=True, text=True, timeout=120)


def _launch(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run([str(LAUNCHER), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


@pytest.fixture
def kb(tmp_path) -> Path:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    kb = tmp_path / "demo-kb"
    res = subprocess.run([exe, "init", str(kb), "--shelf", "manual"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    for cid, text in (("lamp-care", LAMP), ("fan-notes", FAN)):
        src = tmp_path / f"{cid}.txt"
        src.write_text(text, encoding="utf-8")
        res = _klode(kb, "ingest", str(src), "--shelf", "manual", "--id", cid, "--format", "txt")
        assert res.returncode == 0, res.stderr
    res = _klode(kb, "build", "--stamp")
    assert res.returncode == 0, res.stderr
    assert "cards: 2 created" in res.stdout
    (kb / "cards_content").mkdir()
    return kb


def test_published_folded_detected(kb, tmp_path):
    card = kb / "library" / "cards" / "lamp-care.md"
    content = kb / "cards_content" / "lamp-care.md"

    # 1. clean write, strict agrees
    content.write_text(_card(GOOD), encoding="utf-8")
    res = _launch("write-card", str(kb), "lamp-care", cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "\nzoom: full\n" in card.read_text(encoding="utf-8")
    strict = _klode(kb, "check", "--strict")
    assert strict.returncode == 0 and "0 errors" in strict.stdout, strict.stdout
    ok = _launch("check-published", "lamp-care", "--kb", str(kb), cwd=tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # 2. B2: the gate refuses the folded anchor, nothing is written
    content.write_text(_card([FOLDED] + GOOD[1:]), encoding="utf-8")
    before = card.read_bytes()
    res = _launch("write-card", str(kb), "lamp-care", cwd=tmp_path)
    assert res.returncode == 1
    assert "A2 NOT FOUND" in res.stdout + res.stderr
    assert card.read_bytes() == before

    # 3. a human forces it in
    res = _launch("write-card", str(kb), "lamp-care", "--force", cwd=tmp_path)
    assert res.returncode == 1
    assert FOLDED in card.read_text(encoding="utf-8")
    receipt = json.loads((kb / "write_receipt.json").read_text(encoding="utf-8"))
    assert (receipt["forced"], receipt["status"]) == (True, "failed")

    # 4. B6: klode lets both the folded anchor and the stub card through ...
    strict = _klode(kb, "check", "--strict")
    assert strict.returncode == 0 and "0 errors" in strict.stdout, strict.stdout
    verify = _klode(kb, "verify", "lamp-care", FOLDED)
    assert verify.returncode == 0 and "FOLDED-ONLY" in verify.stdout, verify.stdout
    assert "zoom: stub" in (kb / "library" / "cards" / "fan-notes.md").read_text(encoding="utf-8")

    # ... check_published does not
    js = _launch("check-published", "--all", "--kb", str(kb), "--json", cwd=tmp_path)
    assert js.returncode == 1, js.stdout + js.stderr
    doc = json.loads(js.stdout)
    assert list(Draft202012Validator(P.JSON_SCHEMA).iter_errors(doc)) == []
    by_id = {c["id"]: c for c in doc["cards"]}
    assert set(by_id) == {"lamp-care", "fan-notes"}
    lamp = by_id["lamp-care"]
    assert lamp["pass"] is False
    a2 = [p["message"] for p in lamp["problems"] if p["code"] == "A2"]
    assert a2 and all("NOT FOUND" in m and FOLDED[:40] in m for m in a2)
    assert "FORCED" in {p["code"] for p in lamp["problems"]}
    fan = by_id["fan-notes"]
    assert fan["pass"] is False and {"ZOOM", "F2", "F3"} <= {p["code"] for p in fan["problems"]}

    tx = _launch("check-published", "--all", "--kb", str(kb), cwd=tmp_path)
    assert tx.returncode == 1 and "FAIL  lamp-care" in tx.stdout and FOLDED[:40] in tx.stdout
