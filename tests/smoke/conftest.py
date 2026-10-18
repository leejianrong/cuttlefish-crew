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
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# The Python browser the relay integration tests already use, and this folder's own helpers.
sys.path.insert(0, str(Path(__file__).parents[1] / "integration" / "relay"))
sys.path.insert(0, str(Path(__file__).parent))

from smoke_rig import (
    IMAGE_ENV,
    Container,
    Stack,
    docker,
    free_port,
    gate_hash,
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


def extract_bundle(image: str, into: Path) -> None:
    """Take /app/deploy-bundle out of the image, the way `relay-deploy` does."""
    container = docker("create", image).stdout.strip()
    try:
        docker("cp", f"{container}:/app/deploy-bundle/.", str(into))
    finally:
        docker("rm", "-f", container, check=False)


@pytest.fixture(scope="session")
def stack(image: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """The compose stack from the image's own bundle: the relay behind Caddy, plain http on a free
    loopback port (RELAY_ADDRESS=:80), with a shared password made by the bootstrap's helper."""
    work = tmp_path_factory.mktemp("stack")
    extract_bundle(image, work)
    password = secrets.token_urlsafe(12)
    owner_secret = secrets.token_urlsafe(32)
    (work / "relay.env").write_text(f"CUTTLEFISH_RELAY_OWNER_SECRET={owner_secret}\n")
    (work / "caddy.env").write_text(
        f"RELAY_ADDRESS=:80\nGATE_USER=owner\nGATE_PASSWORD_HASH='{gate_hash(password)}'\n"
    )
    http_port, https_port = free_port(), free_port()
    project = f"cuttlefish-smoke-{secrets.token_hex(4)}"
    env = {
        **os.environ,
        "RELAY_IMAGE": image,
        "RELAY_ENV_FILE": str(work / "relay.env"),
        "CADDY_ENV_FILE": str(work / "caddy.env"),
        "HTTP_PORT": str(http_port),
        "HTTPS_PORT": str(https_port),
    }
    compose = ["docker", "compose", "-p", project, "-f", str(work / "compose.yaml")]
    try:
        up = subprocess.run(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "90"],
            env=env, capture_output=True, text=True, timeout=180,
        )  # fmt: skip
        assert up.returncode == 0, up.stdout + up.stderr
        yield Stack(
            f"http://127.0.0.1:{http_port}",
            f"ws://127.0.0.1:{http_port}",
            "owner",
            password,
            owner_secret,
            project,
        )
    finally:
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], env=env, capture_output=True)
