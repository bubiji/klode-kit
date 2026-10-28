"""WI-15j: explicit test mapping for the mutation gate (``[tool.klode_kit.mutation.extra_tests]`` in pyproject.toml).

Modules that tests reach only through a subprocess (``bin/klode-kit mcp`` ...) are never named in a test file, so the
static / coverage mapping cannot select those tests. The explicit map is unioned with both, honours the lane
markers, and a path that does not exist is an error (never a silently shrunk test set).
"""

from __future__ import annotations

import pytest

from klode_kit.devtools import mutation_gate as G

pytestmark = pytest.mark.unit

REL = "src/pkg/mod.py"
ALL = "unit or contract_replay or cli_integration"


def _tree(tmp_path, extra_toml, files=("tests/unit/test_a.py", "tests/cli/test_b.py", "tests/contract/test_c.py")):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("def f():\n    return 1\n")
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def test_x():\n    pass\n")
    (tmp_path / "pyproject.toml").write_text(extra_toml)
    return tmp_path


def _toml(**mapping):
    body = "".join(f'"{k}" = {v!r}\n'.replace("'", '"') for k, v in mapping.items())
    return "[tool.klode_kit.mutation.extra_tests]\n" + body


def test_listed_files_are_selected(tmp_path):
    root = _tree(tmp_path, _toml(**{REL: ["tests/unit/test_a.py", "tests/cli/test_b.py"]}))
    assert G.extra_tests(root, REL, ALL) == ["tests/cli/test_b.py", "tests/unit/test_a.py"]


def test_directory_entry_expands_to_its_test_files(tmp_path):
    root = _tree(tmp_path, _toml(**{REL: ["tests/contract"]}))
    assert G.extra_tests(root, REL, ALL) == ["tests/contract/test_c.py"]


def test_cli_files_need_cli_integration_in_markers(tmp_path):
    root = _tree(tmp_path, _toml(**{REL: ["tests/unit/test_a.py", "tests/cli/test_b.py", "tests/cli"]}))
    assert G.extra_tests(root, REL, "unit or contract_replay") == ["tests/unit/test_a.py"]


def test_other_source_files_and_missing_config_select_nothing(tmp_path):
    root = _tree(tmp_path, _toml(**{"src/pkg/other.py": ["tests/unit/test_a.py"]}))
    assert G.extra_tests(root, REL, ALL) == []
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert G.extra_tests(root, REL, ALL) == []
    (root / "pyproject.toml").unlink()
    assert G.extra_tests(root, REL, ALL) == []


def test_missing_path_is_an_error_even_when_the_lane_is_not_selected(tmp_path):
    root = _tree(tmp_path, _toml(**{REL: ["tests/cli/test_gone.py"]}))
    with pytest.raises(ValueError, match="test_gone.py"):
        G.extra_tests(root, REL, "unit")


def test_missing_path_of_another_file_is_also_reported(tmp_path):
    root = _tree(tmp_path, _toml(**{"src/pkg/other.py": ["tests/unit/nope.py"]}))
    with pytest.raises(ValueError, match="nope.py"):
        G.extra_tests(root, REL, ALL)


def test_union_with_static_and_coverage_mapping(tmp_path):
    root = _tree(tmp_path, _toml(**{REL: ["tests/cli/test_b.py"]}))
    (root / "tests" / "unit" / "test_a.py").write_text("from pkg import mod\n\n\ndef test_x():\n    assert mod.f() == 1\n")
    entry = {"funcs": {"f": ["tests/contract/test_c.py::test_x", "tests/unit/test_a.py::test_x"]}}
    out = G._with_static(root, REL, entry, ALL)
    assert out["static"] == ["tests/cli/test_b.py", "tests/unit/test_a.py"]
    assert out["all"] == ["tests/contract/test_c.py::test_x", "tests/cli/test_b.py", "tests/unit/test_a.py"]


def test_marker_filter_applies_through_with_static(tmp_path):
    root = _tree(tmp_path, _toml(**{REL: ["tests/cli/test_b.py"]}))
    out = G._with_static(root, REL, {"funcs": {"f": []}}, "unit or contract_replay")
    assert out["static"] == [] and out["all"] == []


def test_the_repository_mapping_points_at_real_tests():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    got = G.extra_tests(root, "src/klode_kit/mcp_wrap.py", ALL)
    assert "tests/cli/test_mcp_wrap.py" in got and "tests/unit/test_mcp_wrap_einval_retry.py" in got
    assert "tests/cli/test_u5_probe_cli.py" in G.extra_tests(root, "src/klode_kit/u5_probe.py", ALL)
    assert "tests/cli/test_mcp_wrap.py" not in G.extra_tests(root, "src/klode_kit/mcp_wrap.py", "unit or contract_replay")
