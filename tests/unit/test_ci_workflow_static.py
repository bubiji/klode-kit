"""WI-15b: static checks of ``.github/workflows/ci.yml`` (Actions cannot run locally).

The contract_replay lane drives the real ``klode`` from PATH (build/loops tests use ``shutil.which("klode")``), so
the CI job must (a) install the locked dependencies - ``klode==0.5.0`` is one of them since WI-15a - with
``uv sync --locked``, (b) put the venv's ``bin`` on PATH before the lanes, (c) never install the macOS-only OCR
extras, (d) keep the order privacy_scan -> unit -> contract_replay -> coverage gate, tag lane receipts not-release.

No law: static facts about a file. YAML is read with a tiny line parser (PyYAML is only a transitive dependency).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _support.flows import REPO

pytestmark = pytest.mark.unit

CI = REPO / ".github" / "workflows" / "ci.yml"


def steps() -> list[dict]:
    """[{name, run, uses}] from the job's ``steps:`` list (block scalars joined)."""
    out: list[dict] = []
    cur: dict | None = None
    block_indent: int | None = None
    key = None
    in_steps = False
    for raw in CI.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*steps:\s*$", raw):
            in_steps = True
            continue
        if not in_steps:
            continue
        if block_indent is not None:
            if raw.strip() == "" or len(raw) - len(raw.lstrip()) >= block_indent:
                cur[key] += raw.strip() + "\n"
                continue
            block_indent = None
        m = re.match(r"^\s*- (name|uses|run):\s*(.*)$", raw) or re.match(r"^\s+(name|uses|run|if):\s*(.*)$", raw)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if raw.lstrip().startswith("- "):
            cur = {"name": "", "run": "", "uses": "", "if": ""}
            out.append(cur)
        key = k
        if v in ("|", ">", "|-", ">-"):
            block_indent = len(raw) - len(raw.lstrip()) + 1
            cur[k] = ""
        else:
            cur[k] = v
    return out


def index(fragment: str) -> int:
    for i, s in enumerate(steps()):
        if fragment in s["run"] or fragment in s["name"]:
            return i
    raise AssertionError(f"no step mentions {fragment!r}")


def test_job_runs_on_linux_and_installs_locked_dependencies_without_mac_extras():
    text = CI.read_text(encoding="utf-8")
    assert "runs-on: ubuntu-latest" in text
    sync = [s["run"] for s in steps() if "uv sync" in s["run"]]
    assert len(sync) == 1 and "--locked" in sync[0] and "--frozen" not in sync[0]
    for banned in ("ocr-mac", "mlx", "ocr-local", "--with-mlx"):
        assert banned not in sync[0]
    assert "macos" not in text.lower() or "No macOS" in text  # no macOS job (comment mention allowed)


def test_venv_bin_is_on_path_before_the_replay_lane():
    """The contract_replay build tests run the real klode found on PATH."""
    path_step = index("GITHUB_PATH")
    assert steps()[path_step]["run"].count(".venv/bin") == 1
    assert index("GITHUB_PATH") < index("run_lane contract_replay")
    assert index("uv sync") < path_step


def test_lane_order_and_not_release_tags():
    order = [index("privacy_scan"), index("run_lane unit"), index("run_lane contract_replay"), index("coverage_gate")]
    assert order == sorted(order) and len(set(order)) == 4
    for lane in ("unit", "contract_replay"):
        assert "--not-release" in steps()[index(f"run_lane {lane}")]["run"]
    text = CI.read_text(encoding="utf-8")
    for lane in ("cli_integration", "model_integration", "host_acceptance"):
        assert f"run_lane {lane}" not in text


def test_a_preflight_step_proves_klode_is_found_the_way_the_tests_find_it():
    run = steps()[index("shutil.which")]["run"]
    assert "klode" in run and ".venv/bin/python" in run


def test_every_command_uses_the_venv_python_module_form():
    for s in steps():
        for line in s["run"].splitlines():
            line = line.strip()
            if line.startswith(".venv/bin/") and not line.startswith(".venv/bin/python "):
                pytest.fail(f"console script in CI: {line}")


def test_the_ci_assumption_holds_locally_klode_is_in_the_venv_bin():
    """Local simulation of the PATH step: with only the venv's bin on PATH, ``klode`` and ``klode-mcp`` resolve."""
    venv_bin = Path(sys.prefix) / "bin"
    assert shutil.which("klode", path=str(venv_bin))
    assert shutil.which("klode-mcp", path=str(venv_bin))
    proc = subprocess.run([sys.executable, "-c", "import shutil,sys; sys.exit(0 if shutil.which('klode') else 1)"],
                          env={"PATH": str(venv_bin), "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 0
