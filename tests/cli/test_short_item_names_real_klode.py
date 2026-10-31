"""WI-33j (real-data pilot P16): items whose labels slug to 1-2 ASCII characters (``R``, ``b``) must not make the
real klode 0.5.0 ``check --strict`` warn (citation-rot on the synthesis ``cards:`` list), and ``kb-check``'s strict
row passes. Synthetic records. A missing ``klode`` FAILS (never skips)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import export_fx as X
from _support.flows import REPO
from klode_kit.build import gen_synthesis, records_to_txt as R
from klode_kit.check import kb_check

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
A1 = "aaaaaaaaaaaa/a1"


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


def test_one_and_two_character_item_labels_pass_strict_with_desk_syntheses(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    for rec, date, val in (("r-" + "a" * 12, "2026-03-01", "25"), ("r-" + "b" * 12, "2026-04-01", "31")):
        fields = {f"{rec}/date": date}
        for n, label in enumerate(("R", "b", "ab", "Hemoglobin")):
            fields[f"{rec}/labs[{n}].item"] = label
            fields[f"{rec}/labs[{n}].value"] = str(int(val) + 100 * n)
            fields[f"{rec}/labs[{n}].unit"] = "U/L"
        X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                     date=date)
    build = tmp_path / "build"
    R.export(project, build, kb_id="short-names")
    init = subprocess.run([_exe(), "init", "--shelf", "items", "--shelf", "records", str(build)],
                          capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, init.stderr
    assert _klode(build, "build", "--stamp").returncode == 0
    for cmd in (("records-to-l1", str(build)), ("write-card", str(build))):
        proc = _launch(*cmd, cwd=tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    for cmd in (("gen-desk", str(build), "--project", str(project)),
                ("desk-build", str(build), "--apply", "--project", str(project))):
        proc = _launch(*cmd, cwd=tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    import json
    spec = json.loads((build / "src" / "desk.json").read_text(encoding="utf-8"))
    gen_synthesis.write(build, spec["dimensions"])

    strict = _klode(build, "check", "--strict")
    assert strict.returncode == 0, strict.stdout + strict.stderr
    assert "0 errors, 0 warning(s)" in strict.stdout.strip().splitlines()[-1], strict.stdout
    synth = (build / "library" / "frameworks" / "_syntheses").glob("*.md")
    assert any(synth)
    row = kb_check.strict_check(build, _exe())
    assert row.status == "PASS", row
