"""The headless Codex CLI delegation (ADR-0005, ADR-0018): mirrors
``claude_code.py``'s shape.

Wraps ``codex exec --json ...`` — Codex's own non-interactive surface,
verified directly against the real binary (2026-09-28, ``codex-cli`` 0.155.1),
not assumed from documentation (Codex has no public source to read, unlike
kopicode). Parses its JSONL event stream into the same shared
:class:`~cuttlefish.agents.outcome.DelegationOutcome`
:func:`classify_stream <cuttlefish.delegate.claude_code.classify_stream>`
produces for the other two backends, so a caller never has to know which
backend actually ran.

**The policy mapping here is coarser than either existing backend's own —
an honest limitation of Codex's own surface, not a lesser cuttlefish
effort.** kopicode's declared-allowlist and Claude Code's
``--allowedTools``/``--disallowedTools`` both let an operator name specific
commands. Codex's own ``--sandbox`` flag is a three-tier OS sandbox policy
(``read-only`` / ``workspace-write`` / ``danger-full-access``) with no
per-command filtering at all — verified live, there is no flag that accepts
a command list the way kopicode's policy file or Claude Code's
``Bash(<cmd>:*)`` patterns do. This module maps cuttlefish's own declared
``allow`` list onto the coarsest available approximation: nothing declared
(the fail-closed default every backend holds to) maps to ``--sandbox
read-only``; anything declared at all maps to ``--sandbox workspace-write``,
regardless of which commands were actually named. A declared allowlist here
grants broader access than the operator may have intended — named here as a
real, open gap (mirroring ``cuttlefish.delegate.claude_code``'s own
``--allowedTools`` gap, docs/QUESTIONS.md Q36), not asserted as parity.

**Refusal has no dedicated event on Codex's own stream — verified live.** A
denied write (read-only sandbox) produces no ``item.completed`` of any
"denied" shape at all: the model's own turn still ends in ``turn.completed``
(a graceful "I couldn't do that" completion, not a failure), and the only
signal that anything was declined is an unstructured Rust log line on
stderr (``... error=patch rejected: ... rejected by user approval
settings``). :func:`classify_stream` falls back to a substring check on
that stderr text when no edit landed — a heuristic on an internal,
undocumented log message that could change wording in a future Codex
release without notice, not a stable contract. Named honestly, the same way
kopicode's own ``_redacted_stderr_tail`` already folds a stderr snippet into
a ``"failed"`` outcome's ``reason`` when nothing more structured is
available.

**No dollar figure at all.** ``turn.completed``'s own ``usage`` object
reports token counts (``input_tokens``/``output_tokens``, plus
``cached_input_tokens``/``cache_write_input_tokens``/
``reasoning_output_tokens`` as subset/breakdown detail — verified live that
``cached_input_tokens`` is smaller than, not additional to, ``input_tokens``,
so only the two top-level totals are summed here to avoid a plausible
over-count) but never a cost figure — verified live against a real
authenticated run. ``DelegationOutcome.cost_usd`` is therefore always
``None`` for this backend, the identical honest gap
``cuttlefish.delegate.kopicode`` already documents for its own reason
(ADR-0017).

Credential note: this build's own ``codex`` authenticates via an existing
ChatGPT OAuth session (``codex login status`` reports "Logged in using
ChatGPT"). Verified live, and a real, load-bearing finding: unlike Claude
Code, Codex's headless ``exec`` surface does **not** read ``OPENAI_API_KEY``
as an ambient credential at invocation time at all — a syntactically valid
key set in the environment produced the identical ``401 Unauthorized:
Missing bearer or basic authentication`` as no key at all, with no
``Authorization`` header ever attached to the request. Only a persisted
``~/.codex/auth.json`` (written by ``codex login``, either the interactive
ChatGPT flow or ``codex login --with-api-key``) authenticates a real
invocation. ``cuttlefish.agents.codex`` still forwards ``OPENAI_API_KEY`` the
same way every other backend's ambient credential is forwarded (harmless,
and future-proof if Codex's own precedence rules change), but this project's
sandboxed-credential-forwarding mechanism (ADR-0006) is consequently inert
for this backend — a sandboxed delegation fails closed on a 401, the
identical "real, accepted gap for this slice" posture
``cuttlefish.agents.claude_code``'s own OAuth gap already holds (Q37), just
via a different credential file than an env var.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome, ToolCallRecord
from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.sandbox.provider import SandboxError, SandboxHandle, SandboxProvider

#: `--json`'s per-line `type` values this module reads.
_TYPE_ITEM_COMPLETED = "item.completed"
_TYPE_TURN_COMPLETED = "turn.completed"
_TYPE_TURN_FAILED = "turn.failed"

#: An `item.completed` item's own `type` for a landed file edit.
_ITEM_TYPE_FILE_CHANGE = "file_change"

#: The Rust-side log line kopicode's own sandbox rejection prints to stderr --
#: an internal message, not a documented contract (this module's own doc
#: comment explains why this is the best signal available).
_REJECTION_MARKER = "rejected"


def build_codex_argv(
    binary: str, task_text: str, *, allow: list[list[str]] | None = None
) -> list[str]:
    """The argv for one ``binary exec --json task_text`` invocation.

    Shared by :func:`run_codex` (a direct host subprocess) and
    :func:`run_codex_in_sandbox` (a `SandboxProvider.exec` call), the same
    split every other backend's own ``build_*_argv`` makes.

    ``--skip-git-repo-check`` is always passed -- verified live that Codex
    otherwise refuses outright ("Not inside a trusted directory") against a
    plain scratch checkout that isn't a git repository, exactly the kind of
    root V1's own original delegation already runs against.
    """
    sandbox_mode = "workspace-write" if allow else "read-only"
    return [
        binary,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        sandbox_mode,
        task_text,
    ]


def classify_stream(
    events: Iterable[Mapping[str, Any]], *, root: str | None = None, stderr_tail: str = ""
) -> DelegationOutcome:
    """Reduce an already-parsed sequence of ``--json`` event lines to one outcome.

    Pure and synchronous, the same split every other backend's own
    ``classify_stream`` makes. ``stderr_tail`` is the caller's already
    redacted/truncated stderr (see :func:`classify_codex_output`) -- folded in
    only to distinguish a refusal from "nothing to do" when no edit landed,
    since Codex's own item stream carries no structured signal for either
    (this module's own doc comment).
    """
    edited_paths: list[str] = []
    tool_calls: list[ToolCallRecord] = []
    turn_completed: Mapping[str, Any] | None = None
    turn_failed: Mapping[str, Any] | None = None

    for event in events:
        kind = event.get("type")
        if kind == _TYPE_ITEM_COMPLETED:
            item = event.get("item")
            for path in _edited_paths_from_item(item):
                if root is not None:
                    path = _relativize(path, root)
                if path not in edited_paths:
                    edited_paths.append(path)
            record = _tool_call_from_item(item)
            if record is not None:
                tool_calls.append(record)
        elif kind == _TYPE_TURN_FAILED:
            turn_failed = event.get("error")
        elif kind == _TYPE_TURN_COMPLETED:
            turn_completed = event

    if turn_failed is not None:
        reason = (
            turn_failed.get("message") if isinstance(turn_failed, Mapping) else str(turn_failed)
        )
        return DelegationOutcome(
            kind="failed",
            summary="Codex did not finish cleanly",
            reason=str(reason),
            tool_calls=tool_calls,
        )

    if turn_completed is None:
        raise DelegationError("Codex's stream ended with no turn.completed or turn.failed event")

    tokens = _tokens_from_usage(turn_completed.get("usage"))

    if edited_paths:
        return DelegationOutcome(
            kind="completed",
            summary=f"Codex edited {len(edited_paths)} file(s)",
            edited_paths=edited_paths,
            tokens=tokens,
            tool_calls=tool_calls,
        )
    if _REJECTION_MARKER in stderr_tail.lower():
        return DelegationOutcome(
            kind="refused",
            summary="Codex's sandbox policy declined every action it needed",
            reason=stderr_tail,
            tokens=tokens,
            tool_calls=tool_calls,
        )
    return DelegationOutcome(
        kind="completed",
        summary="Codex finished with no edit needed",
        tokens=tokens,
        tool_calls=tool_calls,
    )


#: `item.completed`'s own `item.type` values this module records as a tool call
#: (KAN-1714/ADR-0019) -- `agent_message`/`error` items are the model's own
#: text or a mid-stream anomaly notice, neither a tool invocation, so neither
#: is recorded here.
_ITEM_TYPE_COMMAND_EXECUTION = "command_execution"


def _tool_call_from_item(item: object) -> ToolCallRecord | None:
    if not isinstance(item, Mapping):
        return None
    item_type = item.get("type")
    if item_type == _ITEM_TYPE_FILE_CHANGE:
        changes = item.get("changes")
        detail = (
            "; ".join(
                f"{change.get('kind')} {change.get('path')}"
                for change in changes
                if isinstance(change, Mapping)
            )
            if isinstance(changes, list)
            else ""
        )
        # Verified live: a rejected patch never produces a `file_change` item at
        # all (this module's own doc comment) -- every one actually observed is
        # a landed edit.
        return ToolCallRecord(tool=item_type, detail=detail, status="ok")
    if item_type == _ITEM_TYPE_COMMAND_EXECUTION:
        command = item.get("command")
        exit_code = item.get("exit_code")
        status: Literal["ok", "denied", "error"] = "ok" if exit_code == 0 else "error"
        return ToolCallRecord(
            tool=item_type, detail=command if isinstance(command, str) else "", status=status
        )
    return None


def _tokens_from_usage(usage: object) -> int | None:
    """`input_tokens + output_tokens` off `turn.completed`'s own `usage` object,
    or `None` when neither is present.

    `cached_input_tokens`/`cache_write_input_tokens`/`reasoning_output_tokens`
    are deliberately not added on top -- verified live (`cached_input_tokens`
    was always smaller than `input_tokens` across every probe) that they read
    as a subset/breakdown of the two totals summed here, not an additional
    pool the way Claude Code's own cache token fields genuinely are. Summing
    them too would silently overcount.
    """
    if not isinstance(usage, Mapping):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    return int(input_tokens or 0) + int(output_tokens or 0)


def _edited_paths_from_item(item: object) -> list[str]:
    if not isinstance(item, Mapping) or item.get("type") != _ITEM_TYPE_FILE_CHANGE:
        return []
    changes = item.get("changes")
    if not isinstance(changes, list):
        return []
    paths: list[str] = []
    for change in changes:
        if isinstance(change, Mapping):
            path = change.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    return paths


def _relativize(path: str, root: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        # Genuinely outside root -- keep the absolute path rather than raising,
        # the same posture `cuttlefish.delegate.claude_code._relativize` holds.
        return path


async def run_codex(
    *,
    binary: str,
    task_text: str,
    root: str,
    allow: list[list[str]] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run ``binary exec --json task_text ...`` in `root` and classify what it did.

    Raises :class:`DelegationError` for anything short of a recorded result:
    the binary missing, a non-JSON line, or a stream with no
    `turn.completed`/`turn.failed` event.
    """
    args = build_codex_argv(binary, task_text, allow=allow)

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merge_env(env),
        )
    except FileNotFoundError as exc:
        raise DelegationError(f"Codex binary {binary!r} not found") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.gather(process.stdout.read(), process.stderr.read()), timeout=timeout
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise DelegationError(f"Codex timed out after {timeout}s") from exc
    await process.wait()

    return classify_codex_output(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        returncode=process.returncode,
        root=root,
    )


async def run_codex_in_sandbox(
    provider: SandboxProvider,
    handle: SandboxHandle,
    *,
    binary: str,
    task_text: str,
    root: str,
    allow: list[list[str]] | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run Codex inside an already-created sandbox and classify what it did.

    `binary` and `root` must already be paths *inside* the sandbox, the same
    contract every other backend's own ``run_*_in_sandbox`` gives. See this
    module's own doc comment for why this path is unauthenticated in
    practice today (Codex's own credential story has no ambient env-var path
    at all, verified live) -- a sandboxed call here reliably fails closed on
    a 401, not a silent success.
    """
    args = build_codex_argv(binary, task_text, allow=allow)
    try:
        result = await provider.exec(handle, args, cwd=root, timeout=timeout)
    except SandboxError as exc:
        raise DelegationError(f"Codex sandbox exec failed: {exc}") from exc
    return classify_codex_output(
        result.stdout, result.stderr, returncode=result.exit_code, root=root
    )


#: How much of stderr to keep for the refusal heuristic/a failed session's own
#: reason -- the same bound `cuttlefish.delegate.kopicode._STDERR_TAIL_CHARS`
#: already uses, for the identical reason (never fold an unbounded log into a
#: task's own return value).
_STDERR_TAIL_CHARS = 2000


def classify_codex_output(
    stdout_text: str, stderr_text: str, *, returncode: int | None, root: str | None = None
) -> DelegationOutcome:
    """Classify one already-captured stdout/stderr pair.

    Shared by both invocation paths so parsing and classification never
    diverge between them -- only how the raw output was obtained differs.
    """
    events = parse_stream_json(stdout_text)
    if not events:
        raise DelegationError(
            f"Codex produced no session events (exit {returncode}); "
            f"stderr: {stderr_text.strip()[-_STDERR_TAIL_CHARS:] or '<empty>'}"
        )
    return classify_stream(events, root=root, stderr_tail=stderr_text.strip()[-_STDERR_TAIL_CHARS:])


def parse_stream_json(stdout_text: str) -> list[Mapping[str, Any]]:
    """Every line of a `--json` stdout stream, parsed as one JSON object.

    Verified live: every stdout line is a real JSON object with no header or
    banner line to skip (unlike kopicode's own `"kind":"stream"` first line) --
    the "Reading additional input from stdin..." notice observed in earlier
    probes lands on stderr, never stdout.
    """
    events: list[Mapping[str, Any]] = []
    for line in stdout_text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DelegationError(f"Codex emitted a non-JSON line: {line!r}") from exc
        if not isinstance(parsed, dict):
            raise DelegationError(f"Codex emitted a non-object JSON line: {line!r}")
        events.append(parsed)
    return events
