"""Smoke (CUT-143): the Droplet bootstrap script, run for real in a fresh Ubuntu 24.04 container.

It installs packages from Ubuntu's and Caddy's real repositories (so it needs the network and takes
a few minutes), then `verify.sh` checks the result, and a real sshd in the container is logged into
as the deploy user, the admin user and root. What a container cannot show (ufw, systemd, a real
reboot, the certificate) is not run: `BOOTSTRAP_SKIP_SERVICES=1` leaves those alone. Opt in with
`CUTTLEFISH_SMOKE_BOOTSTRAP=1` (`make test-bootstrap`).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from smoke_rig import docker, free_port

REPO = Path(__file__).parents[2]
DOMAIN = "relay.example.test"


def _enabled() -> None:
    if os.environ.get("CUTTLEFISH_SMOKE_BOOTSTRAP") != "1":
        pytest.skip("set CUTTLEFISH_SMOKE_BOOTSTRAP=1 (make test-bootstrap): it installs packages")
    if shutil.which("docker") is None or shutil.which("ssh-keygen") is None:
        pytest.skip("needs docker and ssh-keygen")


@dataclass
class Box:
    name: str
    ssh_port: int
    keys: Path
    admin_public: str
    deploy_public: str

    def sh(self, script: str, env: dict[str, str] | None = None, check: bool = False):  # type: ignore[no-untyped-def]
        args = ["docker", "exec"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        return subprocess.run(
            [*args, self.name, "bash", "-c", script],
            capture_output=True,
            text=True,
            check=check,
            timeout=900,
        )

    def bootstrap(self, **extra: str):  # type: ignore[no-untyped-def]
        env = {
            "RELAY_DOMAIN": DOMAIN,
            "ADMIN_PUBLIC_KEY": self.admin_public,
            "DEPLOY_PUBLIC_KEY": self.deploy_public,
            "BOOTSTRAP_SKIP_SERVICES": "1",
            **extra,
        }
        return self.sh("/repo/deploy/droplet/bootstrap.sh", env)

    def verify(self):  # type: ignore[no-untyped-def]
        return self.sh("/repo/deploy/droplet/verify.sh --no-services")

    def ssh(self, key: str, user: str, command: str, *options: str):  # type: ignore[no-untyped-def]
        return subprocess.run(
            [
                "ssh", "-i", str(self.keys / key), "-p", str(self.ssh_port),
                "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", *options, f"{user}@127.0.0.1", command,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )  # fmt: skip

    def read(self, path: str) -> str:
        return self.sh(f"cat {path}", check=True).stdout


def _keypair(directory: Path, name: str) -> str:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(directory / name)],
        check=True,
    )
    return (directory / f"{name}.pub").read_text().strip()


def _start(tmp: Path, name: str) -> Box:
    port = free_port()
    keys = tmp / "keys"
    keys.mkdir(exist_ok=True)
    admin = (
        _keypair(keys, "admin")
        if not (keys / "admin.pub").exists()
        else (keys / "admin.pub").read_text().strip()
    )
    deploy = (
        _keypair(keys, "deploy")
        if not (keys / "deploy.pub").exists()
        else (keys / "deploy.pub").read_text().strip()
    )
    docker(
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{port}:2222",
        "ubuntu:24.04",
        "sleep",
        "infinity",
    )
    docker("exec", name, "mkdir", "-p", "/repo")
    docker("cp", str(REPO / "deploy"), f"{name}:/repo/deploy")
    return Box(name, port, keys, admin, deploy)


@pytest.fixture(scope="module")
def box(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Box]:
    _enabled()
    name = f"cuttlefish-smoke-droplet-{secrets.token_hex(4)}"
    b = _start(tmp_path_factory.mktemp("droplet"), name)
    try:
        yield b
    finally:
        docker("rm", "-f", name, check=False)


@pytest.fixture
def scratch(tmp_path: Path) -> Iterator[Box]:
    """A second, untouched container, for inputs that must be refused before anything changes."""
    _enabled()
    name = f"cuttlefish-smoke-droplet-scratch-{secrets.token_hex(4)}"
    b = _start(tmp_path, name)
    try:
        yield b
    finally:
        docker("rm", "-f", name, check=False)


# -- refused before anything changes --


@pytest.mark.parametrize(
    ("what", "extra", "message"),
    [
        ("no domain", {"RELAY_DOMAIN": ""}, "RELAY_DOMAIN"),
        ("a domain with a shell in it", {"RELAY_DOMAIN": "x; reboot"}, "not a host name"),
        ("no admin key", {"ADMIN_PUBLIC_KEY": ""}, "lock you out"),
        ("no deploy key", {"DEPLOY_PUBLIC_KEY": ""}, "DEPLOY_PUBLIC_KEY"),
        (
            "not a key",
            {"ADMIN_PUBLIC_KEY": "ssh-ed25519 notbase64!!"},
            "not a readable SSH public key",
        ),
        (
            "a key that carries options",
            {"ADMIN_PUBLIC_KEY": 'command="x" ssh-ed25519 AAAA'},
            "not a readable",
        ),
        ("a gate hash that is a password", {"GATE_PASSWORD_HASH": "hunter2"}, "bcrypt"),
        ("root as the deploy user", {"DEPLOY_USER": "root"}, "non-root"),
        ("the same user twice", {"DEPLOY_USER": "admin"}, "two different"),
    ],
)
def test_bad_input_is_refused_before_anything_is_installed(
    scratch: Box, what: str, extra: dict[str, str], message: str
) -> None:
    result = scratch.bootstrap(**extra)
    assert result.returncode != 0, what
    assert message in result.stderr, (what, result.stderr)
    assert scratch.sh("test -e /etc/ssh/sshd_config.d/00-relay-hardening.conf").returncode != 0
    assert scratch.sh("id deploy").returncode != 0


def test_the_same_key_for_admin_and_deploy_is_refused(scratch: Box) -> None:
    result = scratch.bootstrap(DEPLOY_PUBLIC_KEY=scratch.admin_public)
    assert result.returncode != 0 and "must be different keys" in result.stderr


# -- the real thing --


def test_a_wrong_caddy_signing_key_is_refused(box: Box) -> None:
    # Ubuntu's own archive key is a real key with another fingerprint than Caddy's.
    box.sh(
        "apt-get update -qq && apt-get install -y -qq gnupg ubuntu-keyring >/dev/null", check=True
    )
    box.sh("cp /usr/share/keyrings/ubuntu-archive-keyring.gpg /not-caddy.key", check=True)
    result = box.bootstrap(CADDY_KEY_URL="file:///not-caddy.key")
    assert result.returncode != 0
    assert "refusing to trust it" in result.stderr
    assert box.sh("test -e /etc/apt/sources.list.d/caddy-stable.list").returncode != 0


def test_bootstrap_finishes_and_verify_passes(box: Box) -> None:
    first = box.bootstrap()
    assert first.returncode == 0, first.stdout[-2000:] + first.stderr[-2000:]
    # The shared password is made and shown once, and only its hash is kept.
    assert "password:" in first.stdout
    password = next(line.split()[-1] for line in first.stdout.splitlines() if "password:" in line)
    assert password not in box.read("/etc/caddy/relay.env")
    verified = box.verify()
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "all checks passed" in verified.stdout


def test_running_it_again_changes_no_secret_and_shows_no_password(box: Box) -> None:
    def fingerprint() -> tuple[str, str]:
        return (
            hashlib.sha256(box.read("/opt/cuttlefish-relay/relay.env").encode()).hexdigest(),
            hashlib.sha256(box.read("/etc/caddy/relay.env").encode()).hexdigest(),
        )

    before = fingerprint()
    again = box.bootstrap()
    assert again.returncode == 0, again.stdout[-2000:] + again.stderr[-2000:]
    assert "password:" not in again.stdout
    assert fingerprint() == before
    assert box.verify().returncode == 0


def test_the_secrets_have_the_right_owners_and_never_appear_in_output(box: Box) -> None:
    modes = box.sh(
        "stat -c '%U:%G %a %n' /opt/cuttlefish-relay/relay.env /etc/caddy/relay.env "
        "/usr/local/bin/relay-deploy /home/deploy/.ssh/authorized_keys",
        check=True,
    ).stdout
    assert "root:deploy 640 /opt/cuttlefish-relay/relay.env" in modes
    assert "root:caddy 640 /etc/caddy/relay.env" in modes
    assert "root:root 755 /usr/local/bin/relay-deploy" in modes
    assert "deploy:deploy 600 /home/deploy/.ssh/authorized_keys" in modes
    secret = box.read("/opt/cuttlefish-relay/relay.env").split("=", 1)[1].strip()
    assert len(secret) >= 32
    assert secret not in box.verify().stdout


def test_verify_notices_when_the_box_is_changed_and_bootstrap_puts_it_right(box: Box) -> None:
    box.sh("chmod 666 /usr/local/bin/relay-deploy", check=True)
    broken = box.verify()
    assert (
        broken.returncode == 1
        and "FAIL  relay-deploy is writable by group or others" in broken.stdout
    )
    box.sh(
        "sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' "
        "/etc/ssh/sshd_config.d/00-relay-hardening.conf",
        check=True,
    )
    assert "FAIL  passwords are not off" in box.verify().stdout
    assert box.bootstrap().returncode == 0
    assert box.verify().returncode == 0


# -- a real sshd, logged into --


@pytest.fixture(scope="module")
def sshd(box: Box) -> Iterator[None]:
    box.sh("ssh-keygen -A >/dev/null 2>&1; mkdir -p /run/sshd", check=True)
    # Root's key is the admin key, so that only the server's own rules can turn root away.
    box.sh(
        "mkdir -p /root/.ssh && cp /home/admin/.ssh/authorized_keys /root/.ssh/authorized_keys "
        "&& chmod 600 /root/.ssh/authorized_keys",
        check=True,
    )
    docker("exec", "-d", box.name, "/usr/sbin/sshd", "-D", "-e", "-p", "2222")
    for _ in range(40):
        if box.ssh("admin", "admin", "true").returncode == 0:
            break
        time.sleep(0.5)
    yield


def test_the_admin_can_log_in_with_a_key(box: Box, sshd: None) -> None:
    result = box.ssh("admin", "admin", "id -un && sudo -n true && echo sudo-ok")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["admin", "sudo-ok"]


def test_root_cannot_log_in_even_with_a_key_in_its_authorized_keys(box: Box, sshd: None) -> None:
    result = box.ssh("admin", "root", "id -un")
    assert result.returncode != 0 and "Permission denied" in result.stderr


def test_a_password_cannot_be_used(box: Box, sshd: None) -> None:
    result = box.ssh(
        "admin", "admin", "true",
        "-o", "PubkeyAuthentication=no",
        "-o", "PreferredAuthentications=password,keyboard-interactive",
    )  # fmt: skip
    assert result.returncode != 0 and "Permission denied" in result.stderr


def test_the_deploy_key_can_run_only_relay_deploy_whatever_it_asks_for(
    box: Box, sshd: None
) -> None:
    for asked in ("ls /", "id -un", "cat /etc/shadow", "bash -c 'touch /tmp/pwned'", ""):
        result = box.ssh("deploy", "deploy", asked)
        assert result.returncode == 64, (asked, result.returncode, result.stdout, result.stderr)
        assert (
            "refused: not an image of ghcr.io/leejianrong/cuttlefish-relay by digest"
            in result.stdout
        )
    assert box.sh("test -e /tmp/pwned").returncode != 0
    digest = "ghcr.io/leejianrong/cuttlefish-relay@sha256:" + "a" * 64
    # A well-formed request gets past the check and on to docker, which this container does not run.
    well_formed = box.ssh("deploy", "deploy", digest)
    assert "refused" not in well_formed.stdout


def test_the_deploy_key_has_no_shell_no_forwarding_and_no_other_user(box: Box, sshd: None) -> None:
    forwarded = box.ssh(
        "deploy", "deploy", "x", "-L", "19999:127.0.0.1:2222", "-o", "ExitOnForwardFailure=yes"
    )
    assert forwarded.returncode != 0
    as_admin = box.ssh("deploy", "admin", "id -un")
    assert as_admin.returncode != 0 and "Permission denied" in as_admin.stderr


def test_the_deploy_user_cannot_change_what_its_key_runs(box: Box) -> None:
    for command in (
        "echo x >> /usr/local/bin/relay-deploy",
        "echo x > /opt/cuttlefish-relay/relay.env",
        "cat /etc/caddy/relay.env",
        "echo x >> /etc/caddy/Caddyfile",
    ):
        result = box.sh(f"runuser -u deploy -- bash -c '{command}'")
        assert result.returncode != 0, command
    assert box.sh("runuser -u deploy -- cat /opt/cuttlefish-relay/relay.env").returncode == 0
