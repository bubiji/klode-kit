"""WI-15a: scripts/install.sh and the launcher after a real install (matrix 15a-01..05; venv addendum
15a-V1 and 15a-V7; V1-V3).

Every install runs in a COPY of the tracked sources under pytest's tmp directory with a tmp HOME and a tmp
cwd; the repository's own ``.venv`` is never touched. PyPI packages come from the uv cache (the cache
directory is passed explicitly because HOME is redirected) and from the public index when missing (gate G4:
packages from PyPI only; no Python is downloaded, no weights are fetched).

Law (15a-02): installing again is idempotent - lock bytes, installed package set and the version output
do not change however many times it runs (S5).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from _support.flows import REPO

pytestmark = pytest.mark.cli_integration

COPY_ITEMS = ["bin", "scripts", "src", "pyproject.toml", "uv.lock", "README.md"]
PACKAGE_VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
FINGERPRINT = Path(".venv") / "klode-kit-install.json"


def _uv_cache() -> str:
    return subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True).stdout.strip()


UV_CACHE = _uv_cache()


def make_copy(dest: Path) -> Path:
    dest.mkdir(parents=True)
    for item in COPY_ITEMS:
        src = REPO / item
        if src.is_dir():
            shutil.copytree(src, dest / item, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif src.exists():
            shutil.copy2(src, dest / item)
    return dest


def clean_env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "KLODE_KIT_PYTHON", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON")
           and not k.startswith("KLODE_KIT_")}
    env["HOME"] = str(home)
    env["UV_CACHE_DIR"] = UV_CACHE
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def install(root: Path, home: Path, cwd: Path, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(root / "scripts" / "install.sh"), *args], capture_output=True, text=True,
                          env=clean_env(home), cwd=cwd, timeout=timeout)


def launcher(root: Path, home: Path, cwd: Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(root / "bin" / "klode-kit"), *args], capture_output=True, text=True,
                          env=clean_env(home, **extra), cwd=cwd, timeout=120)


FAKES_BIN = (REPO / "tests" / "fakes" / "bin").resolve()


def host_env(home: Path) -> dict[str, str]:
    env = clean_env(home)
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if p and Path(p).resolve() != FAKES_BIN)
    return env


def venv_python(root: Path) -> str:
    return str(root / ".venv" / "bin" / "python")


def package_set(root: Path) -> list[str]:
    code = ("import importlib.metadata as m; "
            "print('\\n'.join(sorted(f'{d.metadata[\"Name\"].lower()}=={d.version}' for d in m.distributions())))")
    res = subprocess.run([venv_python(root), "-P", "-c", code], capture_output=True, text=True, timeout=60,
                         env=clean_env(root))
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> dict[str, Path]:
    """One installed copy shared by the read-only cases (launcher, import smoke, fingerprint)."""
    base = tmp_path_factory.mktemp("install")
    root, home, cwd = make_copy(base / "repo"), base / "home", base / "elsewhere"
    home.mkdir()
    cwd.mkdir()
    res = install(root, home, cwd, "--no-ocr")
    assert res.returncode == 0, res.stdout + res.stderr
    return {"root": root, "home": home, "cwd": cwd}


# --------------------------------------------------------------------------- 15a-01


def test_install_outside_repo(sandbox):
    """15a-01 (L3/S1): install.sh run by absolute path from a cwd outside the repository, with a tmp HOME:
    the launcher then prints the package version; nothing was written under HOME."""
    root, home, cwd = sandbox["root"], sandbox["home"], sandbox["cwd"]
    assert Path(venv_python(root)).exists()
    res = launcher(root, home, cwd, "--version")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == PACKAGE_VERSION
    assert list(home.iterdir()) == []  # no ~/.claude, ~/.klode, ~/.cache ... created by the install


def test_install_script_relative_cwd_symlink_entry(tmp_path):
    """15a-01 (L3/S2): the script finds the repository from its REAL path - started through a symlink from
    an unrelated cwd it still builds the venv in the repository, not next to the symlink."""
    root, home, cwd = make_copy(tmp_path / "repo"), tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    link = cwd / "do-install"
    link.symlink_to(root / "scripts" / "install.sh")
    res = subprocess.run(["sh", str(link), "--no-ocr"], capture_output=True, text=True, env=clean_env(home),
                         cwd=cwd, timeout=900)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (root / ".venv" / "bin" / "python").exists()
    assert not (cwd / ".venv").exists()


def test_install_records_fingerprint(sandbox):
    """15a-01 (L2/S2): V6 input - the venv carries a fingerprint with the repository root realpath, the venv
    python realpath, the lock sha and the klode version."""
    root = sandbox["root"]
    fp = json.loads((root / FINGERPRINT).read_text(encoding="utf-8"))
    assert fp["schema"] == "klode-kit.install/1"
    assert fp["repo_root"] == os.path.realpath(root)
    assert fp["venv_python"] == os.path.realpath(venv_python(root))
    assert fp["lock_sha256"] == sha(root / "uv.lock")
    assert fp["klode_version"] == "0.5.0"


# --------------------------------------------------------------------------- 15a-02


def test_reinstall_idempotent(tmp_path):
    """15a-02 (L2/S5): installing again (twice more) leaves uv.lock, the installed package set, the
    fingerprint and the `--version` output unchanged, and rebuilds nothing (no _trash entry)."""
    root, home, cwd = make_copy(tmp_path / "repo"), tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    first = install(root, home, cwd, "--no-ocr")
    assert first.returncode == 0, first.stdout + first.stderr
    lock, pkgs = sha(root / "uv.lock"), package_set(root)
    fp = (root / FINGERPRINT).read_text(encoding="utf-8")
    version = launcher(root, home, cwd, "--version").stdout
    assert version.strip() == PACKAGE_VERSION
    for _ in range(2):
        again = install(root, home, cwd, "--no-ocr")
        assert again.returncode == 0, again.stdout + again.stderr
        assert sha(root / "uv.lock") == lock
        assert package_set(root) == pkgs
        assert (root / FINGERPRINT).read_text(encoding="utf-8") == fp
        assert launcher(root, home, cwd, "--version").stdout == version
        assert not (root / "_trash").exists()
    assert list(home.iterdir()) == []


# --------------------------------------------------------------------------- 15a-03


def test_klode_import_smoke(sandbox):
    """15a-03 (L3/S1): klode.lib imports, ``klode.lib.__version__`` and the package
    metadata both say 0.5.0, and the module lives inside this repository's venv."""
    root = sandbox["root"]
    code = ("import klode, klode.lib, importlib.metadata as m; "
            "print(klode.lib.__version__); print(m.version('klode')); print(klode.__file__)")
    res = subprocess.run([venv_python(root), "-P", "-c", code], capture_output=True, text=True, timeout=60,
                         env=clean_env(sandbox["home"]))
    assert res.returncode == 0, res.stderr
    lib_version, meta_version, where = res.stdout.splitlines()
    assert (lib_version, meta_version) == ("0.5.0", "0.5.0")
    assert Path(os.path.realpath(where)).is_relative_to(Path(os.path.realpath(root / ".venv")))


def test_installed_klode_matches_pin_in_metadata(sandbox):
    """15a-03 (L3/S2): the package metadata agrees with the module attribute (pin is what got installed)."""
    root = sandbox["root"]
    assert "klode==0.5.0" in package_set(root)


# --------------------------------------------------------------------------- 15a-04


def test_launcher_python_override(tmp_path):
    """15a-04 (L3/S2): KLODE_KIT_PYTHON selects the interpreter - a stand-in interpreter that reports its own
    path is the one that runs (`sys.executable`-style evidence), and the repo .venv is not consulted."""
    fake = tmp_path / "alt-python"
    fake.write_text('#!/bin/sh\ncase "$1" in -c) exit 0 ;; esac\necho "interpreter=$0"\n', encoding="utf-8")
    fake.chmod(0o755)
    res = launcher(REPO, tmp_path, tmp_path, "--version", KLODE_KIT_PYTHON=str(fake))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == f"interpreter={fake}"


def test_launcher_override_real_interpreter_reports_it(sandbox, tmp_path):
    """15a-04 (L3/S2): KLODE_KIT_PYTHON pointing at the venv python runs the real package."""
    root = sandbox["root"]
    res = launcher(root, sandbox["home"], tmp_path, "--version", KLODE_KIT_PYTHON=venv_python(root))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == PACKAGE_VERSION
    cfg = launcher(root, sandbox["home"], tmp_path, "config", "show", KLODE_KIT_PYTHON=venv_python(root))
    assert cfg.returncode == 0, cfg.stderr
    assert isinstance(json.loads(cfg.stdout), dict)


def test_missing_venv_message(tmp_path):
    """15a-05 (L3/S3): a copy without .venv: exit 40, no stdout, and the fix command it prints is a real,
    executable install script."""
    root = make_copy(tmp_path / "repo")
    res = launcher(root, tmp_path, tmp_path, "--version")
    assert res.returncode == 40
    assert res.stdout == ""
    fix = [ln.split("fix:", 1)[1].strip() for ln in res.stderr.splitlines() if "fix:" in ln]
    assert fix == [f"{os.path.realpath(root)}/scripts/install.sh"]
    assert os.access(fix[0], os.X_OK)


# --------------------------------------------------------------------------- 15a-V1 / V7


def test_repo_moved_launcher_still_works(tmp_path):
    """15a-V1 (L3/S3): install, rename the directory. At the new name the launcher works and `doctor` (run with the
    host PATH, real claude / codex / MLX weights) exits 0; the old name is gone. The venv console scripts are stale
    after the rename (V1) - neither launcher nor doctor uses them. The install fingerprint still names the old
    root, which lets a receipt consumer (V6) notice the move."""
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    old = make_copy(tmp_path / "before-rename")
    res = install(old, home, cwd, "--with-mlx")
    assert res.returncode == 0, res.stdout + res.stderr
    new = tmp_path / "after-rename-v2"
    old.rename(new)
    assert not old.exists()
    ver = launcher(new, home, cwd, "--version")
    assert ver.returncode == 0, ver.stderr
    assert ver.stdout.strip() == PACKAGE_VERSION
    doc = subprocess.run([str(new / "bin" / "klode-kit"), "doctor"], capture_output=True, text=True, cwd=cwd,
                         env=host_env(Path(os.path.expanduser("~"))), timeout=300)
    assert doc.returncode == 0, doc.stdout[-3000:] + doc.stderr
    receipt = json.loads(doc.stdout)
    assert receipt["missing"] == 0
    assert os.path.realpath(receipt["repo_root"]) == os.path.realpath(new)
    stale = json.loads((new / FINGERPRINT).read_text(encoding="utf-8"))
    assert stale["repo_root"] == os.path.realpath(old)
    assert stale["repo_root"] != os.path.realpath(new)


def test_install_after_move_refreshes_environment(tmp_path):
    """15a-V1 (L3/S3): re-running install.sh after the rename rebuilds the environment for the new location
    (old one moved to _trash/), and the fingerprint then names the new root."""
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    old = make_copy(tmp_path / "plugin-0.1")
    assert install(old, home, cwd, "--no-ocr").returncode == 0
    new = tmp_path / "plugin-0.2"
    old.rename(new)
    res = install(new, home, cwd, "--no-ocr")
    assert res.returncode == 0, res.stdout + res.stderr
    fp = json.loads((new / FINGERPRINT).read_text(encoding="utf-8"))
    assert fp["repo_root"] == os.path.realpath(new)
    assert [p.name for p in (new / "_trash").iterdir() if p.name.startswith("venv-")]
    assert launcher(new, home, cwd, "--version").stdout.strip() == PACKAGE_VERSION


def test_install_rebuilds_broken_venv(tmp_path):
    """15a-V7 (L3/S3): `.venv/bin/python` turned into a dangling link (the launcher then exits 40);
    install.sh detects it, moves the broken venv into _trash/ (never deletes), rebuilds and the launcher and
    `import klode.lib` work again."""
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    root = make_copy(tmp_path / "repo")
    assert install(root, home, cwd, "--no-ocr").returncode == 0
    py = root / ".venv" / "bin" / "python"
    py.rename(tmp_path / "python.moved-away")
    py.symlink_to(tmp_path / "does-not-exist" / "python3.12")
    broken = launcher(root, home, cwd, "--version")
    assert broken.returncode == 40
    res = install(root, home, cwd, "--no-ocr")
    assert res.returncode == 0, res.stdout + res.stderr
    assert launcher(root, home, cwd, "--version").stdout.strip() == PACKAGE_VERSION
    smoke = subprocess.run([venv_python(root), "-P", "-c", "import klode.lib"], capture_output=True, text=True,
                           env=clean_env(home), timeout=60)
    assert smoke.returncode == 0, smoke.stderr
    moved = [p for p in (root / "_trash").iterdir() if p.name.startswith("venv-")]
    assert len(moved) == 1
    assert (moved[0] / "bin" / "python").is_symlink()  # the broken one, kept as it was


def test_install_without_uv_fails_with_instruction(tmp_path):
    """15a-V7 (L3/S3): uv missing from PATH -> exit 40 and a message naming uv; no partial .venv."""
    root = make_copy(tmp_path / "repo")
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for tool in ("sh", "dirname", "basename", "readlink", "date", "mkdir", "mv", "uname", "cat"):
        found = shutil.which(tool)
        if found:
            (bare / tool).symlink_to(found)
    env = clean_env(tmp_path)
    env["PATH"] = str(bare)
    res = subprocess.run(["/bin/sh", str(root / "scripts" / "install.sh"), "--no-ocr"],
                         capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert res.returncode == 40
    assert "uv" in res.stderr
    assert not (root / ".venv").exists()


def test_install_rejects_unknown_option(tmp_path):
    """15a-V7 (L3/S3): usage errors are exit 2 with a usage text, before anything is touched."""
    root = make_copy(tmp_path / "repo")
    res = install(root, tmp_path, tmp_path, "--bogus")
    assert res.returncode == 2
    assert "usage" in (res.stdout + res.stderr).lower()
    assert not (root / ".venv").exists()


def test_lock_is_consistent_with_pyproject():
    """15a-10 (L3/S2): `uv lock --check` accepts the committed lock (offline, uses the cache)."""
    res = subprocess.run(["uv", "lock", "--check", "--offline"], capture_output=True, text=True, cwd=REPO,
                         env=clean_env(Path(os.path.expanduser("~"))), timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
