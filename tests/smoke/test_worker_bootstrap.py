"""Smoke (CUT-213): the worker bootstrap, run for real in a fresh Ubuntu 24.04 container.

`deploy/worker/bootstrap.sh` installs what the worker profile lists from Ubuntu's repositories and
from the pinned, checksummed downloads (so it needs the network and takes a few minutes), then the
*Python* check, `cuttlefish worker verify`, is run in the same container: the script and the
profile's own parser must agree about what a matching machine is. Opt in with
`CUTTLEFISH_SMOKE_WORKER_BOOTSTRAP=1` (`make test-worker-bootstrap`).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from smoke_rig import docker

REPO = Path(__file__).parents[2]
PROFILE = REPO / "src" / "cuttlefish" / "worker" / "profile.toml"
# What the Python check needs to run from a checkout, and nothing it does not.
CHECKOUT = ("pyproject.toml", "uv.lock", "README.md", "LICENSE", "src")


def _enabled() -> None:
    if os.environ.get("CUTTLEFISH_SMOKE_WORKER_BOOTSTRAP") != "1":
        pytest.skip(
            "set CUTTLEFISH_SMOKE_WORKER_BOOTSTRAP=1 (make test-worker-bootstrap): "
            "it installs packages"
        )
    if shutil.which("docker") is None:
        pytest.skip("needs docker")


@dataclass
class Box:
    name: str

    def sh(
        self, script: str, env: dict[str, str] | None = None, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        args = ["docker", "exec"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        return subprocess.run(
            [*args, self.name, "bash", "-c", script],
            capture_output=True,
            text=True,
            check=check,
            timeout=1200,
        )

    def bootstrap(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return self.sh(
            "/repo/deploy/worker/bootstrap.sh",
            {
                "BOOTSTRAP_SKIP_SERVICES": "1",
                "PROFILE": "/repo/src/cuttlefish/worker/profile.toml",
                **extra,
            },
        )

    def verify(self) -> subprocess.CompletedProcess[str]:
        """The Python check, from the checkout, with the tools the bootstrap installed."""
        return self.sh("cd /repo && uv run --frozen --no-dev cuttlefish worker verify")

    def fingerprint(self) -> str:
        listing = self.sh(
            "cd /usr/local/bin && sha256sum $(ls) "
            "&& dpkg-query -W -f='${Package} ${Version}\\n' | sort",
            check=True,
        ).stdout
        return hashlib.sha256(listing.encode()).hexdigest()


def _start(name: str) -> Box:
    docker("run", "-d", "--name", name, "ubuntu:24.04", "sleep", "infinity")
    docker("exec", name, "mkdir", "-p", "/repo/src/cuttlefish/worker")
    docker("cp", str(REPO / "deploy" / "worker"), f"{name}:/repo/deploy-worker")
    docker(
        "exec",
        name,
        "bash",
        "-c",
        "mkdir -p /repo/deploy && mv /repo/deploy-worker /repo/deploy/worker",
    )
    docker("cp", str(PROFILE), f"{name}:/repo/src/cuttlefish/worker/profile.toml")
    box = Box(name)
    _copy_checkout(box)
    return box


def _copy_checkout(box: Box) -> None:
    for item in CHECKOUT:
        target = f"{box.name}:/repo/{item}"
        if item == "src":  # the profile is already there; the rest of the package comes with it
            docker(
                "cp", str(REPO / "src" / "cuttlefish") + "/.", f"{box.name}:/repo/src/cuttlefish"
            )
        else:
            docker("cp", str(REPO / item), target)


@pytest.fixture(scope="module")
def box() -> Iterator[Box]:
    _enabled()
    name = f"cuttlefish-smoke-worker-{secrets.token_hex(4)}"
    b = _start(name)
    try:
        yield b
    finally:
        docker("rm", "-f", name, check=False)


@pytest.fixture
def scratch() -> Iterator[Box]:
    _enabled()
    name = f"cuttlefish-smoke-worker-scratch-{secrets.token_hex(4)}"
    b = _start(name)
    try:
        yield b
    finally:
        docker("rm", "-f", name, check=False)


def test_the_plan_lists_every_profile_tool_without_installing_anything() -> None:
    """`--plan` needs no root and no network, so it runs on any machine."""
    done = subprocess.run(
        ["bash", str(REPO / "deploy" / "worker" / "bootstrap.sh"), "--plan"],
        capture_output=True,
        text=True,
        check=True,
    )
    from cuttlefish.worker.profile import load

    rows = [line.split("\t") for line in done.stdout.splitlines()]
    expected = load().tools
    assert [r[0] for r in rows] == [t.name for t in expected]
    for row, tool in zip(rows, expected, strict=True):
        assert row[1] == tool.command and row[2].split() == list(tool.version_args)
        assert row[3] == (tool.minimum or "-") and row[4] == (tool.group or "-")
        assert row[5].split() == (list(tool.apt) or ["-"]) and row[6] == (tool.how or "-")


@pytest.mark.parametrize(
    ("what", "extra", "message"),
    [
        ("an agent that is not a name", {"WORKER_AGENTS": "x;reboot"}, "WORKER_AGENTS"),
        ("an agent the profile lacks", {"WORKER_AGENTS": "emacs"}, "does not list"),
        ("a version with a shell in it", {"UV_VERSION": "1; reboot"}, "plain version"),
        ("a user that is not there", {"WORKER_USER": "nobody-here"}, "does not exist"),
    ],
)
def test_bad_input_is_refused_before_anything_is_installed(
    scratch: Box, what: str, extra: dict[str, str], message: str
) -> None:
    result = scratch.bootstrap(**extra)
    assert result.returncode != 0, what
    assert message in result.stderr, (what, result.stderr)
    assert scratch.sh("command -v git").returncode != 0, "something was installed"


def test_a_tool_that_is_already_there_is_left_alone_and_an_old_one_is_replaced(
    scratch: Box,
) -> None:
    """A docker put there some other way is not given a second one beside it; node 18 is too old."""
    scratch.sh(
        'printf \'#!/bin/sh\\nif [ "$1" = compose ]; then echo 2.29.0; '
        "else echo Docker version 27.1.1; fi\\n' > /usr/local/bin/docker "
        "&& chmod 755 /usr/local/bin/docker "
        "&& printf '#!/bin/sh\\necho v18.19.1\\n' > /usr/local/bin/node "
        "&& chmod 755 /usr/local/bin/node",
        check=True,
    )
    result = scratch.bootstrap()
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "docker is already here" in result.stdout
    assert "docker-compose is already here" in result.stdout
    assert scratch.sh("dpkg -s docker.io").returncode != 0, "a second docker was installed"
    assert "node is already here" not in result.stdout
    assert scratch.sh("node --version").stdout.strip() == "v22.12.0"


def test_a_fresh_ubuntu_matches_the_profile_after_bootstrap(box: Box) -> None:
    first = box.bootstrap(WORKER_AGENTS="kopicode,claude")
    assert first.returncode == 0, first.stdout[-2500:] + first.stderr[-2500:]
    assert "matches the worker profile" in first.stdout
    assert "MISSING" not in first.stdout
    # codex was not asked for, and one agent satisfies the profile's group.
    assert box.sh("command -v codex").returncode != 0
    for tool in ("git", "tmux", "gh", "docker", "uv", "node", "kopicode", "claude"):
        assert box.sh(f"command -v {tool}").returncode == 0, tool
    assert box.sh("docker compose version").returncode == 0
    verified = box.verify()
    assert verified.returncode == 0, verified.stdout[-1500:] + verified.stderr[-1500:]
    assert "matches the worker profile" in verified.stdout


def test_running_it_again_changes_nothing(box: Box) -> None:
    before = box.fingerprint()
    again = box.bootstrap(WORKER_AGENTS="kopicode,claude")
    assert again.returncode == 0, again.stdout[-2000:] + again.stderr[-2000:]
    for tool in ("git", "docker", "uv", "node", "kopicode", "claude"):
        assert f"{tool} is already here" in again.stdout, tool
    assert box.fingerprint() == before


def test_verify_names_what_is_missing_and_bootstrap_puts_it_right(box: Box) -> None:
    box.sh("apt-get remove -y -qq docker.io docker-compose-v2 >/dev/null 2>&1", check=True)
    broken = box.verify()
    assert broken.returncode == 1
    assert "DRIFT   docker missing" in broken.stdout
    assert "DRIFT   docker-compose missing" in broken.stdout
    assert box.bootstrap(WORKER_AGENTS="kopicode,claude").returncode == 0
    assert box.verify().returncode == 0
