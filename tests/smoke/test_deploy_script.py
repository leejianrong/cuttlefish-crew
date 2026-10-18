"""Smoke (CUT-141, CUT-153): `relay-deploy`, the one script the pipeline's key may run on the
Droplet, run for real against docker, docker compose and a local registry. A release is a relay
image by digest that carries its own compose file and Caddyfile; the script deploys it, rolls back
to the previous release when the new one is not healthy, and refuses anything that is not this
repository at a digest or whose compose file breaks the allow-list in deploy/relay/policy.py."""

from __future__ import annotations

import base64
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from smoke_rig import docker, free_port, gate_hash

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "deploy" / "relay" / "relay-deploy.sh"
POLICY = REPO / "deploy" / "relay" / "policy.py"


@dataclass
class Registry:
    repo: str  # localhost:<port>/relay-test
    good_a: str  # image references by digest
    good_b: str
    unhealthy_relay: str
    bad_caddyfile: str
    not_read_only: str
    privileged: str
    host_mount: str
    no_bundle: str
    symlinked_caddyfile: str
    extra_file: str


def _build(image: str, tag: str, root_steps: str) -> None:
    """A release derived from the good image, with its bundle (or its command) changed."""
    dockerfile = f"FROM {image}\nUSER root\n{root_steps}\nUSER relay\n"
    subprocess.run(
        ["docker", "build", "-q", "-t", tag, "-"],
        input=dockerfile, text=True, capture_output=True, check=True, timeout=180,
    )  # fmt: skip


def _push(tag: str) -> str:
    docker("push", "-q", tag)
    return docker("image", "inspect", "-f", "{{index .RepoDigests 0}}", tag).stdout.strip()


@pytest.fixture(scope="module")
def registry(image: str) -> Iterator[Registry]:
    port = free_port()
    name = f"cuttlefish-smoke-registry-{secrets.token_hex(4)}"
    docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5000", "registry:2")
    repo = f"localhost:{port}/relay-test"
    bundle = "/app/deploy-bundle"
    variants = {
        "good_a": None,
        "good_b": "LABEL smoke.version=b",
        # The relay exits at once: a release that never becomes healthy.
        "unhealthy_relay": 'CMD ["false"]',
        # Caddy cannot start on this Caddyfile.
        "bad_caddyfile": "RUN echo 'this is not { a caddyfile' > " + bundle + "/Caddyfile",
        # Each of these is a compose file the allow-list must refuse.
        "not_read_only": (
            f"RUN sed -i 's/^    read_only: true/    read_only: false/' {bundle}/compose.yaml"
        ),
        "privileged": (
            f"RUN sed -i 's/^    restart: unless-stopped/    restart: unless-stopped\\n"
            f"    privileged: true/' {bundle}/compose.yaml"
        ),
        "host_mount": (
            f"RUN sed -i 's#^      - relay-data:/data#      - relay-data:/data\\n      - /:/host#' "
            f"{bundle}/compose.yaml"
        ),
        "no_bundle": f"RUN rm -rf {bundle}",
        "symlinked_caddyfile": f"RUN ln -sf /etc/hostname {bundle}/Caddyfile",
        "extra_file": f"RUN echo hi > {bundle}/surprise.sh",
    }
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        refs: dict[str, str] = {}
        for key, steps in variants.items():
            tag = f"{repo}:{key.replace('_', '-')}"
            if steps is None:
                docker("tag", image, tag)
            else:
                _build(image, tag, steps)
            refs[key] = _push(tag)
        assert len(set(refs.values())) == len(refs)
        yield Registry(repo, **refs)
    finally:
        docker("rm", "-f", name, check=False)


@pytest.fixture(scope="module")
def gate() -> tuple[str, str]:
    password = secrets.token_urlsafe(12)
    return password, gate_hash(password)


class Droplet:
    """One scratch Droplet: its own state folder, compose project and ports."""

    def __init__(self, registry: Registry, tmp_path: Path, gate: tuple[str, str]) -> None:
        self.registry = registry
        self.password = gate[0]
        self.state = tmp_path / "state"
        self.state.mkdir()
        (self.state / "relay.env").write_text(
            f"CUTTLEFISH_RELAY_OWNER_SECRET={secrets.token_urlsafe(32)}\n"
        )
        (self.state / "caddy.env").write_text(
            f"RELAY_ADDRESS=:80\nGATE_USER=owner\nGATE_PASSWORD_HASH='{gate[1]}'\n"
        )
        self.project = f"cuttlefish-smoke-{secrets.token_hex(4)}"
        self.http_port = free_port()
        self.https_port = free_port()
        self.env = {
            **os.environ,
            "RELAY_IMAGE_REPO": registry.repo,
            "RELAY_STATE_DIR": str(self.state),
            "RELAY_PROJECT": self.project,
            "RELAY_HTTP_PORT": str(self.http_port),
            "RELAY_HTTPS_PORT": str(self.https_port),
            "RELAY_POLICY_FILE": str(POLICY),
            "RELAY_HEALTH_SECONDS": "40",
        }

    def deploy(self, request: str, *, via_ssh: bool = False) -> subprocess.CompletedProcess[str]:
        args = [str(SCRIPT)]
        env = dict(self.env)
        if via_ssh:  # what sshd does for a forced command
            env["SSH_ORIGINAL_COMMAND"] = request
        else:
            args.append(request)
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=300)

    def container(self, service: str) -> str:
        return f"{self.project}-{service}-1"

    def exists(self, service: str = "relay") -> bool:
        return docker("inspect", self.container(service), check=False).returncode == 0

    def running_image(self) -> str:
        return docker("inspect", "-f", "{{.Config.Image}}", self.container("relay")).stdout.strip()

    def container_id(self, service: str) -> str:
        return docker("inspect", "-f", "{{.Id}}", self.container(service)).stdout.strip()

    def gate_status(self, *, authed: bool = False) -> int:
        request = urllib.request.Request(f"http://127.0.0.1:{self.http_port}/healthz")
        if authed:
            token = base64.b64encode(f"owner:{self.password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        except OSError:
            return 0

    def current(self) -> str:
        return (self.state / "current").read_text().strip()

    def cleanup(self) -> None:
        subprocess.run(
            ["docker", "compose", "-p", self.project, "down", "-v", "--remove-orphans"],
            capture_output=True, timeout=120,
        )  # fmt: skip


@pytest.fixture
def droplet(registry: Registry, tmp_path: Path, gate: tuple[str, str]) -> Iterator[Droplet]:
    d = Droplet(registry, tmp_path, gate)
    try:
        yield d
    finally:
        d.cleanup()


def digest_of(ref: str) -> str:
    return ref.split("@sha256:")[1]


def test_a_first_deploy_starts_relay_and_caddy_by_digest_and_records_the_release(
    droplet: Droplet,
) -> None:
    result = droplet.deploy(droplet.registry.good_a)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOYED sha256:" in result.stdout
    assert droplet.running_image() == droplet.registry.good_a
    assert droplet.exists("caddy")
    assert droplet.gate_status() == 401 and droplet.gate_status(authed=True) == 200
    assert droplet.current() == digest_of(droplet.registry.good_a)
    release = droplet.state / "releases" / droplet.current()
    assert sorted(p.name for p in release.iterdir()) == ["Caddyfile", "compose.yaml", "image"]


def test_the_request_may_arrive_the_way_sshd_hands_it_over(droplet: Droplet) -> None:
    result = droplet.deploy(droplet.registry.good_a, via_ssh=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert droplet.running_image() == droplet.registry.good_a


def test_the_relay_is_not_published_and_caddy_publishes_only_the_two_ports(
    droplet: Droplet,
) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    assert docker("port", droplet.container("relay")).stdout.strip() == ""
    ports = docker("port", droplet.container("caddy")).stdout
    assert f"0.0.0.0:{droplet.http_port}" in ports and f"0.0.0.0:{droplet.https_port}" in ports
    assert "8450" not in ports and "2019" not in ports


def test_a_second_deploy_replaces_the_first_and_the_data_survives(droplet: Droplet) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    docker("exec", droplet.container("relay"), "sh", "-c", "echo kept > /data/survives")
    result = droplet.deploy(droplet.registry.good_b)
    assert result.returncode == 0, result.stdout + result.stderr
    assert droplet.running_image() == droplet.registry.good_b
    assert droplet.current() == digest_of(droplet.registry.good_b)
    assert (droplet.state / "previous").read_text().strip() == digest_of(droplet.registry.good_a)
    assert (
        docker("exec", droplet.container("relay"), "cat", "/data/survives").stdout.strip() == "kept"
    )
    assert droplet.gate_status(authed=True) == 200


def test_redeploying_the_running_release_changes_nothing(droplet: Droplet) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    before = (droplet.container_id("relay"), droplet.container_id("caddy"))
    again = droplet.deploy(droplet.registry.good_a)
    assert again.returncode == 0, again.stdout + again.stderr
    assert (droplet.container_id("relay"), droplet.container_id("caddy")) == before
    assert (droplet.state / "releases" / droplet.current() / "compose.yaml").exists()


@pytest.mark.parametrize("broken", ["unhealthy_relay", "bad_caddyfile"])
def test_a_release_that_is_not_healthy_is_rolled_back_to_the_previous_one(
    droplet: Droplet, broken: str
) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    bad = getattr(droplet.registry, broken)
    result = droplet.deploy(bad)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ROLLED BACK to sha256:" + digest_of(droplet.registry.good_a) in result.stdout
    assert droplet.running_image() == droplet.registry.good_a
    assert droplet.gate_status(authed=True) == 200
    assert droplet.current() == digest_of(droplet.registry.good_a)  # never recorded the bad one
    assert not (droplet.state / "releases" / digest_of(bad)).exists()


def test_a_first_deploy_that_is_unhealthy_leaves_nothing_running(droplet: Droplet) -> None:
    result = droplet.deploy(droplet.registry.unhealthy_relay)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no previous deploy to restore" in result.stdout
    assert not droplet.exists("relay") and not droplet.exists("caddy")
    assert not (droplet.state / "current").exists()


@pytest.mark.parametrize("variant", ["not_read_only", "privileged", "host_mount"])
def test_a_compose_file_that_breaks_the_allow_list_is_refused_and_nothing_changes(
    droplet: Droplet, variant: str
) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    before = (droplet.container_id("relay"), droplet.container_id("caddy"))
    result = droplet.deploy(getattr(droplet.registry, variant))
    assert result.returncode == 65, result.stdout + result.stderr
    assert "does not pass policy" in result.stdout
    assert (droplet.container_id("relay"), droplet.container_id("caddy")) == before
    assert droplet.current() == digest_of(droplet.registry.good_a)
    assert droplet.gate_status(authed=True) == 200


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        ("no_bundle", "has no /app/deploy-bundle"),
        ("symlinked_caddyfile", "not a plain file"),
        ("extra_file", "besides compose.yaml and Caddyfile"),
    ],
)
def test_a_bundle_that_is_missing_or_unusual_is_refused(
    droplet: Droplet, variant: str, message: str
) -> None:
    result = droplet.deploy(getattr(droplet.registry, variant))
    assert result.returncode == 65, result.stdout + result.stderr
    assert message in result.stdout
    assert not droplet.exists("relay")


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
    assert not droplet.exists("relay")
    assert not Path("/tmp/pwned").exists()


@pytest.mark.parametrize("missing", ["relay.env", "caddy.env"])
def test_a_droplet_that_is_not_set_up_says_so(droplet: Droplet, missing: str) -> None:
    (droplet.state / missing).unlink()
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


def test_the_relay_is_healthy_again_after_its_container_restarts(droplet: Droplet) -> None:
    assert droplet.deploy(droplet.registry.good_a).returncode == 0
    docker("restart", droplet.container("relay"))
    wait_for_ok = droplet.gate_status
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and wait_for_ok(authed=True) != 200:
        time.sleep(1)
    assert droplet.gate_status(authed=True) == 200
