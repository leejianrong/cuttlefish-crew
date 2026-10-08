# ADR-0028: "Needs you" requests are journaled records, and kopicode's consent request waits for a person

- Status: Accepted
- Date: 2026-10-06
- Deciders: Jian (docs/SLICES.md V4-H)

## Context

`ConsentPolicy.decide` answers every kopicode `consent.request` at once from rules and never
asks a person (ADR-0021). Ask first therefore refuses every command (ADR-0025), and a command
off the Standard list is refused too. The mockup (`docs/design/ui-redesign/2-project-needs-you.dc.html`)
wants those to become cards a person answers while the agent is paused.

What the code and kopicode actually give us (checked, not assumed):

- **The wait fits the reader loop already.** `ServeChild._start_consent` runs each request in
  its own task and `_answer_consent` already awaits an async `Decider` under a deadline
  (`DEFAULT_CONSENT_DEADLINE`, 30s). The reader loop is never blocked by a pending request.
- **kopicode's own timeout is the real limit.** A `consent.request` unanswered after 60s is
  denied by kopicode, and a late reply is dropped (serve protocol doc). The 60s is fixed in
  **v0.2.0**; `--consent-timeout <1s..24h>` was merged in kopicode#169 today and is in no
  release yet (v0.2.0's `serve.go` has no such flag). So a person has at most ~45s on a
  released kopicode, and as long as we like once a release carries the flag.
- **`ask` has no live wire.** `--ask-policy-file` is process-level: its note answers every
  `ask` call of every session, and unset means the fixed "no human is present" refusal. The
  protocol has four client-to-server methods and one server-to-client request
  (`consent.request`); there is no `ask.request`. Whether an `ask` call shows up in the
  `session.event` stream is **not verified** (`run-print-protocol.md` names no ask event).
- **`PATCH /api/projects/{id}/allow` does not validate.** `_allow_from_body` only copies tuples
  and `ProjectStore.update_allow` stores them. A bad entry is caught later, at delegation time,
  as `ConsentPolicyError` ("unusable shell allowlist"). So "the same validation as a
  hand-typed command" does not exist at the API today, and this slice adds it.
- **The journal is written after the fact.** `ConsentDecided` rows are appended by the
  workflow after the round (`workflow.py`, `team.py`). A request that is pending now cannot be
  derived from them; it has to be journaled live, the way `TeamResumed` is written straight to
  the store by the daemon.
- `Runtime` is a `contextvars` object read inside the satay task, so anything the task needs
  from the daemon can ride it without becoming a task argument (ADR-0006, CLAUDE.md).

## Decision

### 1. A request is two journal events; state is derived from them

Two new payloads in `episodic/events.py`, appended to the team's journal (`task_id` = team id)
by the daemon-side broker, redacted at write time like every event:

- `RequestRaised`: `request_id` (uuid hex, new per raise), `kind` (`permission` | `question` |
  `blocked`), `role`, `backend`, `title`, `detail` (the full command line, at most 1 KiB, or the
  question text), `why` (kopicode's `reason`, plus our rule in plain words: "not on the
  command list"), `suggested_rule` (words, or `None`), `answers` (the allowed answers for this
  request), `expires_at` (UTC), `lands` (`now` | `end_of_turn` | `next_round`, so a card says
  when an answer takes effect).
- `RequestResolved`: `request_id`, `resolution` (`allowed_once` | `allowed_always` | `denied` |
  `expired` | `cancelled` | `abandoned`), `by` (`person` | `timeout` | `system`), `rule`
  (the words added, for `allowed_always`).

Lifecycle: `pending` is a raise with no resolve; every other state is the resolve's
`resolution`. Exactly one resolve per request, enforced by the broker.

**No parallel transcript (ADR-0004).** No requests table. The broker keeps an in-memory index of
the pending ones, holding the record `EpisodicStore.append` returned (already redacted, so the
API never serves an unredacted copy). It is a cache of the journal and starts empty, which is
correct because nothing can be pending after a restart (decision 2). History comes from folding
the project's last team's events.

**Owner:** the team (and so the project and the role). The role comes from `RequestContext.role_for(task_text)`: `team.py` notes which role it
dispatched each text to, and the delegation task looks its own text up (two roles with one
text are shown without a role). A contextvar set around the call does not reach satay's own
tasks, and a `role` task argument would change every team call's recorded arguments.

**Kinds.** V4-H raises only `permission`, and only for kopicode. `question` and `blocked` are in
the model so V4-I and the Claude Code and Codex slices add producers without a second ADR.
Commands that are never allowed, `write_outside_root`, an unsafe flag or a path escape stay
instant denials, journaled as `ConsentDecided` as today; nobody is asked, because no answer
could change them.

### 2. kopicode's `consent.request` is held open by an async decider

`KopicodeBackend` builds a decider that calls `ConsentPolicy.decide` first. A decision of
`allow`, or a denial that is never overridable, is answered at once as today. A denial that a
person could override is turned into a request. **Askable denials:** `no_shell_allowed`,
`no_matching_allow_entry`, `not_a_plain_word_list`. **Not askable:** every `never_allowed:*`,
`write_outside_root_never`, `unsafe_flag`, `argument_escapes_root`, `not_a_sh_c_command`,
`command_length`, `unknown_kind`, `malformed_request`.

When asking applies: only for access `standard` and `ask-first`, and only when the runtime
carries a broker (the daemon). `auto` never asks; `read-only` never asks (a reviewer must not
be talked into shell access); `cuttlefish run` has no inbox, so it keeps today's instant denial
and says so. The backend learns the mode for `ask-first` as `mode="ask-first"` (it receives it
only for `auto` and `read-only` today); `mode` is not a task argument, so recorded arguments
and replay are unchanged.

The wait:

- `await asyncio.wait_for(future, expires_in)` inside the per-request task `_answer_consent`
  already makes. The reader loop, the write lock and the pool lock are not held. Nothing
  blocks; answering resolves the future on the same loop (the broker lives in the daemon
  process, and `ServePool` already pins a child to its loop).
- **Window.** The daemon asks for `CUTTLEFISH_REQUEST_WINDOW` (default 10 minutes). The
  effective window is the smaller of that and what the binary supports: `serve` is spawned with
  `--consent-timeout <window + 30s>` when `kopicode serve --help` lists the flag (probed once
  per binary, cached), and the window is **45s** when it does not (v0.2.0's fixed 60s minus
  margin). `expires_at` is the effective window, so a card never promises time it does not have.
- **Expiry denies.** Timeout resolves `expired` / `timeout`, replies `deny`, and the agent is
  told no, as with any refusal.
- **Stop or cancel.** `cancel_session` (a delegation cancelled in-process) already cancels the
  per-request tasks and replies `deny`; the decider's `CancelledError` path resolves each
  `cancelled` / `system`. But the operator's Stop goes through satay's `cancel_run`, which
  only lands when the round ends, and a round held open for a person does not end (found in
  H2, not in the first draft of this ADR). So `FleetDaemon.stop` also resolves the team's
  pending requests `cancelled`, which tells the waiting agent no and lets the round, and then
  the cancel, finish.
- **The child exits** while a request waits: the reader's exit path cancels the waits, which
  resolve `abandoned` / `system` ("the agent process ended").
- **The daemon restarts.** The held request cannot survive: the child dies with the daemon's
  stdin, and kopicode denies and gives up. On startup, before `resume_pending`, the daemon
  appends `RequestResolved(abandoned, system)` for every unresolved raise in each project's last
  team. What satay then does with an interrupted side-effecting delegation is unchanged by this
  ADR; a resumed run raises a **new** request with a new id. Pending is never shown for a
  request with no live future, even before that sweep runs.
- **Replay and secrets.** Nothing about a request is a task argument or return value, and no
  secret value is involved: the broker is reached through `Runtime`, the command text is
  redacted on journal write, and the API serves only the journaled form.
- Asked decisions are journaled as the raised/resolved pair only, not also as `ConsentDecided`,
  so the activity feed has one row per decision.

### 3. Answers: Allow once, Always allow, Deny

- **Allow once**: reply `allow` for this request, nothing stored.
- **Deny**: reply `deny`.
- **Always allow** takes a `rule` (default `suggested_rule`: the leading words up to the first
  flag or path-like word, at most three: `docker compose up`, `uv run pytest`). The server
  validates it, and a failure is a 422 with the reason; the request stays pending:
  1. It is a word-prefix of this request's own line (a person cannot grant something the agent
     did not ask for), and the line was a plain word list (otherwise the card offers only Allow
     once and Deny, and says why).
  2. It passes the same check as a hand-typed entry: `ConsentPolicy`'s `_pattern_from_entry`
     (plain words, `never_allowed_reason`), extracted as one public `validate_allow_entry`.
  3. It is not a single launcher or interpreter word (`sh`, `bash`, `env`, `xargs`, `python`,
     `node`, ...): "too broad, it would allow any script" (ADR-0021's `["python"]` warning,
     made a check).
  The never-allowed list therefore still wins after any answer, because the rule goes through
  `ConsentPolicy`, whose `decide` runs `never_allowed_reason` before any pattern.
- **Where it goes and when it applies.** Two places, both stated on the button:
  *this team now*: the broker keeps the team's granted rules and the decider builds
  `ConsentPolicy(allow + grants)` per decision, so the agent's next matching command is not
  asked again; and *future starts*: `ProjectStore.add_allow` appends it to `Project.allow`
  (idempotent), which `_build_role_inputs` already hands to every non-read-only role, so it
  shows on the Permissions tab as a command chip the operator can remove. This is project-wide,
  not per role. ADR-0027's "settings apply at team start" still holds for everything else; the
  live grant is the one deliberate exception, because an Always that did not apply to the
  running team would be a lie. After a daemon restart a resumed team rebuilds from its
  persisted roles, so the broker re-seeds that team's grants from its own journal
  (`RequestResolved` rows with a `rule`), not from `Project.allow`.
- The same `validate_allow_entry` is added to `PATCH /allow` (400 with the reason), so both
  routes into `Project.allow` share one rule.

### 4. Questions from the agent

**Not live in V4-H.** kopicode has no `ask` request on the wire (see Context): its `ask` calls
get the process-level note or the fixed refusal, and that cannot be changed per session or
answered later. Wrapping it would mean inventing a protocol, which CLAUDE.md and ADR-0003 rule
out. Checked in H2: kopicode's engine events include `ask_requested` and `ask_answered`, and serve
tees every non-delta journal event, so an `ask` call appears in `session.event`. cuttlefish does
not record them yet; H4 shows a kopicode `ask` in the activity log as "asked a question nobody
could answer".

Recommendation: file an upstream kopicode issue for an `ask.request` server-to-client request
shaped like `consent.request` (id, session, question, context; reply `{text}`; same bounded
timeout). That is kopicode's decision. `question` stays a valid kind, and a live kopicode path
is a later slice once that ships.

### 5. Claude Code and Codex

V4-H does not touch them. They cannot pause, so there is no live card and none is drawn. What
happens meanwhile is what happens today: the round's outcome (refused or failed) and the
backend's reported denials land in the activity log. V4-I adds `blocked` cards with "Delivered
when this turn ends" or "Takes effect next round"; V4-J and V4-L are spikes. The Needs-you tab
carries one line for a project with such roles: those agents cannot pause, and their refusals
appear in Activity.

### 6. API

All under the existing token or session; answering grants shell execution, the same bar as
`start`/`stop`. No new auth.

- `GET /api/requests`: every pending request across the fleet (`project`, `project_name`,
  plus the record and `expires_in_s`). Cheap: it reads the in-memory index, not journals.
- `GET /api/projects/{id}/requests`: pending first, then the last 50 resolved (folded from the
  journal).
- `POST /api/projects/{id}/requests/{rid}/answer`, body `{"answer": "allow_once" |
  "allow_always" | "deny", "rule": [...]?}`.
  - Pending: resolves, returns the resolution (200).
  - Already resolved with the same answer: 200, `already: true`. With a different one, or
    after expiry or cancel: 409 with the existing resolution.
  - Unknown id for that project: 404. An `abandoned` one (daemon restart, stopped team): 409
    `abandoned`.
  - Invalid `rule` or answer not in the request's `answers`: 422 with the reason.
- The answer path is in-process and touches no satay control API, so the `asyncio.to_thread`
  rule is not in play. It is a plain coroutine on the daemon's loop.
- **Polling, no SSE.** The dashboard already refreshes every 2.5s; windows are 45s to 10
  minutes, and a card shows a client-side countdown from `expires_in_s`. SSE would add a second
  channel, its auth and reconnect story, for a latency nobody can feel at this scale. Revisit
  if a window under 10s ever exists.

### 7. Dashboard

- Project page: a **Needs you · N** tab (coral `attention`, M3 roles only) following screen 2:
  permission cards with the command in mono, why, Allow once, Always allow `<editable rule>`,
  Deny, and "Denies on its own in m:ss". A resolved card collapses to a one-line activity row.
- Rail: a **Needs you** destination (the mockup called it Fleet) with an `attention` badge
  counting `GET /api/requests`; it lists every project's pending requests, grouped by project.
  It is added to `nav.ts` only in the PR that ships its screen, as that file says.
- **Ask first's words change in the PR that changes the behaviour** (H3 for the engine, H4 for
  the copy, shipped together with the screen so nothing says "asks" before it can): "Ask first
  does not ask yet" goes from AddProject, PermissionsTab, `GET /api/permissions` notes,
  known-gaps.md, ADR-0025's Consequences (an "Amended by ADR-0028" note, the text left in
  place), the design README and the docs site. New text: kopicode pauses and asks; Claude Code
  and Codex cannot pause, so their roles still refuse (and Codex cannot edit).
- Standard also asks (decision 2), so its card copy changes: "A command off the list pauses
  that agent until you answer." The Permissions tab says an unanswered request denies after the
  window.
- Per-role refusal counts on role cards (ux-review-v4g "Later") are out of scope here.

### 8. Tests

- **Unit:** the raise/resolve fold and state machine (exactly one resolve; late and double
  answers); `validate_allow_entry` (plain words, never-allowed, too broad, not a prefix of the
  line); the askable/not-askable table for every deny rule in `ConsentPolicy`, by mode; window
  selection with and without the flag; the broker's grants feeding `ConsentPolicy`;
  redaction (a secret in a command line never leaves the API).
- **Integration against a stub `serve` child** (`tests/unit/delegate/fake_kopicode_serve.py`,
  extended to hold a request): request held and allowed; denied; expires and denies; allow-always makes the
  next matching command pass without a second request; stop/cancel resolves `cancelled`;
  child killed mid-wait resolves `abandoned`; the answer after expiry is 409; a role context
  reaches the request; concurrent requests from two roles.
- **Daemon:** crash with a pending request, restart, the sweep resolves it `abandoned` and the
  resumed run does not show it pending.
- **HTTP:** list (project and fleet), answer, idempotency, 404/409/422, auth on all three.
- **Frontend:** Vitest for the countdown and the rule editor; the badge count; Ask first copy.
- **Real kopicode:** `requires_kopicode` covers what needs no model: the binary's `--help`
  lists `--consent-timeout` (the probe), and a `serve` child starts with it. Making the real gate
  ask needs a model to call `run_shell`, so that test is `requires_live_credential`, beside
  `test_kopicode_serve_consent_live.py`. It needs a kopicode release with the flag; until then
  the v0.2.0 path is covered by the stub only.
- **Cannot be tested without a live model:** that a real model keeps working after a deny, an
  expiry or an Always, and what it does while paused for minutes; whether kopicode surfaces an
  `ask` call in its events. Those are checked by hand in the demo, and recorded in
  known-gaps.md if they disappoint.

### 9. Build order (one small PR each)

1. **H1 Model and validation.** `RequestRaised`/`RequestResolved`, the broker (in-memory
   index, one-resolve rule, grants), `validate_allow_entry`, the askable table in
   `ConsentPolicy`, and `PATCH /allow` validation. No behaviour change for a running team.
2. **H2 kopicode wiring.** `Runtime.requests`, role attribution by dispatched task text (a contextvar does not reach `satay.gather`'s tasks, and a task argument would change every recorded call), the async decider, window probe
   and `--consent-timeout`, cancel/exit/restart handling, the startup sweep, the `ask` event
   check. Behaviour is switched on for ask-first and standard only when a broker is present, and the daemon only attaches one when `CUTTLEFISH_NEEDS_YOU=1` (a team that stops for a person nobody can yet answer would be a regression); H4 removes the switch.
   Stub-child integration tests. No UI yet, so the daemon answers by an internal call only.
3. **H3 API.** The three endpoints, `ProjectStore.add_allow`, live grants, re-seeding on
   resume. The Ask-first wording (`GET /api/permissions`, known-gaps.md, the dashboard) does
   **not** change here: asking is still behind `CUTTLEFISH_NEEDS_YOU` until H4, and saying
   "asks" before it is on by default would be the false claim CLAUDE.md forbids. It moves to H4 with
   the switch.
4. **H4 Dashboard.** Needs-you tab, rail destination and badge, the copy changes in
   AddProject and PermissionsTab, design README, docs site.
5. **H5 Real kopicode and docs.** The `requires_kopicode` test once a release carries the flag;
   README and docs-site pass; the KAN-1883 card gets the two new tools (list and answer
   pending requests) for the MCP follow-up.

## Consequences

- A role on kopicode in Standard or Ask first now **stops** at an off-list command for up to
  the window (45s on kopicode 0.2.0, 10 minutes after a release with the flag). An unattended
  team spends that time idle for each such command and then continues on a denial. Auto does
  not stop and read-only never asks.
- Always allow edits `Project.allow` without the operator opening the Permissions tab. The
  tab's draft can overwrite it with a stale list on Save; it refetches while open, and the
  window is small, but it is a real race.
- A pending request exists only while its process does. Nothing is resumed or re-asked after a
  restart, only recorded as abandoned.
- The journal gains two event types and nothing else changes shape, so older readers see them
  as `UnknownPayload`.
- kopicode questions are not answerable until kopicode adds a wire for them; the card kind
  exists but nothing raises it yet.
- A stricter way to bound a long wait (a per-project window setting) is not built; the
  environment variable covers the daemon as a whole.

## Decided questions

1. Standard asks as well as Ask first.
2. The default window is 10 minutes (`CUTTLEFISH_REQUEST_WINDOW`).
3. Always allow is project-wide.
4. The upstream asks are filed on kopicode: #173 (`ask.request`), #174 (a release with
   `--consent-timeout`), #175 (version and capability list), #176 (read-only session option),
   #177 (structured command field), #178 (per-session consent timeout).

## Update (V4-H5, 2026-10-06): what the real kopicode showed

The decisions above stand. Three of its premises about kopicode have moved, and one claim was
checked against kopicode's source rather than a live run.

- **The flag is released.** kopicode **v0.3.0** (released the same day) carries
  `--consent-timeout` (#169), so "no release has it" no longer holds: the 10-minute window
  applies on v0.3.0 and later, and the 45-second fallback applies only to v0.2.0 and older. CI
  builds kopicode from `main` (`ci.yml` clones it unpinned, not a release), so the flag is
  there; nothing was pinned or bumped. v0.3.0 also lists the flag in `kopicode version --json`
  (`features: consent_timeout.flag`, #175), and a real-binary test checks that cuttlefish's
  help-text probe agrees with it. Moving the probe to that list is possible and not done.
- **`ask` has a wire on kopicode `main`, not in v0.3.0.** `ask.request` (#173, ADR-0020, merged
  after v0.3.0, with `ask_mode: "remote"` on `session.start`) puts the model's `ask` to the client
  and takes `{text}` back, with the same bounded timeout. Decision 4 ("not live") is still true
  for every released kopicode and for what cuttlefish does today; a live question card is a
  later slice once kopicode releases it. v0.3.0 also adds `consent.request` `command`/`argv`
  (#177) and a per-session `consent_timeout` (#178); cuttlefish uses neither yet.
- **What an `ask` call looks like in `session.event`** (read from kopicode's `internal/engine/
  event.go`, not seen live): `tool_call_parsed` with `tool: "ask"` and `detail` the call's raw
  arguments, `{"question":...,"context":...}`, whitespace-collapsed and cut at 120 characters
  with `…`; then `ask_requested` and `ask_answered` (`source: "policy"`, `reason: "refused"`
  when nobody was present). So the dashboard's JSON parse of `detail` is right for a short
  question and wrong for a long one; the fix (#79) reads the question out of the cut text.
- **Checked live (same day, kopicode v0.3.0, a real model):** a held request survives past
  kopicode's default 60 seconds under `--consent-timeout`: one answered after 65 seconds was
  honoured. A real model's off-list `run_shell` was held, one command allowed once and one
  denied through the broker, and both reached kopicode's own tool-call statuses (`ok` and
  `denied`); the model carried on to the second command after the first answer
  (`test_kopicode_serve_needs_you_live.py`, both tests passed). Still not checked: what a model
  does after an expiry or an Always, and the `ask` event shape, which is read from source only.
- **One check for every route into the command list** (found by the first-time-operator test,
  KAN-1897): the "too broad" launcher check (`sh`, `python` alone) moved from the Always allow
  path into `validate_allow_entry`, so `PATCH /allow` and registration refuse it too. Decision 3
  item 3 therefore holds on both routes. Entries already stored are not re-checked.

### Update: kopicode v0.4.0 is released and CI is pinned to it

- `ask.request` (ADR-0020 in kopicode) and `ask_mode: "remote"` shipped in v0.4.0, so the wire the
  `ask` gap waited for now exists in a released kopicode. cuttlefish does not use it yet.
- The `tool_result` events in the serve stream still carry only the tool, exit code and size, so the
  stuck-agent detector (ADR-0029) keeps reading the session record.
- The session record and the stop reasons are unchanged apart from additive fields.
- `ci.yml` no longer clones kopicode `main`; it builds the `v0.4.0` tag, so a kopicode change cannot
  break CI unannounced. Bump the tag on purpose when a slice needs a newer kopicode.
