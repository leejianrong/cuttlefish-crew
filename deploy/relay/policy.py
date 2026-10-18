#!/usr/bin/env python3
"""The rules a relay release's compose file must satisfy before `relay-deploy` runs it (CUT-153).

The compose file travels inside the relay image (`/app/deploy-bundle/compose.yaml`), so what docker
runs as root on the Droplet is described by something CI built. Reaching the Droplet with a stolen
SSH key is not enough to change that, but a compromised image push would be, so the Droplet does
not take the file on trust: `relay-deploy` renders it with `docker compose config` and refuses to
start anything this check does not recognise. An allow-list, so a new compose feature is refused
until someone decides it is safe.

    docker compose config --no-env-resolution --format json | python3 policy.py \\
        --relay-image IMAGE --release-dir DIR --relay-env FILE --caddy-env FILE \\
        --http-port 80 --https-port 443

Prints one line per problem and exits 1, or prints nothing and exits 0. Standard library only.
Installed by the bootstrap, root-owned, at /usr/local/lib/cuttlefish-relay/policy.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

SERVICES = {"relay", "caddy"}
NETWORKS = {"internal", "edge"}
VOLUMES = {"relay-data", "caddy-data", "caddy-config", "caddy-logs"}
ALLOWED_SERVICE_KEYS = {
    "image", "restart", "env_file", "environment", "volumes", "ports", "networks", "depends_on",
    "read_only", "tmpfs", "cap_drop", "cap_add", "security_opt", "pids_limit", "mem_limit",
    "expose", "healthcheck",
}  # fmt: skip
CADDY_IMAGE = re.compile(r"^(docker\.io/library/)?caddy:[0-9][0-9A-Za-z._-]*@sha256:[0-9a-f]{64}$")
#: volume name -> where each service may mount it
MOUNTS = {
    "relay": {"relay-data": "/data"},
    "caddy": {
        "caddy-data": "/data",
        "caddy-config": "/config",
        "caddy-logs": "/var/log/caddy",
    },
}


def _set(value: Any) -> set[str]:
    return {str(item) for item in (value or [])}


def violations(
    config: dict[str, Any],
    *,
    relay_image: str,
    release_dir: str,
    relay_env: str,
    caddy_env: str,
    http_port: str,
    https_port: str,
) -> list[str]:
    problems: list[str] = []

    def bad(message: str) -> None:
        problems.append(message)

    extra = set(config) - {"name", "services", "networks", "volumes"}
    if extra:
        bad(f"top-level keys not allowed: {sorted(extra)}")

    services = config.get("services") or {}
    if set(services) != SERVICES:
        bad(f"services must be exactly {sorted(SERVICES)}, found {sorted(services)}")

    for name, service in services.items():
        if name not in SERVICES:
            continue
        # A key set to null is the same as absent (compose prints `command: null`).
        service = {key: value for key, value in service.items() if value is not None}
        unknown = set(service) - ALLOWED_SERVICE_KEYS
        if unknown:
            bad(f"{name}: keys not allowed: {sorted(unknown)}")
        if service.get("read_only") is not True:
            bad(f"{name}: must be read_only")
        if "ALL" not in _set(service.get("cap_drop")):
            bad(f"{name}: must drop ALL capabilities")
        wanted_caps = {"NET_BIND_SERVICE"} if name == "caddy" else set()
        if _set(service.get("cap_add")) != wanted_caps:
            bad(
                f"{name}: cap_add must be {sorted(wanted_caps)}, "
                f"found {sorted(_set(service.get('cap_add')))}"
            )
        if _set(service.get("security_opt")) != {"no-new-privileges:true"}:
            bad(f"{name}: security_opt must be exactly no-new-privileges:true")
        if not service.get("mem_limit") or not service.get("pids_limit"):
            bad(f"{name}: needs a memory and a pids limit")
        if not _set(service.get("networks")) <= NETWORKS or not service.get("networks"):
            bad(f"{name}: networks must be among {sorted(NETWORKS)}")
        if name == "relay" and _set(service.get("networks")) != {"internal"}:
            bad("relay: must be on the internal network only")
        if name == "relay" and service.get("ports"):
            bad("relay: must not publish a port to the host")

        expected_env = relay_env if name == "relay" else caddy_env
        env_files = [
            entry.get("path") if isinstance(entry, dict) else entry
            for entry in service.get("env_file") or []
        ]
        if env_files != [expected_env]:
            bad(f"{name}: env_file must be exactly {expected_env}, found {env_files}")

        environment = service.get("environment") or {}
        allowed_env = {"RELAY_UPSTREAM": "relay:8450"} if name == "caddy" else {}
        if dict(environment) != allowed_env:
            bad(f"{name}: environment must be exactly {allowed_env}, found {dict(environment)}")

        image = service.get("image", "")
        if name == "relay" and image != relay_image:
            bad(f"relay: image must be {relay_image}, found {image}")
        if name == "caddy" and not CADDY_IMAGE.match(image):
            bad(f"caddy: image must be caddy:<version>@sha256:<digest>, found {image}")

        for mount in service.get("volumes") or []:
            kind, source, target = mount.get("type"), mount.get("source"), mount.get("target")
            if (
                kind == "volume"
                and MOUNTS[name].get(source) == target
                and not mount.get("read_only")
            ):
                continue
            if (
                kind == "bind" and name == "caddy"
                and source == f"{release_dir}/Caddyfile"
                and target == "/etc/caddy/Caddyfile" and mount.get("read_only") is True
            ):  # fmt: skip
                continue
            bad(f"{name}: mount not allowed: {kind} {source} -> {target}")

        if name == "caddy":
            allowed_ports = {
                (80, http_port, "tcp"),
                (443, https_port, "tcp"),
                (443, https_port, "udp"),
            }
            published = {
                (p.get("target"), str(p.get("published")), p.get("protocol"))
                for p in service.get("ports") or []
            }
            if published != allowed_ports:
                want, found = sorted(map(str, allowed_ports)), sorted(map(str, published))
                bad(f"caddy: ports must be exactly {want}, found {found}")
            if any(p.get("host_ip") for p in service.get("ports") or []):
                bad("caddy: ports must not name a host address")
            if (service.get("depends_on") or {}).keys() != {"relay"}:
                bad("caddy: must depend on relay")
        elif service.get("depends_on"):
            bad("relay: must not depend on another service")

    for name, network in (config.get("networks") or {}).items():
        network = network or {}
        if name not in NETWORKS:
            bad(f"network not allowed: {name}")
        if (
            network.get("external")
            or network.get("driver_opts")
            or network.get("driver") not in (None, "bridge")
        ):
            bad(f"network {name}: external, driver and driver_opts are not allowed")
        if name == "internal" and network.get("internal") is not True:
            bad("network internal: must be `internal: true`")
    for name, volume in (config.get("volumes") or {}).items():
        short = name.split("_", 1)[-1] if name.startswith("cuttlefish-relay_") else name
        extra_keys = {k for k in (volume or {}) if k != "name"}
        if short not in VOLUMES:
            bad(f"volume not allowed: {name}")
        if extra_keys:
            # `driver_opts` on the local driver can mount any host directory.
            bad(f"volume {name}: {sorted(extra_keys)} are not allowed")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relay-image", required=True)
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--relay-env", required=True)
    parser.add_argument("--caddy-env", required=True)
    parser.add_argument("--http-port", default="80")
    parser.add_argument("--https-port", default="443")
    args = parser.parse_args()
    try:
        config = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"not valid JSON from docker compose config: {exc}")
        return 1
    problems = violations(
        config,
        relay_image=args.relay_image,
        release_dir=args.release_dir.rstrip("/"),
        relay_env=args.relay_env,
        caddy_env=args.caddy_env,
        http_port=args.http_port,
        https_port=args.https_port,
    )
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
