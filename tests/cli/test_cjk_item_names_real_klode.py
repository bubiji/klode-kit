"""WI-33g (real-data pilot P12, D-38): a KB built from CJK-labelled records passes the real klode 0.5.0
``check --strict`` and ``klode search <中文项目名>`` finds the item card. Synthetic records, labels unknown to
items.yaml. A missing ``klode`` FAILS (never skips)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import export_fx as X
from _support.flows import REPO
from klode_kit import items as items_mod
from klode_kit.build import records_to_txt as R

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


def test_cjk_items_strict_check_and_search(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    for rec, date, val in (("r-" + "a" * 12, "2026-03-01", "120"), ("r-" + "b" * 12, "2026-04-01", "140")):
        fields = {f"{rec}/date": date, f"{rec}/labs[0].item": "肌酸激酶", f"{rec}/labs[0].value": val,
                  f"{rec}/labs[0].unit": "U/L", f"{rec}/labs[1].item": "总胆红素",
                  f"{rec}/labs[1].value": "12.5", f"{rec}/labs[1].unit": "umol/L"}
        X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                     date=date)
    build = tmp_path / "build"
    R.export(project, build, kb_id="cjk-l1")
    init = subprocess.run([_exe(), "init", "--shelf", "items", "--shelf", "records", str(build)],
                          capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, init.stderr
    stamp = _klode(build, "build", "--stamp")
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    gen = _launch("records-to-l1", str(build), cwd=tmp_path)
    assert gen.returncode == 0, gen.stdout + gen.stderr
    wrote = _launch("write-card", str(build), cwd=tmp_path)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr

    strict = _klode(build, "check", "--strict")
    assert strict.returncode == 0, strict.stdout + strict.stderr
    assert "0 errors" in strict.stdout, strict.stdout

    stem = items_mod.file_stem(items_mod.slugify("肌酸激酶"))
    found = _klode(build, "--json", "search", "肌酸激酶")
    assert found.returncode == 0, found.stdout + found.stderr
    assert stem in found.stdout, found.stdout
    assert (build / "library" / "cards" / f"{stem}.md").is_file()
    assert "肌酸激酶" in (build / "library" / "cards" / f"{stem}.md").read_text(encoding="utf-8")
