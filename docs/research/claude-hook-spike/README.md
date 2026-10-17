# Claude Code permission hook spike (CUT-126)

Throwaway scripts behind "Check 5" in `../session-control-spike.md`. Not part of the package (ruff excludes this folder).

- `hook.py` logs every hook event it receives to `hook.log` next to it, and for `PermissionRequest` acts on the first
  word of a file called `mode`: `none`, `allow N`, `deny N`, `hang`, `fail`, `exit2` (N seconds of delay).
- `run.sh <mode>` starts `claude --model haiku --settings <settings.json> $EXTRA` in tmux (on the socket in `$HS_SOCK`)
  in a `proj/` folder here. Use `EXTRA="--permission-mode default"`: without it this install starts in auto mode and
  nothing ever prompts.
- `ask.sh <dir> <seconds>` sends a prompt that needs permission, samples the pane once a second, and prints the hook
  timeline.
- `settings.json` is generated: `{"hooks": {"<Event>": [{"hooks": [{"type": "command", "command": "python3 <path>/hook.py"}]}]}}`
  for `PermissionRequest`, `Notification` (matcher `permission_prompt`), `PostToolUse`, `PostToolUseFailure`,
  `UserPromptSubmit`, `Stop` and `PreToolUse`. A `timeout` (seconds) on a hook entry sets its limit.

Needs a signed-in `claude`. Each run spends a few cents on tiny prompts.
