"""pytest plugin with the lane guards (plan section 0.1).

- Lane markers: tests under ``tests/<dir>/`` get the lane of that directory; every test
  must end up with at least one lane marker, otherwise the session is a usage error.
- ``unit`` / ``contract_replay`` tests run with sockets blocked and ``KLODE_KIT_REAL=0``.
- Any selected ``gate_cloud`` test without a valid ``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>``
  aborts the whole session with exit 2 before anything runs (never a skip).
"""

from __future__ import annotations

import os
import re

import pytest

LANES = ("unit", "contract_replay", "cli_integration", "model_integration", "host_acceptance")
OFFLINE_LANES = frozenset({"unit", "contract_replay"})
DIR_LANE = {
    "unit": "unit",
    "contract": "contract_replay",
    "replay": "contract_replay",
    "cli": "cli_integration",
    "docs": "cli_integration",
    "model": "model_integration",
    "host": "host_acceptance",
}
ACK_RE = re.compile(r"^G2-\d{8}$")
MARKERS = {
    "unit": "unit lane",
    "contract_replay": "contract-replay lane",
    "cli_integration": "cli-integration lane",
    "model_integration": "model-integration lane",
    "host_acceptance": "host-acceptance lane",
    "gate_cloud": "needs KLODE_KIT_GATE_ACK=G2-<yyyymmdd>",
    "macos": "cli_integration subset needing macOS",
    "macos_mlx": "cli_integration subset needing MLX weights",
    "needs_node": "cli_integration subset needing node",
}


def pytest_configure(config: pytest.Config) -> None:
    for name, doc in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {doc}")


def _lanes_of(item: pytest.Item) -> set[str]:
    return {m.name for m in item.iter_markers() if m.name in LANES}


def pytest_itemcollected(item: pytest.Item) -> None:
    """Directory -> lane, applied before mark-expression deselection runs."""
    try:
        rel = item.path.relative_to(item.config.rootpath)
    except ValueError:
        return
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "tests" and parts[1] in DIR_LANE:
        lane = DIR_LANE[parts[1]]
        if lane not in _lanes_of(item):
            item.add_marker(getattr(pytest.mark, lane))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    orphans = [item.nodeid for item in items if not _lanes_of(item)]
    if orphans:
        raise pytest.UsageError(
            "tests without a lane marker (they would never run in any lane): " + ", ".join(orphans)
        )


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    gated = [item.nodeid for item in session.items if item.get_closest_marker("gate_cloud")]
    if not gated:
        return
    ack = os.environ.get("KLODE_KIT_GATE_ACK", "")
    if not ACK_RE.match(ack):
        pytest.exit(
            "usage error: gate_cloud tests selected but KLODE_KIT_GATE_ACK is missing or not "
            f"G2-<yyyymmdd> (got {ack!r}); refusing to run {len(gated)} gated test(s)",
            returncode=2,
        )


@pytest.fixture(autouse=True)
def _klode_kit_lane_guards(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    offline = bool(_lanes_of(request.node) & OFFLINE_LANES)
    if not offline:
        yield
        return
    import pytest_socket

    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    pytest_socket.disable_socket(allow_unix_socket=True)
    try:
        yield
    finally:
        pytest_socket.enable_socket()
