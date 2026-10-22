"""Cloud read-image invocations: argv, cwd, env and the send directory (WI-00c1; design 02 r3.5, r4.3 (a)).

Nothing here runs a model. ``claude()`` / ``codex()`` only describe the process that a sender
(WI-05b / 06a / 06b) is about to start; ``isolation_check.check()`` reviews exactly that
description before anything is sent.

Send directory (r3.5): ``tempfile.mkdtemp(prefix="klode-kit-send-", dir=<system TMPDIR>)`` with
mode 0700, holding exactly ``<der_sha256>.png``, ``prompt.txt`` and ``schema.json``. It is the
cloud process's cwd, outside any data project. The caller removes it after the call (05b).

Claude route (r4.3 (a); flags checked against ``claude --help`` 2.1.285 and
https://code.claude.com/docs/en/cli-reference):

    claude -p <user prompt> --restricted --tools Read --strict-mcp-config
           --permission-mode dontAsk --permission-prompts none --setting-sources ""
           --settings <plugin>/src/klode_kit/config/cloud_settings.json --no-session-persistence
           --system-prompt <prompts/ocr_read.md> --disable-slash-commands
           --model <m> --effort <e> --output-format json --json-schema <schema> --max-budget-usd <x>

- ``--restricted``: removes command/code tools and WebFetch, confines file tools to the working
  directories, loads only managed settings and ``--settings`` (v2.1.248+).
- ``--permission-prompts none``: anything that would prompt is denied (v2.1.259+; hence the
  version floor in ``isolation_check``).
- ``--tools`` is variadic in the CLI, so the user prompt is placed right after ``-p`` and every
  ``--tools`` value is followed by another flag.
- Whether ``--setting-sources ""`` is accepted and whether hooks from ``--settings`` fire under
  ``--restricted`` are open items (02 r3.7 (12), r4.7 (14)) settled by the WI-00c2 spike.

Codex route (r4.3 (a); ``codex exec --help`` 0.163.0; WI-00c2c, user decision 2026-10-23 plan A):

    codex exec -C <send dir> --ephemeral --ignore-user-config --ignore-rules
               -c mcp_servers={} <CODEX_PROFILE_CONFIG: -c pairs, see below>
               --skip-git-repo-check -m <m> -c model_reasoning_effort=<e>
               -i <der_sha256>.png --output-schema schema.json --json <user prompt>

``--skip-git-repo-check`` is needed because the send directory is not a git repository.

No ``-s`` / ``--sandbox``: ``-s read-only`` let model commands read the data project and ``$HOME``
(WI-00c2 F3), and a legacy sandbox flag makes Codex ignore ``default_permissions`` ("If ... you pass
--sandbox ... Codex uses those older sandbox settings instead of default_permissions",
https://developers.openai.com/codex/permissions , saved copy canonical
https://learn.chatgpt.com/docs/permissions). Instead a named permission profile ``klode-kit-ocr`` is
defined and selected on the command line (exact spelling measured in the WI-00c2b spike,
``work/isolation/wi-00c2b-discovery-20261022T225300Z/``):

- ``default_permissions="klode-kit-ocr"`` - "Name of the default permissions profile to apply to
  sandboxed tool calls ... custom profile names require matching [permissions.<name>] tables"
  (https://developers.openai.com/codex/config-reference);
- ``permissions.klode-kit-ocr.filesystem={":minimal"="read",":workspace_roots"={"."="read"}}`` -
  ``:minimal`` = "Platform and runtime paths needed by common tools", ``:workspace_roots`` = "The current
  session's workspace roots" (here only ``-C``, the send dir), "." = the root itself (permissions page);
  nothing else is readable, nothing is writable;
- ``permissions.klode-kit-ocr.network.enabled=false`` - "Enables network access for commands in the
  profile" (default false; set explicitly) (permissions page).

Model-visible extras switched off (user decision 2026-10-23):

- ``skills.include_instructions=false`` - WI-00c2b found that Codex sends the ``~/.codex/skills``
  catalogue (private skill descriptions) to the model and ``--ignore-user-config`` does not stop it; with
  this key the ``<skills_instructions>`` block is gone from the captured request. The key is not in the
  saved config-reference (2026-10-23): evidence is the spike's request capture only;
- ``features.apps=false`` - "Enable app (connector) integrations (stable; on by default)"
  (config-reference);
- ``agents.enabled=false`` - "Enable or disable multi-agent tools (default: true)" (config-reference);
  the spike capture loses the ``collaboration.*`` tools with it;
- ``features.view_image=false`` / ``features.image_generation=false`` - local-image attachment tool and
  image generation; not under these names in the saved config-reference (it lists ``tools.view_image``):
  spike capture only. The page image reaches the model through ``-i``, not through a tool;
- ``web_search="disabled"`` - "Use ... "disabled" to remove the tool" (config-reference);
- ``allow_login_shell=false`` - "when false, login = true requests are rejected and omitted login
  defaults to non-login shells" (config-reference): no ``zsh -l`` sourcing of the user's profile.

Environment: only the explicit allowlist ``CLOUD_ENV_ALLOWLIST`` (r5), taken from the source
environment, with the plugin's ``bin/`` put first on ``PATH`` so that the exec-form hook command
``klode-kit hook isolation-guard`` pinned in ``cloud_settings.json`` resolves to this plugin.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from klode_kit.config import CLOUD_ENV_ALLOWLIST

PACKAGE = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PACKAGE.parents[1]
PLUGIN_BIN = PLUGIN_ROOT / "bin"
CLOUD_SETTINGS = PACKAGE / "config" / "cloud_settings.json"
OCR_PROMPT = PACKAGE / "prompts" / "ocr_read.md"
OUTPUT_SCHEMA = PACKAGE / "schemas" / "ocr_cloud_output.json"

SEND_PREFIX = "klode-kit-send-"
CODEX_PERMISSION_PROFILE = "klode-kit-ocr"
CODEX_PROFILE_CONFIG = (
    f'default_permissions="{CODEX_PERMISSION_PROFILE}"',
    f'permissions.{CODEX_PERMISSION_PROFILE}.filesystem={{":minimal"="read",":workspace_roots"={{"."="read"}}}}',
    f"permissions.{CODEX_PERMISSION_PROFILE}.network.enabled=false",
    "skills.include_instructions=false",
    "features.apps=false",
    "agents.enabled=false",
    "features.view_image=false",
    "features.image_generation=false",
    'web_search="disabled"',
    "allow_login_shell=false",
)
PROMPT_FILE = "prompt.txt"
SCHEMA_FILE = "schema.json"

CLAUDE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
CODEX_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,127}")
SHA_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Invocation:
    """One cloud process as it is about to be started: ``argv[0]`` is resolved on ``env['PATH']``."""

    route: str
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)


def system_prompt() -> str:
    return OCR_PROMPT.read_text(encoding="utf-8")


def output_schema_text() -> str:
    return OUTPUT_SCHEMA.read_text(encoding="utf-8")


def user_prompt(der_sha256: str) -> str:
    """The per-image instruction: names the image file only, never a path."""
    return f"Transcribe the image file {der_sha256}.png in the current directory as instructed. Reply with JSON only."


def prepare_send_dir(png: bytes, der_sha256: str) -> Path:
    """Fresh 0700 send directory under the system TMPDIR with exactly png + prompt.txt + schema.json.

    The png must hash to ``der_sha256``; otherwise ``ValueError`` and nothing is created. Whether
    the sha is approved for sending (MANIFEST) is judged by ``isolation_check``, not here.
    """
    if not isinstance(der_sha256, str) or not SHA_RE.fullmatch(der_sha256):
        raise ValueError(f"der_sha256 must be 64 lowercase hex characters, got {der_sha256!r}")
    data = bytes(png)
    actual = hashlib.sha256(data).hexdigest()
    if actual != der_sha256:
        raise ValueError(f"png sha256 {actual} does not match der_sha256 {der_sha256}")
    send = Path(tempfile.mkdtemp(prefix=SEND_PREFIX, dir=tempfile.gettempdir()))
    os.chmod(send, 0o700)
    _write_new(send / f"{der_sha256}.png", data)
    _write_new(send / PROMPT_FILE, user_prompt(der_sha256).encode("utf-8"))
    _write_new(send / SCHEMA_FILE, output_schema_text().encode("utf-8"))
    return send


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def cloud_env(source_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Allowlisted keys only (r5); the plugin ``bin/`` first on PATH for the pinned guard hook."""
    source = os.environ if source_env is None else source_env
    env = {k: str(source[k]) for k in sorted(CLOUD_ENV_ALLOWLIST) if k in source}
    rest = [p for p in env.get("PATH", "").split(os.pathsep) if p and p != str(PLUGIN_BIN)]
    env["PATH"] = os.pathsep.join([str(PLUGIN_BIN), *rest])
    return env


def _model(model: str) -> str:
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise ValueError(f"model must match {MODEL_RE.pattern}, got {model!r}")
    return model


def _effort(effort: str, allowed: frozenset[str]) -> str:
    if effort not in allowed:
        raise ValueError(f"effort must be one of {sorted(allowed)}, got {effort!r}")
    return effort


def _image_sha(send_dir: Path) -> str:
    pngs = sorted(p.stem for p in Path(send_dir).glob("*.png"))
    if len(pngs) != 1 or not SHA_RE.fullmatch(pngs[0]):
        raise ValueError(f"send dir {send_dir} must hold exactly one <der_sha256>.png, found {pngs}")
    return pngs[0]


def claude(send_dir: str | os.PathLike, *, model: str, effort: str, max_budget_usd: float,
           source_env: Mapping[str, str] | None = None) -> Invocation:
    send = Path(send_dir)
    sha = _image_sha(send)
    budget = float(max_budget_usd)
    if not budget > 0 or budget != budget or budget == float("inf"):
        raise ValueError(f"max_budget_usd must be a positive finite number, got {max_budget_usd!r}")
    argv = (
        "claude", "-p", user_prompt(sha),
        "--restricted",
        "--tools", "Read",
        "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--setting-sources", "",
        "--settings", str(CLOUD_SETTINGS),
        "--no-session-persistence",
        "--system-prompt", system_prompt(),
        "--disable-slash-commands",
        "--model", _model(model),
        "--effort", _effort(effort, CLAUDE_EFFORTS),
        "--output-format", "json",
        "--json-schema", output_schema_text(),
        "--max-budget-usd", repr(budget),
    )
    return Invocation("claude", argv, send, cloud_env(source_env))


def codex(send_dir: str | os.PathLike, *, model: str, effort: str,
          source_env: Mapping[str, str] | None = None) -> Invocation:
    send = Path(send_dir)
    sha = _image_sha(send)
    argv = (
        "codex", "exec",
        "-C", str(send),
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-c", "mcp_servers={}",
        *(tok for value in CODEX_PROFILE_CONFIG for tok in ("-c", value)),
        "--skip-git-repo-check",
        "-m", _model(model),
        "-c", f"model_reasoning_effort={_effort(effort, CODEX_EFFORTS)}",
        "-i", f"{sha}.png",
        "--output-schema", SCHEMA_FILE,
        "--json",
        user_prompt(sha),
    )
    return Invocation("codex", argv, send, cloud_env(source_env))
