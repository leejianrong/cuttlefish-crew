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

Nothing deploys until all of this exists. The pipeline builds and smoke-tests an image without it.

1. **Make the GHCR package public** after the first push (GitHub, your profile, Packages, `cuttlefish-relay`, Package
   settings). The image holds the open-source code and the dashboard build and no secret, and a public package lets the Droplet
   pull without a credential.
2. **Create the `production` environment** (Settings, Environments) with **Required reviewers** set to you. Approval is
   needed on every deploy for now.
3. **Environment secrets** on `production`: `DEPLOY_SSH_KEY` (the private half of a key made for this) and
   `DEPLOY_KNOWN_HOSTS` (the Droplet's host key line from `ssh-keyscan`, checked against what the Droplet's console shows;
   the job never trusts a host on first use).
4. **Environment variables** on `production`: `DEPLOY_HOST`, `DEPLOY_USER` (the deploy user) and `RELAY_PUBLIC_URL`
   (`https://...`).
5. **Repository variable** `DEPLOY_ENABLED` set to `true`. Until then the deploy jobs are skipped.
6. **On the Droplet**, the bootstrap script (CUT-143) installs `relay-deploy` as `/usr/local/bin/relay-deploy`, makes
   `/opt/cuttlefish-relay/relay.env` (the owner secret, readable by the deploy user only), and puts this line in the deploy
   user's `authorized_keys`:

   ```
   restrict,command="/usr/local/bin/relay-deploy" ssh-ed25519 AAAA... deploy-key
   ```

A change to `relay-deploy` itself is not deployed by the pipeline: the deploy user cannot write the script, on purpose, so
a stolen key cannot rewrite what it is allowed to run. Update it by re-running the bootstrap.

## What is checked, and what is not

`tests/smoke/` runs `relay-deploy` for real against docker and a local registry: a first deploy, a second that replaces
it with its data kept, an unhealthy release rolled back to the previous one, a first deploy that fails leaving nothing
running, a read-only unprivileged container on loopback, refusal of eleven kinds of bad request before docker is touched,
and two deploys never running at once. The workflow files pass `actionlint`.

**Not checked, because it needs the Droplet or GitHub's settings:** the deploy and rollback steps of the workflow as a
whole, SSH with the restricted key and a forced command, the real certificate and Caddy on the host, the bootstrap script
on a fresh machine (CUT-143), and the `production` environment's approval.
