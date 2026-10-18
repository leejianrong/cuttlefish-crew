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
CADDYFILE = Path(__file__).parents[2] / "deploy" / "relay" / "Caddyfile"
COMPOSE = Path(__file__).parents[2] / "deploy" / "relay" / "compose.yaml"
HASH_HELPER = Path(__file__).parents[2] / "deploy" / "droplet" / "gate_hash.sh"


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


def caddy_image() -> str:
    """The Caddy image the stack pins, read the way the bootstrap reads it."""
    for line in COMPOSE.read_text().splitlines():
        if line.startswith("    image: caddy:"):
            return line.split("image: ", 1)[1].strip()
    raise AssertionError("no pinned Caddy image in compose.yaml")


def gate_hash(password: str, *, newline: bool = False) -> str:
    """A bcrypt hash of `password`, made by the helper the bootstrap uses (which gets the password
    with no trailing newline, from `printf '%s'`)."""
    done = subprocess.run(
        [str(HASH_HELPER), caddy_image()], input=password + ("\n" if newline else ""),
        capture_output=True, text=True, timeout=120, check=True,
    )  # fmt: skip
    return done.stdout.strip()


@dataclass
class Stack:
    """The compose stack, plain http on a free loopback port, behind the shared password."""

    http: str
    ws: str
    user: str
    password: str
    owner_secret: str
    project: str

    @property
    def basic(self) -> tuple[str, str]:
        return (self.user, self.password)

    def container(self, service: str) -> str:
        return f"{self.project}-{service}-1"
