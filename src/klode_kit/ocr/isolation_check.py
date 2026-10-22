"""Deterministic isolation review of a cloud invocation, layer (a) of design 02 r4.3 (WI-00c1).

``check(invocation, project=...)`` judges the exact argv / cwd / env that is about to run and
returns one boolean per item; any False means ``cloud_isolation: FAIL`` (exit 61 at the sender,
zero real-data calls on that route). Nothing in here can widen what the builder produces: the
review is closed-world (unknown flags, unknown ``-c`` keys and stray positionals fail) and fails
closed on anything it cannot read.

Check names (stable; the receipt carries them):

- ``required:<flag>``     the must-have flag occurs exactly once with exactly the required value
                          (``-p``/``--print`` etc. are the same flag);
- ``forbidden:<flag>``    the flag appears in no spelling (``--x``, ``--x=v``) anywhere in argv;
- ``argv:closed``         program name, every other flag known with a valid value at most once,
                          positionals exactly the user prompt (codex: ``exec`` + prompt);
- ``settings:pinned``     (claude) the file ``--settings`` actually points at, by realpath, is a
                          regular file <= 2 MiB whose sha256 is ``CLOUD_SETTINGS_SHA256``;
- ``guard:on_path``       (claude) the pinned exec-form hook command ``klode-kit`` resolves on the
                          invocation PATH to this plugin's launcher;
- ``cwd:outside_project`` realpath(cwd) and realpath(project) are disjoint by path components
                          (neither inside the other; not ``startswith`` - matrix F1);
- ``cwd:system_tmp``      realpath(cwd) is a ``klode-kit-send-*`` directory directly in the system
                          TMPDIR (``tempfile.gettempdir()``);
- ``cwd:mode_0700``       owned by us, mode exactly 0700;
- ``tmpdir:contents``     exactly ``<sha>.png``, ``prompt.txt``, ``schema.json``, regular files,
                          prompt/schema byte-equal to what the builder writes;
- ``tmpdir:png_approved`` the png hashes to its own name and that sha is in the data project's
                          ``_masked/MANIFEST.jsonl`` or the synthetic ``tests/fixtures/MANIFEST.json``;
- ``env:allowlist``       env keys are a subset of ``CLOUD_ENV_ALLOWLIST`` (explicit keys, r5);
- ``cli:version``         ``<argv[0]> --version``, resolved on the invocation PATH, is numerically
                          >= the floor (matrix F2).

Version floors: ``claude >= 2.1.285`` (WI-00c1b, user decision 2026-10-23): every piece of host
evidence for this route - hook firing under ``--restricted``, ``--setting-sources ""`` semantics,
the denial shape, the ``USER`` requirement - was taken on 2.1.285 only (WI-00c2 spike). The
documented minimum is lower: ``--permission-prompts`` "Requires Claude Code v2.1.259 or later"
(https://code.claude.com/docs/en/cli-reference), ``--restricted`` v2.1.248+ and
``permissions.blockReadsOutsideWorkingDirectories`` v2.1.257+
(https://code.claude.com/docs/en/settings-reference). ``codex >= 0.163.0`` is the version whose
``codex exec --help`` the codex argv was verified against; the WI-00c2 spike may raise it.

The receipt (``schemas/isolation_receipt.json``) binds ``cli_version``, ``effective_config_sha``
(route, realpath cwd, env, the settings file sha, prompt and output schema shas) and
``argv_sha``; ``receipt_still_valid(receipt, binding(invocation))`` is False as soon as any of
the three changes, or when the receipt is not a schema-valid PASS.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from klode_kit.config import CLOUD_ENV_ALLOWLIST
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA

CLAUDE_MIN_VERSION = "2.1.285"
CODEX_MIN_VERSION = "0.163.0"
CLOUD_SETTINGS_SHA256 = "eb57a8ea24aa686cb0c49b68d972da523afada9b320529483e567244e348fc3d"
SETTINGS_MAX_BYTES = 2 * 1024 * 1024  # cli-reference --settings: "a regular file no larger than 2 MiB"
RECEIPT_SCHEMA = "klode-kit.isolation_receipt/1"
GUARD_COMMAND = "klode-kit"
VERSION_TIMEOUT_S = 30
UNKNOWN_VERSION = "0.0.0"
FIXTURES_MANIFEST = CA.PLUGIN_ROOT / "tests" / "fixtures" / "MANIFEST.json"
BINDING_KEYS = ("cli_version", "effective_config_sha", "argv_sha")

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SHA_RE = re.compile(r"[0-9a-f]{64}")

Validator = Callable[[str | None], bool]


def _none(value: str | None) -> bool:
    return value is None


def _eq(expected: str) -> Validator:
    return lambda value: value == expected


def _some(value: str | None) -> bool:
    return value is not None and value != ""


def _model(value: str | None) -> bool:
    return value is not None and CA.MODEL_RE.fullmatch(value) is not None


def _one_of(allowed: Iterable[str]) -> Validator:
    allowed = frozenset(allowed)
    return lambda value: value in allowed


def _budget(value: str | None) -> bool:
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(x) and x > 0


def _packaged(path: Path) -> Validator:
    return lambda value: value is not None and value == path.read_text(encoding="utf-8")


def _bare_png(value: str | None) -> bool:
    """``<sha>.png``: a file name in cwd, never a path."""
    return value is not None and value.endswith(".png") and CA.SHA_RE.fullmatch(value[:-4]) is not None


@dataclass(frozen=True)
class RouteSpec:
    program: str
    aliases: Mapping[str, str]          # spelling -> canonical flag
    required: Mapping[str, Validator]   # canonical flag -> value rule
    optional: Mapping[str, Validator]
    forbidden: Mapping[str, int]        # flag spelling -> arity (for parsing)
    config_required: Mapping[str, str]  # codex -c key -> exact "key=value"
    config_optional: Mapping[str, Validator]
    leading: tuple[str, ...] = ()       # positionals before the prompt (codex: "exec")


CLAUDE = RouteSpec(
    program="claude",
    aliases={"--print": "-p"},
    required={
        "-p": _none,
        "--restricted": _none,
        "--tools": _eq("Read"),
        "--strict-mcp-config": _none,
        "--permission-mode": _eq("dontAsk"),
        "--permission-prompts": _eq("none"),
        "--setting-sources": _eq(""),
        "--settings": _some,
        "--no-session-persistence": _none,
        "--system-prompt": _packaged(CA.OCR_PROMPT),
        "--disable-slash-commands": _none,
    },
    optional={
        "--model": _model,
        "--effort": _one_of(CA.CLAUDE_EFFORTS),
        "--output-format": _eq("json"),
        "--json-schema": _packaged(CA.OUTPUT_SCHEMA),
        "--max-budget-usd": _budget,
    },
    forbidden={
        # r4.3 (a)
        "--add-dir": 1, "--agents": 1, "--plugin-dir": 1, "--mcp-config": 1,
        "--dangerously-skip-permissions": 0, "--bare": 0,
        # same class of risk, from `claude --help` 2.1.285
        "--allow-dangerously-skip-permissions": 0, "--append-system-prompt": 1,
        "--append-system-prompt-file": 1, "--system-prompt-file": 1, "--agent": 1,
        "--permission-prompt-tool": 1, "--continue": 0, "-c": 0, "--resume": 1, "-r": 1,
        "--fork-session": 0, "--worktree": 0, "-w": 0, "--allowedTools": 1, "--allowed-tools": 1,
    },
    config_required={},
    config_optional={},
)

CODEX = RouteSpec(
    program="codex",
    aliases={"--cd": "-C", "--sandbox": "-s", "--config": "-c", "--model": "-m", "--image": "-i"},
    required={
        "-C": _some,  # equality with cwd is judged in check()
        "-s": _eq("read-only"),
        "--ephemeral": _none,
        "--ignore-user-config": _none,
        "--ignore-rules": _none,
    },
    optional={
        "--skip-git-repo-check": _none,
        "-m": _model,
        "-i": _bare_png,
        "--output-schema": _eq(CA.SCHEMA_FILE),
        "--json": _none,
    },
    forbidden={
        # r4.3 (a)
        "--full-auto": 0,
        # same class of risk, from `codex exec --help` 0.163.0
        "--dangerously-bypass-approvals-and-sandbox": 0, "--dangerously-bypass-hook-trust": 0,
        "--yolo": 0, "--add-dir": 1, "--oss": 0, "--local-provider": 1, "-p": 1, "--profile": 1,
        "--approve-for-me": 0, "--worktree": 0, "--enable": 1, "--disable": 1,
        "--cyber-access-program": 1, "--strict-config": 0, "--thread-source": 1,
    },
    config_required={"mcp_servers": "mcp_servers={}"},
    config_optional={"model_reasoning_effort": lambda v: v is not None and v.partition("=")[2] in CA.CODEX_EFFORTS},
    leading=("exec",),
)

SPECS = {"claude": CLAUDE, "codex": CODEX}
FLOORS = {"claude": CLAUDE_MIN_VERSION, "codex": CODEX_MIN_VERSION}


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Result:
    route: str
    checks: tuple[Check, ...]
    cli_version: str | None
    argv_sha: str
    effective_config_sha: str
    cloud_settings_sha: str | None
    cwd: str

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]

    def receipt(self, now: datetime | None = None) -> dict[str, Any]:
        at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        return {
            "schema": RECEIPT_SCHEMA,
            "route": self.route,
            "cli_version": self.cli_version or UNKNOWN_VERSION,
            "effective_config_sha": self.effective_config_sha,
            "argv_sha": self.argv_sha,
            "cloud_settings_sha": self.cloud_settings_sha,
            "at": at,
            "cwd": self.cwd,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "cloud_isolation": "PASS" if self.ok else "FAIL",
        }


# --------------------------------------------------------------------------- helpers


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def version_at_least(version: str | None, floor: str) -> bool:
    got, need = parse_version(version or ""), parse_version(floor)
    return got is not None and need is not None and got >= need


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def argv_sha(argv: Iterable[str]) -> str:
    return _sha_bytes(_canonical(list(argv)))


def _realpath(p: str | os.PathLike) -> Path:
    return Path(os.path.realpath(os.fspath(p)))


def _spec(route: str) -> RouteSpec:
    if route not in SPECS:
        raise ValueError(f"unknown route {route!r}")
    return SPECS[route]


def _looks_like_flag(token: str) -> bool:
    return token.startswith("-") and len(token) > 1


@dataclass
class _Parsed:
    occurrences: list[tuple[str, str | None]] = field(default_factory=list)  # (canonical flag, value)
    unknown: list[str] = field(default_factory=list)
    positionals: list[str] = field(default_factory=list)


def _parse(spec: RouteSpec, tokens: list[str]) -> _Parsed:
    arity: dict[str, int] = {}
    for table in (spec.required, spec.optional):
        for f, rule in table.items():
            arity[f] = 0 if rule is _none else 1
    arity.update(spec.forbidden)
    if spec.config_required or spec.config_optional:
        arity["-c"] = 1
    out = _Parsed()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not _looks_like_flag(tok):
            out.positionals.append(tok)
            continue
        if tok.startswith("--"):
            name, eq, inline = tok.partition("=")
        else:
            name, eq, inline = tok, "", ""
        canon = spec.aliases.get(name, name)
        if canon not in arity:
            out.unknown.append(name)
            continue
        value: str | None = None
        if eq:
            value = inline
        elif arity[canon] == 1:
            if i < len(tokens) and not _looks_like_flag(tokens[i]):
                value = tokens[i]
                i += 1
        out.occurrences.append((canon, value))
    return out


def manifest_shas(project: str | os.PathLike) -> frozenset[str]:
    """der_sha256 of ``<project>/_masked/MANIFEST.jsonl`` plus png shas of the synthetic fixtures."""
    found: set[str] = set()
    masked = Path(project) / "_masked" / "MANIFEST.jsonl"
    try:
        lines = masked.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        sha = entry.get("der_sha256") if isinstance(entry, dict) else None
        if isinstance(sha, str) and _SHA_RE.fullmatch(sha):
            found.add(sha)
    try:
        manifest = json.loads(FIXTURES_MANIFEST.read_text(encoding="utf-8"))
        files = manifest.get("files", []) if isinstance(manifest, dict) else []
    except (OSError, UnicodeDecodeError, ValueError):
        files = []
    for entry in files:
        if isinstance(entry, dict) and str(entry.get("path", "")).endswith(".png"):
            sha = entry.get("sha256")
            if isinstance(sha, str) and _SHA_RE.fullmatch(sha):
                found.add(sha)
    return frozenset(found)


def read_cli_version(argv0: str, path_env: str) -> tuple[str | None, str]:
    """``<argv0> --version`` resolved on ``path_env``; (``"x.y.z"`` or None, detail)."""
    exe = shutil.which(argv0, path=path_env)
    if exe is None:
        return None, f"{argv0} not found on the invocation PATH"
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, timeout=VERSION_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{exe} --version failed: {exc}"
    if proc.returncode != 0:
        return None, f"{exe} --version exited {proc.returncode}"
    parsed = parse_version(proc.stdout.decode("utf-8", "replace"))
    if parsed is None:
        return None, f"{exe} --version printed no x.y.z version"
    return ".".join(map(str, parsed)), exe


def _settings_target(spec: RouteSpec, parsed: _Parsed, cwd: Path) -> Path | None:
    if spec is not CLAUDE:
        return None
    values = [v for f, v in parsed.occurrences if f == "--settings"]
    if len(values) != 1 or not values[0]:
        return None
    p = Path(values[0])
    return _realpath(p if p.is_absolute() else cwd / p)


def _file_sha(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > SETTINGS_MAX_BYTES:
            return None
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def effective_config_sha(inv: CA.Invocation) -> str:
    spec = _spec(inv.route)
    cwd = _realpath(inv.cwd)
    parsed = _parse(spec, list(inv.argv[1:]))
    data = {
        "route": inv.route,
        "cwd": str(cwd),
        "env": {str(k): str(v) for k, v in sorted(inv.env.items())},
        "settings_sha": _file_sha(_settings_target(spec, parsed, cwd)),
        "system_prompt_sha": _sha_bytes(CA.OCR_PROMPT.read_bytes()),
        "output_schema_sha": _sha_bytes(CA.OUTPUT_SCHEMA.read_bytes()),
    }
    return _sha_bytes(_canonical(data))


def binding(inv: CA.Invocation) -> dict[str, str]:
    """The three values a receipt is bound to, computed for this invocation now."""
    version, _ = read_cli_version(inv.argv[0] if inv.argv else "", inv.env.get("PATH", ""))
    return {
        "cli_version": version or UNKNOWN_VERSION,
        "effective_config_sha": effective_config_sha(inv),
        "argv_sha": argv_sha(inv.argv),
    }


def receipt_still_valid(receipt: Any, env: Mapping[str, Any]) -> bool:
    """True only for a schema-valid PASS receipt whose binding equals ``env`` on all three keys."""
    if not isinstance(receipt, Mapping) or not isinstance(env, Mapping):
        return False
    if issues("isolation_receipt", dict(receipt)):
        return False
    if receipt.get("cloud_isolation") != "PASS" or not all(c.get("ok") is True for c in receipt["checks"]):
        return False
    return all(receipt.get(k) == env.get(k) for k in BINDING_KEYS)


# --------------------------------------------------------------------------- the review


def _argv_checks(spec: RouteSpec, argv: list[str], cwd: Path, parsed: _Parsed) -> list[Check]:
    checks: list[Check] = []
    for flag, rule in spec.required.items():
        values = [v for f, v in parsed.occurrences if f == flag]
        ok = len(values) == 1 and rule(values[0])
        if ok and flag == "-C":
            ok = _realpath(values[0]) == cwd  # type: ignore[arg-type]
        checks.append(Check(f"required:{flag}", ok, f"occurrences={values!r}"[:300]))
    config_values = [v or "" for f, v in parsed.occurrences if f == "-c"]
    for key, exact in spec.config_required.items():
        mine = [v for v in config_values if v.partition("=")[0] == key]
        checks.append(Check(f"required:-c {exact}", mine == [exact], f"occurrences={mine!r}"))
    for flag in spec.forbidden:
        hits = [t for t in argv[1:] if t == flag or (flag.startswith("--") and t.startswith(flag + "="))]
        checks.append(Check(f"forbidden:{flag}", not hits, f"found={hits!r}" if hits else ""))

    problems: list[str] = []
    if argv[0] != spec.program:
        problems.append(f"program {argv[0]!r} is not {spec.program!r}")
    if parsed.unknown:
        problems.append(f"unknown flags {parsed.unknown!r}")
    for flag, rule in spec.optional.items():
        values = [v for f, v in parsed.occurrences if f == flag]
        if len(values) > 1 or (values and not rule(values[0])):
            problems.append(f"{flag} {values!r}"[:200])
    for value in config_values:
        key = value.partition("=")[0]
        if key in spec.config_required:
            continue
        rule = spec.config_optional.get(key)
        if rule is None or not rule(value) or sum(v.partition("=")[0] == key for v in config_values) > 1:
            problems.append(f"-c {value!r}"[:200])
    expected_prompt = _read_text(cwd / CA.PROMPT_FILE)
    if parsed.positionals != [*spec.leading, expected_prompt] or (spec.leading and argv[1:2] != list(spec.leading)):
        problems.append(f"positionals {[p[:60] for p in parsed.positionals]!r}")
    checks.append(Check("argv:closed", not problems, "; ".join(problems)))
    return checks


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _cwd_checks(cwd: Path, project: Path, approved: frozenset[str]) -> list[Check]:
    checks: list[Check] = []
    inside = cwd == project or cwd.is_relative_to(project) or project.is_relative_to(cwd)
    checks.append(Check("cwd:outside_project", not inside, f"cwd={cwd} project={project}"))

    systmp = _realpath(tempfile.gettempdir())
    in_tmp = cwd.parent == systmp and cwd.name.startswith(CA.SEND_PREFIX) and cwd.is_dir()
    checks.append(Check("cwd:system_tmp", in_tmp, f"cwd={cwd} tmp={systmp}"))

    try:
        st = os.stat(cwd)
        mode_ok = stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700 and st.st_uid == os.getuid()
        mode_detail = f"mode={oct(stat.S_IMODE(st.st_mode))}"
    except OSError as exc:
        mode_ok, mode_detail = False, str(exc)
    checks.append(Check("cwd:mode_0700", mode_ok, mode_detail))

    try:
        names = sorted(os.listdir(cwd))
    except OSError:
        names = []
    pngs = [n for n in names if n.endswith(".png")]
    expected_rest = {CA.PROMPT_FILE, CA.SCHEMA_FILE}
    contents_ok = (
        len(pngs) == 1 and set(names) == {*pngs, *expected_rest}
        and all(stat.S_ISREG(os.lstat(cwd / n).st_mode) for n in names)
        and _read_text(cwd / CA.SCHEMA_FILE) == CA.output_schema_text()
        and _read_text(cwd / CA.PROMPT_FILE) == CA.user_prompt(pngs[0].removesuffix(".png"))
    )
    checks.append(Check("tmpdir:contents", contents_ok, f"files={names!r}"[:300]))

    png_ok, png_detail = False, f"pngs={pngs!r}"
    if len(pngs) == 1:
        stem = pngs[0].removesuffix(".png")
        try:
            actual = _sha_bytes((cwd / pngs[0]).read_bytes())
        except OSError:
            actual = ""
        png_ok = actual == stem and stem in approved
        png_detail = f"name={stem} content={actual} approved={stem in approved}"
    checks.append(Check("tmpdir:png_approved", png_ok, png_detail))
    return checks


def check(inv: CA.Invocation, *, project: str | os.PathLike,
          approved_shas: frozenset[str] | None = None) -> Result:
    """Review ``inv`` against r4.3 (a). Never raises for a malformed invocation: it fails instead."""
    spec = _spec(inv.route)
    argv = [str(t) for t in inv.argv] or [""]
    cwd = _realpath(inv.cwd)
    proj = _realpath(project)
    approved = manifest_shas(proj) if approved_shas is None else frozenset(approved_shas)
    parsed = _parse(spec, argv[1:])

    checks = _argv_checks(spec, argv, cwd, parsed)
    settings_sha: str | None = None
    if spec is CLAUDE:
        settings_sha = _file_sha(_settings_target(spec, parsed, cwd))
        checks.append(Check("settings:pinned", settings_sha == CLOUD_SETTINGS_SHA256, f"sha={settings_sha}"))
        guard = shutil.which(GUARD_COMMAND, path=inv.env.get("PATH", ""))
        launcher = _realpath(CA.PLUGIN_BIN / GUARD_COMMAND)
        checks.append(Check("guard:on_path", guard is not None and _realpath(guard) == launcher, f"resolved={guard}"))
    checks += _cwd_checks(cwd, proj, approved)

    extra = sorted(set(inv.env) - CLOUD_ENV_ALLOWLIST)
    checks.append(Check("env:allowlist", not extra, f"not allowed: {extra!r}" if extra else ""))

    version, detail = read_cli_version(argv[0], inv.env.get("PATH", ""))
    floor = FLOORS[inv.route]
    checks.append(Check("cli:version", version_at_least(version, floor), f"version={version} floor={floor} {detail}"))

    return Result(
        route=inv.route,
        checks=tuple(checks),
        cli_version=version,
        argv_sha=argv_sha(argv),
        effective_config_sha=effective_config_sha(inv),
        cloud_settings_sha=settings_sha,
        cwd=str(cwd),
    )
