#!/usr/bin/env bash
# Check that a Droplet is still the way bootstrap.sh left it (CUT-143, ADR-0043). Read-only: it
# changes nothing and prints no secret. Run as root; exits non-zero if anything is wrong.
#
#   sudo deploy/droplet/verify.sh                 on the Droplet
#   sudo deploy/droplet/verify.sh --no-services   skip what needs systemd, ufw and live sockets
#
# ADMIN_USER, DEPLOY_USER and STATE_DIR can be set as for bootstrap.sh.

# shellcheck disable=SC2015  # `A && pass || fail`: pass cannot fail
set -uo pipefail

ADMIN_USER="${ADMIN_USER:-admin}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
STATE_DIR="${STATE_DIR:-/opt/cuttlefish-relay}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
live=1
[[ "${1:-}" == "--no-services" ]] && live=0

failures=0
pass() { printf '  ok    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }
check() { # description, then a command
    local description="$1"
    shift
    if "$@" >/dev/null 2>&1; then pass "$description"; else fail "$description"; fi
}
owner_mode() { stat -c '%U:%G %a' "$1" 2>/dev/null; }
sshd_value() { # user, key -> the effective value
    sshd -T -C "user=$1,host=localhost,addr=127.0.0.1" 2>/dev/null | awk -v k="$2" '$1 == k { $1 = ""; sub(/^ /, ""); print }'
}

[[ "$(id -u)" == "0" ]] || { echo "run as root" >&2; exit 2; }

echo "ssh"
[[ "$(sshd_value "$ADMIN_USER" permitrootlogin)" == "no" ]] && pass "root login is off" || fail "root login is not off"
[[ "$(sshd_value "$ADMIN_USER" passwordauthentication)" == "no" ]] && pass "passwords are off" || fail "passwords are not off"
[[ "$(sshd_value "$ADMIN_USER" kbdinteractiveauthentication)" == "no" ]] && pass "keyboard-interactive is off" || fail "keyboard-interactive is not off"
[[ "$(sshd_value "$ADMIN_USER" pubkeyauthentication)" == "yes" ]] && pass "public keys are on" || fail "public keys are not on"
allowed="$(sshd_value "$ADMIN_USER" allowusers | tr '\n' ' ' | xargs)" # one line per user
[[ " $allowed " == *" $ADMIN_USER "* && " $allowed " == *" $DEPLOY_USER "* && "$(wc -w <<<"$allowed")" == "2" ]] &&
    pass "only $ADMIN_USER and $DEPLOY_USER may log in" || fail "AllowUsers is '$allowed'"
[[ "$(sshd_value "$DEPLOY_USER" allowtcpforwarding)" == "no" && "$(sshd_value "$DEPLOY_USER" permittty)" == "no" ]] &&
    pass "$DEPLOY_USER has no forwarding and no tty" || fail "$DEPLOY_USER can forward or open a tty"
[[ "$(sshd_value "$ADMIN_USER" allowtcpforwarding)" != "no" || "$(sshd_value "$ADMIN_USER" permittty)" != "no" ]] &&
    pass "the Match block for $DEPLOY_USER does not leak to $ADMIN_USER" || fail "the deploy restrictions leak to $ADMIN_USER"

echo "users and keys"
deploy_home="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
admin_home="$(getent passwd "$ADMIN_USER" | cut -d: -f6)"
lines="$(grep -c . "$deploy_home/.ssh/authorized_keys" 2>/dev/null || echo 0)"
[[ "$lines" == "1" ]] && pass "$DEPLOY_USER has exactly one key" || fail "$DEPLOY_USER has $lines keys"
grep -q '^restrict,command="/usr/local/bin/relay-deploy" ' "$deploy_home/.ssh/authorized_keys" 2>/dev/null &&
    pass "that key may run only relay-deploy" || fail "the deploy key is not restricted to relay-deploy"
! grep -q 'command=' "$admin_home/.ssh/authorized_keys" 2>/dev/null && pass "$ADMIN_USER's key is unrestricted" || fail "$ADMIN_USER's key carries a command="
check "$DEPLOY_USER is in the docker group" bash -c "id -nG $DEPLOY_USER | tr ' ' '\n' | grep -qx docker"
check "$DEPLOY_USER is not a sudoer" bash -c "! id -nG $DEPLOY_USER | tr ' ' '\n' | grep -qx sudo"
check "$ADMIN_USER is a sudoer" bash -c "id -nG $ADMIN_USER | tr ' ' '\n' | grep -qx sudo"
[[ "$(owner_mode "$deploy_home/.ssh/authorized_keys")" == "$DEPLOY_USER:$DEPLOY_USER 600" ]] && pass "authorized_keys is private" || fail "authorized_keys is not 600 and owned by $DEPLOY_USER"

echo "the deploy script"
[[ "$(stat -c '%U' /usr/local/bin/relay-deploy 2>/dev/null)" == "root" ]] && pass "relay-deploy is owned by root" || fail "relay-deploy is not owned by root"
mode="$(stat -c '%a' /usr/local/bin/relay-deploy 2>/dev/null || echo 777)"
(( (8#$mode & 8#022) == 0 )) && pass "relay-deploy is writable by root only" || fail "relay-deploy is writable by group or others ($mode)"
cmp -s /usr/local/bin/relay-deploy "$REPO_DIR/deploy/relay/relay-deploy.sh" && pass "it matches this checkout" || fail "it differs from this checkout (re-run bootstrap.sh to update it)"

echo "secrets on disk"
[[ "$(owner_mode "$STATE_DIR/relay.env")" == "root:$DEPLOY_USER 640" ]] && pass "relay.env is root:$DEPLOY_USER 640" || fail "relay.env is '$(owner_mode "$STATE_DIR/relay.env")'"
secret="$(sed -n 's/^CUTTLEFISH_RELAY_OWNER_SECRET=//p' "$STATE_DIR/relay.env" 2>/dev/null)"
(( ${#secret} >= 32 )) && pass "the owner secret is 32 characters or more" || fail "the owner secret is missing or short"
unset secret
[[ "$(owner_mode /etc/caddy/relay.env)" == "root:caddy 640" ]] && pass "caddy's relay.env is root:caddy 640" || fail "caddy's relay.env is '$(owner_mode /etc/caddy/relay.env)'"
grep -q "^GATE_PASSWORD_HASH='\\\$2[aby]\\\$" /etc/caddy/relay.env 2>/dev/null && pass "the gate holds a bcrypt hash, not a password" || fail "the gate password hash is missing or not bcrypt"

echo "caddy"
cmp -s /etc/caddy/Caddyfile "$REPO_DIR/deploy/relay/Caddyfile" && pass "the Caddyfile matches this checkout" || fail "the Caddyfile differs from this checkout"
check "the Caddyfile validates" runuser -u caddy -- bash -c 'set -a; . /etc/caddy/relay.env; set +a; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'
check "caddy's service reads relay.env" grep -q 'EnvironmentFile=/etc/caddy/relay.env' /etc/systemd/system/caddy.service.d/relay.conf

echo "updates and docker"
check "package lists and security updates run daily" bash -c "apt-config dump | grep -q 'APT::Periodic::Unattended-Upgrade \"1\"' && apt-config dump | grep -q 'APT::Periodic::Update-Package-Lists \"1\"'"
check "Caddy's repository is allowed to update itself" grep -q 'cloudsmith/caddy/stable:any-version' /etc/apt/apt.conf.d/52relay-unattended-upgrades
check "docker rotates its logs" grep -q '"max-size": "10m"' /etc/docker/daemon.json
check "caddy's key is the pinned one" bash -c "gpg --show-keys --with-colons /usr/share/keyrings/caddy-stable-archive-keyring.gpg | awk -F: '/^fpr:/{print \$10; exit}' | grep -qx 65760C51EDEA2017CEA2CA15155B6D79CA56EA34"

if ((live)); then
    echo "running services"
    for unit in ssh docker caddy unattended-upgrades; do
        check "$unit is active" systemctl is-active --quiet "$unit"
    done
    ufw_status="$(ufw status verbose 2>/dev/null)"
    [[ "$ufw_status" == *"Status: active"* && "$ufw_status" == *"deny (incoming)"* ]] && pass "ufw is on and denies incoming" || fail "ufw is not on, or does not deny incoming"
    allowed_ports="$(awk '/ALLOW IN|LIMIT IN/ && $1 !~ /\(v6\)/ {print $1 ":" $2}' <<<"$ufw_status" | sort | tr '\n' ' ')"
    [[ "$allowed_ports" == "22/tcp:LIMIT 443:ALLOW 443/tcp:ALLOW 443/udp:ALLOW 80/tcp:ALLOW " || "$allowed_ports" == "22/tcp:LIMIT 443/tcp:ALLOW 443/udp:ALLOW 80/tcp:ALLOW " ]] &&
        pass "ufw opens 22 (limited), 80 and 443 only" || fail "ufw opens: $allowed_ports"
    # Anything listening on a public address must be ssh, http or https. The relay is on loopback.
    stray="$(ss -H -ltun 2>/dev/null | awk '{print $5}' | grep -Ev '^(127\.|\[::1\]|\[fe80|::1)' | grep -Ev ':(22|80|443)$' | grep -Ev '^(0\.0\.0\.0|\*|\[::\]):(68|323)$' || true)"
    [[ -z "$stray" ]] && pass "nothing else listens on a public address" || fail "listening on: $(tr '\n' ' ' <<<"$stray")"
fi

echo
if ((failures)); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
