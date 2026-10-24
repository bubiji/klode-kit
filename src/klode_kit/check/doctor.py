"""``klode-kit doctor`` (also ``klode-kit kb-check --doctor``): run every prerequisite for real (WI-10b).

Design 02 r2.5 doctor row, r3.4 R2-07 (data-project trust), r4.2 degrade levels / H18, venv addendum V3/V4/V5,
decision F9 (test seams). Every item is RUN, never judged by a path existing (project-workflow#180: a renamed
directory left console scripts with a dead shebang while ``bin/python`` still looked fine).

Sections (``--only host,project,seams``; default ``host,seams`` plus ``project`` when ``--project`` is given):

host
  ``platform``        macOS arm64 (local routes A/B) and Python >= 3.12
  ``venv``            ``<repo>/.venv/bin/python -P -c <probe>`` really runs and imports ``klode_kit`` and
                      ``klode.lib`` (V4); missing / not executable / failing -> ``<repo>/scripts/install.sh``
  ``import_origin``   ``klode_kit.__file__`` (this process and the venv probe) lies under the repository root,
                      else "imported from elsewhere: an old copy" (V3)
  ``launcher``        ``<repo>/bin/klode-kit --version`` exits 0
  ``klode``           ``klode`` on PATH runs (``--help``) and is 0.5.x (``--version`` when it has one, else the
                      version its own interpreter reports - klode 0.5.0 has no ``--version``)
  ``klode_mcp``       ``klode-mcp --help`` exits 0
  ``mcp_degrade``     which MCP wrapper level works (r4.2): 0 ``import klode.lib.mcp_server`` in the venv;
                      1 ``klode-mcp`` subprocess proxy; 2 ``klode --json`` per call; none -> missing
  ``poppler``         ``pdftotext -v``, ``pdftoppm -v``, ``pdfinfo -v``
  ``tesseract``       ``tesseract --list-langs`` lists ``chi_sim`` and ``jpn``
  ``ocrmac``          imported by the venv probe
  ``mlx``             the pinned MLX snapshot is complete in the HF cache (``engines.mlx_weights_status``, file
                      system only) and ``mlx_vlm`` is installed; the fix is the download command - never run here
  ``node``            ``node --version``
  ``claude``/``codex`` ``--version`` >= the code floors (``isolation_check.CLAUDE_MIN_VERSION`` /
                      ``CODEX_MIN_VERSION``, WI-00c1/00c1b)
  ``codex_config``    ``$CODEX_HOME/config.toml`` (default ``~/.codex/config.toml``) is readable
seams
  ``seams``           a test-seam variable (``KLODE_KIT_TEST_SEAMS``, ``CRASH_AT``, ``PAUSE_AT``, ``PAUSE_DIR``,
                      ``ENGINE_REPLAY``) set while the seams are inactive (F9: active only with
                      ``KLODE_KIT_TEST_SEAMS=1`` and ``KLODE_KIT_REAL != 1``) -> ``unset ...``. Runs themselves
                      just ignore such variables; only the doctor fails on them.
project
  ``project_trust``           workspace trust accepted for the data project itself (``hostenv.trusted``, ⑬);
                              not trusted or undetectable -> "在数据项目目录交互运行一次 claude"
  ``project_settings_local``  ``.claude/settings.local.json`` not tracked by git, and writable
  ``project_gitignore``       ``.gitignore`` holds ``_work/ _raw/ _masked/ _mask_review/ _private/ work/``
                              (``/x``, ``x``, ``x/``, ``/x/`` all count; comments and ``!x`` do not)
  ``project_mcp``             every ``.mcp.json`` server started by a ``.../klode-kit`` launcher: the path exists,
                              is executable and answers one ``initialize`` (V5); else the re-registration hint

Exit 0 every check passed; 40 any check failed (each failure printed on stderr with its fix command); 2 usage.
The receipt (``klode-kit.doctor/1``, defined here) is printed on stdout and, with ``--receipt F``, written
atomically to ``F``. The doctor is read-only otherwise: it downloads nothing, installs nothing, never writes
``~/.claude*`` (probes run with ``PYTHONDONTWRITEBYTECODE=1``).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import selectors
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import klode_kit
from klode_kit import hostenv
from klode_kit.ocr import engines
from klode_kit.ocr.isolation_check import (CLAUDE_MIN_VERSION, CODEX_MIN_VERSION, parse_version, read_cli_version,
                                           version_at_least)
from klode_kit.seams import seams_active

SCHEMA = "klode-kit.doctor/1"
ENV_EXIT = 40
USAGE_EXIT = 2
SECTIONS = ("host", "project", "seams")
TIMEOUT_S = 60
MCP_INIT_TIMEOUT_S = 20

KLODE_SERIES = (0, 5)
KLODE_INSTALL = "uv tool install klode==0.5.0"
POPPLER = ("pdftotext", "pdftoppm", "pdfinfo")
TESSERACT_LANGS = ("chi_sim", "jpn")
SEAM_VARS = ("KLODE_KIT_TEST_SEAMS", "KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_PAUSE_DIR",
             "KLODE_KIT_ENGINE_REPLAY")
PROJECT_GITIGNORE = ("_work/", "_raw/", "_masked/", "_mask_review/", "_private/", "work/")
SETTINGS_LOCAL = ".claude/settings.local.json"
TRUST_HINT = "在数据项目目录交互运行一次 claude 并接受工作区信任（R2-07）"
RENAME_HINT = "运行 `/pfix rename` 或 `bin/klode-kit register --replay` 重新登记启动器路径"
FOREIGN_HINT = "导入的是别处的旧副本"

PROBE = r"""
import importlib, importlib.util, json, sys
out = {"python": sys.version.split()[0]}
for name in ("klode_kit", "klode.lib", "klode.lib.mcp_server", "ocrmac"):
    try:
        mod = importlib.import_module(name)
        out[name] = {"ok": True, "file": getattr(mod, "__file__", None)}
    except BaseException as exc:
        out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
try:
    out["mlx_vlm"] = {"ok": importlib.util.find_spec("mlx_vlm") is not None}
except BaseException as exc:
    out["mlx_vlm"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
print(json.dumps(out))
"""


@dataclass
class Check:
    id: str
    category: str
    ok: bool
    detail: str = ""
    fix: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "category": self.category, "ok": self.ok, "detail": self.detail,
                "fix": None if self.ok else self.fix, **self.extra}


@dataclass
class Ran:
    rc: int | None
    out: str = ""
    err: str = ""
    problem: str | None = None  # the process could not be started / finished

    @property
    def ok(self) -> bool:
        return self.problem is None and self.rc == 0

    def why(self) -> str:
        if self.problem:
            return self.problem
        tail = (self.err or self.out).strip().splitlines()[-1:] or [""]
        return f"exited {self.rc}: {tail[0][:200]}"


# --------------------------------------------------------------------------- helpers


def _run(argv: list[str], env: Mapping[str, str], timeout: float = TIMEOUT_S) -> Ran:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", env=dict(env),
                              timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        return Ran(None, problem=f"missing: {argv[0]}")
    except PermissionError:
        return Ran(None, problem=f"exists but not executable: {argv[0]}")
    except subprocess.TimeoutExpired:
        return Ran(None, problem=f"{argv[0]} did not finish within {timeout}s")
    except OSError as exc:
        return Ran(None, problem=f"cannot start {argv[0]}: {exc}")
    return Ran(proc.returncode, proc.stdout, proc.stderr)


def tool_env(env: Mapping[str, str]) -> dict[str, str]:
    """Environment for programs outside this repository: our PYTHONPATH must not shadow their packages."""
    out = {k: v for k, v in env.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    return out


def default_repo_root() -> Path:
    """The repository whose ``.venv`` runs this process (V3: independent of which klode_kit copy was imported)."""
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix).parent
    return Path(__file__).resolve().parents[3]


def _under(path: str | None, root: Path) -> bool:
    if not path:
        return False
    return Path(os.path.realpath(path)).is_relative_to(Path(os.path.realpath(root)))


def _install(root: Path) -> str:
    return f"{os.path.realpath(root)}/scripts/install.sh"


def _which(name: str, env: Mapping[str, str]) -> str | None:
    return shutil.which(name, path=env.get("PATH", ""))


# --------------------------------------------------------------------------- host


def _venv_probe(root: Path, env: Mapping[str, str]) -> tuple[Check, dict[str, Any] | None]:
    py = root / ".venv" / "bin" / "python"
    fix = _install(root)
    if not os.path.lexists(py):
        return Check("venv", "venv", False, f"venv python missing: {py}", fix), None
    if not py.exists():
        return Check("venv", "venv", False, f"venv python missing (dangling link): {py}", fix), None
    if not os.access(py, os.X_OK):
        return Check("venv", "venv", False, f"venv python exists but not executable: {py}", fix), None
    probe_env = dict(env)
    probe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    ran = _run([str(py), "-P", "-c", PROBE], probe_env)
    if not ran.ok:
        return Check("venv", "venv", False, f"venv python cannot run: {ran.why()}", fix), None
    try:
        probe = json.loads(ran.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Check("venv", "venv", False, f"venv python probe printed no result: {ran.out[:200]!r}", fix), None
    errors = [f"import {name}: {probe[name]['error']}" for name in ("klode_kit", "klode.lib")
              if not probe[name]["ok"]]
    if errors:
        return Check("venv", "venv", False, f"{py}: " + "; ".join(errors), fix), probe
    return Check("venv", "venv", True, f"{py} (python {probe['python']}) imports klode_kit, klode.lib"), probe


def _import_origin(root: Path, probe: dict[str, Any] | None) -> Check:
    seen = {"this process": getattr(klode_kit, "__file__", None)}
    if probe and probe["klode_kit"]["ok"]:
        seen["venv probe"] = probe["klode_kit"]["file"]
    bad = {k: os.path.realpath(v) if v else str(v) for k, v in seen.items() if not _under(v, root)}
    if bad:
        where = "; ".join(f"{k}: {v}" for k, v in bad.items())
        return Check("import_origin", "venv", False,
                     f"klode_kit imported outside {os.path.realpath(root)} - {FOREIGN_HINT}: {where}",
                     f"用启动器 {os.path.realpath(root)}/bin/klode-kit（它把 <repo>/src 放在 PYTHONPATH 最前），"
                     f"并清掉 PYTHONPATH / .pth 里指向旧副本的路径；或重跑 {_install(root)}")
    return Check("import_origin", "venv", True, f"klode_kit from {os.path.realpath(seen['this process'])}")


def _launcher(root: Path, env: Mapping[str, str]) -> Check:
    launcher = root / "bin" / "klode-kit"
    run_env = {k: v for k, v in env.items() if k != "PYTHONPATH"}
    ran = _run([str(launcher), "--version"], run_env)
    if ran.ok and ran.out.strip():
        return Check("launcher", "launcher", True, f"{launcher} --version -> {ran.out.strip()}")
    return Check("launcher", "launcher", False, f"{launcher} --version: {ran.why()}", _install(root))


def _klode_version(exe: str, env: Mapping[str, str]) -> str | None:
    ran = _run([exe, "--version"], env)
    if ran.ok and parse_version(ran.out + ran.err):
        return ".".join(map(str, parse_version(ran.out + ran.err)))
    try:
        with open(os.path.realpath(exe), "rb") as fh:
            first = fh.readline(4096)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    interp = shlex.split(first[2:].decode("utf-8", "replace").strip())
    if not interp:
        return None
    ran = _run([*interp, "-c", "import importlib.metadata as m; print(m.version('klode'))"], env)
    return ran.out.strip() if ran.ok and parse_version(ran.out) else None


klode_version = _klode_version  # public: the build records it among its inputs (WI-09b)


def _klode(env: Mapping[str, str]) -> Check:
    exe = _which("klode", env)
    if not exe:
        return Check("klode", "klode", False, "klode not found on PATH", KLODE_INSTALL)
    ran = _run([exe, "--help"], env)
    if not ran.ok:
        return Check("klode", "klode", False, f"{exe} --help: {ran.why()}", f"{KLODE_INSTALL} --force")
    version = _klode_version(exe, env)
    parsed = parse_version(version or "")
    if parsed is None or parsed[:2] != KLODE_SERIES:
        return Check("klode", "klode", False, f"{exe}: version {version or 'undetectable'}, need 0.5.x",
                     f"{KLODE_INSTALL} --force", {"version": version})
    return Check("klode", "klode", True, f"{exe} {version}", extra={"version": version})


def _klode_mcp(env: Mapping[str, str]) -> Check:
    exe = _which("klode-mcp", env)
    if not exe:
        return Check("klode_mcp", "klode", False, "klode-mcp not found on PATH", KLODE_INSTALL)
    ran = _run([exe, "--help"], env)
    if not ran.ok:
        return Check("klode_mcp", "klode", False, f"{exe} --help: {ran.why()}", f"{KLODE_INSTALL} --force")
    return Check("klode_mcp", "klode", True, f"{exe} runs")


def _degrade(probe: dict[str, Any] | None, klode: Check, klode_mcp: Check, root: Path) -> Check:
    if probe and probe["klode.lib.mcp_server"]["ok"]:
        level, how = 0, "import klode.lib.mcp_server in the venv (in-process wrapper)"
    elif klode_mcp.ok:
        level, how = 1, "klode-mcp subprocess proxy (klode.lib.mcp_server not importable in the venv)"
    elif klode.ok:
        level, how = 2, "klode --json per tools/call (no klode-mcp)"
    else:
        level, how = None, "no MCP path: neither klode.lib.mcp_server, klode-mcp nor klode works"
    why = ""
    if probe and not probe["klode.lib.mcp_server"]["ok"]:
        why = f"; import error: {probe['klode.lib.mcp_server']['error']}"
    return Check("mcp_degrade", "mcp", level is not None, f"level {level}: {how}{why}",
                 f"{KLODE_INSTALL}; {_install(root)}", {"level": level})


def _poppler(env: Mapping[str, str]) -> Check:
    bad = []
    for tool in POPPLER:
        exe = _which(tool, env)
        ran = _run([exe, "-v"], env) if exe else Ran(None, problem="not on PATH")
        if not ran.ok:
            bad.append(f"{tool}: {ran.why()}")
    if bad:
        return Check("poppler", "poppler", False, "; ".join(bad), "brew install poppler")
    return Check("poppler", "poppler", True, ", ".join(POPPLER) + " run")


def _tesseract(env: Mapping[str, str]) -> Check:
    exe = _which("tesseract", env)
    fix = "brew install tesseract tesseract-lang"
    if not exe:
        return Check("tesseract", "tesseract", False, "tesseract not found on PATH", fix)
    ran = _run([exe, "--list-langs"], env)
    if not ran.ok:
        return Check("tesseract", "tesseract", False, f"{exe} --list-langs: {ran.why()}", fix)
    langs = {ln.strip() for ln in (ran.out + "\n" + ran.err).splitlines()}
    missing = [lang for lang in TESSERACT_LANGS if lang not in langs]
    if missing:
        return Check("tesseract", "tesseract", False, f"tesseract language data missing: {', '.join(missing)}",
                     fix, {"missing_langs": missing})
    return Check("tesseract", "tesseract", True, f"{exe} with {', '.join(TESSERACT_LANGS)}")


def _ocrmac(probe: dict[str, Any] | None, root: Path) -> Check:
    if probe is None:
        return Check("ocrmac", "ocrmac", False, "not checked: the venv python did not run", _install(root))
    if not probe["ocrmac"]["ok"]:
        return Check("ocrmac", "ocrmac", False, f"import ocrmac: {probe['ocrmac']['error']}", _install(root))
    return Check("ocrmac", "ocrmac", True, "import ocrmac works in the venv")


def _mlx(probe: dict[str, Any] | None, root: Path) -> Check:
    status = engines.mlx_weights_status()
    problems = []
    fix = []
    if not status.ok:
        problems.append(status.problem or f"weights missing: {status.path}")
        fix.append(f'"{os.path.realpath(root)}/.venv/bin/python" -m huggingface_hub.cli.hf download '
                   f"{engines.MLX_MODEL} --revision {engines.MLX_SNAPSHOT}"
                   "（需你同意后手动运行；doctor 从不下载，闸门 G4）")
    if probe is None or not probe["mlx_vlm"]["ok"]:
        problems.append("mlx_vlm is not installed in the venv (dependency group ocr-local)")
        fix.append(_install(root))
    if problems:
        return Check("mlx", "mlx", False, "; ".join(problems), "; ".join(fix), {"weights": str(status.path)})
    return Check("mlx", "mlx", True, f"snapshot {engines.MLX_SNAPSHOT[:12]} complete in {status.path.parent.parent}",
                 extra={"weights": str(status.path)})


def _node(env: Mapping[str, str]) -> Check:
    exe = _which("node", env)
    ran = _run([exe, "--version"], env) if exe else Ran(None, problem="node not found on PATH")
    if not ran.ok:
        return Check("node", "node", False, ran.why(), "brew install node")
    return Check("node", "node", True, f"{exe} {ran.out.strip()}")


def _cli(name: str, floor: str, fix: str, env: Mapping[str, str]) -> Check:
    version, detail = read_cli_version(name, env.get("PATH", ""))
    if not version_at_least(version, floor):
        return Check(name, "cli", False, f"{name} {version or 'unavailable'}, floor {floor} ({detail})", fix,
                     {"version": version, "floor": floor})
    return Check(name, "cli", True, f"{name} {version} >= {floor}", extra={"version": version, "floor": floor})


def _codex_config(env: Mapping[str, str]) -> Check:
    base = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else Path(env.get("HOME") or Path.home()) / ".codex"
    cfg = base / "config.toml"
    try:
        with open(cfg, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        return Check("codex_config", "cli", False, f"{cfg} not readable: {exc.strerror or exc}",
                     f"确认 {cfg} 存在且可读（登录一次 codex 会生成）")
    return Check("codex_config", "cli", True, f"{cfg} readable")


def host_checks(root: Path, env: Mapping[str, str]) -> list[Check]:
    tenv = tool_env(env)
    ok_platform = sys.platform == "darwin" and platform.machine() == "arm64" and sys.version_info >= (3, 12)
    checks = [Check("platform", "platform", ok_platform,
                    f"{sys.platform} {platform.machine()} python {platform.python_version()}",
                    "本机 OCR 路 A/B 需要 macOS arm64 与 Python >= 3.12")]
    venv, probe = _venv_probe(root, env)
    klode, klode_mcp = _klode(tenv), _klode_mcp(tenv)
    checks += [venv, _import_origin(root, probe), _launcher(root, env), klode, klode_mcp,
               _degrade(probe, klode, klode_mcp, root), _poppler(tenv), _tesseract(tenv), _ocrmac(probe, root),
               _mlx(probe, root), _node(tenv),
               _cli("claude", CLAUDE_MIN_VERSION, f"升级 Claude Code 到 >= {CLAUDE_MIN_VERSION}（claude update）",
                    tenv),
               _cli("codex", CODEX_MIN_VERSION,
                    f"升级 codex-cli 到 >= {CODEX_MIN_VERSION}（npm install -g @openai/codex@latest）", tenv),
               _codex_config(env)]
    return checks


# --------------------------------------------------------------------------- seams (F9)


def seam_check(env: Mapping[str, str]) -> tuple[Check, bool]:
    active = seams_active(env)
    present = [name for name in SEAM_VARS if env.get(name)]
    if active:
        return Check("seams", "seams", True, f"test mode (seams active): {', '.join(present)}",
                     extra={"leftover": []}), True
    if present:
        return Check("seams", "seams", False,
                     f"test-seam variables set outside test mode (ignored by runs): {', '.join(present)}",
                     f"unset {' '.join(present)}", {"leftover": present}), False
    return Check("seams", "seams", True, "no test-seam variables set", extra={"leftover": []}), False


# --------------------------------------------------------------------------- data project


def _trust(project: Path, env: Mapping[str, str]) -> Check:
    state = hostenv.trusted(project, env=env)
    if state is True:
        return Check("project_trust", "project", True, f"workspace trust accepted for {project}")
    detail = ("trust undetectable (~/.claude.json unreadable, or CLAUDE_CONFIG_DIR set)"
              if state == "undetectable" else f"workspace trust not accepted for {project}")
    return Check("project_trust", "project", False, detail, f"{TRUST_HINT}: cd '{project}' && claude")


def _settings_local(project: Path, env: Mapping[str, str]) -> Check:
    target = project / SETTINGS_LOCAL
    fix = (f"cd '{project}' && git rm --cached {SETTINGS_LOCAL} && "
           f"echo '{SETTINGS_LOCAL}' >> .gitignore")
    ran = _run(["git", "-C", str(project), "ls-files", "--error-unmatch", "--", SETTINGS_LOCAL], tool_env(env))
    if ran.problem and (project / ".git").exists():
        return Check("project_settings_local", "project", False, f"cannot ask git: {ran.problem}",
                     "安装 git（xcode-select --install）")
    if ran.ok:
        return Check("project_settings_local", "project", False,
                     f"{target} is tracked by git (claude keeps per-machine approvals there)", fix)
    if target.exists() and not os.access(target, os.W_OK):
        return Check("project_settings_local", "project", False, f"{target} is not writable", f"chmod u+w '{target}'")
    if not target.exists() and target.parent.is_dir() and not os.access(target.parent, os.W_OK):
        return Check("project_settings_local", "project", False, f"{target.parent} is not writable",
                     f"chmod u+w '{target.parent}'")
    return Check("project_settings_local", "project", True, f"{SETTINGS_LOCAL} untracked and writable")


def _gitignore(project: Path) -> Check:
    path = project / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    names = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        names.add(line.strip("/"))
    missing = [entry for entry in PROJECT_GITIGNORE if entry.strip("/") not in names]
    if missing:
        return Check("project_gitignore", "project", False, f"{path} lacks: {' '.join(missing)}",
                     f"printf '%s\\n' {' '.join(missing)} >> '{path}'", {"missing_entries": missing})
    return Check("project_gitignore", "project", True, f"{path} ignores {' '.join(PROJECT_GITIGNORE)}",
                 extra={"missing_entries": []})


def _initialize(command: str, args: list[str], cwd: Path, env: Mapping[str, str]) -> str | None:
    """Start the server, send one ``initialize``; None when a result came back, else the problem."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "klode-kit-doctor", "version": klode_kit.__version__}}}
    try:
        proc = subprocess.Popen([command, *args], cwd=cwd, env=dict(env), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return f"initialize: cannot start {command}: {exc}"
    try:
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except OSError:
            pass
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + MCP_INIT_TIMEOUT_S
        buf = b""
        while time.monotonic() < deadline:
            if not sel.select(timeout=max(0.0, deadline - time.monotonic())):
                break
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                proc.wait(timeout=5)
                return f"initialize not answered: server exited {proc.returncode}"
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == 1:
                    return None if "result" in msg else f"initialize answered with an error: {msg.get('error')}"
        return f"initialize not answered within {MCP_INIT_TIMEOUT_S}s"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


def _mcp(project: Path, env: Mapping[str, str]) -> Check:
    path = project / ".mcp.json"
    if not path.exists():
        return Check("project_mcp", "project", True, "no .mcp.json yet (nothing registered)", extra={"servers": []})
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcpServers is not an object")
    except (OSError, ValueError, AttributeError) as exc:
        return Check("project_mcp", "project", False, f"{path} unreadable: {exc}", RENAME_HINT, {"servers": []})
    problems, seen = [], []
    for name, spec in sorted(servers.items()):
        command = spec.get("command", "") if isinstance(spec, dict) else ""
        if os.path.basename(command) != "klode-kit":
            continue
        seen.append(name)
        if not os.path.isabs(command) or not os.path.isfile(command):
            problems.append(f"{name}: registered launcher missing: {command}")
            continue
        if not os.access(command, os.X_OK):
            problems.append(f"{name}: registered launcher not executable: {command}")
            continue
        server_env = tool_env(env)
        server_env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
        args = [str(a) for a in spec.get("args") or []]
        problem = _initialize(command, args, project, server_env)
        if problem:
            problems.append(f"{name}: {problem}")
    if problems:
        return Check("project_mcp", "project", False, "; ".join(problems), RENAME_HINT, {"servers": seen})
    return Check("project_mcp", "project", True, f"registered launchers answer initialize: {', '.join(seen) or '-'}",
                 extra={"servers": seen})


def project_checks(project: Path, env: Mapping[str, str]) -> list[Check]:
    return [_trust(project, env), _settings_local(project, env), _gitignore(project), _mcp(project, env)]


# --------------------------------------------------------------------------- receipt / CLI


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def run(*, sections: tuple[str, ...], root: Path, project: Path | None, env: Mapping[str, str]) -> dict[str, Any]:
    checks: list[Check] = []
    if "host" in sections:
        checks += host_checks(root, env)
    test_mode = seams_active(env)
    if "seams" in sections:
        seam, test_mode = seam_check(env)
        checks.append(seam)
    if "project" in sections and project is not None:
        checks += project_checks(project, env)
    failed = [c for c in checks if not c.ok]
    by_cat: dict[str, list[str]] = {}
    for c in failed:
        by_cat.setdefault(c.category, []).append(c.id)
    degrade = next((c.extra["level"] for c in checks if c.id == "mcp_degrade"), None)
    return {
        "schema": SCHEMA,
        "ok": not failed,
        "missing": len(failed),
        "missing_by_category": by_cat,
        "mcp_degrade_level": degrade,
        "repo_root": os.path.realpath(root),
        "venv_python": str(root / ".venv" / "bin" / "python"),
        "klode_kit_file": os.path.realpath(klode_kit.__file__) if getattr(klode_kit, "__file__", None) else None,
        "project": os.path.realpath(project) if project is not None else None,
        "sections": [s for s in SECTIONS if s in sections],
        "test_mode": test_mode,
        "checks": [c.as_dict() for c in checks],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit doctor", description="run every prerequisite for real")
    parser.add_argument("--project", type=Path, help="data project to check (trust, .gitignore, settings, .mcp.json)")
    parser.add_argument("--repo", type=Path, help="repository root to check (default: the one whose .venv runs this)")
    parser.add_argument("--only", help=f"comma list of sections: {','.join(SECTIONS)}")
    parser.add_argument("--receipt", type=Path, help="also write the receipt here")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    if args.only is not None:
        sections = tuple(s.strip() for s in args.only.split(",") if s.strip())
        unknown = [s for s in sections if s not in SECTIONS]
        if unknown or not sections:
            print(f"klode-kit doctor: usage error: unknown section(s) {unknown or [args.only]}; "
                  f"choose from {','.join(SECTIONS)}", file=sys.stderr)
            return USAGE_EXIT
        if "project" in sections and args.project is None:
            print("klode-kit doctor: usage error: --only project needs --project DIR", file=sys.stderr)
            return USAGE_EXIT
    else:
        sections = ("host", "seams") + (("project",) if args.project is not None else ())
    if args.project is not None and not args.project.is_dir():
        print(f"klode-kit doctor: usage error: --project is not a directory: {args.project}", file=sys.stderr)
        return USAGE_EXIT
    root = args.repo if args.repo is not None else default_repo_root()
    receipt = run(sections=sections, root=root, project=args.project, env=os.environ)
    text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.receipt is not None:
        _write_atomic(args.receipt, text.encode("utf-8"))
    sys.stdout.write(text)
    for c in receipt["checks"]:
        if not c["ok"]:
            print(f"doctor: MISSING {c['id']} [{c['category']}]: {c['detail']}\n  fix: {c['fix']}", file=sys.stderr)
    if receipt["ok"]:
        print(f"doctor: all {len(receipt['checks'])} checks passed", file=sys.stderr)
        return 0
    print(f"doctor: {receipt['missing']} item(s) missing -> exit {ENV_EXIT}", file=sys.stderr)
    return ENV_EXIT
