"""Smoke (CUT-141): `relay-deploy`, the one script the pipeline's key may run on the Droplet, run
for real against docker and a local registry. It deploys by digest, rolls back to the previous
container when the new one is not healthy, and refuses anything that is not this repository at a
digest."""

from __future__ import annotations

import os
import secrets
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from smoke_rig import docker, free_port, wait_for

SCRIPT = Path(__file__).parents[2] / "deploy" / "relay" / "relay-deploy.sh"


@dataclass
class Registry:
    repo: str  # localhost:<port>/relay-test
    good_a: str  # image references by digest
    good_b: str
    broken: str


def _push(image: str, tag: str, dockerfile: str | None = None) -> str:
    if dockerfile is None:
        docker("tag", image, tag)
    else:
        subprocess.run(
            ["docker", "build", "-q", "-t", tag, "-"],
            input=dockerfile,
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
        )
    docker("push", "-q", tag)
    digests = docker("image", "inspect", "-f", "{{index .RepoDigests 0}}", tag).stdout.strip()
    return digests


@pytest.fixture(scope="module")
def registry(image: str) -> Iterator[Registry]:
    port = free_port()
    name = f"cuttlefish-smoke-registry-{secrets.token_hex(4)}"
    docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5000", "registry:2")
    repo = f"localhost:{port}/relay-test"
    try:
        wait_for(f"http://127.0.0.1:{port}/v2/")
        good_a = _push(image, f"{repo}:a")
        good_b = _push(image, f"{repo}:b", f"FROM {image}\nLABEL smoke.version=b\n")
        # Starts and stops at once: a release that never becomes healthy.
        broken = _push(image, f"{repo}:broken", f'FROM {image}\nCMD ["false"]\n')
        assert len({good_a, good_b, broken}) == 3
        yield Registry(repo, good_a, good_b, broken)
    finally:
        docker("rm", "-f", name, check=False)


class Droplet:
    """One scratch Droplet: its own state folder, container name, volume and port."""

    def __init__(self, registry: Registry, tmp_path: Path) -> None:
        self.registry = registry
        self.state = tmp_path / "state"
        self.state.mkdir()
        (self.state / "relay.env").write_text(
            f"CUTTLEFISH_RELAY_OWNER_SECRET={secrets.token_urlsafe(32)}\n"
        )
        self.name = f"cuttlefish-smoke-deploy-{secrets.token_hex(4)}"
        self.port = free_port()
        self.env = {
            **os.environ,
            "RELAY_IMAGE_REPO": registry.repo,
            "RELAY_STATE_DIR": str(self.state),
            "RELAY_CONTAINER": self.name,
            "RELAY_VOLUME": self.name + "-data",
            "RELAY_PORT": str(self.port),
            "RELAY_HEALTH_SECONDS": "25",
        }

    def deploy(self, request: str, *, via_ssh: bool = False) -> subprocess.CompletedProcess[str]:
        args = [str(SCRIPT)]
        env = dict(self.env)
        if via_ssh:  # what sshd does for a forced command
            env["SSH_ORIGINAL_COMMAND"] = request
        else:
            args.append(request)
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)

    def running_image(self) -> str:
        return docker("inspect", "-f", "{{.Config.Image}}", self.name).stdout.strip()

    def healthz(self) -> int:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=3) as r:
                return int(r.status)
        except OSError:
            return 0

    def current(self) -> str:
        return (self.state / "current").read_text().strip()

    def cleanup(self) -> None:
        docker("rm", "-f", self.name, self.name + "-previous", check=False)
        docker("volume", "rm", "-f", self.name + "-data", check=False)


@pytest.fixture
def droplet(registry: Registry, tmp_path: Path) -> Iterator[Droplet]:
    d = Droplet(registry, tmp_path)
    try:
        yield d
    finally:
        d.cleanup()


def digest_of(ref: str) -> str:
    return ref.split("@sha256:")[1]


def test_a_first_deploy_starts_the_relay_by_digest_and_records_it(droplet: Droplet) -> None:
    result = droplet.deploy(droplet.registry.good_a)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOYED sha256:" in result.stdout
    assert droplet.healthz() == 200
    assert droplet.running_image() == droplet.registry.good_a
    assert droplet.current() == digest_of(droplet.registry.good_a)


def test_the_request_may_arrive_the_way_sshd_hands_it_over(droplet: Droplet) -> None:
    result = droplet.deploy(droplet.registry.good_a, via_ssh=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert droplet.running_image() == droplet.registry.good_a


def test_the_container_is_read_only_unprivileged_and_listens_on_loopback_only(
    droplet: Droplet,
) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    inspect = docker(
        "inspect",
        "-f",
        "{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}} "
        "{{.HostConfig.Memory}}",
        droplet.name,
    ).stdout
    assert "true" in inspect and "ALL" in inspect and "no-new-privileges" in inspect
    assert "536870912" in inspect
    ports = docker("port", droplet.name).stdout
    assert "127.0.0.1:" + str(droplet.port) in ports and "0.0.0.0" not in ports


def test_a_second_deploy_replaces_the_first_and_data_survives(droplet: Droplet) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    marker = "/data/survives"
    docker("exec", droplet.name, "sh", "-c", f"echo kept > {marker}")
    result = droplet.deploy(droplet.registry.good_b)
    assert result.returncode == 0, result.stdout + result.stderr
    assert droplet.running_image() == droplet.registry.good_b
    assert droplet.current() == digest_of(droplet.registry.good_b)
    assert docker("exec", droplet.name, "cat", marker).stdout.strip() == "kept"
    assert docker("inspect", droplet.name + "-previous", check=False).returncode != 0


def test_an_unhealthy_release_is_rolled_back_to_the_previous_one(droplet: Droplet) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    result = droplet.deploy(droplet.registry.broken)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ROLLED BACK to sha256:" + digest_of(droplet.registry.good_a) in result.stdout
    assert droplet.healthz() == 200
    assert droplet.running_image() == droplet.registry.good_a
    assert droplet.current() == digest_of(droplet.registry.good_a)  # never recorded the bad one


def test_a_first_deploy_that_is_unhealthy_leaves_nothing_running(droplet: Droplet) -> None:
    result = droplet.deploy(droplet.registry.broken)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no previous deploy to restore" in result.stdout
    assert docker("inspect", droplet.name, check=False).returncode != 0
    assert not (droplet.state / "current").exists()


@pytest.mark.parametrize(
    "request_text",
    [
        "",
        "ls -la",
        "cuttlefish-relay:latest",  # a tag moves
        "{repo}:a",
        "{repo}@sha256:abc",  # too short
        "{repo}@sha256:" + "G" * 64,  # not hex
        "{repo}@sha256:" + "a" * 64 + "; touch /tmp/pwned",
        "{repo}@sha256:" + "a" * 64 + " --privileged",
        "other.example/relay@sha256:" + "a" * 64,  # another repository
        "{repo}x@sha256:" + "a" * 64,
        "$(id)",
    ],
)
def test_anything_but_this_repository_at_a_digest_is_refused_before_docker_is_touched(
    droplet: Droplet, request_text: str
) -> None:
    result = droplet.deploy(request_text.format(repo=droplet.registry.repo))
    assert result.returncode == 64, result.stdout + result.stderr
    assert docker("inspect", droplet.name, check=False).returncode != 0
    assert not Path("/tmp/pwned").exists()


def test_a_droplet_that_is_not_set_up_says_so(droplet: Droplet) -> None:
    (droplet.state / "relay.env").unlink()
    result = droplet.deploy(droplet.registry.good_a)
    assert result.returncode == 78 and "not set up" in result.stdout


def test_two_deploys_never_run_at_once(droplet: Droplet) -> None:
    holder = subprocess.Popen(["flock", str(droplet.state / "deploy.lock"), "sleep", "6"])
    try:
        time.sleep(0.5)
        result = droplet.deploy(droplet.registry.good_a)
        assert result.returncode == 3 and "another deploy is running" in result.stdout
    finally:
        holder.kill()
        holder.wait()
