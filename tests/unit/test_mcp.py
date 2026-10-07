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
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

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
        "get_permissions",
        "list_builtin_roles",
        "list_templates",
        "set_project_mode",
        "update_roles",
        "list_requests",
        "answer_request",
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


async def test_a_fleet_api_error_reaches_the_client_with_the_daemons_reason(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `ToolError` is the one exception type whose message the SDK passes to the client;
    anything else becomes a bare "Error executing tool <name>" (the V5-E1b finding: the
    dashboard showed why a start failed and an MCP client did not)."""

    def raise_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise FleetApiError("POST /api/projects/p1/start -> 409: 'kopicode' is not on PATH")

    monkeypatch.setattr(mcp_module, "_request", raise_error)
    with pytest.raises(ToolError, match="'kopicode' is not on PATH") as caught:
        await server.call_tool("start_project", {"project_id": "p1", "roles": []})

    assert not isinstance(caught.value, UnexpectedToolError)


def test_the_http_error_body_is_reduced_to_its_detail_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import urllib.error

    body = b"{\"detail\":\"project 'p1' failed to start: 'kopicode' is not on PATH\"}"

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("http://x", 409, "Conflict", {}, io.BytesIO(body))  # type: ignore[arg-type]

    monkeypatch.setattr(mcp_module.urllib.request, "urlopen", refuse)
    with pytest.raises(FleetApiError) as caught:
        mcp_module._request("http://x", "t", "POST", "/api/projects/p1/start", {})

    assert str(caught.value) == (
        "POST /api/projects/p1/start -> 409: project 'p1' failed to start: "
        "'kopicode' is not on PATH"
    )


def test_a_body_that_is_not_json_is_kept_as_it_is() -> None:
    assert mcp_module._detail_text("plain text") == "plain text"
    assert mcp_module._detail_text('{"other": 1}') == '{"other": 1}'


async def test_register_project_forwards_mode_and_template(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_request(monkeypatch, {"id": "new"})
    await server.call_tool(
        "register_project",
        {"name": "demo", "root": "/tmp/demo", "mode": "ask-first", "template": "builder-reviewer"},
    )
    assert calls[0][4] == {
        "name": "demo",
        "root": "/tmp/demo",
        "mode": "ask-first",
        "template": "builder-reviewer",
    }


@pytest.mark.parametrize(
    ("tool", "args", "method", "path", "body"),
    [
        ("get_permissions", {}, "GET", "/api/permissions", None),
        ("list_builtin_roles", {}, "GET", "/api/roles", None),
        ("list_templates", {}, "GET", "/api/templates", None),
        (
            "set_project_mode",
            {"project_id": "p", "mode": "auto"},
            "PATCH",
            "/api/projects/p/mode",
            {"mode": "auto"},
        ),
        (
            "update_roles",
            {"project_id": "p", "roles": [{"name": "reviewer", "access": "read-only"}]},
            "PATCH",
            "/api/projects/p/roles",
            {"roles": [{"name": "reviewer", "access": "read-only"}]},
        ),
        ("list_requests", {}, "GET", "/api/requests", None),
        ("list_requests", {"project_id": "p"}, "GET", "/api/projects/p/requests", None),
        (
            "answer_request",
            {"project_id": "p", "request_id": "r1", "answer": "deny"},
            "POST",
            "/api/projects/p/requests/r1/answer",
            {"answer": "deny"},
        ),
        (
            "answer_request",
            {
                "project_id": "p",
                "request_id": "r1",
                "answer": "allow_always",
                "rule": ["docker", "compose"],
            },
            "POST",
            "/api/projects/p/requests/r1/answer",
            {"answer": "allow_always", "rule": ["docker", "compose"]},
        ),
    ],
)
async def test_the_v4_tools_call_the_right_route(
    server: Any,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    args: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    calls = _stub_request(monkeypatch, {})
    result = await server.call_tool(tool, args)
    assert not result.is_error
    assert calls == [("http://127.0.0.1:9999", "test-token", method, path, body)]


async def test_a_409_from_answering_a_request_is_not_hidden(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: Any, **__: Any) -> dict[str, Any]:
        raise FleetApiError("POST /x -> 409: already denied")

    monkeypatch.setattr(mcp_module, "_request", refuse)
    with pytest.raises(ToolError, match="already denied"):
        await server.call_tool(
            "answer_request", {"project_id": "p", "request_id": "r", "answer": "allow_once"}
        )
