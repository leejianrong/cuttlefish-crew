"""Unit (CUT-292): what the backends report about their limits, read as they send it, with nothing
invented for a backend that says nothing."""

from __future__ import annotations

from pathlib import Path

import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectStore
from cuttlefish.usage import BOARD, UsageBoard, claude_windows, codex_windows


def test_claude_per_window_utilization_is_a_percent_and_a_warning_at_eighty() -> None:
    windows = claude_windows(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "unifiedWindows": {
                    "five_hour": {"utilization": 0.79, "resetsAt": 1_800_000_000},
                    "seven_day": {"utilization": 0.8, "resetsAt": 1_800_500_000},
                },
            },
        }
    )
    assert [(w.name, w.used_percent, w.warn) for w in windows] == [
        ("five_hour", 79.0, False),
        ("seven_day", 80.0, True),
    ]


def test_claude_single_window_and_its_own_warning_status() -> None:
    [window] = claude_windows(
        {
            "rate_limit_info": {
                "status": "allowed_warning",
                "rateLimitType": "five_hour",
                "resetsAt": 5,
                "utilization": 0.1,
            }
        }
    )
    assert window.warn is True and window.used_percent == 10.0  # the backend's word counts
    assert claude_windows({"rate_limit_info": {"status": "allowed"}}) == []  # nothing to show
    assert claude_windows({"rate_limit_info": "x"}) == [] and claude_windows({}) == []


def test_codex_windows_and_a_sparse_update_that_keeps_what_it_leaves_out() -> None:
    board = UsageBoard(now=lambda: 1000.0)
    board.report_codex(
        {
            "rateLimits": {
                "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 9},
                "secondary": {"usedPercent": 50},
            }
        }
    )
    board.report_codex({"rateLimits": {"primary": {"usedPercent": 45}, "secondary": None}})
    shown = board.as_json()["codex"]
    by_name = {w["name"]: w for w in shown["windows"]}
    assert by_name["primary"]["used_percent"] == 45.0 and by_name["primary"]["resets_at"] is None
    assert by_name["secondary"]["used_percent"] == 50.0
    assert shown["at"] == 1000.0 and shown["warn"] is False
    assert codex_windows({"rateLimits": {"primary": {"usedPercent": True}}}) == {}


def test_a_backend_that_has_said_nothing_is_not_reported_never_zero() -> None:
    shown = UsageBoard().as_json()
    assert shown["claude-code"] == {"reported": False, "note": "Not reported yet."}
    assert shown["codex"]["reported"] is False
    assert shown["kopicode"] == {"reported": False, "note": "Does not report usage."}


def test_a_worker_heartbeat_carries_only_backends_that_reported() -> None:
    board = UsageBoard()
    assert board.heartbeat() == {}
    board.report_codex({"rateLimits": {"primary": {"usedPercent": 90, "resetsAt": 7}}})
    assert list(board.heartbeat()) == ["codex"]


def test_usage_is_a_view_tier_route_that_lists_every_backend(tmp_path: Path) -> None:
    assert required_tier("GET", "/api/usage") is Tier.VIEW
    store = ProjectStore.open(tmp_path / "p.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    BOARD.reset()
    BOARD.report_claude(
        {"rate_limit_info": {"rateLimitType": "five_hour", "utilization": 0.9, "resetsAt": 3}}
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as client:
            body = client.get("/api/usage").json()
    finally:
        BOARD.reset()
        store.close()
    assert body["warn_at_percent"] == 80.0
    assert list(body["local"]) == ["claude-code", "codex", "kopicode"]
    assert body["local"]["claude-code"]["warn"] is True
    assert body["local"]["codex"]["reported"] is False
