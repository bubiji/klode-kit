"""WI-00d2: host environment probes, deterministic part (matrix 00d2-08; ⑬ trust probe logic; ⑮).

``hostenv.parse_mcp_get(text)`` maps the text of ``claude mcp get <name>`` to one of
``connected | pending | failed | missing | unknown``. Inputs are real recordings (Claude Code 2.1.285,
tests/recordings/host/mcp_get/); the paired real path is 00d2-06 (host_acceptance).

``hostenv.trusted(path, claude_json=..., home=..., env=...)`` answers question ⑬ from the documented trust store
(docs: permissions "Project allow rules and workspace trust"; errors "workspace has not been trusted"):
``projects["<key>"].hasTrustDialogAccepted`` in ``~/.claude.json``, where the key is the git repository root
(the main checkout for a worktree) or, outside any repository, the folder itself. Trusting only a parent
folder does not count (R2-07 needs ``.claude/settings.local.json`` approvals, which a parent's trust does not
cover). The home directory's trust is never saved. Anything it cannot read is ``"undetectable"``.
Paired real path: 00d2-05.

Law (parser, ⑮ metamorphic, S6): adding, removing or rewording ``Environment:`` lines - including values that
look like status words - never changes the parsed status.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit import hostenv

pytestmark = pytest.mark.unit

REC = REPO / "tests" / "recordings" / "host" / "mcp_get"


def _rec(name: str) -> str:
    return (REC / f"{name}.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- parse_mcp_get (00d2-08)


@pytest.mark.parametrize("name, expected", [
    ("connected", "connected"),
    ("pending", "pending"),
    ("failed", "failed"),
    ("missing", "missing"),
])
def test_hostenv_parse_mcp_get_variants(name, expected):
    """00d2-08 (L1/S2): the recorded outputs parse to connected / pending / failed (/ missing)."""
    assert hostenv.parse_mcp_get(_rec(name)) == expected


@pytest.mark.parametrize("text", [
    "",
    "spike-klode:\n  Scope: Project config\n  Type: stdio\n",              # no Status line
    "spike-klode:\n  Status: ? Reticulating\n",                           # unknown wording
    "Connected\n",                                                         # bare word, not a Status line
])
def test_parse_unknown_is_never_connected(text):
    """S3: anything without a recognised Status line is ``unknown`` - never ``connected`` (fail closed)."""
    assert hostenv.parse_mcp_get(text) == "unknown"


def _with_env_lines(text: str, lines: list[str]) -> str:
    out = []
    for ln in text.splitlines():
        out.append(ln)
        if ln.strip() == "Environment:":
            out.extend(f"    {x}" for x in lines)
    return "\n".join(out) + "\n"


ENV_LINE = st.one_of(
    st.from_regex(r"\A[A-Z_]{1,12}=[ -~]{0,30}\Z"),
    st.sampled_from(["STATUS=✔ Connected", "X=Status: ✔ Connected", "Y=⏸ Pending approval", "Z=✘ Failed to connect",
                     "Status: ✔ Connected", "  Status: ✔ Connected"]),
)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.sampled_from(["connected", "pending", "failed"]), lines=st.lists(ENV_LINE, max_size=6))
def test_parse_env_lines_never_change_status(name, lines):
    """⑮ (S6 metamorphic): extra Environment entries - even ones that look like a Status - do not move the result."""
    base = hostenv.parse_mcp_get(_rec(name))
    assert hostenv.parse_mcp_get(_with_env_lines(_rec(name), lines)) == base


def test_parse_without_env_block_same_as_with():
    """⑮ (S6): dropping the Environment block entirely leaves the status unchanged."""
    for name in ("connected", "pending", "failed"):
        text = _rec(name)
        stripped = "\n".join(ln for ln in text.splitlines()
                             if ln.strip() != "Environment:" and not ln.startswith("    KLODE_KIT_BUILD="))
        assert hostenv.parse_mcp_get(stripped) == hostenv.parse_mcp_get(text)


# --------------------------------------------------------------------------- trusted (⑬)


def _store(tmp: Path, projects: dict) -> Path:
    path = tmp / "claude.json"
    path.write_text(json.dumps({"numStartups": 3, "projects": projects}), encoding="utf-8")
    return path


def _repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def _trusted(path, store, home):
    return hostenv.trusted(path, claude_json=store, home=home, env={})


def test_trusted_repo_root_covers_subdirectory(tmp_path, home):
    """S1: inside a repository the key is the repository root, so a lab subdirectory of a trusted repo is trusted."""
    repo = _repo(tmp_path / "proj").resolve()
    lab = repo / "tests" / "host" / "_lab"
    lab.mkdir(parents=True)
    store = _store(tmp_path, {str(repo): {"hasTrustDialogAccepted": True}})
    assert _trusted(lab, store, home) is True
    assert _trusted(repo, store, home) is True


@pytest.mark.parametrize("entry", [
    pytest.param(None, id="no-entry"),
    pytest.param({}, id="entry-without-flag"),
    pytest.param({"hasTrustDialogAccepted": False}, id="flag-false"),
    pytest.param({"hasTrustDialogAccepted": "true"}, id="flag-string-not-bool"),
])
def test_untrusted_variants_are_false(tmp_path, home, entry):
    """S2: absent / false / non-boolean flag -> False (only a literal true counts)."""
    repo = _repo(tmp_path / "proj").resolve()
    projects = {} if entry is None else {str(repo): entry}
    assert _trusted(repo, _store(tmp_path, projects), home) is False


def test_nested_repo_not_covered_by_outer_trust(tmp_path, home):
    """S3: a git repository nested in a trusted one is keyed on its own root (observed: Pending in the lab)."""
    outer = _repo(tmp_path / "outer").resolve()
    inner = _repo(outer / "tests" / "host" / "_lab" / "nested").resolve()
    store = _store(tmp_path, {str(outer): {"hasTrustDialogAccepted": True}})
    assert _trusted(inner, store, home) is False


def test_outside_repo_exact_folder_only(tmp_path, home):
    """S3: outside a repository only the folder's own key counts; a trusted parent is not enough (R2-07)."""
    parent = (tmp_path / "plain").resolve()
    child = parent / "data-project"
    child.mkdir(parents=True)
    store = _store(tmp_path, {str(parent): {"hasTrustDialogAccepted": True}})
    assert _trusted(child, store, home) is False
    assert _trusted(parent, store, home) is True


def test_worktree_keys_on_main_checkout(tmp_path, home):
    """S2: a worktree (``.git`` file -> ``<main>/.git/worktrees/<n>``) is keyed on the main checkout's root."""
    main = _repo(tmp_path / "main").resolve()
    (main / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    wt = (tmp_path / "wt1").resolve()
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt1'}\n", encoding="utf-8")
    store = _store(tmp_path, {str(main): {"hasTrustDialogAccepted": True}})
    assert _trusted(wt / "", store, home) is True


def test_submodule_gitfile_is_its_own_root(tmp_path, home):
    """S2: a ``.git`` file that is not a worktree pointer (submodule) makes that folder its own root."""
    outer = _repo(tmp_path / "outer").resolve()
    (outer / ".git" / "modules" / "sub").mkdir(parents=True)
    sub = outer / "sub"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    store = _store(tmp_path, {str(outer): {"hasTrustDialogAccepted": True}})
    assert _trusted(sub, store, home) is False


def test_symlinked_path_resolves_to_real_key(tmp_path, home):
    """S2: keys are real paths (observed key ``/private/tmp`` for ``/tmp``); a symlink is resolved first."""
    real = (tmp_path / "realdir").resolve()
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    store = _store(tmp_path, {str(real): {"hasTrustDialogAccepted": True}})
    assert _trusted(link, store, home) is True


def test_home_directory_never_trusted(tmp_path, home):
    """S3: trust for the home directory is never saved (docs), so even a stored true is not believed."""
    store = _store(tmp_path, {str(home.resolve()): {"hasTrustDialogAccepted": True}})
    assert _trusted(home, store, home) is False


@pytest.mark.parametrize("content", [
    pytest.param(None, id="missing-file"),
    pytest.param("{not json", id="malformed"),
    pytest.param("[]", id="not-an-object"),
    pytest.param('{"projects": []}', id="projects-not-a-map"),
])
def test_unreadable_store_is_undetectable(tmp_path, home, content):
    """S3: a store it cannot read gives ``"undetectable"``, never a guessed boolean."""
    store = tmp_path / "claude.json"
    if content is not None:
        store.write_text(content, encoding="utf-8")
    d = (tmp_path / "d").resolve()
    d.mkdir()
    assert _trusted(d, store, home) == "undetectable"


def test_config_dir_override_is_undetectable_by_default(tmp_path, home):
    """S3: with CLAUDE_CONFIG_DIR set and no explicit store, the store location is not documented -> undetectable."""
    d = (tmp_path / "d").resolve()
    d.mkdir()
    assert hostenv.trusted(d, home=home, env={"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}) == "undetectable"


def test_default_store_is_home_claude_json(tmp_path, home):
    """S1: without an explicit store the probe reads ``<home>/.claude.json`` (read-only)."""
    d = (tmp_path / "d").resolve()
    d.mkdir()
    store = home / ".claude.json"
    store.write_text(json.dumps({"projects": {str(d): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    before = store.read_bytes(), os.stat(store).st_mtime_ns
    assert hostenv.trusted(d, home=home, env={}) is True
    assert (store.read_bytes(), os.stat(store).st_mtime_ns) == before
