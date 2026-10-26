"""The registrar: the four registrations of a published build (WI-11b; design 02 r3.2 "外部登记", r4.2; extracted from
``publish.py`` where WI-09d put the first real version - ``publish.ACTION_IMPL`` is this module's ``ACTION_IMPL``).

    klode-kit register --kb kb/<id> --build ID [--project DIR]

==============  =====================================================================================================
``mcp_json``    ``<project>/.mcp.json``: only the key ``mcpServers.<kb>-klode`` is added or updated - ``command`` the
                absolute path of the launcher (``$KLODE_KIT_BIN`` or this repository's ``bin/klode-kit``), ``args``
                ``["mcp", "--kb", <abs kb>]``, ``env.KLODE_KIT_BUILD`` the build id (the marker); every other key of
                the file, and every other key of our own entry, stays
``registry``    ``$KLODE_REGISTRY`` (default: ``registry.toml`` in the ``.klode`` directory of ``HOME``): the ``[[kb]]`` entry of the id, upserted
                (``registry_write.upsert``) with ``path`` ``<kb>/current/library.toml`` and ``build_id``
``agent``       the build's ``agent/*.md`` into ``<project>/.claude/agents/`` (its ``<!-- klode-kit build ID -->`` line,
                after the frontmatter, is the marker)
``approve``     the server name in ``<project>/.claude/settings.local.json`` ``enabledMcpjsonServers`` (once)
==============  =====================================================================================================

Every action reads, changes only its own keys and writes through a temporary file and one ``os.replace``; a file that
cannot be read (bad JSON / TOML, a key of the wrong type) is left byte for byte as it is and that action fails - the
others are independent and still run. ``publish`` calls the actions one by one (it adds the marker comparison between
builds and the log rows); this command calls all four and reports ``{action, status, detail}`` for each.

Paths: nothing is resolved from the process's home when an environment is given. Tests run with ``HOME`` and
``KLODE_REGISTRY`` in tmp (gate G3: writing the real ``.mcp.json`` / ``settings.local.json`` of a real data project, the
real klode registry in the home directory or the user-level Claude configuration is the user's manual step).

Exit codes: 0 all four done, 2 usage, 40 an action failed (a file left as it is). stdout: one JSON line
``klode-kit.register/1``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from klode_kit import registry_write as RW
from klode_kit.pipeline import EXIT_CODE, Reason, default_launcher
from klode_kit.registry_write import BUILD_RE, ActionError, atomic_write, marker_of

SCHEMA = "klode-kit.register/1"
ACTIONS = ("mcp_json", "registry", "agent", "approve")
MARKER_RE = re.compile(r"<!--\s*klode-kit build (b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8})\s*-->")


@dataclass(frozen=True)
class Target:
    kb: Path
    project: Path
    build_id: str
    env: Mapping[str, str]

    @property
    def server(self) -> str:
        return f"{self.kb.name}-klode"

    @property
    def build(self) -> Path:
        return self.kb / "builds" / self.build_id


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ActionError(f"{path.name} cannot be read as JSON ({exc}); left as it is") from exc
    if not isinstance(doc, dict):
        raise ActionError(f"{path.name} is not a JSON object; left as it is")
    return doc


def _dump_json(doc: Mapping) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def launcher(t: Target) -> str:
    return t.env.get("KLODE_KIT_BIN") or str(default_launcher())


# mcp_json ---------------------------------------------------------------------------------------------------


def _mcp_marker(t: Target) -> str | None:
    doc = _read_json_object(t.project / ".mcp.json")
    servers = doc.get("mcpServers", {})
    entry = servers.get(t.server) if isinstance(servers, dict) else None
    env = entry.get("env") if isinstance(entry, dict) else None
    return marker_of(env.get("KLODE_KIT_BUILD")) if isinstance(env, dict) else None


def _mcp_apply(t: Target) -> str:
    path = t.project / ".mcp.json"
    doc = _read_json_object(path)
    servers = doc.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ActionError(".mcp.json: mcpServers is not an object; left as it is")
    old = servers.get(t.server) if isinstance(servers.get(t.server), dict) else {}
    old_env = old.get("env") if isinstance(old.get("env"), dict) else {}
    servers[t.server] = {**old, "command": launcher(t), "args": ["mcp", "--kb", os.path.abspath(t.kb)],
                         "env": {**old_env, "KLODE_KIT_BUILD": t.build_id}}
    atomic_write(path, _dump_json(doc))
    return f"{t.server} -> build {t.build_id}"


# registry ---------------------------------------------------------------------------------------------------


def _registry_marker(t: Target) -> str | None:
    return RW.marker(RW.registry_path(t.env), t.kb.name)


def _registry_apply(t: Target) -> str:
    return RW.upsert(RW.registry_path(t.env), t.kb.name,
                     os.path.abspath(t.kb / "current" / "library.toml"), t.build_id)


# agent ------------------------------------------------------------------------------------------------------


def _agent_files(t: Target) -> list[Path]:
    files = sorted((t.build / "agent").glob("*.md")) if (t.build / "agent").is_dir() else []
    if not files:
        raise ActionError(f"builds/{t.build_id}/agent holds no agent file")
    return files


def _agent_marker(t: Target) -> str | None:
    dest = t.project / ".claude" / "agents" / _agent_files(t)[0].name
    if not dest.is_file():
        return None
    head = "".join(dest.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[:40])
    m = MARKER_RE.search(head)
    return m.group(1) if m else None


def _agent_apply(t: Target) -> str:
    names = []
    for src in _agent_files(t):
        atomic_write(t.project / ".claude" / "agents" / src.name, src.read_bytes())
        names.append(src.name)
    return ", ".join(names)


# approve ----------------------------------------------------------------------------------------------------


def _approve_apply(t: Target) -> str:
    path = t.project / ".claude" / "settings.local.json"
    doc = _read_json_object(path)
    enabled = doc.setdefault("enabledMcpjsonServers", [])
    if not isinstance(enabled, list):
        raise ActionError("settings.local.json: enabledMcpjsonServers is not a list; left as it is")
    if t.server in enabled:
        return f"{t.server} already enabled"
    enabled.append(t.server)
    atomic_write(path, _dump_json(doc))
    return f"{t.server} enabled"


# (marker reader or None, applier) per action - the seam ``publish`` runs its registrations through
ACTION_IMPL: dict[str, tuple[Callable[[Target], str | None] | None, Callable[[Target], str]]] = {
    "mcp_json": (_mcp_marker, _mcp_apply),
    "registry": (_registry_marker, _registry_apply),
    "agent": (_agent_marker, _agent_apply),
    "approve": (None, _approve_apply),
}


# --------------------------------------------------------------------------- the command


def apply_all(t: Target) -> list[dict[str, str]]:
    """All four actions, each on its own: a failing one is reported and the others still run."""
    done = []
    for name in ACTIONS:
        try:
            detail, status = ACTION_IMPL[name][1](t) or "", "done"
        except (ActionError, OSError) as exc:
            detail, status = str(exc), "failed"
        done.append({"action": name, "status": status, "detail": detail})
    return done


def _project_of(kb: Path, project: str | os.PathLike | None) -> Path | None:
    if project is not None:
        return Path(project)
    return kb.parent.parent if kb.parent.name == "kb" else None


def _body(kb: Path, build_id: str | None, reason: Reason, **extra: Any) -> dict:
    return {"schema": SCHEMA, "kb_id": kb.name, "build_id": build_id, "exit_code": EXIT_CODE[reason],
            "reason": reason.value, **extra}


def run(kb: str | os.PathLike, build_id: str, project: str | os.PathLike | None = None,
        env: Mapping[str, str] | None = None) -> tuple[int, dict]:
    kb = Path(kb)
    if not BUILD_RE.fullmatch(build_id or ""):
        return EXIT_CODE[Reason.usage], _body(kb, build_id, Reason.usage, detail=f"{build_id!r} is not a build id")
    proj = _project_of(kb, project)
    if proj is None:
        return EXIT_CODE[Reason.usage], _body(kb, build_id, Reason.usage,
                                              detail="no --project and the kb is not below <project>/kb/")
    t = Target(kb, proj, build_id, dict(os.environ if env is None else env))
    actions = apply_all(t)
    reason = Reason.config if any(a["status"] == "failed" for a in actions) else Reason.ok
    return EXIT_CODE[reason], _body(kb, build_id, reason, project=str(proj), actions=actions)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="klode-kit register", description="register a published build")
    parser.add_argument("--kb", type=Path, required=True, help="kb root (kb/<id>)")
    parser.add_argument("--build", required=True, help="build id")
    parser.add_argument("--project", type=Path, default=None, help="data project (default: the directory above kb/)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    code, body = run(args.kb, args.build, project=args.project)
    sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return code
