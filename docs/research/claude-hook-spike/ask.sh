#!/bin/sh
# usage: ask.sh <dirname> <seconds to watch>   sends a prompt that needs permission, samples the pane each second
H=$(dirname "$0"); T="tmux -S ${HS_SOCK:-/tmp/hs.sock}"; N=$1; W=${2:-10}
: > $H/hook.log
START=$(date +%s.%N)
$T send-keys -t hs "Use the Bash tool to run exactly: mkdir $N" Enter
i=0; while [ $i -lt $W ]; do sleep 1; i=$((i+1))
  D=no; $T capture-pane -p -t hs | grep -q "Do you want to proceed" && D=YES
  E=no; [ -d $H/proj/$N ] && E=yes
  echo "+${i}s dialog_visible=$D dir_created=$E"
done
python3 - <<PY
import json
s=$START
for l in open("$H/hook.log"):
    d=json.loads(l); print("  hook +%.1fs %s %s" % (d["t"]-s, d["ev"], d.get("note","")))
PY
