# Hosting the relay

The hosted relay is the service that lets a phone with no VPN reach the dashboard on your PC. It runs on one
DigitalOcean Droplet, and **it reaches the Droplet only through a CI/CD pipeline**, never by hand. This page says what
runs there, how a change gets there, and what has to be set up once. The design is
[ADR-0043](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0043-the-hosted-relay-is-multi-user-with-accounts-that-own-rooms-and-hold-no-project-data.md).

## What runs on the Droplet

| Piece | What it is |
|---|---|
| Caddy | On the host. Gets the certificate, ends https, and asks for one shared password on every route except the daemon's websocket (`deploy/relay/Caddyfile`). The password stays until the outside security review. |
| The relay | One Docker container from `deploy/relay/Dockerfile`: the relay and the dashboard build. It listens on `127.0.0.1:8450` only, runs as a non-root user with a read-only filesystem and no capabilities, and keeps its accounts in a volume mounted at `/data`. |
| `relay-deploy` | One script (`deploy/relay/relay-deploy.sh`), owned by root, that the pipeline's SSH key is restricted to. |

It holds accounts, which rooms belong to whom, online state and byte counts. It holds no project data, no model keys and
no agent credentials.

## How a change gets there

1. **A pull request** runs the checks in `ci.yml`, including `Relay image smoke test (docker)`: it builds the image and
   runs it as deployed (the page loads, a daemon registers a room, a browser pairs, Caddy's gate holds).
2. **A merge to `main`**, once CI is green there, starts `deploy.yml`. It builds the image, pushes it to
   `ghcr.io/leejianrong/cuttlefish-relay`, records the digest (in the run summary and an artifact), pulls that digest back
   and smoke-tests it.
3. **The deploy job** waits in the protected `production` environment for a reviewer, then runs the Droplet's one allowed
   command over SSH with the image reference **by digest**. `relay-deploy` pulls it, replaces the container, waits for it
   to answer `/healthz` and serve the page, and **puts the previous container back if it does not**. The job fails when
   that happens, and says so.
4. The job then checks the public address answers (401 while the password gate is on, 200 after).

To go back to an earlier release, run the workflow by hand (**Actions, Deploy relay, Run workflow**) with that release's
digest. It goes through the same approval and the same script.

`relay-deploy` refuses anything that is not `ghcr.io/leejianrong/cuttlefish-relay@sha256:<64 hex characters>`: not a tag,
not another repository, nothing a shell would treat specially. The key cannot run anything else.

## What you set up once

Nothing deploys until all of this exists. The pipeline builds and smoke-tests an image without it. The first Droplet
setup is by hand, once; everything after it goes through the pipeline.

1. **Create the Droplet** (Ubuntu 24.04, the smallest size is enough to start) and point a DNS A record for the name you
   will use (`relay.example.com`) at it. Caddy gets its certificate once the name resolves.
2. **Make a key for the pipeline** on your own machine, with no passphrase, and keep the private half for step 4:

   ```bash
   ssh-keygen -t ed25519 -N "" -C pipeline-deploy-key -f relay-deploy-key
   ```

3. **Run the bootstrap on the Droplet**, as root, from a checkout of this repository:

   ```bash
   git clone https://github.com/leejianrong/cuttlefish-crew && cd cuttlefish-crew
   RELAY_DOMAIN=relay.example.com \
   ADMIN_PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
   DEPLOY_PUBLIC_KEY="$(cat relay-deploy-key.pub)" \
   sudo -E deploy/droplet/bootstrap.sh
   ```

   (`ADMIN_PUBLIC_KEY` and `DEPLOY_PUBLIC_KEY` are the *public* keys, pasted from your machine.) It makes key-only SSH with no
   root login, an `admin` user for you and a `deploy` user for the pipeline, a firewall that opens 22, 80 and 443, automatic
   security updates, docker, Caddy with the shared password, and the relay's owner secret. It shows the shared password once
   and prints the exact GitHub settings to make next. Run `sudo deploy/droplet/verify.sh` on the Droplet any time to check it
   is still that way, and re-run the bootstrap to update `relay-deploy`.
4. **In GitHub**, create the `production` environment (Settings, Environments) with **Required reviewers** set to you (approval
   on every deploy for now). On it set the secrets `DEPLOY_SSH_KEY` (the private half from step 2) and `DEPLOY_KNOWN_HOSTS`
   (the line the bootstrap printed, which pins the Droplet's host key), and the variables `DEPLOY_HOST`, `DEPLOY_USER`
   and `RELAY_PUBLIC_URL`.
5. **Turn it on:** set the repository variable `DEPLOY_ENABLED` to `true`. Until then the deploy jobs are skipped.

The image package `ghcr.io/leejianrong/cuttlefish-relay` is public (anonymous pulls were checked on 2026-10-18), so the
Droplet pulls it with no credential. If it ever shows as private, make it public in the package settings.

A change to `relay-deploy` itself is not deployed by the pipeline: the deploy user cannot write the script, on purpose, so
a stolen key cannot rewrite what it is allowed to run. Update it by re-running the bootstrap.

## What the bootstrap does to the machine

| Area | Result |
|---|---|
| SSH | Key-only, no root login, no passwords, `AllowUsers admin deploy`, three tries. A drop-in named `00-` beats the image's own `50-cloud-init.conf`. |
| The deploy key | One key, `restrict,command="/usr/local/bin/relay-deploy"`: it can run that script and nothing else, with no tty and no forwarding. The `deploy` user is in the `docker` group, which is root-equivalent, so the root-owned, narrow script is what contains it. |
| Firewall | `ufw` denies incoming and allows 22 (rate limited), 80 and 443 (tcp, and udp for HTTP/3). The relay container publishes on `127.0.0.1` only, because docker's published ports bypass `ufw`. |
| Updates | `unattended-upgrades` for Ubuntu and for Caddy's repository, rebooting at 04:30 only when an update needs it. The relay container restarts itself; daemons reconnect. |
| Caddy | From Caddy's own signed apt repository, whose key is checked against a pinned fingerprint. The password hash lives in `/etc/caddy/relay.env` (root and caddy only). |
| Secrets | The owner secret is in `/opt/cuttlefish-relay/relay.env` (root writes, the deploy user reads, nobody else). It is made once and never shown or rotated by a re-run. |

## What is checked, and what is not

`tests/smoke/` runs `relay-deploy` for real against docker and a local registry: a first deploy, a second that replaces
it with its data kept, an unhealthy release rolled back to the previous one, a first deploy that fails leaving nothing
running, a read-only unprivileged container on loopback, refusal of eleven kinds of bad request before docker is touched,
and two deploys never running at once. The workflow files pass `actionlint`.

`tests/smoke/test_bootstrap.py` runs the bootstrap for real in a fresh Ubuntu 24.04 container (`make test-bootstrap`, which
installs packages from the real repositories): bad input refused before anything changes, a wrong Caddy signing key refused,
a run that finishes and passes `verify.sh`, a second run that changes no secret, owners and modes of every secret, `verify.sh`
catching a tampered box, and a **real sshd** logged into as the admin (works, with sudo), as root (refused even with a key),
with a password (refused), and as the deploy user (every command it asks for is replaced by `relay-deploy`, which refuses it).

**Not checked, because it needs the Droplet or GitHub's settings:** the workflow's deploy and rollback steps as a whole,
`ufw`, systemd, the reboot, the real certificate and Caddy on the host, a first boot of the bootstrap on a real Droplet (a
container is not one: `BOOTSTRAP_SKIP_SERVICES=1` leaves those parts out), and the `production` environment's approval.
