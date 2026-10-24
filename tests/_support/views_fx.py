"""Helpers for the view tests (WI-08d; matrix 08d-*): run the real ``views`` command and read the views back.

Views (design 02 R2-10, r4.1, R4-01): ``records/<date>-<type>[-k].md`` and ``verified/<date>-<type>[-k].md`` are
symbolic links to revision bodies ``records/_rev/<rec_id>.r<N>.md``; a link name is read with ``os.readlink`` and
a view's content through the link (JSON front matter, as every record file).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from _support import writeback_fx as W
from _support.flows import base_env


def links(project: Path) -> dict[str, str]:
    """``{"records/<name>": target, "verified/<name>": target}`` of every symbolic link of the two view
    directories (top level only)."""
    out = {}
    for sub in ("records", "verified"):
        d = project / sub
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_symlink():
                    out[f"{sub}/{p.name}"] = os.readlink(p)
    return out


def views_cli(project: Path, *extra: str, timeout: float = 120) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "klode_kit", "views", "--project", str(project), *extra]
    return subprocess.run(argv, env=base_env(), capture_output=True, timeout=timeout)


def views_ok(project: Path) -> None:
    res = views_cli(project)
    assert res.returncode == 0, res.stderr.decode()


def views_inproc(project: Path) -> int:
    from klode_kit.audit import views as V

    return V.main(["--project", str(project)], out=io.StringIO(), stdout=io.StringIO())


def view(project: Path, rel: str) -> dict:
    """The front matter read through a view link."""
    return W._front(project / rel)


def recs_by_key(project: Path, fx: Path, adir: Path) -> dict[str, str]:
    """``{page key: rec_id}`` of the candidates of ``adir`` (one record per page in these fixtures)."""
    pages = {p["page_id"]: p["key"] for p in json.loads((fx / "scenario.json").read_text(encoding="utf-8"))["pages"]}
    return {pages[c["page_id"]]: c["rec_id"] for c in W.candidates(adir)}


def body_link(rec_id: str, rev: int) -> str:
    return f"_rev/{rec_id}.r{rev}.md"


def verified_link(rec_id: str, rev: int) -> str:
    return f"../records/_rev/{rec_id}.r{rev}.md"


def dangling(project: Path) -> list[str]:
    return [k for k in links(project) if not (project / k).exists()]
