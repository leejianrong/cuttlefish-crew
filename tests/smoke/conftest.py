"""Smoke (CUT-144): the relay *image*, run as it will be deployed, with real sockets.

These tests need a built image and a docker daemon, so they skip themselves unless
`CUTTLEFISH_SMOKE_IMAGE` names one (`make test-smoke` builds `deploy/relay/Dockerfile` and sets it).
The deploy pipeline (CUT-141) sets it to the image it just built, by digest, before anything takes
traffic. Nothing here reads a real secret: the owner secret and the gate password are made per run.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

# The Python browser the relay integration tests already use, and this folder's own helpers.
sys.path.insert(0, str(Path(__file__).parents[1] / "integration" / "relay"))
sys.path.insert(0, str(Path(__file__).parent))

from smoke_rig import (
    CADDY_IMAGE_ENV,
    CADDYFILE,
    IMAGE_ENV,
    Container,
    Gate,
    docker,
    free_port,
    wait_for,
)


@pytest.fixture(scope="session")
def image() -> str:
    name = os.environ.get(IMAGE_ENV)
    if not name:
        pytest.skip(f"set {IMAGE_ENV} to a built relay image (make test-smoke does)")
    if shutil.which("docker") is None:
        pytest.skip("needs a docker daemon on PATH")
    return name


@pytest.fixture(scope="session")
def relay(image: str) -> Iterator[Container]:
    """The image, started the way the Droplet starts it (its own CMD), on a free loopback port."""
    port = free_port()
    secret = secrets.token_urlsafe(32)
    name = f"cuttlefish-smoke-relay-{secrets.token_hex(4)}"
    docker(
        "run", "-d", "--name", name,
        "-p", f"127.0.0.1:{port}:8450",
        "-e", f"CUTTLEFISH_RELAY_OWNER_SECRET={secret}",
        image,
    )  # fmt: skip
    container = Container(name, port, secret)
    try:
        wait_for(f"{container.http}/healthz")
        yield container
    finally:
        docker("rm", "-f", name, check=False)


@pytest.fixture(scope="session")
def caddy(relay: Container) -> Iterator[Gate]:
    """The repository's Caddyfile in front of the relay, with a shared password, on plain http."""
    caddy_image = os.environ.get(CADDY_IMAGE_ENV, "caddy:2")
    password = secrets.token_urlsafe(12)
    hashed = docker("run", "--rm", caddy_image, "caddy", "hash-password", "--plaintext", password)
    port = free_port()
    name = f"cuttlefish-smoke-caddy-{secrets.token_hex(4)}"
    docker(
        "run", "-d", "--name", name, "--network", "host",
        "-v", f"{CADDYFILE}:/etc/caddy/Caddyfile:ro",
        "-e", f"RELAY_ADDRESS=:{port}",
        "-e", "GATE_USER=owner",
        "-e", f"GATE_PASSWORD_HASH={hashed.stdout.strip()}",
        "-e", f"RELAY_UPSTREAM=127.0.0.1:{relay.port}",
        caddy_image,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
                break
            except urllib.error.HTTPError:
                break  # a 401 means Caddy is up and asking for the password
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("caddy never came up: " + docker("logs", name, check=False).stderr)
        yield Gate(f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}", "owner", password)
    finally:
        docker("rm", "-f", name, check=False)
