"""WI-15a: static packaging checks (matrix 15a-V2; plan WI-15a files; impl-deviations "WI-15a 前置").

- ``pyproject.toml``: extras ``core`` / ``ocr-mac`` / ``mlx`` / ``dev``; ``klode==0.5.0`` pinned in the
  runtime dependencies; the extras and the dependency groups they mirror cannot drift apart.
- ``uv.lock``: klode 0.5.0 from the public registry; nothing machine-specific (no absolute path, no
  ``file://``, no user name) - the repository is public.
- ``scripts/install.sh``: exists and is executable, never deletes anything (cleanup moves into ``_trash/``),
  never touches user-global directories, downloads no Python.
- V1: no tracked runner / CI / skill / doc text calls a venv console script (``.venv/bin/<not python>``).

No law: static facts about files.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest

from _support.flows import REPO

pytestmark = pytest.mark.unit

INSTALL = REPO / "scripts" / "install.sh"


def _project() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _names(reqs: list[str]) -> set[str]:
    return {r.strip() for r in reqs}


def test_extras_are_core_ocr_mac_mlx_dev():
    """15a-06 (L1/S2): the optional-dependency table has exactly the four planned extras."""
    extras = _project()["project"]["optional-dependencies"]
    assert set(extras) == {"core", "ocr-mac", "mlx", "dev"}


def test_klode_is_pinned_exactly():
    """15a-07 (L1/S2): klode==0.5.0 is a runtime dependency (installed with the plain package)."""
    deps = _names(_project()["project"]["dependencies"])
    assert "klode==0.5.0" in deps
    assert not [d for d in deps if re.match(r"klode\b(?!-)", d) and d != "klode==0.5.0"]


def test_ocr_extras_carry_the_pinned_engines():
    """15a-08 (L1/S2): ocr-mac -> ocrmac (macOS only); mlx -> mlx-vlm (Apple silicon only)."""
    extras = _project()["project"]["optional-dependencies"]
    assert [r.split(";")[0].strip() for r in extras["ocr-mac"]] == ["ocrmac==1.0.1"]
    assert "darwin" in extras["ocr-mac"][0]
    assert [r.split(";")[0].strip() for r in extras["mlx"]] == ["mlx-vlm==0.7.1"]
    assert "arm64" in extras["mlx"][0] and "darwin" in extras["mlx"][0]


def test_extras_and_groups_do_not_drift():
    """15a-09 (L1/S5): the ``dev`` extra equals the ``dev`` group; the ``ocr-local`` group (kept because
    error messages and docs name it) is exactly the union of the two OCR extras."""
    data = _project()
    extras, groups = data["project"]["optional-dependencies"], data["dependency-groups"]
    assert _names(extras["dev"]) == _names(groups["dev"])
    assert _names(groups["ocr-local"]) == _names(extras["ocr-mac"]) | _names(extras["mlx"])


LOCK = (REPO / "uv.lock").read_text(encoding="utf-8")


def test_lock_pins_klode_from_the_public_registry():
    """15a-10 (L1/S2)."""
    m = re.search(r'\[\[package\]\]\nname = "klode"\nversion = "([^"]+)"\nsource = (\{[^}]*\})', LOCK)
    assert m, "klode is not in uv.lock"
    assert m.group(1) == "0.5.0"
    assert m.group(2) == '{ registry = "https://pypi.org/simple" }'


def test_lock_has_no_machine_specific_text():
    """15a-11 (L1/S3): no absolute path, no file:// URL, no home-directory or user-name text."""
    assert "file://" not in LOCK
    bad = re.findall(r'["\'=\s](/(?:Users|home|private|var|tmp|opt|Volumes)/[^"\s]*)', LOCK)
    assert bad == []
    for key in {os.environ.get("USER", ""), Path.home().name} - {""}:
        assert key not in LOCK


def test_lock_matches_pyproject_for_the_local_project():
    """15a-12 (L1/S2): the lock's own package entry is the editable project root and lists the extras."""
    m = re.search(r'\[\[package\]\]\nname = "klode-kit"\n.*?(?=\n\[\[package\]\]|\Z)', LOCK, re.S)
    assert m
    block = m.group(0)
    assert 'source = { editable = "." }' in block
    for extra in ("core", "ocr-mac", "mlx", "dev"):
        assert f'name = "{extra}"' in block or f"\n{extra} = [" in block


def test_install_script_exists_and_is_executable():
    """15a-13 (L1/S2)."""
    assert INSTALL.is_file()
    assert os.access(INSTALL, os.X_OK)
    assert INSTALL.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


def test_install_script_never_deletes_and_stays_out_of_user_dirs():
    """15a-14 (L1/S3): moves into _trash/, never rm; no absolute home path; no ~/.claude / ~/.codex /
    ~/.klode writes; no network fetch of anything but the locked sync; no Python download."""
    text = INSTALL.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"(^|[\s;&|(])rm(\s|$)", code)
    assert "_trash" in code
    for forbidden in ("/Users/", "/home/", "~/.claude", ".claude.json", "~/.codex", "~/.klode", "$HOME/.claude",
                      "curl", "wget", "brew install", "sudo"):
        assert forbidden not in code, forbidden
    assert "--locked" in code
    assert "--no-python-downloads" in code


# --------------------------------------------------------------------------- 15a-V2

_SCAN_ROOTS = [".github", "skills", "bin", "scripts", "agents", "hooks", "workflows", "README.md",
               "src/klode_kit/devtools"]
_CONSOLE = re.compile(r"\.venv/bin/(?!python[0-9.]*(?![\w-]))[A-Za-z_][\w.-]*")
_ALLOWED_VENV_FILES = {"activate"}


def _scan_files() -> list[Path]:
    out: list[Path] = []
    for entry in _SCAN_ROOTS:
        p = REPO / entry
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts
                    and f.suffix in {"", ".md", ".py", ".sh", ".js", ".json", ".yml", ".yaml", ".toml"}]
    docs = REPO / "docs"
    out += [f for f in docs.rglob("*.md") if not set(f.relative_to(docs).parts) & {"pipeline", "spikes", "adr"}]
    return out


def test_stale_console_scripts_not_used():
    """15a-V2 (L1/S3): V1 - nothing the plugin ships or documents invokes ``.venv/bin/<console script>``
    (a renamed directory leaves their shebangs pointing at the old path)."""
    hits = []
    for f in _scan_files():
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in _CONSOLE.finditer(line):
                if m.group(0).rsplit("/", 1)[1] not in _ALLOWED_VENV_FILES:
                    hits.append(f"{f.relative_to(REPO)}:{n}: {m.group(0)}")
    assert hits == []


def test_console_script_pattern_catches_what_it_should():
    """15a-V2 (L1/S2): the scanner pattern itself - python variants pass, console scripts are hits."""
    assert not _CONSOLE.search(".venv/bin/python -m pytest")
    assert not _CONSOLE.search(".venv/bin/python3.12 -c 'x'")
    for bad in (".venv/bin/pytest -q", ".venv/bin/klode-kit doctor", ".venv/bin/pip install x",
                ".venv/bin/python-config"):
        assert _CONSOLE.search(bad), bad


def test_core_extra_equals_runtime_dependencies():
    """15a-09b (L1/S5): added while implementing - the ``core`` extra and ``dependencies`` cannot drift."""
    project = _project()["project"]
    assert _names(project["optional-dependencies"]["core"]) == _names(project["dependencies"])
