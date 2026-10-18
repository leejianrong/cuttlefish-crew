"""Unit (CUT-153): the allow-list a relay release's compose file must pass before `relay-deploy`
runs it. The baseline is the shape `docker compose config --format json` prints
for deploy/relay/compose.yaml; each test breaks one rule. A smoke test renders the real file."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "relay_policy", REPO / "deploy" / "relay" / "policy.py"
)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

IMAGE = "ghcr.io/leejianrong/cuttlefish-relay@sha256:" + "a" * 64
CADDY = "caddy:2.11.7@sha256:" + "b" * 64
RELEASE = "/opt/cuttlefish-relay/releases/" + "a" * 64
RELAY_ENV = "/opt/cuttlefish-relay/relay.env"
CADDY_ENV = "/opt/cuttlefish-relay/caddy.env"
KWARGS: dict[str, Any] = {
    "relay_image": IMAGE,
    "release_dir": RELEASE,
    "relay_env": RELAY_ENV,
    "caddy_env": CADDY_ENV,
    "http_port": "80",
    "https_port": "443",
}


def baseline() -> dict[str, Any]:
    hardening: dict[str, Any] = {
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 256,
        "restart": "unless-stopped",
        "command": None,
        "entrypoint": None,
    }
    return {
        "name": "cuttlefish-relay",
        "services": {
            "relay": {
                **hardening,
                "image": IMAGE,
                "env_file": [{"path": RELAY_ENV}],
                "volumes": [{"type": "volume", "source": "relay-data", "target": "/data"}],
                "networks": {"internal": None},
                "expose": ["8450"],
                "tmpfs": ["/tmp"],
                "mem_limit": "536870912",
            },
            "caddy": {
                **hardening,
                "image": CADDY,
                "cap_add": ["NET_BIND_SERVICE"],
                "depends_on": {"relay": {"condition": "service_healthy", "required": True}},
                "env_file": [{"path": CADDY_ENV}],
                "environment": {"RELAY_UPSTREAM": "relay:8450"},
                "ports": [
                    {"mode": "ingress", "target": 80, "published": "80", "protocol": "tcp"},
                    {"mode": "ingress", "target": 443, "published": "443", "protocol": "tcp"},
                    {"mode": "ingress", "target": 443, "published": "443", "protocol": "udp"},
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": f"{RELEASE}/Caddyfile",
                        "target": "/etc/caddy/Caddyfile",
                        "read_only": True,
                    },
                    {"type": "volume", "source": "caddy-data", "target": "/data"},
                    {"type": "volume", "source": "caddy-config", "target": "/config"},
                    {"type": "volume", "source": "caddy-logs", "target": "/var/log/caddy"},
                ],
                "networks": {"internal": None, "edge": None},
                "tmpfs": ["/tmp"],
                "mem_limit": "268435456",
                "healthcheck": {"test": ["CMD", "wget"], "interval": "10s"},
            },
        },
        "networks": {
            "internal": {"name": "cuttlefish-relay_internal", "internal": True},
            "edge": {"name": "cuttlefish-relay_edge"},
        },
        "volumes": {
            "relay-data": {"name": "cuttlefish-relay_relay-data"},
            "caddy-data": {"name": "cuttlefish-relay_caddy-data"},
            "caddy-config": {"name": "cuttlefish-relay_caddy-config"},
            "caddy-logs": {"name": "cuttlefish-relay_caddy-logs"},
        },
    }


def check(config: dict[str, Any], **override: str) -> list[str]:
    return list(policy.violations(config, **{**KWARGS, **override}))


def test_the_expected_shape_passes() -> None:
    assert check(baseline()) == []


def mutated(service: str | None, edit: Any) -> dict[str, Any]:
    config = copy.deepcopy(baseline())
    edit(config["services"][service] if service else config)
    return config


@pytest.mark.parametrize(
    ("what", "service", "edit", "needle"),
    [
        ("privileged", "relay", lambda s: s.update(privileged=True), "keys not allowed"),
        ("host network", "caddy", lambda s: s.update(network_mode="host"), "keys not allowed"),
        ("host pid", "relay", lambda s: s.update(pid="host"), "keys not allowed"),
        ("a device", "relay", lambda s: s.update(devices=["/dev/sda"]), "keys not allowed"),
        ("a command override", "relay", lambda s: s.update(command=["sh"]), "keys not allowed"),
        ("a user override", "caddy", lambda s: s.update(user="0"), "keys not allowed"),
        ("a sysctl", "relay", lambda s: s.update(sysctls={"a": "b"}), "keys not allowed"),
        ("a writable root", "relay", lambda s: s.update(read_only=False), "read_only"),
        ("capabilities kept", "relay", lambda s: s.update(cap_drop=[]), "drop ALL"),
        (
            "a capability added to the relay",
            "relay",
            lambda s: s.update(cap_add=["SYS_ADMIN"]),
            "cap_add",
        ),
        (
            "more caps on caddy",
            "caddy",
            lambda s: s.update(cap_add=["NET_BIND_SERVICE", "SYS_ADMIN"]),
            "cap_add",
        ),
        (
            "seccomp off",
            "relay",
            lambda s: s.update(security_opt=["seccomp=unconfined"]),
            "security_opt",
        ),
        ("no memory limit", "relay", lambda s: s.pop("mem_limit"), "limit"),
        (
            "the relay published",
            "relay",
            lambda s: s.update(ports=[{"target": 8450, "published": "8450", "protocol": "tcp"}]),
            "must not publish",
        ),
        (
            "the relay on the edge",
            "relay",
            lambda s: s.update(networks={"internal": None, "edge": None}),
            "internal network only",
        ),
        ("an unknown network", "caddy", lambda s: s.update(networks={"host": None}), "networks"),
        (
            "another env file",
            "relay",
            lambda s: s.update(env_file=[{"path": "/etc/shadow"}]),
            "env_file",
        ),
        (
            "a second env file",
            "caddy",
            lambda s: s["env_file"].append({"path": "/root/.ssh/id_ed25519"}),
            "env_file",
        ),
        ("an extra variable", "caddy", lambda s: s["environment"].update(X="1"), "environment"),
        (
            "a different upstream",
            "caddy",
            lambda s: s["environment"].update(RELAY_UPSTREAM="evil:80"),
            "environment",
        ),
        (
            "another relay image",
            "relay",
            lambda s: s.update(image="docker.io/evil/relay@sha256:" + "c" * 64),
            "relay: image",
        ),
        ("an unpinned caddy", "caddy", lambda s: s.update(image="caddy:latest"), "caddy: image"),
        (
            "another caddy repo",
            "caddy",
            lambda s: s.update(image="evil/caddy:2@sha256:" + "d" * 64),
            "caddy: image",
        ),
        (
            "the host root mounted",
            "relay",
            lambda s: s["volumes"].append({"type": "bind", "source": "/", "target": "/host"}),
            "mount not allowed",
        ),
        (
            "the docker socket",
            "caddy",
            lambda s: s["volumes"].append(
                {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"}
            ),
            "mount not allowed",
        ),
        (
            "a writable Caddyfile",
            "caddy",
            lambda s: s["volumes"][0].update(read_only=False),
            "mount not allowed",
        ),
        (
            "another Caddyfile",
            "caddy",
            lambda s: s["volumes"][0].update(source="/etc/passwd"),
            "mount not allowed",
        ),
        (
            "a volume at another path",
            "relay",
            lambda s: s["volumes"][0].update(target="/etc"),
            "mount not allowed",
        ),
        (
            "an unknown volume",
            "relay",
            lambda s: s["volumes"].append({"type": "volume", "source": "other", "target": "/x"}),
            "mount not allowed",
        ),
        (
            "a host ssh port",
            "caddy",
            lambda s: s["ports"].append({"target": 22, "published": "22", "protocol": "tcp"}),
            "ports must be exactly",
        ),
        (
            "another published port",
            "caddy",
            lambda s: s["ports"][0].update(published="8080"),
            "ports must be exactly",
        ),
        (
            "a named host address",
            "caddy",
            lambda s: s["ports"][0].update(host_ip="0.0.0.0"),
            "host address",
        ),
        (
            "caddy not waiting for the relay",
            "caddy",
            lambda s: s.update(depends_on={}),
            "depend on relay",
        ),
    ],
)
def test_each_rule_catches_its_break(what: str, service: str, edit: Any, needle: str) -> None:
    problems = check(mutated(service, edit))
    assert any(needle in p for p in problems), (what, problems)


@pytest.mark.parametrize(
    ("what", "edit", "needle"),
    [
        (
            "a third service",
            lambda c: c["services"].update(shell={"image": "alpine"}),
            "services must be exactly",
        ),
        ("a missing service", lambda c: c["services"].pop("caddy"), "services must be exactly"),
        ("secrets", lambda c: c.update(secrets={"x": {"file": "/etc/shadow"}}), "top-level keys"),
        ("configs", lambda c: c.update(configs={"x": {"file": "/etc/shadow"}}), "top-level keys"),
        ("an external network", lambda c: c["networks"]["edge"].update(external=True), "external"),
        (
            "the host network driver",
            lambda c: c["networks"]["edge"].update(driver="host"),
            "driver",
        ),
        (
            "a network that is not internal",
            lambda c: c["networks"]["internal"].update(internal=False),
            "internal: true",
        ),
        ("an extra network", lambda c: c["networks"].update(other={}), "network not allowed"),
        (
            "a volume bound to a host path",
            lambda c: c["volumes"]["relay-data"].update(
                driver_opts={"type": "none", "o": "bind", "device": "/"}
            ),
            "not allowed",
        ),
        ("an unknown volume", lambda c: c["volumes"].update(other={}), "volume not allowed"),
    ],
)
def test_top_level_rules_catch_their_break(what: str, edit: Any, needle: str) -> None:
    config = copy.deepcopy(baseline())
    edit(config)
    problems = check(config)
    assert any(needle in p for p in problems), (what, problems)


def test_it_does_not_matter_whether_this_compose_version_keeps_env_file_or_inlines_it() -> None:
    inlined = baseline()
    for service in inlined["services"].values():
        service.pop("env_file")  # an older compose merges the file's variables into `environment`
    assert check(inlined) == []


def test_variables_that_an_env_file_of_the_bundles_own_brought_in_are_refused() -> None:
    """Rendered with empty placeholder files, anything in `environment` came from the bundle itself:
    an `env_file` it named that holds values (here as an inlining compose would show them)."""
    for service, extra in (
        ("caddy", "GATE_PASSWORD_HASH"),
        ("relay", "CUTTLEFISH_RELAY_OWNER_SECRET"),
    ):
        config = baseline()
        config["services"][service].pop("env_file")
        config["services"][service].setdefault("environment", {})[extra] = "x"
        problems = check(config)
        assert any("environment must be exactly" in p for p in problems), (service, problems)


def test_other_ports_or_release_paths_are_judged_against_what_the_script_was_told() -> None:
    config = baseline()
    assert check(config, http_port="8080")  # the file publishes 80, the script allows 8080
    for port in config["services"]["caddy"]["ports"]:
        port["published"] = "8080" if port["target"] == 80 else "8443"
    assert check(config, http_port="8080", https_port="8443") == []
    assert check(config, release_dir="/opt/cuttlefish-relay/releases/other")  # the Caddyfile moved


def test_the_command_line_prints_each_problem_and_exits_non_zero() -> None:
    config = baseline()
    config["services"]["relay"]["privileged"] = True
    script = REPO / "deploy" / "relay" / "policy.py"
    args = [
        sys.executable, "-I", str(script), "--relay-image", IMAGE, "--release-dir", RELEASE,
        "--relay-env", RELAY_ENV, "--caddy-env", CADDY_ENV,
    ]  # fmt: skip
    bad = subprocess.run(args, input=json.dumps(config), capture_output=True, text=True)
    assert bad.returncode == 1 and "relay: keys not allowed: ['privileged']" in bad.stdout
    good = subprocess.run(args, input=json.dumps(baseline()), capture_output=True, text=True)
    assert good.returncode == 0 and good.stdout == ""
    broken = subprocess.run(args, input="not json", capture_output=True, text=True)
    assert broken.returncode == 1


@pytest.mark.requires_docker
def test_the_real_compose_file_passes_its_own_policy(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("needs docker")
    compose = REPO / "deploy" / "relay" / "compose.yaml"
    release = tmp_path / "release"
    release.mkdir()
    shutil.copy(compose, release / "compose.yaml")
    (release / "Caddyfile").write_text("")
    for name in ("relay.env", "caddy.env"):
        (tmp_path / name).write_text("")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "RELAY_IMAGE": IMAGE,
        "RELAY_ENV_FILE": str(tmp_path / "relay.env"),
        "CADDY_ENV_FILE": str(tmp_path / "caddy.env"),
    }
    rendered = subprocess.run(
        [
            "docker", "compose", "-f", str(release / "compose.yaml"),
            "config", "--format", "json",
        ],
        env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    problems = policy.violations(
        config,
        relay_image=IMAGE,
        release_dir=str(release),
        relay_env=str(tmp_path / "relay.env"),
        caddy_env=str(tmp_path / "caddy.env"),
        http_port="80",
        https_port="443",
    )
    assert problems == []
