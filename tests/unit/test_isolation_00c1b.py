"""WI-00c1b: isolation fixes after the WI-00c2 host run - the probe side of ``USER`` joining the
cloud env allowlist (user decision 2026-10-23; WI-00c2 finding F1).

``isolation_probe``'s ``probe:env_delta`` check: every environment key the probe adds or changes
must be in ``CLOUD_ENV_ALLOWLIST``; a removed key always fails. Passing ``USER`` (the macOS
claude.ai OAuth Keychain account) is therefore no longer a widening, while ``LOGNAME`` (shown not to
help) and anything else still is.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit.config import CLOUD_ENV_ALLOWLIST
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_probe as IP

pytestmark = pytest.mark.unit

FIXTURE_PNG = REPO / "tests" / "fixtures" / "fx-approx-only" / "images" / "p001.png"


@pytest.fixture
def inv(tmp_path, monkeypatch) -> CA.Invocation:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    png = FIXTURE_PNG.read_bytes()
    send = CA.prepare_send_dir(png, IP.sha256_bytes(png))
    source = {"PATH": os.environ["PATH"], "HOME": "/home/synthetic", "TMPDIR": str(tmp_path), "LANG": "C.UTF-8"}
    return CA.claude(send, model="opus", effort="low", max_budget_usd=5.0, source_env=source)


def test_env_delta_user_is_not_a_widening(inv):
    """Adding USER for the probe keeps probe:env_delta ok; LOGNAME or USER+LOGNAME does not."""
    assert "USER" not in inv.env
    prompt = IP.probe_prompt(["/x/canary-nonce-000000000000.txt"])

    ok, changed = IP._env_delta(inv, IP.probe_invocation(inv, prompt, extra_env={"USER": "synthetic-user"}))
    assert (ok, changed) == (True, ["USER"])

    bad, changed = IP._env_delta(inv, IP.probe_invocation(inv, prompt, extra_env={"LOGNAME": "synthetic-user"}))
    assert (bad, changed) == (False, ["LOGNAME"])

    both, _ = IP._env_delta(inv, IP.probe_invocation(inv, prompt, extra_env={"USER": "u", "LOGNAME": "u"}))
    assert both is False


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(extra=st.dictionaries(
    st.sampled_from(sorted(CLOUD_ENV_ALLOWLIST | {"LOGNAME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL", "FOO"})),
    st.text(alphabet="abcxyz/", min_size=1, max_size=8), max_size=4))
def test_env_delta_ok_iff_changes_within_allowlist(inv, extra):
    """S4: for any added/changed keys, probe:env_delta is ok exactly when every changed key is allowlisted."""
    probe = IP.probe_invocation(inv, "prompt", extra_env=extra)
    ok, changed = IP._env_delta(inv, probe)
    expected_changed = sorted(k for k, v in extra.items() if inv.env.get(k) != v)
    assert changed == expected_changed
    assert ok is all(k in CLOUD_ENV_ALLOWLIST for k in expected_changed)
