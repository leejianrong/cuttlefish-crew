#!/usr/bin/env bash
# Install cuttlefish serve as a systemd --user unit (ADR-0014, KAN-1709), so it
# auto-restarts if it dies instead of needing a human to notice and restart it
# by hand. One-liner:  ./scripts/install-systemd-service.sh
#
# A --user unit, not a system-wide one: no sudo needed, matches this repo's
# own "solo developer on their own machine" persona (project_paperclip_positioning).
# Does NOT start or enable the service -- review the installed unit file first
# (it has a commented-out spot for CUTTLEFISH_SERVE_PASSWORD/extra flags), then
# run the printed next-step commands yourself.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
template="$repo_root/deploy/systemd/cuttlefish-serve.service.template"
unit_dir="$HOME/.config/systemd/user"
unit_path="$unit_dir/cuttlefish-serve.service"

if ! command -v systemctl >/dev/null 2>&1; then
	echo "systemctl not found -- this script needs a systemd-based Linux (or WSL2 with systemd enabled)." >&2
	exit 1
fi

uv_bin="$(command -v uv || true)"
if [ -z "$uv_bin" ]; then
	echo "uv not found on PATH -- install it first (https://docs.astral.sh/uv/)." >&2
	exit 1
fi

mkdir -p "$unit_dir"
sed -e "s#@REPO_ROOT@#$repo_root#g" -e "s#@UV_BIN@#$uv_bin#g" "$template" >"$unit_path"
echo "Wrote $unit_path"

systemctl --user daemon-reload

linger="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo "unknown")"
if [ "$linger" != "yes" ]; then
	echo
	echo "Note: lingering is not enabled for $USER (currently: $linger)."
	echo "Without it, this --user service stops when your last login session ends."
	echo "Enable it with:  loginctl enable-linger $USER"
fi

echo
echo "Review $unit_path (uncomment/add flags or an EnvironmentFile if needed),"
echo "then start it:"
echo "  systemctl --user enable --now cuttlefish-serve"
echo "  systemctl --user status cuttlefish-serve"
echo "  journalctl --user -u cuttlefish-serve -f"
