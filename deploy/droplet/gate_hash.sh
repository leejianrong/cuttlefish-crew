#!/usr/bin/env bash
# Print the bcrypt hash of a password read from standard input, using the Caddy image the stack runs
# (CUT-153). The password never appears on a command line, so it cannot show up in `ps`.
#
#   printf '%s' "$password" | deploy/droplet/gate_hash.sh caddy:2.11.7@sha256:...
#
# Needs a running docker daemon.

set -euo pipefail
image="${1:?usage: gate_hash.sh CADDY_IMAGE  (password on stdin)}"
# `read` fails on a last line with no newline, though it has filled the variable.
IFS= read -r password || [[ -n "$password" ]]
[[ -n "$password" ]] || { echo "gate_hash: empty password" >&2; exit 1; }
# `caddy hash-password` asks for the password twice, from standard input when it is not a terminal.
printf '%s\n%s\n' "$password" "$password" | docker run --rm -i "$image" caddy hash-password 2>/dev/null |
    tail -n 1
