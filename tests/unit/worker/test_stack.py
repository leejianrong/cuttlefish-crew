"""CUT-216: a worker runs a project's compose stack and reports what it published."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.worker.execute import checkout
from cuttlefish.worker.stack import StackRunner, parse_ports, project_name

PS = {
    "Service": "web",
    "Publishers": [
        {"URL": "0.0.0.0", "TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"},
        {"URL": "::", "TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"},
        {"URL": "", "TargetPort": 9000, "PublishedPort": 0, "Protocol": "tcp"},
        {"URL": "0.0.0.0", "TargetPort": 53, "PublishedPort": 5353, "Protocol": "udp"},
    ],
}


def test_published_tcp_ports_are_read_from_either_output_shape_once_each() -> None:
    one = [{"service": "web", "target": 80, "published": 8080}]
    assert parse_ports(json.dumps(PS)) == one  # a single object (one line)
    assert parse_ports(json.dumps(PS) + "\n" + json.dumps({"Service": "db"})) == one  # json lines
    assert parse_ports(json.dumps([PS, {"Publishers": None}])) == one  # one array
    assert parse_ports("not json\n[") == []


def test_a_project_name_is_a_safe_compose_project_name() -> None:
    assert project_name("AB12 cd/../x") == "cuttlefish-ab12cdx"
    assert project_name("!!!") == "cuttlefish-project"


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.workdir = tmp_path / "work"
        self.calls: list[list[str]] = []
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.code, self.out, self.err = 0, json.dumps(PS), ""
        self.runner = StackRunner(self.workdir, lambda _t: None, self.post, self.docker)

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posted.append((path, body))
        return {}

    async def docker(self, args: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
        self.calls.append(args)
        return (
            (self.code, self.out, self.err) if "ps" not in args or self.code else (0, self.out, "")
        )

    async def settle(self) -> None:
        await asyncio.gather(*self.runner._tasks)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


async def test_up_runs_compose_in_the_checkout_and_reports_the_published_ports(rig: Rig) -> None:
    root = checkout(rig.workdir, "p1", None)
    (root / "docker-compose.yml").write_text("services: {}\n")
    rig.runner.handle({"op": "up", "project_id": "p1", "name": "Beta", "repo": None})
    rig.runner.handle({"op": "up", "project_id": "p1"})  # one at a time per project
    await rig.settle()
    up = rig.calls[0]
    assert up[:3] == ["compose", "-p", "cuttlefish-p1"] and up[-3:] == [
        "up",
        "-d",
        "--remove-orphans",
    ]
    assert str(root / "docker-compose.yml") in up
    assert rig.posted == [
        (
            "/api/worker/stack",
            {
                "project_id": "p1",
                "state": "up",
                "ports": [{"service": "web", "target": 80, "published": 8080}],
                "error": "",
            },
        )
    ]


async def test_a_project_with_no_compose_file_fails_with_that_said(rig: Rig) -> None:
    rig.runner.handle({"op": "up", "project_id": "p1", "name": "Beta", "repo": None})
    await rig.settle()
    [(_path, body)] = rig.posted
    assert body["state"] == "failed" and "no compose file" in body["error"] and rig.calls == []


async def test_a_compose_error_is_reported_trimmed_and_down_runs_without_a_file(rig: Rig) -> None:
    root = checkout(rig.workdir, "p1", None)
    (root / "compose.yaml").write_text("services: {}\n")
    rig.code, rig.err = 1, "x" * 400 + "port is already allocated"
    rig.runner.handle({"op": "up", "project_id": "p1"})
    await rig.settle()
    assert rig.posted[-1][1]["state"] == "failed" and rig.posted[-1][1]["error"].endswith(
        "allocated"
    )
    assert len(rig.posted[-1][1]["error"]) == 300
    rig.code, rig.err = 0, ""
    (root / "compose.yaml").unlink()  # down needs only the project name
    rig.runner.handle({"op": "down", "project_id": "p1"})
    await rig.settle()
    assert rig.calls[-1] == ["compose", "-p", "cuttlefish-p1", "down", "--remove-orphans"]
    assert rig.posted[-1][1] == {"project_id": "p1", "state": "down", "ports": [], "error": ""}


async def test_an_order_that_is_not_up_or_down_is_ignored(rig: Rig) -> None:
    rig.runner.handle({"op": "rm -rf", "project_id": "p1"})
    rig.runner.handle({"op": "up"})
    assert not rig.runner._tasks


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hi " + self.path.encode())

    def log_message(self, *_args: Any) -> None:
        pass


async def test_a_page_is_fetched_from_loopback_only_for_a_published_port(rig: Rig) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        root = checkout(rig.workdir, "p1", None)
        (root / "compose.yaml").write_text("services: {}\n")
        rig.out = json.dumps({"Service": "web", "Publishers": [
            {"TargetPort": 80, "PublishedPort": port, "Protocol": "tcp"}
        ]})  # fmt: skip
        rig.runner.handle({"op": "up", "project_id": "p1"})
        await rig.settle()
        rig.runner.serve({"id": "r1", "project_id": "p1", "port": port, "path": "/a?b=1"})
        rig.runner.serve({"id": "r2", "project_id": "p1", "port": port + 1, "path": "/"})
        rig.runner.serve({"id": "r3", "project_id": "p1", "port": port, "path": "http://evil/"})
        await rig.settle()
        answers = {p.rsplit("/", 1)[1]: b for p, b in rig.posted if "/pages/" in p}
        assert answers["r1"]["status"] == 200
        assert base64.b64decode(answers["r1"]["body"]) == b"hi /a?b=1"
        assert answers["r2"]["status"] == 404 and answers["r3"]["status"] == 404
    finally:
        server.shutdown()
