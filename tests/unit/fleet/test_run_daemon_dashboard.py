"""Unit: `run_daemon`'s own `dashboard_dir` validation (ADR-0012, KAN-1707) --
an explicit, missing build directory is a startup error, never a silent
fallback to API-only. The success path (a real `StaticFiles` mount serving
real requests) is covered at the `create_app` layer in
`tests/integration/test_fleet_server_dashboard.py`; `run_daemon` itself binds a
real socket and blocks forever on success, so only the fail-fast validation is
exercised here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import run_daemon
from cuttlefish.projects.store import ProjectStore


async def test_an_explicit_dashboard_dir_with_no_index_html_is_rejected(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    empty_dir = tmp_path / "not-a-build"
    empty_dir.mkdir()

    with pytest.raises(ValueError, match=r"index\.html"):
        await run_daemon(daemon, dashboard_dir=empty_dir)


async def test_a_dashboard_dir_that_does_not_exist_at_all_is_rejected(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))

    with pytest.raises(ValueError, match=r"index\.html"):
        await run_daemon(daemon, dashboard_dir=tmp_path / "does-not-exist")
