"""The kopicode delegation: shells out to ``kopicode run --print`` (ADR-0003).

Wraps kopicode's existing headless surface as-is — no new protocol. Parses its
newline-delimited JSON stream (``cmd/kopicode/print.go``'s documented schema) into a
single, typed :class:`~cuttlefish.agents.outcome.DelegationOutcome`, so a caller
never has to know kopicode's own event vocabulary. kopicode is the reference
:class:`~cuttlefish.agents.backend.AgentBackend` implementation (ADR-0005) — this
module still owns only its native CLI mechanics, not the pluggable-backend seam
itself, the same split ``cuttlefish.delegate.claude_code`` follows for its backend.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome, ToolCallRecord
from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.episodic.redact import DEFAULT_SECRET_ENV_VARS, Redactor
from cuttlefish.sandbox.provider import SandboxError, SandboxHandle, SandboxProvider

#: What kopicode reads from its environment besides the credentials a backend declares (V5-E4).
ENV_PASSTHROUGH = ("KOPICODE_*",)

#: kopicode run --print's per-line `kind` values this module reads. Every other kind
#: (provider_request, ...) is part of the real record but doesn't change the outcome
#: this module decides.
_KIND_STREAM = "stream"
_KIND_PERMISSION_DECIDED = "permission_decided"
_KIND_EDIT_APPLIED = "edit_applied"
_KIND_TOOL_CALL_PARSED = "tool_call_parsed"
_KIND_TOOL_RESULT = "tool_result"
_KIND_SESSION_ENDED = "session_ended"
#: kopicode's own `internal/engine/event.go` copies `journal.ProviderResponse.Tokens
#: .Total` alone into this kind's `size` field (KAN-1712/ADR-0017, verified directly
#: against kopicode's source) -- the prompt/completion split its own journal holds
#: internally never reaches this headless stream, so a summed total is the most this
#: module can ever report for kopicode, and no dollar figure at all (kopicode reports
#: no cost anywhere on this stream, for any kind).
_KIND_PROVIDER_RESPONSE = "provider_response"

#: permission_decided's `decision` field (kopicode internal/permission.Verdict).
_DECISION_DENY = "deny"

#: session_ended's exit_code: 0 is the only success value (kopicode internal/engine/stop.go).
_EXIT_CODE_SUCCESS = 0

#: kopicode's write_file and delete_file never emit edit_applied (internal/engine/
#: dispatch.go: only edit_file and edit_file_fuzzy call journalEdit) — they show up
#: only as a tool_call_parsed/tool_result pair, so a whole-file create/replace or a
#: delete would otherwise be invisible to this module and read as "no edit needed"
#: even though a real write landed. Their tool_call_parsed `detail` is the call's own
#: JSON arguments, always including `path` (internal/engine/catalogue.go).
_WHOLE_FILE_WRITE_TOOLS = frozenset({"write_file", "delete_file"})


def classify_stream(
    events: Iterable[Mapping[str, Any]], *, stderr_tail: str = ""
) -> DelegationOutcome:
    """Reduce an already-parsed sequence of `run --print` event lines to one outcome.

    Pure and synchronous — the subprocess and its NDJSON parsing live in
    :func:`run_kopicode`, which calls this once the stream is fully read. Kept
    separate so this decision logic is unit-testable against literal, representative
    event sequences without a real kopicode process, while parsing an *actual*
    stream stays integration-tested against the real binary (docs/SLICES.md V1 test
    plan) — this function doesn't invent kopicode's vocabulary, it only decides what
    a given sequence of it means.

    `stderr_tail` is folded into a ``"failed"`` outcome's `reason` — kopicode's own
    `session_ended` event carries only `exit_code`/`reason`, which is often too
    generic to act on (e.g. a provider's 401 shows up only on stderr, never in the
    event stream itself). The caller (:func:`classify_kopicode_output`) is
    responsible for already having redacted and truncated it — this function just
    threads a plain string through.
    """
    edited_paths: list[str] = []
    deny_reasons: list[str] = []
    session_ended: Mapping[str, Any] | None = None
    total_tokens = 0
    # A write_file/delete_file tool_call_parsed's path, held until its matching
    # tool_result confirms it actually ran. FIFO is safe: kopicode dispatches one
    # call at a time and journals its result before starting the next
    # (internal/engine/dispatch.go's dispatch loop), so the print stream never
    # interleaves two calls' events.
    pending_whole_file_writes: list[str | None] = []
    # Every tool_call_parsed, held until its own matching tool_result -- the
    # identical FIFO safety the whole-file-write queue above already relies on,
    # generalised to every tool (KAN-1714/ADR-0019), verified live: exactly one
    # tool_result follows each tool_call_parsed before the next one starts.
    pending_tool_calls: list[tuple[str, str]] = []
    tool_calls: list[ToolCallRecord] = []
    # Set by a `permission_decided`/deny and consumed by the *next* tool_result
    # -- verified live that a denial's own tool_result still carries a non-empty
    # `reason` (kopicode's ErrorKind), which alone can't distinguish "denied"
    # from a genuine execution error.
    last_denied = False

    for event in events:
        kind = event.get("kind")
        if kind == _KIND_EDIT_APPLIED:
            path = event.get("path")
            if isinstance(path, str) and path:
                edited_paths.append(path)
        elif kind == _KIND_TOOL_CALL_PARSED:
            tool = event.get("tool")
            detail = event.get("detail")
            if isinstance(tool, str) and isinstance(detail, str):
                pending_tool_calls.append((tool, detail))
            if tool in _WHOLE_FILE_WRITE_TOOLS:
                pending_whole_file_writes.append(_path_from_tool_detail(detail))
        elif kind == _KIND_TOOL_RESULT:
            tool = event.get("tool")
            # tool_result's `reason` carries journal.ToolResult.ErrorKind and is
            # omitted entirely when empty (cmd/kopicode/print.go: "zero fields
            # are omitted") — its presence is what marks this call as failed.
            reason = event.get("reason")
            if pending_tool_calls:
                pending_tool, pending_detail = pending_tool_calls.pop(0)
                if not reason:
                    status: Literal["ok", "denied", "error"] = "ok"
                elif last_denied:
                    status = "denied"
                else:
                    status = "error"
                tool_calls.append(
                    ToolCallRecord(tool=pending_tool, detail=pending_detail, status=status)
                )
            last_denied = False
            if tool in _WHOLE_FILE_WRITE_TOOLS and pending_whole_file_writes:
                path = pending_whole_file_writes.pop(0)
                if path and not reason:
                    edited_paths.append(path)
        elif kind == _KIND_PERMISSION_DECIDED:
            if event.get("decision") == _DECISION_DENY:
                last_denied = True
                reason = event.get("reason")
                deny_reasons.append(reason if isinstance(reason, str) else "denied")
        elif kind == _KIND_SESSION_ENDED:
            session_ended = event
        elif kind == _KIND_PROVIDER_RESPONSE:
            size = event.get("size")
            if isinstance(size, int):
                total_tokens += size

    if session_ended is None:
        raise DelegationError("kopicode's stream ended with no session_ended event")

    exit_code = session_ended.get("exit_code")
    stop_reason = session_ended.get("reason", "unknown")

    if edited_paths:
        return DelegationOutcome(
            kind="completed",
            summary=f"kopicode edited {len(edited_paths)} file(s) ({stop_reason})",
            edited_paths=edited_paths,
            tokens=total_tokens,
            tool_calls=tool_calls,
        )
    if deny_reasons:
        return DelegationOutcome(
            kind="refused",
            summary="kopicode's permission gate declined every action it needed",
            reason="; ".join(deny_reasons),
            tokens=total_tokens,
            tool_calls=tool_calls,
        )
    if exit_code == _EXIT_CODE_SUCCESS:
        return DelegationOutcome(
            kind="completed",
            summary=f"kopicode finished with no edit needed ({stop_reason})",
            tokens=total_tokens,
            tool_calls=tool_calls,
        )
    reason = f"exit_code={exit_code} reason={stop_reason}"
    if stderr_tail:
        reason += f"; stderr: {stderr_tail}"
    return DelegationOutcome(
        kind="failed",
        summary=f"kopicode did not finish cleanly ({stop_reason})",
        reason=reason,
        tokens=total_tokens,
        tool_calls=tool_calls,
    )


def build_kopicode_argv(
    binary: str, task_text: str, *, policy_file: str | None = None
) -> list[str]:
    """The argv for one ``binary run --print task_text`` invocation.

    Shared by :func:`run_kopicode` (a direct host subprocess) and
    :func:`run_kopicode_in_sandbox` (a `SandboxProvider.exec` call) so the two
    invocation paths can never drift on kopicode's own CLI grammar.
    """
    args = [binary, "run", "--print"]
    if policy_file is not None:
        args += ["--policy-file", policy_file]
    args.append(task_text)
    return args


async def run_kopicode(
    *,
    binary: str,
    task_text: str,
    root: str,
    policy_file: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run ``binary run --print task_text`` in `root` and classify what it did.

    Raises :class:`DelegationError` for anything short of a recorded session: the
    binary missing, a non-JSON line, or a stream with no `session_ended`. Anything
    kopicode itself recorded, cleanly or not, is a :class:`DelegationOutcome`, never
    an exception — QUESTIONS.md Q16's "caught, journaled as a typed episodic event"
    happens one layer up, in the satay task that calls this
    (``cuttlefish.tasks.delegate``).
    """
    args = build_kopicode_argv(binary, task_text, policy_file=policy_file)

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merge_env(env, root=root, passthrough=ENV_PASSTHROUGH),
        )
    except FileNotFoundError as exc:
        raise DelegationError(f"kopicode binary {binary!r} not found") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.gather(process.stdout.read(), process.stderr.read()), timeout=timeout
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise DelegationError(f"kopicode timed out after {timeout}s") from exc
    await process.wait()

    return classify_kopicode_output(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        returncode=process.returncode,
        env=env,
    )


async def run_kopicode_in_sandbox(
    provider: SandboxProvider,
    handle: SandboxHandle,
    *,
    binary: str,
    task_text: str,
    root: str,
    policy_file: str | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run kopicode inside an already-created sandbox and classify what it did.

    `binary`, `root`, and `policy_file` must already be paths *inside* the
    sandbox — making the scratch checkout, the kopicode binary, and the policy
    file visible there (e.g. :class:`~cuttlefish.sandbox.container.ContainerSandboxProvider`'s
    bind mounts) is the caller's job (``cuttlefish.tasks.delegate``); this function
    only runs the command and classifies its output, the same contract
    :func:`run_kopicode` gives for a direct host subprocess.
    """
    args = build_kopicode_argv(binary, task_text, policy_file=policy_file)
    try:
        result = await provider.exec(handle, args, cwd=root, timeout=timeout)
    except SandboxError as exc:
        raise DelegationError(f"kopicode sandbox exec failed: {exc}") from exc
    return classify_kopicode_output(result.stdout, result.stderr, returncode=result.exit_code)


def classify_kopicode_output(
    stdout_text: str,
    stderr_text: str,
    *,
    returncode: int | None,
    env: Mapping[str, str] | None = None,
) -> DelegationOutcome:
    """Classify one already-captured ``run --print`` stdout/stderr pair.

    Shared by both invocation paths (a direct host subprocess, or a sandboxed
    ``exec``) so parsing and classification never diverge between them — only how
    the raw output was obtained differs.

    `env` is the same credential mapping the invocation itself carried (e.g.
    `run_kopicode`'s own `env` argument) — used only to redact a failed session's
    stderr tail before it becomes part of `DelegationOutcome.reason` (see
    :func:`_redacted_stderr_tail`); `None` still redacts against the same ambient
    names the episodic journal's own write-time redactor knows (ADR-0004).
    """
    events = parse_ndjson(stdout_text)
    if not events:
        raise DelegationError(
            f"kopicode produced no session events (exit {returncode}); "
            f"stderr: {stderr_text.strip() or '<empty>'}"
        )
    return classify_stream(events, stderr_tail=_redacted_stderr_tail(stderr_text, env))


#: How much of a failed session's stderr to surface in `DelegationOutcome.reason` --
#: enough for a human to see e.g. "API key expired" without folding an unbounded
#: log into a value that (unlike a caught exception's message) becomes a task's
#: own return value before the episodic journal ever gets a chance to redact it.
_STDERR_TAIL_CHARS = 2000


def _redacted_stderr_tail(stderr_text: str, env: Mapping[str, str] | None) -> str:
    """The last `_STDERR_TAIL_CHARS` of `stderr_text`, with every credential this
    invocation carried replaced first.

    kopicode's own stderr can in principle echo a secret verbatim -- a provider's
    own error response, say -- and this string is threaded straight into
    `DelegationOutcome.reason`, a satay task return value, before the episodic
    journal's own write-time redaction (`cuttlefish.episodic.redact`) ever sees it
    (ADR-0004, CLAUDE.md's "redacted from the episodic journal at write time" rule).
    Redacted here defensively, the same way and against the same known-secret names
    (`DEFAULT_SECRET_ENV_VARS` plus whatever this call's own `env` carried), rather
    than trusting that later write-time pass alone.
    """
    stripped = stderr_text.strip()
    if not stripped:
        return ""
    names = sorted(set(DEFAULT_SECRET_ENV_VARS) | set(env or {}))

    def _lookup(name: str) -> str | None:
        return (env or {}).get(name) or os.environ.get(name)

    redacted, _ = Redactor(names, lookup=_lookup).scrub(stripped)
    return redacted[-_STDERR_TAIL_CHARS:]


def _path_from_tool_detail(detail: object) -> str | None:
    """The `path` argument out of a tool_call_parsed's `detail` (its raw call JSON)."""
    if not isinstance(detail, str):
        return None
    try:
        parsed = json.loads(detail)
    except json.JSONDecodeError:
        return None
    path = parsed.get("path") if isinstance(parsed, dict) else None
    return path if isinstance(path, str) and path else None


def parse_ndjson(stdout_text: str) -> list[Mapping[str, Any]]:
    """Every non-header event line of a `run --print` stream, parsed as JSON."""
    events: list[Mapping[str, Any]] = []
    for line in stdout_text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DelegationError(f"kopicode emitted a non-JSON line: {line!r}") from exc
        if not isinstance(parsed, dict):
            raise DelegationError(f"kopicode emitted a non-object JSON line: {line!r}")
        if parsed.get("kind") == _KIND_STREAM:
            continue
        events.append(parsed)
    return events
