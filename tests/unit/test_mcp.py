"""Unit: `cuttlefish.mcp`'s own tools are thin, correctly-argumented wrappers
over the fleet daemon's HTTP API (KAN-1764/ADR-0020) -- dispatched through
the real `MCPServer.call_tool`, with `_request` itself stubbed so no real
HTTP call happens here. The real HTTP round trip (and the SDK's own
exception-to-tool-error handling) was verified live against a real running
`cuttlefish serve` and a real MCP client before this shipped -- see
docs/adr/0020's own Consequences section.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import UnexpectedToolError

from cuttlefish import mcp as mcp_module
from cuttlefish.mcp import FleetApiError, build_mcp_server


@pytest.fixture
def server() -> Any:
    return build_mcp_server(base_url="http://127.0.0.1:9999", token="test-token")


async def test_list_tools_exposes_exactly_the_expected_surface(server: Any) -> None:
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "list_projects",
        "get_project",
        "register_project",
        "start_project",
        "stop_project",
        "steer_project",
        "approve_project",
        "get_events",
    }


def _stub_request(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake(
        base_url: str, token: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        calls.append((base_url, token, method, path, body))
        return response

    monkeypatch.setattr(mcp_module, "_request", fake)
    return calls


async def test_list_projects_calls_the_right_route(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"projects": []})
    result = await server.call_tool("list_projects", {})
    assert calls == [("http://127.0.0.1:9999", "test-token", "GET", "/api/projects", None)]
    assert not result.is_error
    assert result.structured_content == {"projects": []}


async def test_get_project_includes_the_project_id_in_the_path(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"id": "abc"})
    await server.call_tool("get_project", {"project_id": "abc"})
    assert calls == [("http://127.0.0.1:9999", "test-token", "GET", "/api/projects/abc", None)]


async def test_register_project_omits_roles_when_not_given(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"id": "new"})
    await server.call_tool("register_project", {"name": "demo", "root": "/tmp/demo"})
    assert calls == [
        (
            "http://127.0.0.1:9999",
            "test-token",
            "POST",
            "/api/projects",
            {"name": "demo", "root": "/tmp/demo"},
        )
    ]


async def test_register_project_includes_roles_when_given(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"id": "new"})
    roles = [{"name": "builder", "persona": "ships fast"}]
    await server.call_tool(
        "register_project", {"name": "demo", "root": "/tmp/demo", "roles": roles}
    )
    assert calls[0][4] == {"name": "demo", "root": "/tmp/demo", "roles": roles}


async def test_start_project_defaults_require_approval_to_false(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"team_id": "t1"})
    roles = [{"name": "builder", "text": "add a .gitignore entry"}]
    await server.call_tool("start_project", {"project_id": "abc", "roles": roles})
    assert calls == [
        (
            "http://127.0.0.1:9999",
            "test-token",
            "POST",
            "/api/projects/abc/start",
            {"roles": roles, "require_approval": False},
        )
    ]


async def test_stop_project_calls_the_right_route(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"status": "stopping"})
    await server.call_tool("stop_project", {"project_id": "abc"})
    assert calls == [
        ("http://127.0.0.1:9999", "test-token", "POST", "/api/projects/abc/stop", None)
    ]


async def test_steer_project_sends_role_and_text(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"status": "sent"})
    await server.call_tool(
        "steer_project", {"project_id": "abc", "role": "builder", "text": "focus on tests"}
    )
    assert calls[0][4] == {"role": "builder", "text": "focus on tests"}


async def test_approve_project_sends_a_null_comment_when_none_given(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"status": "sent"})
    await server.call_tool(
        "approve_project", {"project_id": "abc", "role": "builder", "approved": True}
    )
    assert calls[0][4] == {"role": "builder", "approved": True, "comment": None}


async def test_get_events_calls_the_right_route(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"events": []})
    await server.call_tool("get_events", {"project_id": "abc"})
    assert calls == [
        ("http://127.0.0.1:9999", "test-token", "GET", "/api/projects/abc/events", None)
    ]


async def test_a_fleet_api_error_is_never_silently_swallowed(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MCPServer.call_tool` (the low-level API used directly here) re-raises a
    tool crash as `UnexpectedToolError` rather than returning a `CallToolResult`
    -- the SDK's own real protocol dispatch (`_handle_call_tool`) is what turns
    this into an `is_error=True` result a client actually sees, verified live
    against a real MCP client/session before this shipped (docs/adr/0020). This
    test only has to prove `cuttlefish.mcp` itself never catches and hides a
    `FleetApiError`, letting the SDK's own documented contract handle it."""

    def raise_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise FleetApiError("GET /api/projects -> 401: missing or invalid session token")

    monkeypatch.setattr(mcp_module, "_request", raise_error)
    with pytest.raises(UnexpectedToolError):
        await server.call_tool("list_projects", {})
