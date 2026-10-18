"""Helpers for the relay-image smoke tests (CUT-144): docker, a free port, the running container."""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

IMAGE_ENV = "CUTTLEFISH_SMOKE_IMAGE"
CADDY_IMAGE_ENV = "CUTTLEFISH_SMOKE_CADDY_IMAGE"
CADDYFILE = Path(__file__).parents[2] / "deploy" / "relay" / "Caddyfile"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=120
    )


def wait_for(url: str, *, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(0.5)
    raise AssertionError(f"{url} never answered: {last}")


@dataclass
class Container:
    name: str
    port: int
    owner_secret: str

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def restart(self) -> None:
        docker("restart", self.name)
        wait_for(f"{self.http}/healthz")


@dataclass
class Gate:
    http: str
    ws: str
    user: str
    password: str
