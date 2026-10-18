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
#
# `make phone HTTPS=1` is the same check over Tailscale https (ADR-0041), which is what lets a phone
# pair: it starts the relay and the daemon on loopback, offers to run the two `tailscale serve`
# commands (asking first, with sudo if Tailscale wants root), and on exit offers to take them away.
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

https="${HTTPS:-0}"
relay_port=""
if [ "$https" = "1" ]; then
	ts_name="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin).get("CertDomains") or []; print(d[0] if d else "")' 2>/dev/null || true)"
	[ -n "$ts_name" ] || fail "HTTPS is not turned on for this tailnet: Tailscale admin console, DNS, HTTPS Certificates (it publishes this machine's name in public certificate logs)"
	port="$(free_port 127.0.0.1 "$port")" || fail "no free port from $port upwards on 127.0.0.1"
	relay_port="$(free_port 127.0.0.1 $((port + 1)))" || fail "no free relay port from $((port + 1)) upwards"
else
	port="$(free_port "$ts_ip" "$port")" || fail "no free port from $port upwards on $ts_ip"
fi
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

if [ "$https" = "1" ]; then
	cuttlefish_bin="$repo_root/.venv/bin/cuttlefish"
	[ -x "$cuttlefish_bin" ] || fail "no $cuttlefish_bin (run: uv sync)"
	env_run() {
		env HOME="$home" PATH="$home/bin:$PATH" CUTTLEFISH_SECRETS_KEY="$(cat "$home/secrets.key")" \
			CUTTLEFISH_SERVE_PASSWORD="$password" CUTTLEFISH_LLM_PROVIDER=replay \
			CUTTLEFISH_AGENT_BACKEND=claude-code "$@"
	}
	# `tailscale serve` changes this machine's Tailscale configuration, so a person says yes first.
	# Tailscale asks for root unless `sudo tailscale set --operator=$USER` was run once.
	ts() { tailscale "$@" 2>/dev/null || sudo tailscale "$@"; }
	served=0
	relay_pid=""
	cleanup() {
		trap - EXIT INT TERM
		[ -n "$relay_pid" ] && kill "$relay_pid" 2>/dev/null || true
		if [ "$served" = "1" ]; then
			printf '\nRemove the two tailscale serve entries now? [Y/n] '
			read -r answer || answer=n
			case "$answer" in
				[nN]*) echo "Left in place. Remove later: sudo tailscale serve reset (this clears all serve config)." ;;
				*) ts serve --https=443 off || true; ts serve --https=8443 off || true
				   echo "Removed. If one is still listed, run: sudo tailscale serve reset" ;;
			esac
		fi
	}
	trap cleanup EXIT INT TERM

	env_run "$cuttlefish_bin" relay --port "$relay_port" >"$home/relay.log" 2>&1 &
	relay_pid=$!
	# The daemon dials the relay as it starts, so wait until the relay is listening.
	for _ in $(seq 50); do
		python3 -c "import socket,sys; socket.create_connection(('127.0.0.1', int(sys.argv[1])), 0.2)" "$relay_port" 2>/dev/null && break
		sleep 0.2
	done

	existing="$(tailscale serve status 2>/dev/null || true)"
	cat <<MSG

  Tailscale https needs two forwards on this machine:

    tailscale serve --bg --https=443  http://127.0.0.1:$port         (the dashboard)
    tailscale serve --bg --https=8443 http://127.0.0.1:$relay_port   (the relay, as wss)

  This changes this machine's Tailscale serve configuration, and the name $ts_name goes into
  public certificate logs. Anyone on your tailnet can then reach the sign-in page.
MSG
	if echo "$existing" | grep -q "proxy"; then
		echo "  Existing serve config (these two ports would be replaced):"
		echo "$existing" | sed 's/^/    /'
	fi
	printf '\n  Run them now? [y/N] '
	read -r answer || answer=n
	case "$answer" in
		[yY]*)
			ts serve --bg --https=443 "http://127.0.0.1:$port" >/dev/null && ts serve --bg --https=8443 "http://127.0.0.1:$relay_port" >/dev/null \
				&& served=1 || fail "tailscale serve failed (see above)" ;;
		*) echo "  Not run. Run the two commands yourself (with sudo) before opening the phone." ;;
	esac

	cat <<MSG

  On the phone (same tailnet, Tailscale turned on), open:

    https://$ts_name/

  Password:  $password
  Scratch:   $home   (make phone HTTPS=1 RESET=1 starts over)

  Try: Account menu > Devices > Make a pairing link, open it on the phone, check both screens show
  the same six digits, then Confirm here. Then try Remove here, and watch the phone.
  On this PC use the same https address (the sign-in screen asks for a Base URL: type it in).

MSG
	env_run "$cuttlefish_bin" serve --tailscale-https --port "$port" --relay "ws://127.0.0.1:$relay_port" --dashboard-dir "$dist"
	exit 0
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
