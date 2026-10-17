#!/usr/bin/env bash
# `make phone`: try the dashboard from a phone over Tailscale (ADR-0013), with no model credit
# and nothing from your own ~/.cuttlefish. It checks Tailscale and tmux first and says what to
# fix, then runs `cuttlefish serve --tailscale` against a scratch HOME holding one sample
# project whose agent is a fake `claude` in tmux, so the Terminal tab, the phone layout, the
# sliding session and "Sign in again" can all be tried by hand.
#
# Scratch state lives in $PHONE_HOME (default /tmp/cuttlefish-phone) and is kept between runs,
# so the password and the paired browser's session survive a restart of this script. `make
# phone RESET=1` deletes it first. PORT picks the port (default 8431, never 8420).
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
home="${PHONE_HOME:-/tmp/cuttlefish-phone}"
port="${PORT:-8431}"
# The first free port from $port up, so the URL printed below is the one that is really served.
free_port() {
	python3 - "$1" "$2" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
for p in range(port, port + 20):
    with socket.socket() as s:
        try:
            s.bind((host, p))
        except OSError:
            continue
        print(p)
        break
else:
    sys.exit(1)
PY
}
fail() { echo "phone: $*" >&2; exit 1; }

command -v tmux >/dev/null || fail "tmux is not installed (sudo apt install tmux)"
command -v tailscale >/dev/null || fail "the tailscale CLI is not on PATH (https://tailscale.com/download)"
ts_ip="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
[ -n "$ts_ip" ] || fail "tailscale has no address: is tailscaled running and this machine signed in? (sudo tailscale up)"
ts_name="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
peers="$(tailscale status 2>/dev/null | wc -l)"
if [ "$peers" -le 1 ]; then
	echo "phone: warning: no other device is on this tailnet yet. Install Tailscale on the phone and sign in" >&2
	echo "       with the same account (leejianrong2@gmail.com for this machine), then run this again." >&2
fi

port="$(free_port "$ts_ip" "$port")" || fail "no free port from $port upwards on $ts_ip"
[ "${RESET:-0}" = "1" ] && { tmux -S "$home/.cuttlefish/tmux.sock" kill-server 2>/dev/null || true; rm -rf "$home"; }
mkdir -p "$home/bin" "$home/sample" "$home/.cuttlefish"
chmod 700 "$home"

# A fake agent: prints a banner, then echoes what is typed, so typing on a phone keyboard shows.
cat > "$home/bin/claude" <<'FAKE'
#!/bin/sh
echo "fake claude (make phone): nothing here calls a model."
echo "Type a line and press Enter; Ctrl-C ends it. Try the key row, arrows and the keyboard."
while IFS= read -r line; do echo "you typed: $line"; done
FAKE
chmod +x "$home/bin/claude"

# Made once, kept in the scratch dir (mode 600), never printed.
[ -f "$home/secrets.key" ] || ( umask 077; cd "$repo_root" && uv run python -c 'from cuttlefish.secrets import generate_key; print(generate_key())' > "$home/secrets.key" )
# The password is scratch and is shown, because typing it on the phone is the point.
[ -f "$home/password" ] || ( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(9))' > "$home/password" )
password="${PHONE_PASSWORD:-$(cat "$home/password")}"

if [ ! -d "$repo_root/frontend/node_modules" ]; then ( cd "$repo_root/frontend" && npm install ); fi
dist="$repo_root/frontend/dist"
if [ ! -f "$dist/index.html" ] || [ -n "$(find "$repo_root/frontend/src" -newer "$dist/index.html" -print -quit)" ]; then
	echo "Building the dashboard…"
	( cd "$repo_root/frontend" && npm run build )
fi

cd "$repo_root"
uv_cache="$(uv cache dir)"
run() {
	env HOME="$home" PATH="$home/bin:$PATH" UV_CACHE_DIR="$uv_cache" \
		CUTTLEFISH_SECRETS_KEY="$(cat "$home/secrets.key")" CUTTLEFISH_SERVE_PASSWORD="$password" \
		CUTTLEFISH_LLM_PROVIDER=replay CUTTLEFISH_AGENT_BACKEND=claude-code \
		uv run cuttlefish "$@"
}

if ! run projects list 2>/dev/null | grep -q "sample"; then
	run projects add --name sample --root "$home/sample" --backend claude-code >/dev/null
fi

cat <<MSG

  On the phone (signed in to the same tailnet, Tailscale turned on), open:

    http://$ts_ip:$port/
${ts_name:+    http://$ts_name:$port/  (the same, by name)
}
  Password:  $password
  Scratch:   $home   (make phone RESET=1 starts over)

  Try: open "sample" > Terminal > Start the agent session, type on the phone keyboard and the key
  row, check the tab title, then stop this script (Ctrl-C) and reload the phone to see "Sign in again".
  The terminal is typed access to the agent: this password is the only guard, keep it off shared chats.

MSG
exec_args=(serve --tailscale --port "$port" --dashboard-dir "$dist")
run "${exec_args[@]}"
