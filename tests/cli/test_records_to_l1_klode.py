"""WI-01b: deterministic cards for fx-labs-3x through the real klode 0.5.0 (matrix 01b-07, L3 S1).

03 §2 with the real tools and the launcher: the real stages and the WI-01a export -> ``klode init`` (shelves
items, records) -> ``klode build --stamp`` (one scaffold per source) -> ``bin/klode-kit records-to-l1`` ->
``bin/klode-kit write-card`` (gate 1 first) -> ``klode check --strict``: 0 errors, 0 warnings -> ``klode verify
hemoglobin <line>`` FOUND for each of the three observation lines (two of them hold the same value 128) ->
``klode zoom hemoglobin --level full`` holds all three -> ``bin/klode-kit check-published --all`` PASS (every card
published, unchanged, not forced).

klode 0.5.0 prints a found phrase as ``FOUND`` (``--json`` ``resolution: found``); ``VERIFIED`` is klode-mcp's word
for the same result (impl-deviations WI-01a). A missing ``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import export_fx as X
from _support.flows import REPO
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.cli_integration

LABS3 = X.FIXTURES / "fx-labs-3x"
LAUNCHER = REPO / "bin" / "klode-kit"


def _exe() -> str:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    return exe


def _klode(build: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([_exe(), "-c", str(build / "library.toml"), *args], capture_output=True, text=True,
                          timeout=120)


def _launch(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run([str(LAUNCHER), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


def test_fx_labs_3x_real_klode(tmp_path):
    truth = json.loads((LABS3 / "truth.json").read_text(encoding="utf-8"))
    project = tmp_path / "proj"
    project.mkdir()
    X.pipeline(project, LABS3)
    build = tmp_path / "build"
    R.export(project, build, kb_id="fx-labs-3x-l1")

    init = subprocess.run([_exe(), "init", "--shelf", "items", "--shelf", "records", str(build)],
                          capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, init.stderr
    stamp = _klode(build, "build", "--stamp")
    assert stamp.returncode == 0, stamp.stderr
    assert "cards: 6 created" in stamp.stdout, stamp.stdout

    gen = _launch("records-to-l1", str(build), cwd=tmp_path)
    assert gen.returncode == 0, gen.stdout + gen.stderr
    wrote = _launch("write-card", str(build), cwd=tmp_path)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr
    assert wrote.stdout.count("  wrote ") == 6

    strict = _klode(build, "check", "--strict")
    assert strict.returncode == 0, strict.stdout + strict.stderr
    assert "0 errors, 0 warning(s)" in strict.stdout, strict.stdout

    lines = [f"血红蛋白 {row['value']} {row['unit']} {rec['date']} ⟨{rec['rec_id']}⟩"
             for rec in sorted(truth["records"], key=lambda r: r["date"]) for row in rec["rows"]
             if row["item"] == "血红蛋白"]
    assert len(lines) == 3 and len(set(lines)) == 3
    for line in lines:
        found = _klode(build, "--json", "verify", "hemoglobin", line)
        assert found.returncode == 0, found.stdout + found.stderr
        value = json.loads(found.stdout)["value"]
        assert (value["resolution"], value["card"]) == ("found", "hemoglobin")
        assert [ln for _, ln in value["lines"]] == [line]
        prose = _klode(build, "verify", "hemoglobin", line)
        assert prose.returncode == 0 and "FOUND" in prose.stdout and "NOT-FOUND" not in prose.stdout

    zoom = _klode(build, "--json", "zoom", "hemoglobin", "--level", "full")
    assert zoom.returncode == 0, zoom.stdout + zoom.stderr
    body = json.loads(zoom.stdout)["value"]["body"]
    assert all(f"`{line}`" in body for line in lines)
    assert body.count("(grep: ") == 3

    published = _launch("check-published", "--all", "--kb", str(build), "--json", cwd=tmp_path)
    assert published.returncode == 0, published.stdout + published.stderr
    doc = json.loads(published.stdout)
    assert doc["pass"] is True
    assert {c["id"] for c in doc["cards"]} == {"hemoglobin", "platelet", "wbc"} | {
        r["rec_id"] for r in truth["records"]}
    assert all(c["policy"] == "deterministic" and c["coverage"]["covered"] == c["coverage"]["lines"]
               for c in doc["cards"])
