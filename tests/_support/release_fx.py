"""WI-15b fixtures: a tiny but complete "release checkout" in a tmp directory.

``make_root`` writes one file under every glob of the release definition (R3-08), a ``.gitignore`` that ignores
``work/`` and ``.venv/``, the committed ``RELEASE.sha256`` and makes one git commit. ``stamp`` then writes what a
release run leaves behind (five lane receipts, the install fingerprint, the mutation summary) in the ignored places,
so the checkout stays clean. Nothing here calls the code under test except ``release_manifest`` (the single
definition of the sha) - receipts are plain dictionaries so a stale or forged one can be built by hand.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

LANES = ("unit", "contract_replay", "cli_integration", "model_integration", "host_acceptance")
NOW = dt.datetime(2026, 10, 27, 12, 0, 0, tzinfo=dt.timezone.utc)

FILES = {
    ".claude-plugin/plugin.json": '{"name": "klode-kit", "version": "9.9.9"}\n',
    "scripts/install.sh": "#!/bin/sh\nexit 0\n",
    "pyproject.toml": "[project]\nname = 'x'\n",
    "uv.lock": "version = 1\n",
    "src/klode_kit/__init__.py": "x = 1\n",
    "src/klode_kit/templates/agent.md": "template one\n",
    "src/klode_kit/config/defaults.toml": "a = 1\n",
    "src/klode_kit/prompts/write_card.md": "prompt\n",
    "src/klode_kit/schemas/receipt.json": "{}\n",
    "hooks/hooks.json": "{}\n",
    "workflows/kb-pipeline.js": "export const meta = {}\n",
    "agents/writer.md": "---\nname: writer\n---\n",
    "skills/kb-check/SKILL.md": "---\nname: kb-check\n---\n",
    "bin/klode-kit": "#!/bin/sh\nexit 0\n",
    "README.md": "not a run file\n",
    "tests/test_x.py": "def test_x(): pass\n",
    ".gitignore": "work/\n.venv/\n__pycache__/\n",
}


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True, check=True)


def write_files(root: Path, files: dict[str, str] | None = None) -> None:
    for rel, text in (files or FILES).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def iso(when: dt.datetime) -> str:
    return when.isoformat(timespec="seconds")


def make_root(base: Path, name: str = "repo", *, commit: bool = True) -> Path:
    from klode_kit.devtools import release_manifest as M

    root = base / name
    root.mkdir(parents=True)
    write_files(root)
    M.write(root)
    if commit:
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "release")
    return root


def lane_receipt(root: Path, lane: str, *, now: dt.datetime = NOW, age: dt.timedelta = dt.timedelta(minutes=5),
                 sha: str | None = None, python: str | None = None, **over) -> dict:
    from klode_kit.devtools import release_manifest as M

    finished = now - age
    doc = {
        "schema_version": 1, "lane": lane, "status": "PASS", "bootstrap": False,
        "tests_discovered": 3, "tests_failed": 0, "tests_skipped": 0, "pytest_exit_code": 0,
        "started_at": iso(finished - dt.timedelta(minutes=1)), "finished_at": iso(finished),
        "command": ["/usr/bin/python3", "-m", "pytest", "-m", lane],
        "env_fingerprint": {
            "release_sha": "a" * 40,
            "repo_root": os.path.realpath(root),
            "python": python or os.path.realpath(sys.executable),
            "python_version": "3.12.0",
            "platform": "darwin-arm64",
            "code_tree_sha": sha or M.release_sha(root),
        },
        "tags": [],
    }
    doc.update(over)
    return doc


def put_receipt(root: Path, doc: dict) -> Path:
    path = root / "work" / "lanes" / f"{doc['lane']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def put_install(root: Path, **over) -> Path:
    doc = {
        "schema": "klode-kit.install/1", "klode_kit_version": "0.1.0", "klode_version": "0.5.0",
        "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "python_version": "3.12.0", "repo_root": os.path.realpath(root),
        "venv_python": os.path.realpath(sys.executable),
    }
    doc.update(over)
    path = root / ".venv" / "klode-kit-install.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def put_mutation(root: Path, *, score: float = 0.9, now: dt.datetime = NOW, age: dt.timedelta = dt.timedelta(minutes=5),
                 sha: str | None = None) -> Path:
    from klode_kit.devtools import release_manifest as M

    doc = {"schema": "klode-kit.mutation/1", "code_tree_sha": sha or M.release_sha(root), "score": score,
           "finished_at": iso(now - age)}
    path = root / "work" / "mutation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def stamp(root: Path, *, now: dt.datetime = NOW) -> None:
    """Everything a green release run leaves behind (all in ignored places)."""
    for lane in LANES:
        put_receipt(root, lane_receipt(root, lane, now=now))
    put_install(root)
    put_mutation(root, now=now)
