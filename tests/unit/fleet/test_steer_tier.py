"""Unit: steering a project with no running team types into the builder's session, which is
Terminal-tier access, so an Approve device may not do it (CUT-303, ADR-0039). Steering a running
team's role stays Approve, and the manager credential keeps its designed way to message a builder
(ADR-0046, `message_agent`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier, steer_tier_refusal
from cuttlefish.projects.store import ProjectStore


@pytest.mark.parametrize("tier", ["view", "approve"])
def test_a_device_below_terminal_may_not_type_into_the_builder_through_steer(tier: str) -> None:
    refusal = steer_tier_refusal(("person", tier), team_running=False)
    assert refusal is not None
    assert (
        f"paired as {tier}" in refusal and "Terminal" in refusal and "Devices on the PC" in refusal
    )


@pytest.mark.parametrize(
    "caller", [("person", "terminal"), ("person", "admin"), ("person", "owner")]
)
def test_terminal_and_above_and_the_owner_may(caller: tuple[str, str]) -> None:
    assert steer_tier_refusal(caller, team_running=False) is None


def test_the_manager_keeps_its_message_to_a_builder() -> None:
    assert steer_tier_refusal(("manager", "approve"), team_running=False) is None


@pytest.mark.parametrize("tier", ["view", "approve", "terminal", "admin", "owner"])
def test_steering_a_running_team_needs_nothing_more_than_the_route_does(tier: str) -> None:
    assert steer_tier_refusal(("person", tier), team_running=True) is None
    assert required_tier("POST", "/api/projects/p/steer") == Tier.APPROVE


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.project_id = store.register(name="alpha", root=str(tmp_path)).id  # type: ignore[attr-defined]
        yield c
    store.close()


def test_the_owner_still_reaches_the_daemons_own_answer(client: TestClient) -> None:
    """No team and no builder session: the owner is not refused for tier, the daemon says why."""
    pid = client.project_id  # type: ignore[attr-defined]
    answer = client.post(f"/api/projects/{pid}/steer", json={"text": "go"})
    assert answer.status_code == 409 and "nobody to message" in answer.json()["detail"]
