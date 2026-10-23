"""Host environment probes (WI-00d2): ``claude mcp get`` status parsing (⑮) and the workspace-trust probe (⑬).

``parse_mcp_get(text)`` - the text ``claude mcp get <name>`` prints (Claude Code 2.1.285, recordings in
tests/fixtures/host/mcp_get/) -> ``connected | pending | failed | missing | unknown``. Only the server-level
``  Status:`` line (two-space indent, before any ``Environment:`` block) is read, so the ``env`` entries of an
``.mcp.json`` server (the r4.2 ``KLODE_KIT_BUILD`` marker) never move the result (⑮). Anything unrecognised is
``unknown`` - never ``connected``.

``trusted(path)`` - ``True | False | "undetectable"``: whether the folder's workspace trust has been accepted, read
(never written) from the documented store ``projects["<key>"].hasTrustDialogAccepted`` in ``~/.claude.json``
(docs: permissions "Project allow rules and workspace trust", errors "workspace has not been trusted"):

- inside a git repository the key is the repository root (for a worktree, the main checkout's root); a
  repository nested in another one, such as a submodule, is its own root;
- outside a repository the key is the folder itself; a trusted parent folder does not count here, because R2-07
  needs ``.claude/settings.local.json`` approvals, which only the folder's own trust activates;
- the home directory's trust is never saved, so it is ``False``;
- a store that is missing or unreadable, or a ``CLAUDE_CONFIG_DIR`` override without an explicit store, gives
  ``"undetectable"``.

``mcp_get_status(name, cwd, claude)`` runs the real ``claude mcp get`` (no model call) and parses it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal, Mapping

McpStatus = Literal["connected", "pending", "failed", "missing", "unknown"]
Trust = bool | Literal["undetectable"]

_STATUS_LINE = re.compile(r"^  Status: (?P<value>.*)$")
_STATUS_WORDS: tuple[tuple[str, McpStatus], ...] = (
    ("Connected", "connected"),
    ("Pending approval", "pending"),
    ("Failed to connect", "failed"),
)


def parse_mcp_get(text: str) -> McpStatus:
    lines = text.splitlines()
    if any(ln.startswith("No MCP server named ") for ln in lines[:1]):
        return "missing"
    for ln in lines:
        if ln.strip() == "Environment:":
            break
        match = _STATUS_LINE.match(ln)
        if match:
            value = match.group("value")
            for word, status in _STATUS_WORDS:
                # the CLI prefixes a symbol (✔ ⏸ ✘); the word must follow it directly
                if re.match(r"^\S+ " + re.escape(word) + r"\b", value):
                    return status
            return "unknown"
    return "unknown"


def mcp_get_status(name: str, cwd: Path, claude: str = "claude", timeout: float = 120) -> tuple[McpStatus, str]:
    """Run ``claude mcp get <name>`` in ``cwd`` (connects the server, calls no model); ``(status, raw text)``."""
    proc = subprocess.run([claude, "mcp", "get", name], cwd=cwd, capture_output=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)
    text = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return parse_mcp_get(text), text


# --------------------------------------------------------------------------- trust (⑬)


def _trust_key(path: Path) -> Path:
    """Repository root (main checkout for a worktree) containing ``path``, or ``path`` outside any repository."""
    for candidate in (path, *path.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return candidate
        if dot_git.is_file():
            try:
                pointer = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return candidate
            target = pointer.partition("gitdir:")[2].strip()
            gitdir = Path(target) if os.path.isabs(target) else (candidate / target)
            parts = Path(os.path.realpath(gitdir)).parts
            if len(parts) >= 3 and parts[-2] == "worktrees" and parts[-3] == ".git":
                return Path(*parts[:-3])
            return candidate
    return path


def _load_store(claude_json: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("projects", {}), dict):
        return None
    return data.get("projects", {})


def trusted(path: str | os.PathLike, *, claude_json: str | os.PathLike | None = None,
            home: str | os.PathLike | None = None, env: Mapping[str, str] | None = None) -> Trust:
    env = os.environ if env is None else env
    home_dir = Path(os.path.realpath(home if home is not None else (env.get("HOME") or Path.home())))
    if claude_json is None:
        if env.get("CLAUDE_CONFIG_DIR"):
            return "undetectable"
        claude_json = home_dir / ".claude.json"
    projects = _load_store(Path(claude_json))
    if projects is None:
        return "undetectable"
    real = Path(os.path.realpath(path))
    if real == home_dir:
        return False
    entry = projects.get(str(_trust_key(real)))
    return isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True
