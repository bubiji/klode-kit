"""WI-00b2: configuration defaults, data-project override and canonical form (matrix 00b2-11 .. 00b2-14).

Public surface (``klode_kit.config``):
- ``load(project=None, *, defaults=None)`` -> ``Config``: packaged ``defaults.toml`` (or the given
  defaults path) overlaid with ``<project>/klode-kit.toml`` when that file exists;
- ``resolve(overrides, *, defaults=None)`` -> ``Config`` from an in-memory override mapping
  (dotted keys or nested tables);
- ``Config[dotted_key]``, ``Config.flat()`` (dotted key -> value), ``Config.config_version``,
  ``Config.canonical()`` (bytes, excludes ``config_version``);
- ``ConfigError`` (``exit_code == 40``, message names the offending key);
- ``CLOUD_ENV_ALLOWLIST``: the explicit cloud env whitelist (r5), a constant, not a config key;
- ``bin/klode-kit config show [--project DIR]`` prints the effective configuration as JSON;
  any ``ConfigError`` is exit 40 with the message on stderr.

The default table is design r2.6, verbatim.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest

from _support.flows import REPO, base_env
from klode_kit import config

pytestmark = pytest.mark.unit

SYNTH_PROJECT = REPO / "tests" / "fixtures" / "synth_project"
LAUNCHER = REPO / "bin" / "klode-kit"

R26_DEFAULTS = {
    "routes": ["vision", "mlx", "claude", "codex"],
    "base_route": "vision",
    "claude.model": "opus",
    "claude.effort": "max",
    "codex.model": "gpt-6-sol",
    "codex.effort": "max",
    "cloud.timeout_s": 180,
    "cloud.max_cost_usd_per_batch": 5.0,
    "route_retry": 2,
    "repeat_ratio_max": 0.3,
    "line_sim_tau": 0.6,
    "text_layer.min_chars": 50,
    "text_layer.garbage_max": 0.05,
    "cards.min_aliases": 10,
    "cards.min_anchors_full": 5,
    "cards.writer_max_rounds": 3,
    "cards.writer_concurrency": 4,
    "cards.writer_timeout_s": 600,
    "desk.holdout_min": 5,
    "desk.holdout_pass": 0.8,
    "loops.max_total_rounds": 6,
    "loops.no_progress_rounds": 2,
    "deident.mode": "pseudonym",
}


def _packaged_defaults() -> Path:
    return Path(str(resources.files("klode_kit") / "config" / "defaults.toml"))


def _cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(LAUNCHER), *args], capture_output=True, text=True, env=base_env(), timeout=60,
        cwd=str(cwd) if cwd else None,
    )


def _project(tmp_path: Path, text: str) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "klode-kit.toml").write_text(text, encoding="utf-8")
    return proj


def test_defaults_are_r26_table():
    cfg = config.load()
    flat = cfg.flat()
    assert cfg.config_version == 1
    assert {k: v for k, v in flat.items() if k != "config_version"} == R26_DEFAULTS
    assert all(type(flat[k]) is type(v) for k, v in R26_DEFAULTS.items())


def test_config_override_applies():
    """00b2-11 L1·S1: synth_project sets route_retry=5; every other key equals the defaults."""
    cfg = config.load(SYNTH_PROJECT)
    assert cfg["route_retry"] == 5
    expected = {**R26_DEFAULTS, "route_retry": 5}
    assert {k: v for k, v in cfg.flat().items() if k != "config_version"} == expected


def test_config_override_nested_table_key(tmp_path):
    proj = _project(tmp_path, '[claude]\neffort = "high"\n[cloud]\nmax_cost_usd_per_batch = 2\n')
    cfg = config.load(proj)
    assert cfg["claude.effort"] == "high"
    assert cfg["claude.model"] == "opus"
    assert cfg["cloud.max_cost_usd_per_batch"] == 2.0
    assert isinstance(cfg["cloud.max_cost_usd_per_batch"], float)


def test_project_without_override_file_is_defaults(tmp_path):
    assert config.load(tmp_path).flat() == config.load().flat()


def test_config_unknown_key_exit40(tmp_path):
    """00b2-12 L1·S3: unknown key ``routez`` -> ConfigError, exit 40 through bin/klode-kit, message names it."""
    proj = _project(tmp_path, "routez = 3\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load(proj)
    assert exc.value.exit_code == 40
    assert "routez" in str(exc.value)

    res = _cli("config", "show", "--project", str(proj))
    assert res.returncode == 40, res.stderr
    assert "routez" in res.stderr
    assert res.stdout == ""


@pytest.mark.parametrize(
    "text",
    ["[claude]\nmodle = 'x'\n", "[nosuch]\nx = 1\n", "config_version = 2\n", "[cloud]\nenv_allow = ['ANTHROPIC_BASE_URL']\n"],
    ids=["nested-typo", "unknown-table", "config_version-not-overridable", "env-allowlist-not-configurable"],
)
def test_config_unknown_or_forbidden_override_keys(tmp_path, text):
    proj = _project(tmp_path, text)
    with pytest.raises(config.ConfigError) as exc:
        config.load(proj)
    assert exc.value.exit_code == 40


def test_config_missing_default_key_exit40(tmp_path):
    """00b2-13 L1·S3: a defaults copy without ``line_sim_tau`` -> exit 40, message names the key."""
    lines = _packaged_defaults().read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.lstrip().startswith("line_sim_tau")]
    assert len(kept) == len(lines) - 1
    broken = tmp_path / "defaults.toml"
    broken.write_text("".join(kept), encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc:
        config.load(defaults=broken)
    assert exc.value.exit_code == 40
    assert "line_sim_tau" in str(exc.value)


def test_config_unknown_key_in_defaults_rejected(tmp_path):
    broken = tmp_path / "defaults.toml"
    broken.write_text(_packaged_defaults().read_text(encoding="utf-8").replace(
        "route_retry", "surprise = 1\nroute_retry", 1), encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc:
        config.load(defaults=broken)
    assert "surprise" in str(exc.value)


@pytest.mark.parametrize(
    "text",
    [
        'route_retry = "two"\n',
        "route_retry = 2.5\n",
        "route_retry = true\n",
        "routes = 'vision'\n",
        "routes = ['vision', 3]\n",
        "line_sim_tau = 'high'\n",
        "[claude]\nmodel = 4\n",
        "claude = 'opus'\n",
        "[deident]\nmode = 'plain'\n",
    ],
)
def test_config_wrong_type_exit40(tmp_path, text):
    """00b2-14 L1·S3: ``route_retry="two"`` (and other type errors) -> exit 40."""
    proj = _project(tmp_path, text)
    res = _cli("config", "show", "--project", str(proj))
    assert res.returncode == 40, (res.stdout, res.stderr)
    with pytest.raises(config.ConfigError):
        config.load(proj)


def test_config_unparseable_toml_exit40(tmp_path):
    proj = _project(tmp_path, "route_retry = = 3\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load(proj)
    assert "klode-kit.toml" in str(exc.value)


def test_config_show_prints_effective_config(tmp_path):
    res = _cli("config", "show", "--project", str(SYNTH_PROJECT))
    assert res.returncode == 0, res.stderr
    shown = json.loads(res.stdout)
    assert shown["config_version"] == 1
    assert shown["values"]["route_retry"] == 5
    assert shown["values"]["claude"]["model"] == "opus"


def test_config_show_defaults_to_cwd(tmp_path):
    proj = _project(tmp_path, "route_retry = 7\n")
    res = _cli("config", "show", cwd=proj)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["values"]["route_retry"] == 7


def test_canonical_form_is_stable_and_excludes_version(tmp_path):
    """Canonical serialisation: sorted keys, no whitespace variance, config_version left out (r4.4)."""
    cfg = config.load()
    raw = cfg.canonical()
    assert raw == config.load().canonical()
    data = json.loads(raw)
    assert "config_version" not in data
    assert data["route_retry"] == 2 and data["claude"]["effort"] == "max"
    assert raw == json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    copy = tmp_path / "defaults.toml"
    shutil.copy(_packaged_defaults(), copy)
    copy.write_text(copy.read_text(encoding="utf-8").replace("\n", "\n\n"), encoding="utf-8")
    assert config.load(defaults=copy).canonical() == raw


def test_resolve_accepts_dotted_and_nested_overrides():
    a = config.resolve({"claude.effort": "high", "route_retry": 4})
    b = config.resolve({"claude": {"effort": "high"}, "route_retry": 4})
    assert a.flat() == b.flat()
    assert a["claude.effort"] == "high" and a["route_retry"] == 4
    with pytest.raises(config.ConfigError):
        config.resolve({"claude.efort": "high"})


def test_cloud_env_allowlist_is_explicit():
    """r5: explicit keys only; ANTHROPIC_BASE_URL and friends are never allowed.
    WI-00c1b (user decision 2026-10-23): USER joins - macOS claude.ai OAuth reads the Keychain by it;
    LOGNAME was shown not to help (WI-00c2 F1) and stays out."""
    assert config.CLOUD_ENV_ALLOWLIST == frozenset({"PATH", "HOME", "TMPDIR", "LANG", "ANTHROPIC_API_KEY", "USER"})
    assert "ANTHROPIC_BASE_URL" not in config.CLOUD_ENV_ALLOWLIST
    assert "LOGNAME" not in config.CLOUD_ENV_ALLOWLIST


# ---------------------------------------------------------------- added after red (guards; no recorded line changed)


@pytest.mark.parametrize(
    "text",
    ['"" = 1\n', "[claude.inner]\nmodel = 'x'\n"],
    ids=["empty-key", "nested-subtable"],
)
def test_config_malformed_override_shapes(tmp_path, text):
    with pytest.raises(config.ConfigError) as exc:
        config.load(_project(tmp_path, text))
    assert exc.value.exit_code == 40


def test_resolve_rejects_key_given_twice():
    with pytest.raises(config.ConfigError) as exc:
        config.resolve({"claude.model": "a", "claude": {"model": "b"}})
    assert "claude.model" in str(exc.value)


@pytest.mark.parametrize("version", ["0", "'1'", "true"])
def test_defaults_config_version_must_be_positive_int(tmp_path, version):
    broken = tmp_path / "defaults.toml"
    broken.write_text(_packaged_defaults().read_text(encoding="utf-8").replace(
        "config_version = 1", f"config_version = {version}", 1), encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc:
        config.load(defaults=broken)
    assert "config_version" in str(exc.value)


def test_config_item_access():
    cfg = config.load()
    assert cfg["config_version"] == 1
    routes = cfg["routes"]
    routes.append("mutated")
    assert cfg["routes"] == ["vision", "mlx", "claude", "codex"]
    with pytest.raises(KeyError):
        cfg["no.such"]


def test_config_cli_usage_error():
    res = _cli("config", "frobnicate")
    assert res.returncode == 2
