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
#   - automatic security updates for Ubuntu and Caddy, with a reboot at 04:30 when one needs it
#   - docker (Ubuntu's docker.io) with log rotation, and Caddy from its own signed apt repository,
#     whose key is checked against a pinned fingerprint
#   - Caddy in front of the relay with the shared password gate (deploy/relay/Caddyfile)
#   - /opt/cuttlefish-relay/relay.env holding the owner secret (made here, never printed)
#
# Inputs (environment variables):
#   RELAY_DOMAIN        required. The name that points at this Droplet (an A record must exist before
#                       Caddy can get a certificate).
#   ADMIN_PUBLIC_KEY    required. Your SSH public key (one line). Checked before root login is closed.
#   DEPLOY_PUBLIC_KEY   required. The public half of the key the pipeline will use.
#   ADMIN_USER          default `admin`.
#   DEPLOY_USER         default `deploy`.
#   GATE_USER           default `owner`. The user name of the shared password.
#   GATE_PASSWORD_HASH  optional, from `caddy hash-password`. If neither this nor an earlier run's
#                       hash exists, a password is made and shown once at the end.
#
# BOOTSTRAP_SKIP_SERVICES=1 leaves ufw, systemd and the running ssh daemon alone. The tests use it
# inside a container; on a real Droplet never set it.

set -euo pipefail

CADDY_KEY_FINGERPRINT="65760C51EDEA2017CEA2CA15155B6D79CA56EA34" # gitleaks:allow (a public key fingerprint, not a secret)
CADDY_KEY_URL="${CADDY_KEY_URL:-https://dl.cloudsmith.io/public/caddy/stable/gpg.key}" # overridable only so a test can offer a wrong key
CADDY_REPO_LINE="deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main"

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
    die "GATE_PASSWORD_HASH must be a bcrypt hash from \`caddy hash-password\`"
for file in deploy/relay/relay-deploy.sh deploy/relay/Caddyfile; do
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
    docker.io openssl

log "Adding Caddy's apt repository (key checked against a pinned fingerprint)"
tmpkey="$(mktemp)"
curl -fsSL "$CADDY_KEY_URL" -o "$tmpkey"
actual="$(gpg --show-keys --with-colons "$tmpkey" | awk -F: '/^fpr:/ {print $10; exit}')"
if [[ "$actual" != "$CADDY_KEY_FINGERPRINT" ]]; then
    rm -f "$tmpkey"
    die "Caddy's signing key has fingerprint $actual, not the pinned $CADDY_KEY_FINGERPRINT; refusing to trust it"
fi
gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg "$tmpkey"
rm -f "$tmpkey"
chmod 644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
printf '%s\n' "$CADDY_REPO_LINE" >/etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq
apt-get install -y -qq caddy

# -- users ----------------------------------------------------------------------------------------

log "Users: $ADMIN_USER (sudo) and $DEPLOY_USER (the pipeline)"
id "$ADMIN_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN_USER" >/dev/null
usermod -aG sudo "$ADMIN_USER"
# Key-only login, so sudo cannot ask for a password that does not exist.
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$ADMIN_USER" >"/etc/sudoers.d/90-$ADMIN_USER"
chmod 440 "/etc/sudoers.d/90-$ADMIN_USER"
visudo -cf "/etc/sudoers.d/90-$ADMIN_USER" >/dev/null || die "sudoers entry is invalid"

id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER" >/dev/null
# The docker group is root-equivalent. What contains it is that this user's only key can run one
# root-owned script that accepts one kind of argument (see relay-deploy.sh).
usermod -aG docker "$DEPLOY_USER"

install_authorized_keys() { # user, line
    local user="$1" line="$2" home
    home="$(getent passwd "$user" | cut -d: -f6)"
    install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
    printf '%s\n' "$line" >"$home/.ssh/authorized_keys"
    chown "$user:$user" "$home/.ssh/authorized_keys"
    chmod 600 "$home/.ssh/authorized_keys"
}
install_authorized_keys "$ADMIN_USER" "$admin_key admin-key"
# `restrict` turns off forwarding, agent, X11 and a tty; `command=` runs the script whatever the
# client asked for and hands the client's text to it as SSH_ORIGINAL_COMMAND.
install_authorized_keys "$DEPLOY_USER" "restrict,command=\"/usr/local/bin/relay-deploy\" $deploy_key pipeline-deploy-key"

# -- the deploy script, owned by root --------------------------------------------------------------

log "Installing /usr/local/bin/relay-deploy (root-owned; the deploy user cannot change it)"
install -m 755 -o root -g root "$REPO_DIR/deploy/relay/relay-deploy.sh" /usr/local/bin/relay-deploy

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
cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "local",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true
}
JSON

# -- caddy ----------------------------------------------------------------------------------------

log "Caddy: the repository's Caddyfile with the shared password in front"
generated_password=""
if [[ -n "${GATE_PASSWORD_HASH:-}" ]]; then
    gate_hash="$GATE_PASSWORD_HASH"
elif [[ -f /etc/caddy/relay.env ]] && grep -q '^GATE_PASSWORD_HASH=' /etc/caddy/relay.env; then
    gate_hash="$(sed -n "s/^GATE_PASSWORD_HASH='\(.*\)'$/\1/p" /etc/caddy/relay.env)"
else
    generated_password="$(openssl rand -base64 18 | tr -d '=+/\n')"
    gate_hash="$(caddy hash-password --plaintext "$generated_password")"
fi
[[ "$gate_hash" =~ $BCRYPT ]] || die "the gate password hash in /etc/caddy/relay.env is not a bcrypt hash"
install -d -m 755 /etc/caddy
(umask 027 && cat >/etc/caddy/relay.env <<EOF
RELAY_ADDRESS=$RELAY_DOMAIN
GATE_USER=$GATE_USER
GATE_PASSWORD_HASH='$gate_hash'
EOF
)
chown root:caddy /etc/caddy/relay.env
chmod 640 /etc/caddy/relay.env
install -m 644 -o root -g root "$REPO_DIR/deploy/relay/Caddyfile" /etc/caddy/Caddyfile
install -d -m 755 /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/caddy/relay.env\n' >/etc/systemd/system/caddy.service.d/relay.conf

# Validate as the caddy user: as root it would create the access log owned by root, and the service
# could then not write it.
runuser -u caddy -- bash -c 'set -a; . /etc/caddy/relay.env; set +a; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile' >/dev/null 2>&1 ||
    die "Caddy rejected its configuration: run \`runuser -u caddy -- caddy validate --config /etc/caddy/Caddyfile\` to see why"

# -- automatic security updates -------------------------------------------------------------------

log "Automatic security updates (Ubuntu and Caddy), rebooting at 04:30 when one needs it"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
cat >/etc/apt/apt.conf.d/52relay-unattended-upgrades <<'APT'
Unattended-Upgrade::Allowed-Origins:: "cloudsmith/caddy/stable:any-version";
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
    systemctl daemon-reload
    systemctl enable --now docker
    systemctl restart docker
    systemctl enable caddy
    systemctl restart caddy
    systemctl enable --now unattended-upgrades

    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw limit 22/tcp >/dev/null
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
  1. Point the DNS A record for $RELAY_DOMAIN at this Droplet (Caddy gets its certificate once it does).
  2. In GitHub, on the \`production\` environment, set:
       DEPLOY_HOST=$RELAY_DOMAIN   DEPLOY_USER=$DEPLOY_USER   RELAY_PUBLIC_URL=https://$RELAY_DOMAIN
       secret DEPLOY_SSH_KEY    = the private half of the key whose public half you gave as DEPLOY_PUBLIC_KEY
       secret DEPLOY_KNOWN_HOSTS = ${host_key:+$RELAY_DOMAIN $host_key}
     then set the repository variable DEPLOY_ENABLED=true and approve the first deploy.
  3. The relay's owner secret is in $STATE_DIR/relay.env (read it with sudo as $ADMIN_USER).
  4. Check this machine any time with: sudo $REPO_DIR/deploy/droplet/verify.sh
EOF
if [[ -n "$generated_password" ]]; then
    cat <<EOF

The shared password for the site (shown once; only its hash is kept):
  user:     $GATE_USER
  password: $generated_password
EOF
fi
