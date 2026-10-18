#!/usr/bin/env bash
# relay-deploy: the one thing the pipeline's SSH key may run on the Droplet (CUT-141, CUT-153,
# ADR-0043).
#
# Installed by the bootstrap script as /usr/local/bin/relay-deploy, owned by root and not writable by
# the deploy user, and named in that key's `command="..."` in authorized_keys. The pipeline sends one
# image reference as its "command"; sshd hands it to this script as SSH_ORIGINAL_COMMAND, and nothing
# else the client sends is ever run.
#
# A release is a relay image by digest. The image carries its own compose file and Caddyfile
# (/app/deploy-bundle), so this script:
#   1. pulls the image and takes the bundle out of it,
#   2. renders the compose file and refuses it unless it passes policy.py (an allow-list),
#   3. brings the stack up (relay and Caddy) and waits for it to be healthy and to answer,
#   4. and if it is not, brings the previous release back.
# Exit status:
#   0  deployed and healthy
#   1  the new release was not healthy; the previous one is back and healthy (or there was none)
#   2  the new release was not healthy and the previous one could not be restored
#   3  another deploy is running
#   64 the request was not an image reference of the allowed repository, by digest
#   65 the image has no usable deploy bundle, or its compose file was refused by policy.py
#   78 the Droplet is not set up (an env file is missing)
#
# The settings below are overridable only so the tests can run this against a local registry and
# unprivileged ports; over SSH the client cannot set environment variables.

set -euo pipefail

REPO="${RELAY_IMAGE_REPO:-ghcr.io/leejianrong/cuttlefish-relay}"
STATE="${RELAY_STATE_DIR:-/opt/cuttlefish-relay}"
PROJECT="${RELAY_PROJECT:-cuttlefish-relay}"
HTTP_PORT="${RELAY_HTTP_PORT:-80}"
HTTPS_PORT="${RELAY_HTTPS_PORT:-443}"
RELAY_ENV="${RELAY_ENV_FILE:-$STATE/relay.env}"
CADDY_ENV="${CADDY_ENV_FILE:-$STATE/caddy.env}"
POLICY="${RELAY_POLICY_FILE:-/usr/local/lib/cuttlefish-relay/policy.py}"
HEALTH_SECONDS="${RELAY_HEALTH_SECONDS:-90}"

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

for file in "$RELAY_ENV" "$CADDY_ENV"; do
    if [[ ! -r "$file" ]]; then
        log "this Droplet is not set up: $file is missing or unreadable"
        exit 78
    fi
done
mkdir -p "$STATE/releases"

exec 9>"$STATE/deploy.lock"
if ! flock -n 9; then
    log "another deploy is running"
    exit 3
fi

# What compose reads. The image and the ports are ours to set; the rest is in the env files.
export RELAY_ENV_FILE="$RELAY_ENV" CADDY_ENV_FILE="$CADDY_ENV" HTTP_PORT HTTPS_PORT

old_hex="$(cat "$STATE/current" 2>/dev/null || true)"
old_dir="$STATE/releases/$old_hex"
release="$STATE/releases/$hex"
incoming="$STATE/releases/.incoming-$$"
uplog="$STATE/releases/.up-$$.log"
# shellcheck disable=SC2329  # run by the trap
cleanup() { rm -rf "$incoming" "$uplog"; }
trap cleanup EXIT

compose() { # release dir, then compose arguments
    local dir="$1"
    shift
    RELAY_IMAGE="$(cat "$dir/image")" docker compose -p "$PROJECT" -f "$dir/compose.yaml" "$@"
}

# The relay container is running and answers, and Caddy answers on its http port: a domain redirects to
# https (308) and a local run asks for the password (401). `compose up --wait` has already waited for
# both containers' own health checks.
serving() {
    local address host code
    address="$(sed -n 's/^RELAY_ADDRESS=//p' "$CADDY_ENV" | head -n 1 | tr -d "'\"")"
    host=()
    [[ -n "$address" && "$address" != :* ]] && host=(-H "Host: $address")
    code="$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "${host[@]}" "http://127.0.0.1:$HTTP_PORT/healthz" 2>/dev/null || true)"
    case "$code" in 200 | 301 | 302 | 307 | 308 | 401) return 0 ;; *) return 1 ;; esac
}

bring_up() { # release dir
    local dir="$1" deadline=$((SECONDS + HEALTH_SECONDS))
    compose "$dir" up -d --wait --wait-timeout "$HEALTH_SECONDS" --remove-orphans --quiet-pull >"$uplog" 2>&1 || return 1
    while ((SECONDS < deadline)); do
        serving && return 0
        sleep 1
    done
    return 1
}

log "deploying $image"
docker pull --quiet "$image" >/dev/null

# 1. The bundle, out of the exact image being deployed.
mkdir -p "$incoming"
container="$(docker create "$image")"
if ! docker cp "$container:/app/deploy-bundle/." "$incoming/" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null 2>&1 || true
    log "refused: this image has no /app/deploy-bundle (a release from before the compose stack?)"
    exit 65
fi
docker rm -f "$container" >/dev/null 2>&1 || true
for file in compose.yaml Caddyfile; do
    if [[ ! -f "$incoming/$file" || -L "$incoming/$file" ]]; then
        log "refused: the bundle's $file is missing or is not a plain file"
        exit 65
    fi
done
if [[ "$(find "$incoming" -mindepth 1 | wc -l)" != "2" ]]; then
    log "refused: the bundle holds something besides compose.yaml and Caddyfile"
    exit 65
fi
chmod 644 "$incoming/compose.yaml" "$incoming/Caddyfile"
printf '%s\n' "$image" >"$incoming/image"

# 2. Render it and judge it, before anything running is touched.
rendered="$(compose "$incoming" config --no-env-resolution --format json 2>&1)" || {
    log "refused: docker compose could not read the bundle's compose file:"
    printf '%s\n' "$rendered" | sed 's/^/    /'
    exit 65
}
if ! problems="$(printf '%s' "$rendered" | python3 "$POLICY" --relay-image "$image" \
    --release-dir "$incoming" --relay-env "$RELAY_ENV" --caddy-env "$CADDY_ENV" \
    --http-port "$HTTP_PORT" --https-port "$HTTPS_PORT")"; then
    log "refused: the bundle's compose file does not pass policy:"
    printf '%s\n' "$problems" | sed 's/^/    /'
    exit 65
fi
# It passed. It moves to its release folder (the same digest is the same bytes, so a redeploy of the
# running release leaves its folder alone), and the policy is run once more there below.
if [[ "$hex" != "$old_hex" ]]; then
    rm -rf "$release"
    mv "$incoming" "$release"
fi
rendered="$(compose "$release" config --no-env-resolution --format json)"
if ! printf '%s' "$rendered" | python3 "$POLICY" --relay-image "$image" \
    --release-dir "$release" --relay-env "$RELAY_ENV" --caddy-env "$CADDY_ENV" \
    --http-port "$HTTP_PORT" --https-port "$HTTPS_PORT" >/dev/null; then
    log "refused: the policy passed in the staging folder and failed in the release folder"
    [[ "$hex" == "$old_hex" ]] || rm -rf "$release"
    exit 65
fi

# 3. Pull what is new (the relay image is already here), then bring the stack up.
compose "$release" pull --quiet >/dev/null 2>&1 || {
    log "could not pull the stack's images; nothing was changed"
    [[ "$hex" == "$old_hex" ]] || rm -rf "$release"
    exit 1
}
if bring_up "$release"; then
    printf '%s\n' "$hex" >"$STATE/current.tmp"
    mv "$STATE/current.tmp" "$STATE/current"
    printf '%s\n' "$old_hex" >"$STATE/previous"
    # Keep this release and the one before it, drop the rest, and old images nothing uses.
    for dir in "$STATE"/releases/*/; do
        name="$(basename "$dir")"
        [[ "$name" == "$hex" || "$name" == "$old_hex" ]] || rm -rf "$dir"
    done
    docker image prune -af --filter "until=720h" >/dev/null 2>&1 || true
    log "DEPLOYED sha256:$hex (was ${old_hex:-nothing})"
    exit 0
fi

# 4. Not healthy: show why, then put the previous release back.
log "the new release was not healthy; its last output:"
{ cat "$uplog" 2>/dev/null; compose "$release" logs --tail 15 2>&1 || true; } | sed 's/^/    /'

if [[ -z "$old_hex" || ! -f "$old_dir/image" ]]; then
    compose "$release" down >/dev/null 2>&1 || true
    [[ "$hex" == "$old_hex" ]] || rm -rf "$release"
    log "FAILED sha256:$hex and there was no previous deploy to restore"
    exit 1
fi
log "restoring sha256:$old_hex"
if bring_up "$old_dir"; then
    [[ "$hex" == "$old_hex" ]] || rm -rf "$release"
    log "ROLLED BACK to sha256:$old_hex; sha256:$hex was not deployed"
    exit 1
fi
log "FAILED sha256:$hex and the previous release did not come back healthy: look at the Droplet"
exit 2
