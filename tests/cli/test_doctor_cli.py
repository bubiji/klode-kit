"""WI-10b: doctor with real processes (matrix 10b-01, 10b-02, 10b-04, 10b-05, 10b-06; venv addendum 10b-V3,
10b-V4 and V5; design r2.5 doctor row, r3.4 R2-07, r4.2 degrade levels / H18).

Every item is RUN, not looked up: the venv python imports, ``klode`` / ``klode-mcp``, the launcher, poppler,
tesseract (+chi_sim, jpn), node, ``claude`` / ``codex --version``. The doctor is started through the real
launcher ``bin/klode-kit`` unless the case is about bypassing it (10b-V3).

Boundaries replaced in the synthetic cases (each paired with a real path):
- ``claude`` / ``codex``: tiny scripts printing a version (a ``--version`` probe calls no model). Real path:
  ``test_all_present_real_env_exit0`` (real CLIs) and the 00c1 ``cli:version`` cases.
- ``klode.lib`` inside the venv: a stub package on PYTHONPATH. klode 0.5.0 is not installed in the venv until
  WI-15a pins it (design H18), so the real path ``test_all_present_real_env_exit0`` is a strict xfail while
  ``import klode.lib`` fails in the venv; it turns into a normal test the moment klode is installed there.

No law: the doctor checks the environment item by item (matrix WI-10b).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.ocr import engines

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
VENV_PY = REPO / ".venv" / "bin" / "python"
FAKES_BIN = REPO / "tests" / "fakes" / "bin"
GITIGNORE = "_work/\n_raw/\n_masked/\n_mask_review/\n_private/\nwork/\n"
MINI_MCP = REPO / "tests" / "recordings" / "host" / "mini_mcp_server.py"
DROP = ("PYTHONPATH", "KLODE_KIT_PYTHON", "KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_PAUSE_DIR",
        "KLODE_KIT_ENGINE_REPLAY", "CLAUDE_CONFIG_DIR", "HF_HOME", "HF_HUB_CACHE", "CODEX_HOME")


def _klode_in_venv() -> bool:
    proc = subprocess.run([str(VENV_PY), "-c", "import klode.lib.mcp_server"], capture_output=True,
                          env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, timeout=60)
    return proc.returncode == 0


def host_path(*, without: tuple[str, ...] = ()) -> list[str]:
    """The real PATH minus the repository fakes and minus every directory holding one of ``without``."""
    out = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or Path(entry).resolve() == FAKES_BIN.resolve():
            continue
        if any((Path(entry) / name).exists() for name in without):
            continue
        out.append(entry)
    return out


def stub_clis(tmp: Path, claude: str = "2.1.285 (Claude Code)", codex: str = "codex-cli 0.163.0") -> Path:
    bin_dir = tmp / "stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, text in (("claude", claude), ("codex", codex)):
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\necho '{text}'\n", encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def stub_klode_pkg(tmp: Path, *, mcp_server_breaks: bool = False) -> Path:
    root = tmp / "pyshim"
    (root / "klode" / "lib").mkdir(parents=True)
    (root / "klode" / "__init__.py").write_text("", encoding="utf-8")
    (root / "klode" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    body = "raise ImportError('upstream refactor: mcp_server moved')\n" if mcp_server_breaks else ""
    (root / "klode" / "lib" / "mcp_server.py").write_text(body, encoding="utf-8")
    return root


def synthetic_home(tmp: Path, trusted: list[Path] = ()) -> Path:
    home = tmp / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("# synthetic\n", encoding="utf-8")
    projects = {os.path.realpath(p): {"hasTrustDialogAccepted": True} for p in trusted}
    (home / ".claude.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    return home


def data_project(tmp: Path, gitignore: str = GITIGNORE) -> Path:
    project = tmp / "data"
    project.mkdir(parents=True)
    (project / ".gitignore").write_text(gitignore, encoding="utf-8")
    return project


def env_for(*, path: list[str], home: Path | None = None, pythonpath: Path | str | None = None,
            extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    env["PATH"] = os.pathsep.join(path)
    if home is not None:
        env["HOME"] = str(home)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    env.update(extra or {})
    return env


def synthetic_env(tmp: Path, *, trusted: list[Path] = (), mcp_server_breaks: bool = False,
                  without: tuple[str, ...] = ()) -> dict[str, str]:
    """Everything present: real tools, stub claude/codex, stub klode.lib in the venv, real MLX cache (read-only)."""
    return env_for(path=[str(stub_clis(tmp)), *host_path(without=without)], home=synthetic_home(tmp, trusted),
                   pythonpath=stub_klode_pkg(tmp, mcp_server_breaks=mcp_server_breaks),
                   extra={"HF_HUB_CACHE": str(engines.hub_cache_dir())})


def run_doctor(args: list[str], env: dict[str, str], *, argv0: list[str] | None = None,
               cwd: Path | None = None) -> tuple[int, dict | None, str]:
    cmd = (argv0 or [str(LAUNCHER), "doctor"]) + args
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=300)
    try:
        receipt = json.loads(proc.stdout)
    except ValueError:
        receipt = None
    if receipt is not None:
        failed = [c for c in receipt["checks"] if not c["ok"]]
        assert receipt["missing"] == len(failed)
        assert (proc.returncode == 0) is (not failed)
    return proc.returncode, receipt, proc.stderr


def check(receipt: dict, check_id: str) -> dict:
    found = [c for c in receipt["checks"] if c["id"] == check_id]
    assert len(found) == 1, [c["id"] for c in receipt["checks"]]
    return found[0]


def failed_ids(receipt: dict) -> list[str]:
    return [c["id"] for c in receipt["checks"] if not c["ok"]]


# --------------------------------------------------------------------------- 10b-01


def test_missing_binary_exit40(tmp_path):
    """10b-01 (L3/S3): klode off PATH -> `kb-check --doctor` exit 40 with the install command."""
    project = data_project(tmp_path)
    env = synthetic_env(tmp_path, trusted=[project], without=("klode", "klode-mcp"))
    rc, receipt, err = run_doctor(["--doctor", "--project", str(project)], env,
                                  argv0=[str(LAUNCHER), "kb-check"])
    assert rc == 40, err
    assert check(receipt, "klode")["ok"] is False
    assert check(receipt, "klode_mcp")["ok"] is False
    assert "uv tool install klode==0.5.0" in check(receipt, "klode")["fix"]
    assert "uv tool install klode==0.5.0" in err
    assert set(failed_ids(receipt)) <= {"klode", "klode_mcp", "mcp_degrade"}


# --------------------------------------------------------------------------- 10b-02


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, timeout=60)


def test_tracked_settings_local_exit40(tmp_path):
    """10b-02 (L2/S3): `.claude/settings.local.json` tracked in the data project's git -> exit 40 with a hint."""
    project = data_project(tmp_path)
    _git(project, "init", "-q")
    _git(project, "config", "core.excludesFile", os.devnull)  # a user's global excludes may ignore the file
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
    _git(project, "add", ".claude/settings.local.json")
    env = synthetic_env(tmp_path, trusted=[project])
    rc, receipt, err = run_doctor(["--project", str(project), "--only", "project"], env)
    assert rc == 40, err
    assert failed_ids(receipt) == ["project_settings_local"]
    assert "git rm --cached" in check(receipt, "project_settings_local")["fix"]
    assert "settings.local.json" in err and "git rm --cached" in err


def test_untracked_settings_local_passes(tmp_path):
    """10b-02 boundary (L2/S2): the same file present but not tracked -> the item passes."""
    project = data_project(tmp_path, GITIGNORE + ".claude/settings.local.json\n")
    _git(project, "init", "-q")
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
    env = synthetic_env(tmp_path, trusted=[project])
    rc, receipt, err = run_doctor(["--project", str(project), "--only", "project"], env)
    assert rc == 0, err
    assert check(receipt, "project_settings_local")["ok"] is True


# --------------------------------------------------------------------------- 10b-04


def test_all_present_exit0(tmp_path):
    """10b-04 (L3/S1): everything present (synthetic klode.lib / claude / codex, real everything else) ->
    exit 0, nothing missing, MCP degrade level 0 in the receipt."""
    project = data_project(tmp_path)
    env = synthetic_env(tmp_path, trusted=[project])
    out = tmp_path / "doctor.json"
    rc, receipt, err = run_doctor(["--project", str(project), "--receipt", str(out)], env)
    assert rc == 0, err
    assert receipt["missing"] == 0
    assert receipt["mcp_degrade_level"] == 0
    assert json.loads(out.read_text(encoding="utf-8")) == receipt
    ids = {c["id"] for c in receipt["checks"]}
    assert {"platform", "venv", "import_origin", "launcher", "klode", "klode_mcp", "mcp_degrade", "poppler",
            "tesseract", "ocrmac", "mlx", "node", "claude", "codex", "codex_config", "seams", "project_trust",
            "project_settings_local", "project_gitignore", "project_mcp"} <= ids
    assert os.path.realpath(receipt["repo_root"]) == os.path.realpath(REPO)
    assert Path(receipt["klode_kit_file"]).resolve().is_relative_to(REPO.resolve())


@pytest.mark.xfail(condition=not _klode_in_venv(), strict=True,
                   reason="klode 0.5.0 is not installed in the venv until WI-15a pins it (design H18)")
def test_all_present_real_env_exit0(tmp_path):
    """10b-04 real path (L3/S1): this machine as it is (real claude / codex / klode, real HOME) -> exit 0,
    degrade level 0."""
    env = env_for(path=host_path())
    rc, receipt, err = run_doctor([], env)
    assert rc == 0, err
    assert receipt["missing"] == 0
    assert receipt["mcp_degrade_level"] == 0


# --------------------------------------------------------------------------- 10b-05


@pytest.mark.parametrize("breaks, without, level", [
    (False, (), 0),
    (True, (), 1),
    (True, ("klode-mcp",), 2),
])
def test_mcp_import_degrade_level(tmp_path, breaks, without, level):
    """10b-05 (L3/S2): `import klode.lib.mcp_server` works -> 0; a shadow package raising ImportError -> 1
    (klode-mcp subprocess proxy); and without klode-mcp either -> 2 (klode --json per call). Written to the
    receipt; levels 0 and 1 are supported set-ups (exit 0)."""
    env = synthetic_env(tmp_path, mcp_server_breaks=breaks)
    if without:
        only_klode = tmp_path / "klode-only"
        only_klode.mkdir()
        (only_klode / "klode").symlink_to(shutil.which("klode", path=env["PATH"]))
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join([parts[0], str(only_klode),
                                       *[p for p in parts[1:] if not (Path(p) / "klode-mcp").exists()]])
    out = tmp_path / "doctor.json"
    rc, receipt, err = run_doctor(["--receipt", str(out)], env)
    assert receipt["mcp_degrade_level"] == level, err
    assert json.loads(out.read_text(encoding="utf-8"))["mcp_degrade_level"] == level
    assert check(receipt, "mcp_degrade")["level"] == level
    if level < 2:
        assert rc == 0, err
    else:
        assert rc == 40
        assert check(receipt, "klode_mcp")["ok"] is False


# --------------------------------------------------------------------------- 10b-06


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    out = {}
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def _listing(root: Path) -> dict[str, int]:
    if not root.is_dir():
        return {}
    return {p.name: p.lstat().st_mtime_ns for p in root.iterdir()}


def test_no_download(tmp_path):
    """10b-06 (L2/S3): with an empty HOME the MLX weights are missing -> reported with the download command,
    and nothing is downloaded or written: HOME, the real HF cache, the uv cache and brew stay as they were."""
    home = tmp_path / "home"
    home.mkdir()
    real_home = Path.home()
    watched = {
        "hf": real_home / ".cache" / "huggingface" / "hub",
        "uv": real_home / ".cache" / "uv",
        "brew": Path("/opt/homebrew/Cellar"),
    }
    before = {"home": _tree(home), **{k: _listing(v) for k, v in watched.items()}}
    env = env_for(path=[str(stub_clis(tmp_path)), *host_path()], home=home)
    rc, receipt, err = run_doctor([], env)
    after = {"home": _tree(home), **{k: _listing(v) for k, v in watched.items()}}
    assert rc == 40
    assert after == before
    assert not (home / ".claude").exists() and not (home / ".claude.json").exists()
    mlx = check(receipt, "mlx")
    assert mlx["ok"] is False
    assert engines.MLX_SNAPSHOT in mlx["fix"] and "download" in mlx["fix"]
    assert engines.MLX_MODEL in err


# --------------------------------------------------------------------------- 10b-V3


def test_doctor_detects_foreign_klode_kit_import(tmp_path):
    """10b-V3 (L3/S3): an old klode_kit copy first on PYTHONPATH, the venv python run directly -> exit 40,
    naming the imported path ("imported from elsewhere: an old copy")."""
    foreign = tmp_path / "old-checkout" / "src"
    shutil.copytree(REPO / "src" / "klode_kit", foreign / "klode_kit",
                    ignore=shutil.ignore_patterns("__pycache__"))
    env = synthetic_env(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join([str(foreign), env["PYTHONPATH"], str(REPO / "src")])
    rc, receipt, err = run_doctor([], env, argv0=[str(VENV_PY), "-m", "klode_kit", "doctor"], cwd=tmp_path)
    assert rc == 40, err
    origin = check(receipt, "import_origin")
    assert origin["ok"] is False
    foreign_pkg = os.path.realpath(foreign / "klode_kit")
    assert foreign_pkg in origin["detail"]
    assert "别处的旧副本" in err and foreign_pkg in err


def test_launcher_beats_foreign_klode_kit_on_pythonpath(tmp_path):
    """10b-V3 boundary (L3/S2): the same PYTHONPATH through the launcher -> the repository copy wins (V3)."""
    foreign = tmp_path / "old-checkout" / "src"
    shutil.copytree(REPO / "src" / "klode_kit", foreign / "klode_kit",
                    ignore=shutil.ignore_patterns("__pycache__"))
    env = synthetic_env(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join([str(foreign), env["PYTHONPATH"]])
    rc, receipt, err = run_doctor([], env)
    assert check(receipt, "import_origin")["ok"] is True, err
    assert rc == 0, err


# --------------------------------------------------------------------------- 10b-V4


def test_doctor_runs_not_stats(tmp_path):
    """10b-V4 (L3/S3): the venv python exists but cannot be executed (a chmod -x copy, never the real .venv)
    -> exit 40 with the install.sh command for that repository."""
    copy = tmp_path / "renamed-repo"
    (copy / ".venv" / "bin").mkdir(parents=True)
    shutil.copyfile(os.path.realpath(VENV_PY), copy / ".venv" / "bin" / "python")
    (copy / ".venv" / "bin" / "python").chmod(0o644)
    real_mode = os.stat(os.path.realpath(VENV_PY)).st_mode
    env = synthetic_env(tmp_path)
    rc, receipt, err = run_doctor(["--repo", str(copy)], env)
    assert rc == 40
    venv = check(receipt, "venv")
    assert venv["ok"] is False
    assert (copy / ".venv" / "bin" / "python").exists()
    assert "not executable" in venv["detail"]
    fix = f"{os.path.realpath(copy)}/scripts/install.sh"
    assert fix in venv["fix"] and fix in err
    assert os.stat(os.path.realpath(VENV_PY)).st_mode == real_mode
    assert os.stat(os.path.realpath(VENV_PY)).st_mode & stat.S_IXUSR


def test_doctor_venv_python_missing(tmp_path):
    """10b-V4 boundary (L3/S3): no venv python at all -> exit 40, same fix command."""
    copy = tmp_path / "empty-repo"
    copy.mkdir()
    env = synthetic_env(tmp_path)
    rc, receipt, err = run_doctor(["--repo", str(copy)], env)
    assert rc == 40
    venv = check(receipt, "venv")
    assert venv["ok"] is False and "missing" in venv["detail"]
    assert f"{os.path.realpath(copy)}/scripts/install.sh" in err


# --------------------------------------------------------------------------- V5 registered launcher


def _mcp_json(project: Path, command: str) -> None:
    (project / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"fx-klode": {"type": "stdio", "command": command, "args": ["mcp", "--kb", "kb/fx"]}}}),
        encoding="utf-8")


def test_registered_launcher_missing_reported(tmp_path):
    """V5 (L3/S3): `.mcp.json` names a launcher that is gone (directory renamed) -> exit 40 with the
    re-registration hint, not a silent disconnect."""
    project = data_project(tmp_path)
    _mcp_json(project, str(tmp_path / "old-name" / "bin" / "klode-kit"))
    env = synthetic_env(tmp_path, trusted=[project])
    rc, receipt, err = run_doctor(["--project", str(project), "--only", "project"], env)
    assert rc == 40
    assert failed_ids(receipt) == ["project_mcp"]
    fix = check(receipt, "project_mcp")["fix"]
    assert "/pfix rename" in fix and "register --replay" in fix
    assert "/pfix rename" in err


def test_registered_launcher_initialize_runs(tmp_path):
    """V5 (L3/S2): a registered launcher that exists is RUN once (`initialize` answered) -> the item passes;
    one that starts but never answers fails."""
    project = data_project(tmp_path)
    plugin_bin = tmp_path / "plugin" / "bin"
    plugin_bin.mkdir(parents=True)
    good = plugin_bin / "klode-kit"
    good.write_text(f"#!/bin/sh\nexec '{os.path.realpath(VENV_PY)}' '{MINI_MCP}'\n", encoding="utf-8")
    good.chmod(0o755)
    _mcp_json(project, str(good))
    env = synthetic_env(tmp_path, trusted=[project])
    rc, receipt, err = run_doctor(["--project", str(project), "--only", "project"], env)
    assert rc == 0, err
    assert check(receipt, "project_mcp")["ok"] is True

    good.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    rc, receipt, err = run_doctor(["--project", str(project), "--only", "project"], env)
    assert rc == 40
    assert check(receipt, "project_mcp")["ok"] is False
    assert "initialize" in check(receipt, "project_mcp")["detail"]


# --------------------------------------------------------------------------- version floors


def test_claude_below_floor_exit40(tmp_path):
    """r2.5 / WI-00c1 floors (L3/S2): claude 2.1.284 (one below the code constant) -> exit 40 naming it."""
    env = synthetic_env(tmp_path)
    stub_clis(tmp_path, claude="2.1.284 (Claude Code)")
    rc, receipt, err = run_doctor([], env)
    assert rc == 40
    assert failed_ids(receipt) == ["claude"]
    assert "2.1.285" in check(receipt, "claude")["detail"]


# --------------------------------------------------------------------------- added while implementing (host)


def test_bare_path_names_every_missing_tool(tmp_path):
    """r2.5 (L3/S3): a PATH holding only claude/codex and the system dirs -> klode, klode-mcp, poppler, tesseract and node are
    each reported with their own install command (never one vague failure)."""
    env = synthetic_env(tmp_path)
    env["PATH"] = os.pathsep.join([str(tmp_path / "stub-bin"), "/usr/bin", "/bin"])  # the launcher needs sh tools
    rc, receipt, err = run_doctor([], env)
    assert rc == 40
    assert {"klode", "klode_mcp", "poppler", "tesseract", "node"} <= set(failed_ids(receipt))
    assert receipt["mcp_degrade_level"] == 0  # the venv import still works without any PATH tool
    for fix in ("uv tool install klode==0.5.0", "brew install poppler", "brew install tesseract", "brew install node"):
        assert fix in err


def test_tesseract_without_language_data_exit40(tmp_path):
    """r2.5 (L3/S3): tesseract runs but chi_sim / jpn are not in its tessdata -> exit 40 naming both."""
    env = synthetic_env(tmp_path)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    env["TESSDATA_PREFIX"] = str(tessdata)
    rc, receipt, err = run_doctor([], env)
    assert rc == 40
    assert failed_ids(receipt) == ["tesseract"]
    assert check(receipt, "tesseract")["missing_langs"] == ["chi_sim", "jpn"]


def test_codex_config_unreadable_exit40(tmp_path):
    """r2.5 (L3/S3): no readable ~/.codex/config.toml -> exit 40 naming the file."""
    env = synthetic_env(tmp_path)
    (Path(env["HOME"]) / ".codex" / "config.toml").unlink()
    rc, receipt, err = run_doctor([], env)
    assert rc == 40
    assert failed_ids(receipt) == ["codex_config"]
    assert "config.toml" in check(receipt, "codex_config")["detail"]
