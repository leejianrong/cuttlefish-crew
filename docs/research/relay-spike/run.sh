#!/bin/sh
# Reproduce the CUT-117 spike. Needs: a running `cuttlefish serve` on loopback (its URL and token as arguments),
# Node with playwright-core installed next to driver.mjs, a Chromium path in CHROME, and a venv with the
# `noiseprotocol` package for check_vs_ref.py (NOISE_PY). Nothing here is part of the product.
set -eu
DAEMON=${1:?daemon url, e.g. http://127.0.0.1:8533}; TOKEN=${2:?x-cuttlefish-token}
HERE=$(cd "$(dirname "$0")" && pwd); PY=${PY:-python3}; OUT=${OUT:-$(mktemp -d)}
[ -n "${NOISE_PY:-}" ] && "$NOISE_PY" "$HERE/check_vs_ref.py"
"$PY" "$HERE/relay.py" 8541 "$OUT/relay-seen.json" > "$OUT/relay.log" 2>&1 & RELAY=$!
"$PY" -m http.server 8542 --bind 127.0.0.1 --directory "$HERE" > /dev/null 2>&1 & WEB=$!
trap 'kill $RELAY $WEB 2>/dev/null || true' EXIT; sleep 1.5
DS=$("$PY" -c "import os;print(os.urandom(32).hex())")
DP=$(cd "$HERE" && "$PY" -c "
import noise_ikpsk2 as n
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey as K
print(n.pub_bytes(K.from_private_bytes(bytes.fromhex('$DS'))).hex())")
for SC in good wrongpsk; do
  PSK=$("$PY" -c "import os;print(os.urandom(32).hex())"); USE=$PSK
  [ "$SC" = wrongpsk ] && USE=$("$PY" -c "import os;print(os.urandom(32).hex())")
  PAIR_PSK=$PSK DAEMON_STATIC=$DS "$PY" "$HERE/connector.py" "room-$SC" ws://127.0.0.1:8541 "$DAEMON" "$TOKEN" x > "$OUT/connector-$SC.log" 2>&1 & CP=$!
  sleep 1
  (cd "$HERE" && node driver.mjs "$SC" ws://127.0.0.1:8541 http://127.0.0.1:8542/page.html "room-$SC" "$DP" "$USE" x)
  kill $CP 2>/dev/null || true
done
sleep 2; cat "$OUT/relay-seen.json"; echo
