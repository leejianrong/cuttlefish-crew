#!/bin/sh
# usage: run.sh <mode...>   starts a fresh claude in tmux with the spike hooks, waits for the prompt box
H=$(dirname "$0"); SOCK=${HS_SOCK:-/tmp/hs.sock}
tmux -S $SOCK kill-server 2>/dev/null; sleep 1
echo "$*" > $H/mode; : > $H/hook.log
tmux -S $SOCK new-session -d -s hs -x 110 -y 40 -c $H/proj "claude --model haiku --settings ${SETTINGS:-$H/settings.json} ${EXTRA:-}"
sleep 6
tmux -S $SOCK capture-pane -p -t hs | tail -15
