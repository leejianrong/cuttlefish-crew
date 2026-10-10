# Claude Code live permission prompts spike (V4-J, 2026-10-10)

Research only, no product code. The question: can cuttlefish-crew put live prompts, Stop and an
enforced command list on Claude Code by keeping one `claude` process open and answering its permission
requests, the way V4-M does for Codex? Short answer: yes, on everything we tried. The surface is Claude
Code's own (ADR-0003/0005): newline-delimited JSON on stdin and stdout, normalised into
`DelegationOutcome` on our side.

## Method and limits

- `claude` 2.1.296, `claude-haiku-4-5-20251001`, tiny tasks, about 2 cents each. Logged in with the
  operator's own login (no `ANTHROPIC_API_KEY` in the probe).
- A 40-line Python driver over the child's stdin and stdout, one scratch git repo. One run per scenario;
  model behaviour (does it ask?) varies, so "seen once" is not "always".
- Not tried: `--allowedTools` combined with the prompt tool, `--resume` together with stream-json input,
  a second turn on one open process, `updatedPermissions` in an answer, MCP tools, hooks, file reads
  outside the working directory, and a sandboxed `claude`.

## The invocation

```
claude -p --input-format stream-json --output-format stream-json --verbose \
  --permission-mode manual --permission-prompts host --permission-prompt-tool stdio
```

The prompt is one stdin line: `{"type":"user","message":{"role":"user","content":"<text>"}}`.
**`--permission-prompt-tool stdio` is what turns the protocol on.** Without it (`--permission-prompts host`
alone) nothing is ever sent: a command that needs approval is denied by the process itself, with a
`{"type":"system","subtype":"permission_denied"}` line and a tool result saying it "needs approval", and
the agent tells the user it is "pending your permission". That is the behaviour `claude -p` has today.

## Asking and answering

A server request on stdout (`type: "control_request"`, with a `request_id`), answered by a
`control_response` on stdin carrying the same id:

```
{"type":"control_request","request_id":"…","request":{"subtype":"can_use_tool","tool_name":"Bash",
  "input":{"command":"touch made.txt && echo ok","description":"…"},"description":"…",
  "permission_suggestions":[{"type":"addRules","rules":[{"toolName":"Bash","ruleContent":"…"}],
    "behavior":"allow","destination":"localSettings"}, {"type":"addDirectories",…}, {"type":"setMode",…}],
  "blocked_path":"/…/made.txt","tool_use_id":"toolu_…"}}

{"type":"control_response","response":{"subtype":"success","request_id":"…",
  "response":{"behavior":"allow","updatedInput":{…the input, unchanged…}}}}
{"type":"control_response","response":{"subtype":"success","request_id":"…",
  "response":{"behavior":"deny","message":"Denied by operator"}}}
```

| Need | Seen |
|---|---|
| Allow a command | yes; the command ran and the agent carried on. `updatedInput` must be sent (the original input is fine) |
| Deny a command | yes; the tool result is `is_error: true` with our `message`, the agent reported it and ended its turn normally |
| File edit | yes: `Write` sends the same request (`input.file_path`, `input.content`), no `Bash`-style description of a command. Allowed, the file appeared |
| Which command | `request.input.command` is the exact line, as one string (no `bash -lc` wrapper here) |
| `AskUserQuestion` | yes, as a `can_use_tool` request with `tool_name: "AskUserQuestion"`, `requires_user_interaction: true` and `input.questions: [{question, header, options:[{label, description}], multiSelect}]`. The answer is an `allow` whose `updatedInput` is the input plus `answers: {"<question text>": "<label>"}`; the model then saw "Your questions have been answered: … = Green" and used it |
| Stop | yes: a `control_request` `{"subtype":"interrupt"}` on stdin while a request was pending was acknowledged (`still_queued: []`), the turn ended at once (`result` with `stop_reason: "tool_use"`), and the pending command never ran |
| A slow answer | a 150 second wait for the answer was fine; the command ran afterwards and the turn finished. We did not find a timeout |
| Resume | `claude -p --resume <session_id>` in a new process kept context (it recalled a word from the first run). Not tried with stream-json input |
| Usage | the final `result` line carries `total_cost_usd`, `usage` (tokens), `session_id`, `stop_reason`, as `claude -p` already gives us |

What does **not** ask: a read-only command such as `ls` ran with no request even in `--permission-mode
manual`. Claude Code decides for itself what is read-only, so our list is not the only gate there: a
command it considers safe runs without reaching us. Anything that prompts reaches us, including a path
outside the working directory (`blocked_path` says which).

## Findings that matter for the design

1. **One long-lived child, one request at a time per tool call.** The agent waits on our answer; nothing
   else needs a timer, but the round time limit must interrupt (`control_request` interrupt) so a role
   cannot hang on a person who never answers.
2. **The same shape as kopicode and Codex.** Map `can_use_tool` for `Bash` onto
   `ConsentPolicy.decide("run_shell", "/bin/sh -c <line>")` and `AskingDecider`; `Write`/`Edit` onto the
   inside-the-root rule from `delegate/codex_app_server.py`; `AskUserQuestion` onto a `question` card
   (which makes a Claude Code question live, the way kopicode's `ask` is).
3. **`permission_suggestions` is how Always allow could map**, but cuttlefish keeps its own project-wide
   allowed commands (ADR-0028), so answer plain `allow` and keep our own list, as with Codex.
4. **An allowed command runs under Claude Code's own rules, not a sandbox of ours.** Unlike Codex's
   `untrusted` (every command asks, an accepted one runs outside the sandbox), here Claude Code asks only
   about what it does not already allow. So the never-allowed list is enforced for what asks, and
   `--disallowedTools` patterns (today's mapping) must stay as the backstop for what does not.
5. **Read-only role**: keep `--disallowedTools` for the edit tools; a request for one would be declined anyway.
6. The `ANTHROPIC_API_KEY`-only sandbox gap (Q37) is unchanged: this is a host path.

## Verdict for V4-K

Build it, on the shapes above. Constraints: the flag combination above is the entire contract and is
undocumented in `--help` beyond one line, so record `claude` 2.1.296 and fail with a clear message when a
handshake does not start; read-only commands never reach us; stream-json input with `--resume` and a
second turn on one process are untested (one process per round is the simple shape, resuming by
`session_id`, which `claude -p` already supports).
