"""WI-15b: ``release_manifest`` (matrix 15b-01, 15b-06, 15b-08; R3-08, venv addendum V6).

``release_sha`` = sha256 over the concatenated ``sha256(relpath + "\\0" + content)`` hex digests of the run files in
``sorted(relpath)`` order; ``RELEASE.sha256`` lists them.

Law: the sha changes if and only if the (relpath, content) set of the run files changes (S4/S6): order of creation,
build artefacts and files outside the release list do not matter; content, a new/removed run file and a rename do.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import release_fx as F
from _support.flows import REPO, base_env
from klode_kit import kb_check
from klode_kit.devtools import release_manifest as M

pytestmark = pytest.mark.unit

# R3-08 (plan WI-15b): the run resources the digest must cover.
R3_08 = ["templates/**", "scripts/install.sh", "config/**", "prompts/**", "schemas/**", "hooks/**", "workflows/**",
         "agents/**", "skills/**", "bin/**", "src/**", "plugin.json", "pyproject.toml", "uv.lock"]

IN_LIST = ["src/klode_kit/a.py", "src/klode_kit/sub/b.py", "src/klode_kit/templates/t.md", "src/klode_kit/config/c.toml",
           "src/klode_kit/prompts/p.md", "src/klode_kit/schemas/s.json", "templates/root.md", "config/root.toml",
           "prompts/root.md", "schemas/root.json", "hooks/h.json", "workflows/w.js", "agents/a.md", "skills/s/SKILL.md",
           "bin/klode-kit", ".claude-plugin/plugin.json", "pyproject.toml", "uv.lock", "scripts/install.sh"]
OUT_OF_LIST = ["README.md", "RELEASE.sha256", "tests/t.py", "docs/d.md", "work/lanes/unit.json", ".github/workflows/ci.yml",
               "scripts/other.sh", "src/klode_kit/__pycache__/a.cpython-312.pyc", "skills/.DS_Store", "x/y.txt", ".gitignore"]


def tree(root, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    inside=st.dictionaries(st.sampled_from(IN_LIST), st.binary(max_size=20), min_size=1),
    outside=st.dictionaries(st.sampled_from(OUT_OF_LIST), st.binary(max_size=20)),
    target=st.sampled_from(IN_LIST + OUT_OF_LIST),
    action=st.sampled_from(["edit", "add", "delete"]),
    payload=st.binary(min_size=1, max_size=8),
)
def test_release_sha_changes_iff_file_changes(tmp_path_factory, inside, outside, target, action, payload):
    """15b-01 (L1/S4): one change, inside or outside the list - the sha moves exactly when it is inside."""
    root = tmp_path_factory.mktemp("rel")
    files = {**outside, **inside}
    tree(root, files)
    before = M.release_sha(root)
    path = root / target
    if action == "edit":
        if target not in files:
            return
        path.write_bytes(files[target] + payload)
    elif action == "add":
        if target in files:
            return
        tree(root, {target: payload})
    else:
        if target not in files:
            return
        path.unlink()
    inside_list = target in IN_LIST
    changed = M.release_sha(root) != before
    assert changed == inside_list, (target, action)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(files=st.dictionaries(st.sampled_from(IN_LIST), st.binary(max_size=20), min_size=1), seed=st.randoms(use_true_random=False))
def test_sha_is_independent_of_creation_order_and_matches_the_kb_check_definition(tmp_path_factory, files, seed):
    """15b-01b (L1/S4): creation order is irrelevant, and ``kb_check.root_sha`` (the host receipt's root_sha) is this sha."""
    shas = set()
    for _ in range(2):
        root = tmp_path_factory.mktemp("order")
        items = list(files.items())
        seed.shuffle(items)
        tree(root, dict(items))
        shas.add(M.release_sha(root))
        assert kb_check.root_sha(root) == M.release_sha(root)
    assert len(shas) == 1


def test_rename_same_content_changes_sha(tmp_path):
    """15b-08 (L1/S6): the same bytes under another relative path is another release."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    tree(a, {"src/klode_kit/one.py": b"same\n", "bin/x": b"x"})
    tree(b, {"src/klode_kit/two.py": b"same\n", "bin/x": b"x"})
    assert M.release_sha(a) != M.release_sha(b)


def test_swapping_contents_between_two_files_changes_sha(tmp_path):
    """15b-08b (L1/S6): relpath is bound to content, not just a bag of digests."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    tree(a, {"bin/one": b"AAA", "bin/two": b"BBB"})
    tree(b, {"bin/one": b"BBB", "bin/two": b"AAA"})
    assert M.release_sha(a) != M.release_sha(b)


def test_artefacts_and_files_outside_the_list_do_not_count(tmp_path):
    """15b-01c (L1/S2): bytecode caches, .DS_Store, egg-info, docs, tests, the manifest itself."""
    root = tmp_path / "r"
    tree(root, {"src/klode_kit/a.py": b"x"})
    before = M.release_sha(root)
    tree(root, {f: b"junk" for f in OUT_OF_LIST} | {"src/klode_kit.egg-info/PKG-INFO": b"i"})
    assert M.release_sha(root) == before


def test_template_file_is_covered(tmp_path):
    """15b-01d (L1/S1): the file that motivated R3-08 - a template below the package - is in the digest."""
    root = tmp_path / "r"
    tree(root, {"src/klode_kit/templates/confirm.html": b"v1", "pyproject.toml": b"p"})
    before = M.release_sha(root)
    tree(root, {"src/klode_kit/templates/confirm.html": b"v2"})
    assert M.release_sha(root) != before
    assert "src/klode_kit/templates/confirm.html" in M.run_files(root)


# --------------------------------------------------------------------------- 15b-06


def test_glob_names_are_the_r3_08_list():
    """15b-06 (L1/S2): the release definition names exactly the R3-08 resources."""
    assert sorted(M.GLOBS) == sorted(R3_08)


def test_every_glob_has_a_file_in_the_committed_release_manifest():
    """15b-06 (L1/S2): ``RELEASE.sha256`` of this repository lists at least one file for each glob."""
    text = (REPO / "RELEASE.sha256").read_text(encoding="utf-8")
    _sha, rels = M.parse(text)
    for name, patterns in M.GLOBS.items():
        assert any(M.matches(rel, patterns) for rel in rels), f"no file for {name} in RELEASE.sha256"


def test_every_glob_has_a_file_in_this_checkout():
    """15b-06 (L1/S2): and the live tree agrees - nothing the definition names is missing."""
    assert M.uncovered(REPO) == []


def test_complete_fixture_tree_is_covered(tmp_path):
    root = tmp_path / "r"
    F.write_files(root)
    assert M.uncovered(root) == []


@pytest.mark.parametrize("name", R3_08)
def test_empty_glob_is_reported_and_blocks_writing(tmp_path, name):
    """15b-06 (L1/S3): remove every file of one glob -> it is named, ``write`` refuses, the CLI exits 20, no file written."""
    root = tmp_path / "r"
    F.write_files(root)
    for rel in list(F.FILES):
        if M.matches(rel, M.GLOBS[name]) and not any(M.matches(rel, M.GLOBS[other]) for other in M.GLOBS
                                                      if other != name and other != "src/**"):
            (root / rel).unlink()
    if name == "src/**":
        for rel in [r for r in F.FILES if r.startswith("src/")]:
            (root / rel).unlink(missing_ok=True)
    assert name in M.uncovered(root)
    with pytest.raises(M.UncoveredGlobs) as err:
        M.write(root)
    assert name in str(err.value)
    assert not (root / "RELEASE.sha256").exists()
    proc = subprocess.run([sys.executable, "-m", "klode_kit.devtools.release_manifest", "--root", str(root), "--write"],
                          capture_output=True, text=True, env=base_env())
    assert proc.returncode == 20
    assert name in proc.stderr
    assert not (root / "RELEASE.sha256").exists()


# --------------------------------------------------------------------------- file format and CLI


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(files=st.dictionaries(st.sampled_from(IN_LIST), st.binary(max_size=20), min_size=1))
def test_render_parse_round_trip(tmp_path_factory, files):
    """15b-09 (L1/S5): ``parse(render(root))`` gives back the sha and the run-file list."""
    root = tmp_path_factory.mktemp("rt")
    tree(root, files)
    sha, rels = M.parse(M.render(root))
    assert sha == M.release_sha(root)
    assert rels == M.run_files(root)


def test_parse_rejects_malformed_text():
    for bad in ("", "nonsense\n", "release_sha xyz\n", f"release_sha {'0' * 64}\nshort  a\n"):
        with pytest.raises(ValueError):
            M.parse(bad)


def test_cli_write_check_print_sha(tmp_path):
    """15b-10 (L2/S2): ``--write`` creates RELEASE.sha256 (exit 0), ``--check`` is 0 while current and 20 once a run file moved."""
    root = tmp_path / "r"
    F.write_files(root)

    def run(*args):
        return subprocess.run([sys.executable, "-m", "klode_kit.devtools.release_manifest", "--root", str(root), *args],
                              capture_output=True, text=True, env=base_env())

    assert run("--check").returncode == 20  # nothing written yet
    assert run("--write").returncode == 0
    assert (root / "RELEASE.sha256").read_text(encoding="utf-8") == M.render(root)
    assert run("--check").returncode == 0
    assert run("--print-sha").stdout.strip() == M.release_sha(root)
    (root / "src/klode_kit/templates/agent.md").write_text("template two\n", encoding="utf-8")
    stale = run("--check")
    assert stale.returncode == 20
    assert "src/klode_kit/templates/agent.md" in stale.stderr or "stale" in stale.stderr
    assert run("--write").returncode == 0
    assert run("--check").returncode == 0


def test_write_is_deterministic_and_atomic(tmp_path):
    """15b-11 (L1/S5): writing twice gives the same bytes; no temporary file stays behind."""
    root = tmp_path / "r"
    F.write_files(root)
    first = M.write(root).read_bytes()
    assert M.write(root).read_bytes() == first
    assert sorted(p.name for p in root.iterdir() if p.name.startswith(".RELEASE")) == []


def test_lane_receipts_carry_the_manifest_sha(tmp_path):
    """15b-12 (L2/S1): ``run_lane`` stamps ``env_fingerprint.code_tree_sha`` with the release sha of the checkout."""
    import json

    root = tmp_path / "r"
    F.write_files(root)
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_gen.py").write_text("import pytest\n\n@pytest.mark.unit\ndef test_a():\n    assert True\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "klode_kit.devtools.run_lane", "unit", "--root", str(root), "--no-cov"],
                          capture_output=True, text=True, env=base_env(), timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fp = json.loads((root / "work" / "lanes" / "unit.json").read_text(encoding="utf-8"))["env_fingerprint"]
    assert fp["code_tree_sha"] == M.release_sha(root)
