#!/usr/bin/env bash
# relay-deploy: the one thing the pipeline's SSH key may run on the Droplet (CUT-141, ADR-0043).
#
# Installed by the bootstrap script (CUT-143) as /usr/local/bin/relay-deploy, owned by root and not
# writable by the deploy user, and named in that key's `command="..."` in authorized_keys. The
# pipeline sends one image reference as its "command"; sshd hands it to this script as
# SSH_ORIGINAL_COMMAND, and nothing else the client sends is ever run.
#
# It pulls that image by digest, replaces the running relay with it, waits for it to be healthy,
# and if it never is, puts the previous container back. Exit status:
#   0  deployed and healthy
#   1  the new image was not healthy; the previous one is back and healthy (or there was none)
#   2  the new image was not healthy and the previous one could not be restored
#   3  another deploy is running
#   64 the request was not an image reference of the allowed repository, by digest
#   78 the Droplet is not set up (no env file)
#
# The settings below are overridable only so the tests can run this against a local registry; over
# SSH the client cannot set environment variables.

set -euo pipefail

REPO="${RELAY_IMAGE_REPO:-ghcr.io/leejianrong/cuttlefish-relay}"
STATE="${RELAY_STATE_DIR:-/opt/cuttlefish-relay}"
NAME="${RELAY_CONTAINER:-cuttlefish-relay}"
VOLUME="${RELAY_VOLUME:-cuttlefish-relay-data}"
PORT="${RELAY_PORT:-8450}"
ENV_FILE="${RELAY_ENV_FILE:-$STATE/relay.env}"
HEALTH_SECONDS="${RELAY_HEALTH_SECONDS:-60}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

request="${1:-${SSH_ORIGINAL_COMMAND:-}}"

# The reference must be this repository at a sha256 digest: never a tag (which moves), never another
# repository, never anything with a character a shell or docker would treat specially.
if [[ "$request" != "$REPO"@sha256:* ]]; then
    log "refused: not an image of $REPO by digest"
    exit 64
fi
hex="${request#"$REPO"@sha256:}"
if [[ ! "$hex" =~ ^[0-9a-f]{64}$ ]]; then
    log "refused: the digest is not 64 lowercase hex characters"
    exit 64
fi
image="$request"

if [[ ! -r "$ENV_FILE" ]]; then
    log "this Droplet is not set up: $ENV_FILE is missing or unreadable"
    exit 78
fi
mkdir -p "$STATE"

exec 9>"$STATE/deploy.lock"
if ! flock -n 9; then
    log "another deploy is running"
    exit 3
fi

previous_name="$NAME-previous"
current_file="$STATE/current"
old_digest="$(cat "$current_file" 2>/dev/null || true)"

start() {
    docker run -d --name "$NAME" --restart unless-stopped \
        -p "127.0.0.1:$PORT:8450" \
        --env-file "$ENV_FILE" \
        -v "$VOLUME:/data" \
        --read-only --tmpfs /tmp \
        --cap-drop ALL --security-opt no-new-privileges \
        --pids-limit 256 --memory 512m \
        "$1" >/dev/null
}

# Healthy means the container is running, the relay answers, and it serves the page.
healthy() {
    local deadline=$((SECONDS + HEALTH_SECONDS))
    while ((SECONDS < deadline)); do
        if [[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" == "true" ]] &&
            curl -fsS --max-time 3 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 &&
            curl -fsS --max-time 3 -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

log "deploying $image"
docker pull --quiet "$image" >/dev/null

# Keep the running container, stopped, until the new one has proved itself.
docker rm -f "$previous_name" >/dev/null 2>&1 || true
had_previous=0
if docker inspect "$NAME" >/dev/null 2>&1; then
    docker stop "$NAME" >/dev/null
    docker rename "$NAME" "$previous_name"
    had_previous=1
fi

start "$image"
if healthy; then
    printf '%s\n' "$hex" >"$current_file.tmp"
    mv "$current_file.tmp" "$current_file"
    docker rm -f "$previous_name" >/dev/null 2>&1 || true
    log "DEPLOYED sha256:$hex (was ${old_digest:-nothing})"
    exit 0
fi

log "the new image was not healthy; its last output:"
docker logs --tail 20 "$NAME" 2>&1 | sed 's/^/    /' || true
docker rm -f "$NAME" >/dev/null 2>&1 || true

if ((had_previous == 0)); then
    log "FAILED sha256:$hex and there was no previous deploy to restore"
    exit 1
fi
docker rename "$previous_name" "$NAME"
docker start "$NAME" >/dev/null
if healthy; then
    log "ROLLED BACK to sha256:${old_digest:-unknown}; sha256:$hex was not deployed"
    exit 1
fi
log "FAILED sha256:$hex and the previous container did not come back healthy: look at the Droplet"
exit 2
