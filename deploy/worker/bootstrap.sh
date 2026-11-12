#!/usr/bin/env bash
# Bootstrap a bare Ubuntu 22.04 or 24.04 machine into a cuttlefish worker (CUT-213, ADR-0057).
#
# It installs what the worker profile lists (src/cuttlefish/worker/profile.toml, CUT-212) and nothing
# else: no user, no firewall, no service for the worker itself, no secret. Joining the controller
# (`cuttlefish worker join`) is a separate step by hand, because the owner confirms it on the PC.
# Run it as root, from a checkout; running it again is safe, and it skips a tool that is already
# there and recent enough (so a docker or node you installed another way is left alone).
#
# How each tool gets there, by the profile's `apt` / `how` field:
#   apt        Ubuntu's own package.
#   uv         a pinned release tarball from GitHub, checked against its published sha256.
#   node       a pinned tarball from nodejs.org, checked against the published SHASUMS256.txt, into
#              /usr/local (Ubuntu 24.04's own nodejs is too old for the profile).
#   agent-cli  kopicode: a pinned release binary checked against the release's SHA256SUMS. claude and
#              codex: `npm install -g` of the vendor's package. Only the agents in WORKER_AGENTS.
# Nothing is piped into a shell. An agent CLI is installed but not signed in: log it in as the user the
# worker runs as.
#
# Inputs (environment variables):
#   WORKER_AGENTS     agent CLIs to install, comma separated; default `kopicode` (the others need a login
#                     anyway). One agent satisfies the profile's `agent` group.
#   WORKER_USER       optional existing user to add to the docker group.
#   PROFILE           the profile file; default the one beside this checkout's source.
#   UV_VERSION, NODE_VERSION, KOPICODE_VERSION   the pins below, to move one on purpose.
#
# `--plan` prints what would be installed and changes nothing (also needs no root).
# BOOTSTRAP_SKIP_SERVICES=1 leaves systemd alone (docker is not enabled); the tests use it in a container.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${PROFILE:-$HERE/../../src/cuttlefish/worker/profile.toml}"
WORKER_AGENTS="${WORKER_AGENTS:-kopicode}"
UV_VERSION="${UV_VERSION:-0.9.16}"
NODE_VERSION="${NODE_VERSION:-22.12.0}"
KOPICODE_VERSION="${KOPICODE_VERSION:-v0.5.0}"
SKIP_SERVICES="${BOOTSTRAP_SKIP_SERVICES:-0}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'worker bootstrap: %s\n' "$*" >&2; exit 1; }

# The profile is flat TOML (`[[tool]]` tables of `key = "value"` or `key = ["a", "b"]`), which the Python
# parser validates; this reads the same shape with awk so a bare machine needs no Python. One line per
# tool: name, command, version arguments, min, group, apt packages, how; `-` stands for empty.
plan() {
    awk '
        function flush() {
            if (name != "") print name "\t" (command == "" ? name : command) "\t" (vargs == "" ? "--version" : vargs) "\t" (min == "" ? "-" : min) "\t" (group == "" ? "-" : group) "\t" (apt == "" ? "-" : apt) "\t" (how == "" ? "-" : how)
            name = command = vargs = min = group = apt = how = ""
        }
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        /^\[\[tool\]\]/ { flush(); next }
        /^[a-z_]+[[:space:]]*=/ {
            key = $0; sub(/[[:space:]]*=.*/, "", key)
            val = $0; sub(/^[^=]*=[[:space:]]*/, "", val)
            sub(/[[:space:]]+#.*/, "", val)
            gsub(/[][",]/, " ", val); gsub(/^[[:space:]]+|[[:space:]]+$/, "", val); gsub(/[[:space:]]+/, " ", val)
            if (key == "name") name = val; else if (key == "command") command = val
            else if (key == "version" && val !~ /^[0-9]+$/) vargs = val
            else if (key == "min") min = val; else if (key == "group") group = val
            else if (key == "apt") apt = val; else if (key == "how") how = val
        }
        END { flush() }
    ' "$PROFILE"
}

# Is the tool `$1` there: `$2` on PATH, and (when `$4` is not `-`) at least version `$4`? `$3` are the version
# arguments. A tool reached through a subcommand (`docker compose`, so `$2` is not `$1`) must also run.
satisfied() {
    local name="$1" command="$2" vargs="$3" min="$4" out found status=0
    command -v "$command" >/dev/null 2>&1 || return 1
    # shellcheck disable=SC2086  # the arguments are words from the profile
    out="$("$command" $vargs 2>&1)" || status=$?
    [[ "$status" == 0 || "$name" == "$command" ]] || return 1
    [[ "$min" == "-" ]] && return 0
    found="$(head -n1 <<<"$out" | grep -oE '[0-9]+(\.[0-9]+)*' | head -n1 || true)"
    [[ -z "$found" ]] && return 0 # it would not say, so it is not judged (as the profile check does)
    [[ "$(printf '%s\n%s\n' "$min" "$found" | sort -V | head -n1)" == "$min" ]]
}

if [[ "${1:-}" == "--plan" ]]; then
    [[ -f "$PROFILE" ]] || die "no profile at $PROFILE"
    plan
    exit 0
fi

[[ "$(id -u)" == "0" ]] || die "run as root"
. /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] ||
    die "this is written for Ubuntu 22.04 or 24.04 (found ${PRETTY_NAME:-unknown})"
[[ -f "$PROFILE" ]] || die "no profile at $PROFILE"
[[ "$UV_VERSION" =~ ^[0-9.]+$ && "$NODE_VERSION" =~ ^[0-9.]+$ && "$KOPICODE_VERSION" =~ ^v[0-9.]+$ ]] ||
    die "UV_VERSION, NODE_VERSION and KOPICODE_VERSION must be plain version numbers"
[[ "$WORKER_AGENTS" =~ ^[a-z,-]+$ ]] || die "WORKER_AGENTS is a comma separated list of agent names"
if [[ -n "${WORKER_USER:-}" ]]; then
    id "$WORKER_USER" >/dev/null 2>&1 || die "WORKER_USER $WORKER_USER does not exist"
fi
case "$(uname -m)" in
    x86_64) UV_ARCH=x86_64 NODE_ARCH=x64 GO_ARCH=amd64 ;;
    aarch64) UV_ARCH=aarch64 NODE_ARCH=arm64 GO_ARCH=arm64 ;;
    *) die "unsupported machine $(uname -m)" ;;
esac

mapfile -t TOOLS < <(plan)
((${#TOOLS[@]} > 0)) || die "the profile lists no tools"

names=" $(printf '%s\n' "${TOOLS[@]}" | cut -f1 | tr '\n' ' ')"
for agent in ${WORKER_AGENTS//,/ }; do
    [[ "$names" == *" $agent "* ]] || die "WORKER_AGENTS names '$agent', which the profile does not list"
done

export DEBIAN_FRONTEND=noninteractive
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Download $1 to $2 (https only, fails on an HTTP error).
fetch() { curl --proto '=https' --tlsv1.2 -fsSL --retry 3 -o "$2" "$1" || die "could not download $1"; }
# Check the file `$2` against the line for `$3` in the checksum file `$1`.
checksum() { (cd "$(dirname "$2")" && grep -E "[ *]$3\$" "$1" | sha256sum -c - >/dev/null) || die "checksum mismatch for $3"; }

log "base packages"
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl xz-utils >/dev/null

# -- the profile's own apt packages -------------------------------------------------------------------

packages=()
for line in "${TOOLS[@]}"; do
    IFS=$'\t' read -r name command vargs min _group apt _how <<<"$line"
    [[ "$apt" == "-" ]] && continue
    if satisfied "$name" "$command" "$vargs" "$min"; then
        echo "$name is already here"
    else
        # shellcheck disable=SC2206  # package names from the profile, validated there
        packages+=($apt)
    fi
done
if ((${#packages[@]} > 0)); then
    log "apt: ${packages[*]}"
    apt-get install -y -qq --no-install-recommends "${packages[@]}" >/dev/null
fi

# -- the rest, by `how` --------------------------------------------------------------------------------

install_uv() {
    local base="https://github.com/astral-sh/uv/releases/download/$UV_VERSION" file="uv-$UV_ARCH-unknown-linux-gnu.tar.gz"
    fetch "$base/$file" "$WORK/$file"
    fetch "$base/$file.sha256" "$WORK/$file.sha256"
    (cd "$WORK" && sha256sum -c "$file.sha256" >/dev/null) || die "checksum mismatch for $file"
    tar -xzf "$WORK/$file" -C "$WORK"
    install -m 755 "$WORK/uv-$UV_ARCH-unknown-linux-gnu/uv" "$WORK/uv-$UV_ARCH-unknown-linux-gnu/uvx" /usr/local/bin/
}

install_node() {
    local base="https://nodejs.org/dist/v$NODE_VERSION" file="node-v$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
    fetch "$base/$file" "$WORK/$file"
    fetch "$base/SHASUMS256.txt" "$WORK/SHASUMS256.txt"
    checksum "$WORK/SHASUMS256.txt" "$WORK/$file" "$file"
    tar -xJf "$WORK/$file" -C /usr/local --strip-components=1 --no-same-owner \
        --exclude='*/CHANGELOG.md' --exclude='*/LICENSE' --exclude='*/README.md'
}

install_agent() {
    case "$1" in
        kopicode)
            local base="https://github.com/leejianrong/kopicode/releases/download/$KOPICODE_VERSION" file="kopicode-linux-$GO_ARCH"
            fetch "$base/$file" "$WORK/$file"
            fetch "$base/SHA256SUMS" "$WORK/SHA256SUMS"
            checksum "$WORK/SHA256SUMS" "$WORK/$file" "$file"
            install -m 755 "$WORK/$file" /usr/local/bin/kopicode
            ;;
        claude) npm install -g --silent @anthropic-ai/claude-code ;;
        codex) npm install -g --silent @openai/codex ;;
        *) die "no way to install the agent '$1'" ;;
    esac
}

wanted_agent() { [[ ",$WORKER_AGENTS," == *",$1,"* ]]; }

# uv and node first: an agent installed with npm needs node.
for want in uv node agent-cli; do
    for line in "${TOOLS[@]}"; do
        IFS=$'\t' read -r name command vargs min _group _apt how <<<"$line"
        [[ "$how" == "$want" ]] || continue
        if [[ "$how" == "agent-cli" ]] && ! wanted_agent "$name"; then continue; fi
        if satisfied "$name" "$command" "$vargs" "$min"; then
            echo "$name is already here"
            continue
        fi
        log "$name"
        case "$how" in
            uv) install_uv ;;
            node) install_node ;;
            agent-cli) install_agent "$name" ;;
        esac
    done
done

# -- docker, once it is installed ---------------------------------------------------------------------

if [[ -n "${WORKER_USER:-}" ]] && getent group docker >/dev/null; then
    usermod -aG docker "$WORKER_USER"
fi
if [[ "$SKIP_SERVICES" != "1" ]] && command -v systemctl >/dev/null 2>&1 && dpkg -s docker.io >/dev/null 2>&1; then
    systemctl enable --now docker
fi

# -- did it work ---------------------------------------------------------------------------------------

log "checking the machine against the profile"
failed=0
for line in "${TOOLS[@]}"; do
    IFS=$'\t' read -r name command vargs min group _apt _how <<<"$line"
    if [[ "$group" != "-" ]]; then continue; fi # an agent: one of the group is enough, below
    if satisfied "$name" "$command" "$vargs" "$min"; then echo "  ok      $name"; else echo "  MISSING $name"; failed=1; fi
done
agent_ok=0
agents=()
for line in "${TOOLS[@]}"; do
    IFS=$'\t' read -r name command vargs min group _apt _how <<<"$line"
    [[ "$group" != "-" ]] || continue
    agents+=("$name")
    if satisfied "$name" "$command" "$vargs" "$min"; then echo "  ok      $name"; agent_ok=1; fi
done
if ((${#agents[@]} > 0 && agent_ok == 0)); then echo "  MISSING one of: ${agents[*]}"; failed=1; fi
((failed == 0)) || die "the machine does not match the profile (see above)"
echo
echo "This machine matches the worker profile. Next: install cuttlefish for the user that will run the worker,"
echo "sign that user in to its agent CLI, then run \`cuttlefish worker join '<code>'\` (docs-site/cli-reference.md)."
