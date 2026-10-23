"""WI-05b additions to the layer (a) review (``isolation_check.check``), both used by every send:

- ``expected_effort=<configured value>`` adds ``effort:configured``: the argv's effort (claude ``--effort``, codex
  ``-c model_reasoning_effort=``) must be exactly that value - not merely one of the allowed levels (WI-00c3
  hardening). Without the keyword the check is absent and the closed set of 00c1 applies unchanged.
- ``refuse_shared_tmp=True`` adds ``project:outside_shared_tmp``: the data project's real path must not be under
  ``/tmp`` or ``/private/tmp`` (RR-7: codex ``:minimal`` can read /tmp). Judged on the real path, so a symlink into
  /tmp fails too; nothing has to exist.

Boundary: ``--version`` is answered by the ``_support.send_fake.FakeCloud`` CLIs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.send_fake import FIXTURE_PNG, FakeCloud, build, source_env
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC

pytestmark = pytest.mark.unit

PNG = FIXTURE_PNG.read_bytes()
SHA = hashlib.sha256(PNG).hexdigest()
APPROVED = frozenset({SHA})


@pytest.fixture
def systmp(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def rig(tmp_path, systmp):
    home = tmp_path / "home"
    home.mkdir()
    fake = FakeCloud(tmp_path / "fake")
    project = tmp_path / "project"
    project.mkdir()

    def make(route: str, effort: str) -> CA.Invocation:
        return build(route, CA.prepare_send_dir(PNG, SHA), source_env(fake, home, systmp), effort=effort)

    return make, project


def results(res) -> dict[str, bool]:
    return {c.name: c.ok for c in res.checks}


EFFORTS = {"claude": sorted(CA.CLAUDE_EFFORTS), "codex": sorted(CA.CODEX_EFFORTS)}


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.data(), route=st.sampled_from(["claude", "codex"]))
def test_effort_must_equal_configured(rig, data, route):
    """S4: for every pair (argv effort, configured effort) of allowed levels, ``effort:configured`` holds iff
    they are equal, and the whole review passes iff they are equal."""
    make, project = rig
    used = data.draw(st.sampled_from(EFFORTS[route]))
    configured = data.draw(st.sampled_from(EFFORTS[route]))
    inv = make(route, used)

    res = IC.check(inv, project=project, approved_shas=APPROVED, expected_effort=configured)

    assert results(res)["effort:configured"] is (used == configured)
    assert res.ok is (used == configured)


def test_effort_check_absent_without_keyword(rig):
    """The 00c1 behaviour is unchanged when no configured effort is given."""
    make, project = rig
    res = IC.check(make("codex", "low"), project=project, approved_shas=APPROVED)
    assert "effort:configured" not in results(res) and res.ok


@pytest.mark.parametrize("route", ["claude", "codex"])
def test_effort_missing_or_doubled_fails(rig, route):
    """Guard: no effort in the argv at all, or the effort given twice -> ``effort:configured`` False."""
    make, project = rig
    inv = make(route, "max")
    argv = list(inv.argv)
    if route == "claude":
        i = argv.index("--effort")
        missing = argv[:i] + argv[i + 2:]
    else:
        i = argv.index("model_reasoning_effort=max")
        missing = argv[:i - 1] + argv[i + 1:]
    doubled = [*argv[:-1], "--effort" if route == "claude" else "-c",
               "max" if route == "claude" else "model_reasoning_effort=max", argv[-1]]
    for bad in (missing, doubled):
        res = IC.check(dataclasses.replace(inv, argv=tuple(bad)), project=project, approved_shas=APPROVED,
                       expected_effort="max")
        assert results(res)["effort:configured"] is False


@pytest.mark.parametrize("kind", ["tmp", "private_tmp", "symlink"])
def test_project_under_shared_tmp_fails(rig, tmp_path, kind):
    """RR-7 (S2/S3): /tmp/..., /private/tmp/..., and a symlink resolving into /tmp all fail
    ``project:outside_shared_tmp``; the projects need not exist."""
    make, _ = rig
    name = "klode-kit-05b-never-created"
    if kind == "tmp":
        proj = Path("/tmp") / name / "p"
    elif kind == "private_tmp":
        proj = Path("/private/tmp") / name
    else:
        link = tmp_path / "link"
        link.symlink_to("/tmp")
        proj = link / name
    res = IC.check(make("codex", "max"), project=proj, approved_shas=APPROVED, refuse_shared_tmp=True)
    assert results(res)["project:outside_shared_tmp"] is False
    assert not res.ok
    assert not Path("/tmp", name).exists()


@pytest.mark.parametrize("proj", ["/tmpx/p", "/private/tmpdata/p", "/var/tmp-like/p"])
def test_shared_tmp_prefix_is_by_component(rig, proj):
    """Boundary: ``/tmpx`` is not under ``/tmp`` (path components, not ``startswith``)."""
    make, _ = rig
    res = IC.check(make("claude", "max"), project=Path(proj), approved_shas=APPROVED, refuse_shared_tmp=True)
    assert results(res)["project:outside_shared_tmp"] is True


def test_project_elsewhere_passes(rig):
    make, project = rig
    res = IC.check(make("claude", "max"), project=project, approved_shas=APPROVED, refuse_shared_tmp=True,
                   expected_effort="max")
    assert res.ok, res.failed
    assert results(res)["project:outside_shared_tmp"] is True
