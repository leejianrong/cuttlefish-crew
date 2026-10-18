#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 (or 22.04) DigitalOcean Droplet to host the relay (CUT-143, ADR-0043).
#
# Run once as root, by hand, from a checkout of this repository on the Droplet. Running it again is
# safe: it never rotates a secret it already made, and it is how `relay-deploy` is updated (the
# pipeline's key cannot write that script, on purpose). Everything after this goes through the
# deploy pipeline (docs-site/hosted-relay.md).
#
# What it sets up:
#   - key-only SSH, no root login, only the two users below, through a drop-in that wins over the
#     image's own (cloud-init turns passwords on)
#   - an admin user with sudo for you, and a `deploy` user for the pipeline whose key may run only
#     /usr/local/bin/relay-deploy (a `command=` restriction), owned by root
#   - ufw: deny incoming, allow 22 (rate limited), 80, 443
#   - automatic security updates for Ubuntu, with a reboot at 04:30 when one needs it
#   - docker and the compose plugin (Ubuntu's docker.io and docker-compose-v2) with log rotation
#   - the settings the stack reads, in /opt/cuttlefish-relay: relay.env (the owner secret, made here and
#     never printed) and caddy.env (the domain and the shared password's hash). The relay and Caddy
#     themselves are containers that `relay-deploy` starts from the release image (deploy/relay/
#     compose.yaml); this script installs neither
#   - the policy file `relay-deploy` judges each release's compose file with
#
# It also copes with a machine you have already set up a little by hand: an existing admin user (keeps its
# keys and, if it has a password, its sudo password), docker already installed (not installed again), an
# existing /etc/docker/daemon.json (merged), and `ufw allow OpenSSH` (replaced by a rate-limited rule).
#
# Inputs (environment variables):
#   RELAY_DOMAIN        required. The name that points at this Droplet (an A record must exist before
#                       Caddy can get a certificate).
#   ADMIN_PUBLIC_KEY    required. Your SSH public key (one line). Checked before root login is closed.
#   DEPLOY_PUBLIC_KEY   required. The public half of the key the pipeline will use.
#   ADMIN_USER          default `admin`.
#   DEPLOY_USER         default `deploy`.
#   GATE_USER           default `owner`. The user name of the shared password.
#   GATE_PASSWORD_HASH  optional, a bcrypt hash (`printf '%s' pw | deploy/droplet/gate_hash.sh IMAGE`).
#                       If neither this nor an earlier run's hash exists, a password is made and shown
#                       once at the end.
#
# BOOTSTRAP_SKIP_SERVICES=1 leaves ufw, systemd and the running ssh daemon alone. The tests use it
# inside a container; on a real Droplet never set it.

set -euo pipefail

ADMIN_USER="${ADMIN_USER:-admin}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
GATE_USER="${GATE_USER:-owner}"
STATE_DIR="/opt/cuttlefish-relay"
SKIP_SERVICES="${BOOTSTRAP_SKIP_SERVICES:-0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }
services() { [[ "$SKIP_SERVICES" != "1" ]]; }

export DEBIAN_FRONTEND=noninteractive

# -- before touching anything: check what we were given -------------------------------------------

[[ "$(id -u)" == "0" ]] || die "run as root"
. /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] ||
    die "this is written for Ubuntu 22.04 or 24.04 (found ${PRETTY_NAME:-unknown})"
[[ -n "${RELAY_DOMAIN:-}" ]] || die "set RELAY_DOMAIN to the name that points at this Droplet"
[[ "$RELAY_DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "RELAY_DOMAIN is not a host name"
[[ -n "${ADMIN_PUBLIC_KEY:-}" ]] || die "set ADMIN_PUBLIC_KEY: without it closing root login would lock you out"
[[ -n "${DEPLOY_PUBLIC_KEY:-}" ]] || die "set DEPLOY_PUBLIC_KEY to the public half of the pipeline's key"
[[ "$ADMIN_USER" =~ ^[a-z_][a-z0-9_-]{0,30}$ && "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] ||
    die "user names must be plain lowercase"
[[ "$ADMIN_USER" != "$DEPLOY_USER" && "$ADMIN_USER" != "root" && "$DEPLOY_USER" != "root" ]] ||
    die "the admin and deploy users must be two different non-root users"
[[ "$GATE_USER" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || die "GATE_USER must be letters, digits, - or _"
# shellcheck disable=SC2016  # a pattern, not an expansion
BCRYPT='^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'
[[ -z "${GATE_PASSWORD_HASH:-}" || "$GATE_PASSWORD_HASH" =~ $BCRYPT ]] ||
    die "GATE_PASSWORD_HASH must be a bcrypt hash (deploy/droplet/gate_hash.sh makes one)"
for file in deploy/relay/relay-deploy.sh deploy/relay/policy.py deploy/relay/compose.yaml \
    deploy/relay/Caddyfile deploy/droplet/gate_hash.sh; do
    [[ -f "$REPO_DIR/$file" ]] || die "run this from a checkout of the repository ($file is missing)"
done

# Reduce a public key to "<type> <base64>", so an option or a comment cannot ride in with it, and
# refuse anything ssh-keygen cannot read or that is a weak type.
clean_key() {
    local line="$1" tmp type body
    [[ "$line" != *$'\n'* ]] || die "a public key must be one line"
    tmp="$(mktemp)"
    printf '%s\n' "$line" >"$tmp"
    ssh-keygen -l -f "$tmp" >/dev/null 2>&1 || { rm -f "$tmp"; die "not a readable SSH public key: ${line:0:40}..."; }
    rm -f "$tmp"
    read -r type body _ <<<"$line"
    case "$type" in
        ssh-ed25519 | sk-ssh-ed25519@openssh.com | ecdsa-sha2-nistp256 | ecdsa-sha2-nistp384 | ecdsa-sha2-nistp521 | sk-ecdsa-sha2-nistp256@openssh.com) ;;
        *) die "use an ed25519 (or ecdsa) key, not $type" ;;
    esac
    printf '%s %s' "$type" "$body"
}

# ssh-keygen comes from openssh-client, which a Droplet has; make sure before we need it.
command -v ssh-keygen >/dev/null || { apt-get update -qq && apt-get install -y -qq openssh-client; }
admin_key="$(clean_key "$ADMIN_PUBLIC_KEY")"
deploy_key="$(clean_key "$DEPLOY_PUBLIC_KEY")"
[[ "$admin_key" != "$deploy_key" ]] || die "the admin and deploy keys must be different keys"

# -- packages -------------------------------------------------------------------------------------

log "Updating packages"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq ca-certificates curl gnupg openssh-server sudo ufw unattended-upgrades \
    openssl python3
# Docker may already be there (installed by hand, from Ubuntu or from Docker's own repository). Do not
# install a second one on top: docker.io and docker-ce fight over containerd.
if command -v docker >/dev/null; then
    log "Docker is already installed: leaving it alone"
else
    apt-get install -y -qq docker.io
fi
if ! docker compose version >/dev/null 2>&1; then
    if dpkg -s docker-ce >/dev/null 2>&1; then
        apt-get install -y -qq docker-compose-plugin # from Docker's own repository
    else
        apt-get install -y -qq docker-compose-v2
    fi
fi

# -- users ----------------------------------------------------------------------------------------

log "Users: $ADMIN_USER (sudo) and $DEPLOY_USER (the pipeline)"
id "$ADMIN_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN_USER" >/dev/null
usermod -aG sudo "$ADMIN_USER"
# A user made without a password cannot answer sudo's prompt over key-only SSH, so it gets passwordless
# sudo. A user that already has a password (one you made by hand) keeps asking for it.
if [[ "$(passwd -S "$ADMIN_USER" | awk '{print $2}')" == "P" ]]; then
    log "$ADMIN_USER already has a password: sudo keeps asking for it"
else
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$ADMIN_USER" >"/etc/sudoers.d/90-$ADMIN_USER"
    chmod 440 "/etc/sudoers.d/90-$ADMIN_USER"
    visudo -cf "/etc/sudoers.d/90-$ADMIN_USER" >/dev/null || die "sudoers entry is invalid"
fi

id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER" >/dev/null
# The docker group is root-equivalent. What contains it is that this user's only key can run one
# root-owned script that accepts one kind of argument (see relay-deploy.sh).
getent group docker >/dev/null || groupadd docker # a docker that came some other way may not have made it
usermod -aG docker "$DEPLOY_USER"

install_authorized_keys() { # user, line
    local user="$1" line="$2" home
    home="$(getent passwd "$user" | cut -d: -f6)"
    install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
    printf '%s\n' "$line" >"$home/.ssh/authorized_keys"
    chown "$user:$user" "$home/.ssh/authorized_keys"
    chmod 600 "$home/.ssh/authorized_keys"
}
# The admin's own keys are kept (an existing one may be the key you are logged in with); the given key
# is added if it is not there. The deploy user's file is replaced: it holds exactly one restricted key.
ensure_authorized_key() { # user, "<type> <base64>"
    local user="$1" key="$2" home
    home="$(getent passwd "$user" | cut -d: -f6)"
    install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
    touch "$home/.ssh/authorized_keys"
    grep -qF "$key" "$home/.ssh/authorized_keys" || printf '%s admin-key\n' "$key" >>"$home/.ssh/authorized_keys"
    chown "$user:$user" "$home/.ssh/authorized_keys"
    chmod 600 "$home/.ssh/authorized_keys"
}
ensure_authorized_key "$ADMIN_USER" "$admin_key"
# `restrict` turns off forwarding, agent, X11 and a tty; `command=` runs the script whatever the
# client asked for and hands the client's text to it as SSH_ORIGINAL_COMMAND.
install_authorized_keys "$DEPLOY_USER" "restrict,command=\"/usr/local/bin/relay-deploy\" $deploy_key pipeline-deploy-key"

# -- the deploy script, owned by root --------------------------------------------------------------

log "Installing /usr/local/bin/relay-deploy (root-owned; the deploy user cannot change it)"
install -m 755 -o root -g root "$REPO_DIR/deploy/relay/relay-deploy.sh" /usr/local/bin/relay-deploy
install -d -m 755 -o root -g root /usr/local/lib/cuttlefish-relay
install -m 644 -o root -g root "$REPO_DIR/deploy/relay/policy.py" /usr/local/lib/cuttlefish-relay/policy.py

install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$STATE_DIR"
if [[ ! -f "$STATE_DIR/relay.env" ]]; then
    log "Making the relay's owner secret (written to $STATE_DIR/relay.env, not shown)"
    secret="$(openssl rand -base64 48 | tr -d '=+/\n' | cut -c1-48)"
    (umask 077 && printf 'CUTTLEFISH_RELAY_OWNER_SECRET=%s\n' "$secret" >"$STATE_DIR/relay.env")
    unset secret
fi
# Root writes it, the deploy user (and so docker's --env-file) can read it, nobody else.
chown "root:$DEPLOY_USER" "$STATE_DIR/relay.env"
chmod 640 "$STATE_DIR/relay.env"

# -- docker ---------------------------------------------------------------------------------------

log "Docker: rotate container logs, keep containers up across daemon upgrades"
install -d -m 755 /etc/docker
# Merged into whatever is already there (the first time, the old file is kept as daemon.json.bak).
python3 - <<'PY'
import json
import os
import shutil

path = "/etc/docker/daemon.json"
config = {}
if os.path.exists(path):
    if not os.path.exists(path + ".bak"):
        shutil.copy(path, path + ".bak")
    with open(path) as handle:
        text = handle.read().strip()
    config = json.loads(text) if text else {}
config.update(
    {
        "log-driver": "local",
        "log-opts": {"max-size": "10m", "max-file": "3"},
        "live-restore": True,
    }
)
with open(path, "w") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")
PY
if services; then
    systemctl enable --now docker
    systemctl restart docker
fi

# -- the stack's settings ----------------------------------------------------------------------------

log "The settings the relay and Caddy read (the stack itself is started by the pipeline)"
generated_password=""
if [[ -n "${GATE_PASSWORD_HASH:-}" ]]; then
    gate_hash="$GATE_PASSWORD_HASH"
elif [[ -f "$STATE_DIR/caddy.env" ]] && grep -q '^GATE_PASSWORD_HASH=' "$STATE_DIR/caddy.env"; then
    gate_hash="$(sed -n "s/^GATE_PASSWORD_HASH='\(.*\)'$/\1/p" "$STATE_DIR/caddy.env")"
else
    services || die "without a running docker daemon, give GATE_PASSWORD_HASH instead of having one made"
    caddy_image="$(sed -n 's/^    image: \(caddy:[^ ]*\)$/\1/p' "$REPO_DIR/deploy/relay/compose.yaml")"
    [[ -n "$caddy_image" ]] || die "could not read the pinned Caddy image from deploy/relay/compose.yaml"
    generated_password="$(openssl rand -base64 18 | tr -d '=+/\n')"
    gate_hash="$(printf '%s' "$generated_password" | "$REPO_DIR/deploy/droplet/gate_hash.sh" "$caddy_image")"
fi
[[ "$gate_hash" =~ $BCRYPT ]] || die "the gate password hash is not a bcrypt hash"

install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$STATE_DIR"
# Root writes them, the deploy user (and so compose's env_file) can read them, nobody else.
(umask 027 && cat >"$STATE_DIR/caddy.env" <<EOF
RELAY_ADDRESS=$RELAY_DOMAIN
GATE_USER=$GATE_USER
GATE_PASSWORD_HASH='$gate_hash'
EOF
)
chown "root:$DEPLOY_USER" "$STATE_DIR/caddy.env"
chmod 640 "$STATE_DIR/caddy.env"

# -- automatic security updates -------------------------------------------------------------------

log "Automatic security updates, rebooting at 04:30 when one needs it"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
cat >/etc/apt/apt.conf.d/52relay-unattended-upgrades <<'APT'
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
APT

# -- ssh, last, once both keys are in place ---------------------------------------------------------

log "SSH: key-only, no root login, only $ADMIN_USER and $DEPLOY_USER"
install -d -m 755 /etc/ssh/sshd_config.d
# sshd takes the first value it sees and reads this folder in name order, so `00-` beats the
# image's own `50-cloud-init.conf` that turns passwords on.
cat >/etc/ssh/sshd_config.d/00-relay-hardening.conf <<SSH
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
AllowUsers $ADMIN_USER $DEPLOY_USER
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2

Match User $DEPLOY_USER
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitTTY no
    X11Forwarding no
SSH
mkdir -p /run/sshd
sshd -t || die "sshd rejected the new configuration; nothing was reloaded"

if services; then
    log "Starting services and the firewall"
    systemctl enable --now unattended-upgrades

    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw limit 22/tcp >/dev/null
    # A hand-made `ufw allow OpenSSH` is replaced by the rate-limited rule above (added first, so SSH
    # is never closed).
    ufw --force delete allow OpenSSH >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
    ufw allow 443/udp >/dev/null # HTTP/3
    ufw --force enable >/dev/null

    # Reload last, so a mistake above never closes the door we are standing in.
    systemctl reload ssh || systemctl reload sshd
fi

# -- say what to do next -----------------------------------------------------------------------------

host_key="$(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null || true)"
log "Done"
cat <<EOF
Next:
  1. Point the DNS A record for $RELAY_DOMAIN at this Droplet (Caddy gets its certificate once it does;
     add ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory to $STATE_DIR/caddy.env for a
     first run if you want to be sure before using the real rate limit, and remove it afterwards).
  2. In GitHub, on the \`production\` environment, set:
       DEPLOY_HOST=$RELAY_DOMAIN   DEPLOY_USER=$DEPLOY_USER   RELAY_PUBLIC_URL=https://$RELAY_DOMAIN
       secret DEPLOY_SSH_KEY    = the private half of the key whose public half you gave as DEPLOY_PUBLIC_KEY
       secret DEPLOY_KNOWN_HOSTS = ${host_key:+$RELAY_DOMAIN $host_key}
     then set the repository variable DEPLOY_ENABLED=true and approve the first deploy.
  3. The relay's owner secret is in $STATE_DIR/relay.env (read it with sudo as $ADMIN_USER). The stack
     is not running yet: the pipeline's first deploy starts it.
  4. Check this machine any time with: sudo $REPO_DIR/deploy/droplet/verify.sh
EOF
if [[ -n "$generated_password" ]]; then
    cat <<EOF

The shared password for the site (shown once; only its hash is kept):
  user:     $GATE_USER
  password: $generated_password
EOF
fi
