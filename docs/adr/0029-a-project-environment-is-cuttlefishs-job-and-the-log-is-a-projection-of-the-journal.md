# ADR-0029: A project's environment is cuttlefish's job, and the log is a projection of the journal

- Status: Accepted
- Date: 2026-10-07
- Deciders: Jian (docs/SLICES.md V5)

## Context

A real two-role team (builder and reviewer, kopicode) was started from the dashboard against a
Python project with its own `.venv`. Both roles failed with `stop=max_turns` inside 90 seconds.
Nothing in the terminal said why. What we found by digging (checked, not assumed):

- **The agents ran the wrong Python.** kopicode's own session logs under
  `<root>/.kopicode/sessions/` hold the real command output: `No module named 'numpy'`,
  `No module named pytest`, `No module named pip`. `make demo` runs `uv run cuttlefish serve`,
  and `uv run` puts cuttlefish's own `.venv/bin` first on `PATH`. The project's `.venv`
  (which has numpy and scikit-learn) was never used.
- **cuttlefish hands its whole environment to the agent.** `delegate/subprocess_env.py:merge_env`
  returns `{**os.environ, **env}`. `cli.py` calls `load_dotenv()`, so everything in the repo's
  `.env` is in `os.environ` too. That includes `CUTTLEFISH_SECRETS_KEY`, `E2B_API_KEY` and
  `ANTHROPIC_API_KEY`. kopicode's shell removes only its own `OPENROUTER_API_KEY`
  (`internal/tools/shell.go:childEnv`). Read from the code; not yet tested end to end.
- **kopicode is not at fault.** Its shell tool inherits the environment it is given
  (`childEnv`), as Claude Code and Codex do. kopicode's `verify` package finds the project's
  test command by reading files and never runs anything (`internal/verify/verify.go`). Neither
  harness picks a venv. A venv is chosen by whoever launches the harness, or by instructions
  telling the agent to run `uv run pytest`.
- **Other ways the same bug will show up:** on this machine (WSL) the daemon's `PATH` carries
  `/mnt/c/...` entries, including Windows Python and Node. `nvm` is not loaded in a
  non-interactive shell. Go, Rust, Java and Ruby each have their own toolchain and cache
  locations. Some tests need Docker or system libraries nothing can install per project.
- **The log told us nothing.** `cuttlefish.fleet.daemon` logged no failed start, the only
  other logger was the consent one, `DelegationFailed` carried `stop=max_turns` and no cause,
  and the dashboard showed a generic "Couldn't start that team" while the server's 409 response
  held the real reason.

## Decision

### 1. cuttlefish owns the project's environment; backends inherit it

Detecting what a project needs, preparing it, and building the environment an agent runs in
are cuttlefish's job, for every backend. A backend (kopicode, Claude Code, Codex) only ever
receives an environment and runs in it. This is backend-agnostic on purpose: the same leak and
the same wrong `PATH` would hit the Claude Code and Codex backends.

It uses mechanisms that already exist: the `envs` a backend is spawned with
(`_credential_envs`, pooled per environment in `ServePool`) and the task text a role is given.
**No new protocol for any backend** (ADR-0003, ADR-0005). kopicode needs nothing for the first
version. Later asks, if they prove worth it: a `verify_command` parameter on `session.start`,
and command output in serve events.

What stays with kopicode: faithfully inheriting the environment, discovering the verify command
from the tree, and its own shell gate. cuttlefish does not reimplement them.

### 2. Four layers, in the order they are built

1. **Scrub.** The child environment never contains cuttlefish's own venv: `VIRTUAL_ENV` and
   any `PATH` entry under the daemon's own `sys.prefix` are removed. This is the interim fix.
2. **Detect.** A read-only function from a project root to an `EnvironmentSpec`: ecosystems
   found from marker files (`uv.lock`, `pyproject.toml`, `requirements*.txt`, `package.json`
   with a lockfile choosing npm, pnpm, yarn or bun, `go.mod`, `Cargo.toml`, ...) and version
   hints (`.python-version`, `.nvmrc`, `.node-version`, `.tool-versions`, `mise.toml`).
   **Detection never executes anything**, the same rule as kopicode's `verify.Discover`.
3. **Prepare.** cuttlefish runs the install step itself, before the team starts and outside
   any agent's turn budget: `uv sync` (or `uv venv` plus `uv pip install -r`), `npm ci` and its
   pnpm, yarn and bun equivalents. Staleness is the hash of the lockfile and manifest, recorded
   in the project's `.cuttlefish/env.json`. It is journaled as its own events with the command,
   exit code, duration and a redacted output tail. Install steps run arbitrary project code
   (`postinstall`), so a project's **first** prepare needs one confirmation from a person;
   after that it runs automatically when stale. The never-allowed list holds throughout.
4. **Activate and tell.** The child environment is built from an **allowlist**: `PATH`,
   `HOME`, `LANG`, `TERM`, proxy variables and a short fixed set, plus the explicit credentials
   a backend declares (`CREDENTIAL_ENV_VARS`, ADR-0006). On top of that goes the project overlay:
   `VIRTUAL_ENV` and `<root>/.venv/bin` first on `PATH` for Python; `node_modules/.bin` for
   Node. The role's brief gets a short environment note ("Python: `.venv/bin/python`; run tests
   with `uv run pytest`; dependencies are installed; do not install globally").

`/mnt/c/...` entries are dropped from the child `PATH` unless a project opts in (assumed, Q61).

### 3. An agent stuck on its environment stops early and asks

A detector over the journal's tool results: N consecutive shell failures that match environment
signatures (`No module named`, `command not found`, `ENOENT`, `cannot find module`) end the
round and raise a "Needs you" request (ADR-0028) of kind `blocked` naming the evidence, instead
of running to `max_turns`. The signature list is data, not code, and N defaults to 5.

### 4. The log is a projection of the journal, plus operational events

**No parallel transcript (ADR-0004).** The log is not a second record of what agents did. One
hook in `EpisodicStore.append`, the single choke point every event already passes through after
redaction, emits a log line for lifecycle events: task submitted, delegation started, completed
or failed (with its stop reason, failure kind, tokens and role), request raised and resolved,
environment steps. `ToolCallRecorded` and `ConsentDecided` are DEBUG. Because the line is built
from the redacted event, the log is redacted at write time for free.

Events that are not in the journal are logged directly by the owning module, each with the
project id, team id and role carried in a `contextvars` object (the way `cuttlefish.runtime`
is): daemon start and stop, backend resolution with the binary path and version, resolved
environment spec, every HTTP 4xx and 5xx with its detail, and failed starts.

`DelegationFailed` gains `failure_kind` and `record` (the backend's own session directory,
from `session.start`'s `record` field), so a failure points at its evidence and nobody has to
find it by hand. A redacted tail of the failing command's output (`detail`) needs the tool
output, which cuttlefish does not read today; it arrives with the stuck-agent detector
(decision 3), which has to read it anyway.

The file is `~/.cuttlefish/logs/cuttlefish.log`, rotating, always on, level from
`CUTTLEFISH_LOG_LEVEL` (default INFO), every line carrying the project id. `make demo LOG=`
still copies the output to another file. A `cuttlefish doctor` command reports binaries and
versions, which credentials are set (names only, never values), the detected environment and
whether it is in sync, and `PATH` leaks. The dashboard shows the server's real failure reason,
not the generic message.

## Consequences

- Every backend starts in an environment that matches the project, and a missing dependency is
  a visible, journaled step with its own log instead of a silent loss of turns.
- The base-environment allowlist closes the secrets leak. It can also break a tool that relied
  on an inherited variable. Those cases are fixed by adding the name to the allowlist, never by
  going back to inheriting everything. The allowlist lives in one place.
- Prepare adds seconds to a first start and runs code from the project. The one-time
  confirmation is the price of that. A project can turn automatic prepare off.
- Only Python (uv) and Node are covered first. Go, Rust, Java and Ruby are detected in V5-E2
  but not prepared until V5-E6, and the Environment card says so rather than implying support.
- Because the log is derived, a bug in the projection cannot change what the journal says.
  What it cannot do is explain events that were never journaled, so the operational logs above
  are named, not left to chance.
- A hermetic per-project container (a devcontainer or the existing sandbox seam, ADR-0002) is
  the long-term answer to "it works on my machine". It is deliberately not built here.

## Decided questions

Q56 to Q61 in `docs/QUESTIONS.md`.

## Update (V5-E3a, 2026-10-07): how the confirmation works

Decision 2.3 said a project's first install needs "one confirmation from a person" and the slice
text said this would go through Needs you (ADR-0028). It does not: a Needs-you request belongs to a
team, and at the moment of the question the team does not exist yet. The confirmation is an explicit
input to the start call instead:

- A project has an `env_prepare` setting: `ask` (the default; a missing column reads as `ask`),
  `auto`, or `off`. `PATCH /api/projects/{id}/environment` sets it.
- `POST /api/projects/{id}/start` takes `prepare`: `yes` installs first, `skip` starts without.
  Left out, `auto` installs, `off` never does, and `ask` answers 409 naming what is stale and the two
  values, so a dashboard, an MCP client or a script must say which. Nothing is ever installed on a
  guess.
- The install runs inside the team's own lifecycle, after the satay control server is up and before the
  first round, so the start call returns at once. It is journaled (`EnvironmentPrepareStarted`,
  `EnvironmentPrepared` with the exit code, duration and the redacted tail of the output). A failed
  install records every role as failed with why, and no round starts; a stop during the install kills it
  (its whole process group) and ends the team.
- Staleness is the fingerprint in `.cuttlefish/env.json`, written only after success. An install a
  person made themselves is trusted until its files change, then reinstalled.
- `uv sync` runs `--frozen` when there is a `uv.lock`, so cuttlefish never rewrites a person's lockfile.

## Update (V5-E4, 2026-10-07): the environment an agent gets

`merge_env` (`delegate/subprocess_env.py`) no longer copies the daemon's environment. A child gets:
the allowlisted names (`HOME`, `USER`, `LANG`, `LC_*`, `XDG_*`, `TERM`, proxies, CA bundles, toolchain locations such
as `GOPATH` and `JAVA_HOME`); the operator's `CUTTLEFISH_AGENT_ENV_PASSTHROUGH` (comma-separated names, `*` ends a
prefix) and what its backend asks for (`KOPICODE_*`, `CLAUDE_*`, `CODEX_*` and their base URLs); for installs only, the
package tools' own settings (`NPM_*`, `npm_config_*`, `UV_*`, `PIP_*`, ... where a private registry's token lives);
`PATH` without cuttlefish's own venv and without WSL's `/mnt/...`; the project's `.venv` (with `VIRTUAL_ENV`) and
`node_modules/.bin` first on `PATH` when they exist; and last, the credentials the backend declared. `CUTTLEFISH_*`,
`E2B_API_KEY`, `.env` and everything `uv run` set are simply not there.

Two choices the decision text did not spell out:

- **`/mnt/...` is dropped process-wide**, `CUTTLEFISH_KEEP_WINDOWS_PATH=1` keeps it. Q61 assumed a per-project opt-in;
  that needs a column and a UI, and nobody has asked for it. Revisit if someone needs Windows tools in one project only.
- **The resident `kopicode serve` child is keyed by project root.** It reads its environment once at start, so a child
  shared between projects would carry the first project's `.venv` on `PATH` for the second. The pool key is now
  (binary, root, credentials, consent timeout) and the child starts in the project's folder.

Every role's brief also gets a short `Environment:` note between its persona and its task (`environment.brief`): the
project's own environment is first on `PATH`, how to run things (`uv run`, `pnpm run`), and not to install globally.
It says only what cuttlefish sets up (Python and Node), and describes the install that is about to happen when one is.


## Update (V5-E5, 2026-10-07): the stuck-agent detector

Decision 3 said the detector reads "the journal's tool results". Checked against kopicode's source and a real
session, that is not possible: the stream's `tool_result` event carries only the tool, `exit_code`, `size` and
`reason` (`cmd/kopicode/print.go`, `internal/engine/event.go`), never the output, and cuttlefish journals only
`ToolCallRecorded` (tool, detail, status). The output exists in one place, the session's own record. So:

- **Where the evidence is.** `<root>/.kopicode/sessions/<session>/events.jsonl`. cuttlefish chooses the session
  id (`cuttlefish-<hex>`), so the path is known while the round runs, not only after `session.start` returns its
  `record`. A `ToolResult` line holds the output either inline (`output.inline`) or, when large, as a blob under
  `<root>/.kopicode/blobs/<hash>`. Only the tail of a blob is read, bounded. Reading it is not a new protocol: it is the
  file kopicode already writes, and `DelegationFailed.record` already points there.
- **Detection runs live, on the serve transport.** Each `run_shell` `tool_result` with a non-zero exit code makes
  the child read the session's new journal lines (off the event loop) and feed them to a pure `StuckDetector`
  (`cuttlefish.stuck`). N consecutive `run_shell` failures that each match a signature trigger it. A shell call
  that succeeds, or fails without matching, resets the count; other tools do not touch it. Signatures are a data
  tuple of regular expressions in that module, N is `CUTTLEFISH_STUCK_THRESHOLD` (default 5, `0` turns it off).
- **What triggering does.** The child cancels the session (the same `session.cancel` plus close an operator's stop
  uses), the round ends as `failed` with `failure_kind="environment_stuck"`, and `DelegationFailed.detail` holds a
  redacted tail of the last failing output (at most 2 KiB). The agent stops after about N calls instead of
  running to `max_turns`. If the record cannot be read (a sandboxed child, a missing file) nothing triggers; the
  detector fails open to today's behaviour.
- **Only kopicode.** Claude Code and Codex run one-shot and return their output at the end, so there is nothing to
  stop early; their output is not read. The dashboard says nothing about the detector for them.
- **The request (E5b).** After the round ends, the team raises a `blocked` request (ADR-0028) from the failed
  outcome: title naming the role, `detail` the evidence, `answers` empty (nothing a card button can answer),
  `lands="next_round"`. It is not held: nobody is waiting on a future, so the card must not imply a live prompt. It
  ends when the role is steered or approved or rejected (a new resolution, `superseded`) or when the team ends.

## Update (V5-E6a, 2026-10-07): more installs

The fixed program list grew: poetry and pipenv (told to keep the environment in the project, so it lands in `.venv` where the
agent's `PATH` already looks), `go mod download`, `cargo fetch`, `bundle install`, and Maven or Gradle (the project's own wrapper
when it has one). A step can carry settings for its own commands (`PrepareStep.env`).

- **No folder to look at.** Go, Rust, Java and Ruby keep downloads outside the project, so "is it installed" has no answer from the
  files. They are due when cuttlefish has no record of fetching, when the files changed since, or when the last try failed. The
  first start of such a project therefore asks once (under `ask`) even if the person fetched by hand. The commands are idempotent
  and fast when cached.
- **Remember the files after the install, not before.** `cargo fetch`, `poetry install` and `uv sync` with no lock write their own
  lockfile. Recording the planned fingerprint made the next start see a change and install again; the daemon records the
  fingerprint of what is on disk when the install ends (`envprep.fingerprint_now`).
- **Still true:** an install runs project code, so the same confirmation applies; detection runs nothing.

## Update (V5-E6b, 2026-10-07): projects in subfolders

Detection reads the root and then each folder directly under it, with the same detectors. A hit in a subfolder carries its `path`,
and everything that was per ecosystem is now per ecosystem and folder: the plan, the step's working directory, the `env.json` key
(`node:frontend`; the root keeps the bare name, so existing files stay valid), and the install events.

- **A subfolder of an ecosystem the root already has is skipped.** A root `package.json` with workspaces, a Cargo workspace or a uv
  workspace installs its members itself; a second `npm ci` in `packages/a` would be wrong. The cost is that an unrelated `docs/package.json`
  next to a root `package.json` is not installed either. Judged the safer error: a missing install is visible, a wrong one is not.
- **One level, capped at 12.** Deeper discovery needs a rule for which project an agent works in, and a repository of examples would
  otherwise be a wall of installs a person must approve.
- **The agent's `PATH` is unchanged.** Putting several projects' `.venv/bin` on one `PATH` makes `python` depend on their order. The
  brief says where each nested project is and to work from its folder.

