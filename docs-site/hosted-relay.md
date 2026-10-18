# Hosting the relay

The hosted relay is the service that lets a phone with no VPN reach the dashboard on your PC. It runs on one
DigitalOcean Droplet, and **it reaches the Droplet only through a CI/CD pipeline**, never by hand. This page says what
runs there, how a change gets there, and what has to be set up once. The design is
[ADR-0043](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0043-the-hosted-relay-is-multi-user-with-accounts-that-own-rooms-and-hold-no-project-data.md).

## What runs on the Droplet

Two containers, from one compose file (`deploy/relay/compose.yaml`):

| Container | What it is |
|---|---|
| `caddy` | The official Caddy image, pinned by digest. Gets and renews the certificate, ends https, and asks for one shared password on every route except the daemon's websocket (`deploy/relay/Caddyfile`). The password stays until the outside security review. It publishes ports 80 and 443 and nothing else. |
| `relay` | Our image from `deploy/relay/Dockerfile`: the relay and the dashboard build. It sits on an internal network with Caddy, with **no route to the internet and no port published to the host**, so only Caddy can reach it. Its accounts live in a volume mounted at `/data`. |

Both run read-only with every capability dropped (Caddy keeps only the right to bind ports 80 and 443), `no-new-privileges`
and memory limits. Certificates live in a volume, so a redeploy does not ask Let's Encrypt again.

On the host itself there is only `relay-deploy` (`deploy/relay/relay-deploy.sh`), a script owned by root that the pipeline's
SSH key is restricted to, the policy file it judges releases with, and two small settings files in `/opt/cuttlefish-relay`.

The relay holds accounts, which rooms belong to whom, online state and byte counts. It holds no project data, no model
keys and no agent credentials.

## How a change gets there

1. **A pull request** runs the checks in `ci.yml`, including `Relay image smoke test (docker)`: it builds the image, starts
   the same compose stack, and checks it (the page loads, a daemon registers a room, a browser pairs, Caddy's gate holds).
2. **A merge to `main`**, once CI is green there, starts `deploy.yml`. It builds the image, pushes it to
   `ghcr.io/leejianrong/cuttlefish-relay`, records the digest (in the run summary and an artifact), pulls that digest back
   and smoke-tests it.
3. **The deploy job** waits in the protected `production` environment for a reviewer, then runs the Droplet's one allowed
   command over SSH with the image reference **by digest**. The image carries its own compose file and Caddyfile
   (`/app/deploy-bundle`), so configuration reaches the Droplet only with a release. `relay-deploy`:
   - pulls the image and takes the bundle out of it (two plain files; a symlink or an extra file refuses the release),
   - renders the compose file and refuses it unless it passes the allow-list in `deploy/relay/policy.py` (exactly two
     services, our images, no privileged mode, no host mounts, no extra ports),
   - brings the stack up and waits for both containers to be healthy and for Caddy to answer,
   - and **puts the previous release back if they are not**. The job fails when that happens, and says so.
4. The job then checks the public address answers (401 while the password gate is on, 200 after).

To go back to an earlier release, run the workflow by hand (**Actions, Deploy relay, Run workflow**) with that release's
digest. It goes through the same approval and the same script. (A release built before the compose stack has no bundle
and is refused.)

`relay-deploy` refuses anything that is not `ghcr.io/leejianrong/cuttlefish-relay@sha256:<64 hex characters>`: not a tag,
not another repository, nothing a shell would treat specially. The key cannot run anything else. A stolen key alone cannot
change what runs as root either: the compose file comes from an image only our pipeline pushes, and the Droplet checks it
against the allow-list anyway.

### Trying the stack on your own machine

```bash
# from the repository root
REPO="$PWD"
docker build -f deploy/relay/Dockerfile -t cuttlefish-relay:local .
mkdir -p /tmp/relay-try && cd /tmp/relay-try
printf 'CUTTLEFISH_RELAY_OWNER_SECRET=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > relay.env
CADDY="$(sed -n 's/^    image: \(caddy:[^ ]*\)$/\1/p' "$REPO/deploy/relay/compose.yaml")"
HASH="$(printf 'try-it' | "$REPO/deploy/droplet/gate_hash.sh" "$CADDY")"
printf "RELAY_ADDRESS=:80\nGATE_USER=owner\nGATE_PASSWORD_HASH='%s'\n" "$HASH" > caddy.env
RELAY_IMAGE=cuttlefish-relay:local RELAY_ENV_FILE="$PWD/relay.env" CADDY_ENV_FILE="$PWD/caddy.env" \
  HTTP_PORT=8080 HTTPS_PORT=8443 docker compose -f "$REPO/deploy/relay/compose.yaml" up
# then open http://127.0.0.1:8080 and sign in with owner / try-it
```

`RELAY_ADDRESS=:80` makes Caddy serve plain http, with no certificate. On the Droplet it is the domain.

## What you set up once

Nothing deploys until all of this exists. The pipeline builds and smoke-tests an image without it. The first Droplet
setup is by hand, once; everything after it goes through the pipeline.

1. **Create the Droplet** (Ubuntu 24.04, the smallest size is enough to start) and point a DNS A record for the name you
   will use (`relay.example.com`) at it. Caddy gets its certificate from Let's Encrypt once the name resolves, because
   Let's Encrypt proves you control the name by connecting to it on port 80 or 443. Check with
   `dig +short relay.example.com`, which should print the Droplet's IPv4 address. Create an A record only: the Droplet is
   IPv4-only for now (GitHub's runners reach it over IPv4, and Docker does not publish ports on IPv6 by default).
2. **Make a key for the pipeline** on your own machine, with no passphrase, and keep the private half for step 4:

   ```bash
   ssh-keygen -t ed25519 -N "" -C pipeline-deploy-key -f relay-deploy-key
   ```

3. **Run the bootstrap on the Droplet**, as root, from a checkout of this repository:

   ```bash
   git clone https://github.com/leejianrong/cuttlefish-crew && cd cuttlefish-crew
   RELAY_DOMAIN=relay.example.com \
   ADMIN_USER=jian \
   ADMIN_PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
   DEPLOY_PUBLIC_KEY="$(cat relay-deploy-key.pub)" \
   sudo -E deploy/droplet/bootstrap.sh
   ```

   (`ADMIN_USER` is the sudo user you log in as; leave it out to get a new one called `admin`. `ADMIN_PUBLIC_KEY` and
   `DEPLOY_PUBLIC_KEY` are the *public* keys, pasted from your machine.) It makes key-only SSH with no
   root login, an `admin` user for you and a `deploy` user for the pipeline, a firewall that opens 22, 80 and 443, automatic
   security updates, docker and the compose plugin, the shared password's hash (the password is made and shown once) and the
   relay's owner secret. It does **not** start the relay or Caddy: the pipeline's first deploy does. It prints the exact
   GitHub settings to make next. Run `sudo deploy/droplet/verify.sh` on the Droplet any time to check it is still that way,
   and re-run the bootstrap to update `relay-deploy` or its policy file.
   **If you have already set the machine up a little by hand, the bootstrap adapts to it and does not replace it.** An existing
   admin user keeps its SSH keys (the given key is added) and, if it has a password, keeps asking for it on `sudo`. Docker that
   is already installed (from Ubuntu or from Docker's own repository) is left alone, with the compose plugin added if it is
   missing. An existing `/etc/docker/daemon.json` is merged, with the old one saved as `daemon.json.bak`. A `ufw allow
   OpenSSH` rule is replaced by the rate-limited one. Root login and passwords over SSH are turned off, so keep your own
   session open and test a second login as the admin user before you close it. The deploy user is the only one in the
   `docker` group; you keep using `sudo docker`.
4. **In GitHub**, create the `production` environment (Settings, Environments) with **Required reviewers** set to you (approval
   on every deploy for now). On it set the secrets `DEPLOY_SSH_KEY` (the private half from step 2) and `DEPLOY_KNOWN_HOSTS`
   (the line the bootstrap printed, which pins the Droplet's host key), and the variables `DEPLOY_HOST`, `DEPLOY_USER`
   and `RELAY_PUBLIC_URL`.
5. **Turn it on:** set the repository variable `DEPLOY_ENABLED` to `true`. Until then the deploy jobs are skipped. For the
   very first deploy you can add `ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory` to
   `/opt/cuttlefish-relay/caddy.env` on the Droplet, so a mistake cannot use up Let's Encrypt's real rate limit; once the
   staging run works, remove the line and deploy again (the browser will warn about the staging certificate until then).

The image package `ghcr.io/leejianrong/cuttlefish-relay` is public (anonymous pulls were checked on 2026-10-18), so the
Droplet pulls it with no credential. If it ever shows as private, make it public in the package settings.

A change to `relay-deploy` or `policy.py` is not deployed by the pipeline: the deploy user cannot write them, on purpose, so a
stolen key cannot rewrite what it is allowed to run. Update them by re-running the bootstrap. Everything else, including
the compose file, the Caddyfile and the pinned Caddy version, ships inside a release.

## What the bootstrap does to the machine

| Area | Result |
|---|---|
| SSH | Key-only, no root login, no passwords, `AllowUsers admin deploy`, three tries. A drop-in named `00-` beats the image's own `50-cloud-init.conf`. |
| The deploy key | One key, `restrict,command="/usr/local/bin/relay-deploy"`: it can run that script and nothing else, with no tty and no forwarding. The `deploy` user is in the `docker` group, which is root-equivalent, so the root-owned, narrow script is what contains it. |
| Firewall | `ufw` denies incoming and allows 22 (rate limited), 80 and 443 (tcp, and udp for HTTP/3). Docker's published ports bypass `ufw`, which is fine for 80 and 443 and is why nothing else is ever published: the relay has no host port at all. |
| Updates | `unattended-upgrades` for Ubuntu, rebooting at 04:30 only when an update needs it; the containers restart themselves and daemons reconnect. Caddy and the relay are not updated by the OS: a new version of either is a release. |
| The stack's settings | `/opt/cuttlefish-relay/relay.env` holds the owner secret (made once, never shown, never rotated by a re-run) and `caddy.env` holds the site address and the shared password's hash. Root writes them, the deploy user reads them (compose passes them in), nobody else. |
| The policy | `/usr/local/lib/cuttlefish-relay/policy.py`, owned by root: the allow-list every release's compose file must pass. |

## What is checked, and what is not

`tests/smoke/` (`make test-smoke`, and a CI job) builds the image and runs it:
- **The stack,** started from the image's own bundle: the password gate on every route but the daemon's socket (including `..`,
  encoded and doubled-slash paths), a daemon registering through Caddy, the relay unreachable from the host and with no route
  to the internet, both containers read-only with capabilities dropped, and Caddy overwriting a forged `X-Forwarded-For` so the
  per-address limit cannot be dodged.
- **`relay-deploy`,** for real against docker, compose and a local registry: a first deploy, a second that replaces it with its
  data kept, redeploying the running release changing nothing, an unhealthy relay and a broken Caddyfile each rolled back to
  the previous release, a first deploy that fails leaving nothing running, a compose file that is not read-only, is privileged
  or mounts the host root refused with nothing changed, a bundle that is missing, symlinked or holds an extra file refused,
  eleven kinds of bad request refused before docker is touched, a missing settings file, and two deploys never running at
  once. `tests/unit/test_relay_deploy_policy.py` breaks each rule of the allow-list on its own.

`tests/smoke/test_bootstrap.py` runs the bootstrap for real in a fresh Ubuntu 24.04 container (`make test-bootstrap`, which
installs packages from the real repositories): bad input refused before anything changes, a run that finishes and passes
`verify.sh`, a second run that changes no secret, owners and modes of every secret and the policy file, `verify.sh` catching a
tampered box, and a **real sshd** logged into as the admin (works, with sudo), as root (refused even with a key), with a
password (refused), and as the deploy user (every command it asks for is replaced by `relay-deploy`, which refuses it).
The workflow files pass `actionlint` and the scripts pass `shellcheck`.

**Not checked, because it needs the Droplet or GitHub's settings:** the workflow's deploy and rollback steps as a whole,
SSH with the restricted key into a real machine, `ufw`, systemd, the reboot, a real certificate from Let's Encrypt (the local
runs are plain http), Docker restarting the containers after a reboot, a first boot of the bootstrap on a real Droplet (a
container is not one: `BOOTSTRAP_SKIP_SERVICES=1` leaves those parts out), and the `production` environment's approval.
