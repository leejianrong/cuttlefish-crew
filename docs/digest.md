# The project digest

What a manager agent may see of a project (CUT-150, ADR-0046). One function, `cuttlefish.fleet.digest.build_digest`,
derives it from the project's journal and its pending requests. It is served at `GET /api/projects/{id}/digest`
(`?events=N`, default 10, at most 25; View tier) and is the output of the manager's MCP digest tool (CUT-231).

## Schema

| Field | Meaning |
|---|---|
| `project_id`, `name` | The project. |
| `status` | `working`, `waiting` (a request or plan approval is open), `stuck` (a role failed, or Autopilot is stuck or at its limit), `done`, `idle` (nothing has run). Derived from the same role statuses as the dashboard (`fleet/status.py`) and Autopilot's last phase. |
| `roles` | Role name to its derived status. |
| `autopilot`, `goal` | Autopilot's last phase and the goal text, or `null`. |
| `last_events` | The last N events as `{at, role, text}`, oldest first, one line each. |
| `blockers` | Up to 5 one-line blockers: open `blocked` requests and why Autopilot is stuck. |
| `pending_count`, `pending` | Every open request, and up to 25 of them as `{request_id, kind, role, title, expires_at}`. |
| `spend` | `{tokens, cost_usd}`. `cost_usd` is `null` when no backend reported one (kopicode and Codex report none); `null` is not zero. |

## What it never carries

No code, file contents, command output or secrets. This holds by what is picked, not by what is removed:

- A line comes from the **typed fields of an allowlisted event payload** (`_line`). A payload type not listed adds nothing.
  The fields never picked: a round's summary, edited paths (only a count), a tool call's detail, a failure's output tail,
  a consent's detail, a task's result or error text, a handover's body.
- The only free text is a request title, the goal, Autopilot's reason and a pull request URL. Each is flattened to one
  line, cut to 120 characters and scrubbed again of known secret values (the journal already redacted them at write time).
- Events, requests and blockers are capped, so a digest stays under 16 KiB (`MAX_BYTES`).

`tests/unit/test_digest.py` plants a secret and a source file in every payload that can hold text and checks neither
appears, and checks the size bound with 500 requests of 100 KB titles.

A request's title is the agent's own words (untrusted input to a manager: ADR-0046). Detail that a manager needs to
decide a request, such as the command and directory, comes from the structured request, not from the digest.
