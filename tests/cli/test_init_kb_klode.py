"""WI-03a: ``init-kb`` + ``gen-manifest`` with the real klode 0.5.0 (matrix 03a-01, L3 S1; 03 G11/G14).

Original klode-build SKILL.md steps 1-6 with the real tools and the launcher:
``klode init --shelf book`` -> ``bin/klode-kit init-kb`` (id/name/description, guard, dirs, .gitignore) ->
klode itself loads the toml and reports the id/name/description (``klode --registry R --json kbs``) ->
``klode ingest`` the synthetic aurora handbook as ``_whole-book`` -> ``bin/klode-kit gen-manifest --apply``
(scan -> manifest -> split, pieces put back == whole) -> ``klode build --stamp`` creates one card per piece.
A missing ``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from _support.flows import REPO

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
AURORA = REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt"


def _exe() -> str:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    return exe


def _run(*argv: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run(list(argv), capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


def test_init_kb_toml_fields(tmp_path):
    kb = tmp_path / "demo-klode"
    init = _run(_exe(), "init", "--shelf", "book", str(kb), cwd=tmp_path)
    assert init.returncode == 0, init.stderr

    made = _run(str(LAUNCHER), "init-kb", str(kb), "--id", "demo", "--shelf", "book", "--name", "Aurora Kettle",
                "--description", "合成水壶手册（测试用）", cwd=tmp_path)
    assert made.returncode == 0, made.stdout + made.stderr
    cfg = tomllib.loads((kb / "library.toml").read_text(encoding="utf-8"))
    assert cfg["library"]["id"] == "demo"
    assert cfg["library"]["name"] == "Aurora Kettle"
    assert cfg["library"]["description"] == "合成水壶手册（测试用）"
    assert cfg["copyright"]["guard"] is True
    for d in ("library/book", "library/cards", "cards_content", "src", "_raw"):
        assert (kb / d).is_dir(), d
    ignore = (kb / ".gitignore").read_text(encoding="utf-8").splitlines()
    for want in ("library/book/*.txt", "_raw/", "_work/"):
        assert want in ignore, (want, ignore)

    reg = tmp_path / "registry.toml"
    reg.write_text(f'[[kb]]\nid = "demo"\npath = "{kb / "library.toml"}"\n', encoding="utf-8")
    kbs = _run(_exe(), "--registry", str(reg), "--json", "kbs", cwd=tmp_path)
    assert kbs.returncode == 0, kbs.stdout + kbs.stderr
    (entry,) = json.loads(kbs.stdout)["value"]
    assert entry["ok"] is True, entry
    assert (entry["id"], entry["name"], entry["description"]) == ("demo", "Aurora Kettle", "合成水壶手册（测试用）")

    ing = _run(_exe(), "-c", str(kb / "library.toml"), "ingest", str(AURORA), "--shelf", "book", "--id",
               "_whole-book", "--format", "txt", cwd=tmp_path)
    assert ing.returncode == 0, ing.stdout + ing.stderr
    gen = _run(str(LAUNCHER), "gen-manifest", str(kb), "--shelf", "book", "--apply", cwd=tmp_path)
    assert gen.returncode == 0, gen.stdout + gen.stderr
    manifest = json.loads((kb / "src" / "chapters-book.json").read_text(encoding="utf-8"))
    assert [p["start_line"] for p in manifest["pieces"]] == [1, 3, 17, 31]
    assert (kb / "_raw" / "_whole-book.txt").exists()

    stamp = _run(_exe(), "-c", str(kb / "library.toml"), "build", "--stamp", cwd=tmp_path)
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    assert "cards: 4 created" in stamp.stdout, stamp.stdout
    assert sorted(p.stem for p in (kb / "library" / "cards").glob("*.md") if p.stem != "INDEX") \
        == sorted(p["id"] for p in manifest["pieces"])
