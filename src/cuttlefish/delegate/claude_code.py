"""The headless Claude Code delegation (ADR-0005): mirrors kopicode.py's shape.

Wraps ``claude -p ... --output-format stream-json`` — Claude Code's own
non-interactive surface, verified directly against the real binary
(2026-09-20, `claude` 2.1.278), not assumed from documentation. Parses its
stream-json event lines into the same shared
:class:`~cuttlefish.agents.outcome.DelegationOutcome` :func:`classify_stream
<cuttlefish.delegate.kopicode.classify_stream>` produces for kopicode, so a
caller never has to know which backend actually ran.

**The policy mapping here is an honest approximation, not parity with
kopicode's KAN-987 gate.** kopicode's declared-allowlist policy (root + an
explicit shell-command allowlist) is a single, purpose-built mechanism.
Claude Code's own permission model is CLI flags across several axes
(``--permission-mode``, ``--permission-prompts``, ``--allowedTools``/
``--disallowedTools``), verified live to behave like this:

- No declared ``allow`` commands (kopicode's own default posture): pass
  ``--disallowedTools Bash`` — this removes the Bash tool from the model's
  toolset entirely (verified: the model reports having no shell tool at
  all, rather than being prompted and denied), a real fail-closed result,
  not a lesser approximation of one.
- Declared ``allow`` commands: pass ``--allowedTools`` with one
  ``Bash(<command>:*)`` pattern per declared entry. This is
  **unverified against every command shape** kopicode's own grammar
  accepts — Bash's pattern-matching semantics are Claude Code's own, not
  kopicode's, and a declared command that doesn't match its own literal
  prefix exactly may be allowed more broadly, or not matched, than the
  operator intended. Named here as a real, open gap (docs/PLAN.md's Open
  risks), not asserted as parity.
- ``--permission-mode acceptEdits`` auto-accepts file-editing tool
  permissions (Edit/Write/MultiEdit) — the closest analogue to kopicode's
  own ``root``-scoped implicit edit permission, since both this project and
  kopicode already accept the same "one operator, their own task, their own
  checkout" trust model (ADR-0002) rather than a hard filesystem boundary.
- ``--permission-prompts none`` denies, rather than hangs on, anything that
  would otherwise need a human answering a prompt — the fail-closed default
  this project always wants, made explicit rather than relying on
  ``--permission-prompts``'s own ``host`` default behaving safely with no
  host attached.

Credential note: this build's own ``claude`` authenticates via an existing
OAuth session (verified live: its ``stream-json`` init event reports
``apiKeySource: "none"`` with no ``ANTHROPIC_API_KEY`` set at all), not an
API key. ``cuttlefish.agents.claude_code`` forwards ``ANTHROPIC_API_KEY``
into a sandbox the same way kopicode's credential is forwarded — that only
helps an operator whose Claude Code is authenticated by an API key. See that
module for the named, accepted gap this leaves for an OAuth-authenticated
operator.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome, ToolCallRecord
from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.sandbox.provider import SandboxError, SandboxHandle, SandboxProvider

#: stream-json's per-line `type` values this module reads.
_TYPE_ASSISTANT = "assistant"
_TYPE_USER = "user"
_TYPE_RESULT = "result"

#: Tool names whose `input.file_path` is a real edit (verified live: Write
#: and Edit both report this shape; MultiEdit is Claude Code's documented
#: batch-edit tool and follows the same input shape, though not separately
#: probed against the real binary).
_EDIT_TOOL_NAMES = frozenset({"Write", "Edit", "MultiEdit"})


#: The never-allowed list (``never_allowed``) as Claude Code deny patterns, added next to
#: every ``--allowedTools``. Only the prefix-shaped members map; the pipe-to-shell form
#: cannot be a Bash prefix. Not yet verified live that a deny pattern beats an allow
#: pattern for the same command, so this is belt, not braces.
_NEVER_ALLOWED_BASH_PREFIXES = (
    "sudo",
    "su",
    "doas",
    "git push --force",
    "git push -f",
    "git push --force-with-lease",
)


def build_claude_code_argv(
    binary: str, task_text: str, *, allow: list[list[str]] | None = None
) -> list[str]:
    """The argv for one ``binary -p task_text --output-format stream-json ...`` call.

    Shared by :func:`run_claude_code` (a direct host subprocess) and
    :func:`run_claude_code_in_sandbox` (a `SandboxProvider.exec` call), the
    same split kopicode's own ``build_kopicode_argv`` makes.
    """
    args = [
        binary,
        "-p",
        task_text,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--permission-prompts",
        "none",
    ]
    if allow:
        args.append("--allowedTools")
        args.extend(f"Bash({shlex.join(command)}:*)" for command in allow)
        args.append("--disallowedTools")
        args.extend(f"Bash({prefix}:*)" for prefix in _NEVER_ALLOWED_BASH_PREFIXES)
    else:
        args.extend(["--disallowedTools", "Bash"])
    return args


def classify_stream(
    events: Iterable[Mapping[str, Any]], *, root: str | None = None
) -> DelegationOutcome:
    """Reduce an already-parsed sequence of stream-json event lines to one outcome.

    Pure and synchronous, the same split ``cuttlefish.delegate.kopicode
    .classify_stream`` makes: this decides what a given sequence of Claude
    Code's own event vocabulary means, it doesn't invent that vocabulary.

    ``root``, when given, relativizes an edited path against it — verified
    live (2026-09-20) that Claude Code's own ``Write``/``Edit`` tool_use
    blocks report an *absolute* ``file_path``, unlike kopicode's relative
    ``edit_applied.path``. Normalising here keeps
    :class:`~cuttlefish.agents.outcome.DelegationOutcome` genuinely
    backend-agnostic (ADR-0005) rather than leaking one backend's own path
    convention into a shape every caller reads the same way.

    Every ``tool_use`` block is also recorded as a :class:`ToolCallRecord`
    (KAN-1714/ADR-0019), paired with its own later ``tool_result`` block by
    ``tool_use_id`` — verified live as the real join key both blocks actually
    carry, used here rather than assuming kopicode's own strict FIFO
    single-call-at-a-time ordering also holds for Claude Code (unverified
    either way; the id makes that assumption unnecessary). A call's own
    ``status`` starts as ``"ok"``/``"error"`` from its ``tool_result``'s own
    ``is_error``, then any call named in the *final* result event's own
    ``permission_denials`` is upgraded to ``"denied"`` in a second pass, once
    that event is known — the per-call ``is_error`` flag alone can't tell a
    permission denial apart from a genuine execution failure.
    """
    edited_paths: list[str] = []
    result_event: Mapping[str, Any] | None = None
    pending_tool_calls: dict[str, tuple[str, str]] = {}
    tool_call_ids: list[str] = []
    tool_calls: list[ToolCallRecord] = []

    for event in events:
        kind = event.get("type")
        if kind == _TYPE_ASSISTANT:
            message = event.get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            if isinstance(content, list):
                for block in content:
                    path = _edited_path_from_block(block)
                    if path is not None:
                        if root is not None:
                            path = _relativize(path, root)
                        if path not in edited_paths:
                            edited_paths.append(path)
                    call_id, name, detail = _tool_use_from_block(block)
                    if call_id is not None and name is not None:
                        pending_tool_calls[call_id] = (name, detail or "")
        elif kind == _TYPE_USER:
            message = event.get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, Mapping) or block.get("type") != "tool_result":
                        continue
                    call_id = block.get("tool_use_id")
                    if not isinstance(call_id, str) or call_id not in pending_tool_calls:
                        continue
                    name, detail = pending_tool_calls.pop(call_id)
                    status: Literal["ok", "denied", "error"] = (
                        "error" if block.get("is_error") else "ok"
                    )
                    tool_call_ids.append(call_id)
                    tool_calls.append(ToolCallRecord(tool=name, detail=detail, status=status))
        elif kind == _TYPE_RESULT:
            result_event = event

    if result_event is None:
        raise DelegationError("Claude Code's stream ended with no result event")

    # permission_denials is checked before is_error, the same order kopicode's
    # own classify_stream checks deny_reasons before exit_code: verified live
    # (2026-09-20) that a fully denied session still reports is_error=false,
    # subtype="success" — permission_denials is the only reliable signal.
    tokens, cost_usd = _usage_from_result(result_event)

    denials = result_event.get("permission_denials")
    denied_ids = (
        {
            denial.get("tool_use_id")
            for denial in denials
            if isinstance(denial, Mapping) and isinstance(denial.get("tool_use_id"), str)
        }
        if isinstance(denials, list)
        else set()
    )
    tool_calls = [
        dataclasses.replace(record, status="denied") if call_id in denied_ids else record
        for call_id, record in zip(tool_call_ids, tool_calls, strict=True)
    ]

    if isinstance(denials, list) and denials:
        reasons = [
            f"{denial.get('tool_name', 'unknown tool')} denied"
            for denial in denials
            if isinstance(denial, Mapping)
        ]
        return DelegationOutcome(
            kind="refused",
            summary="Claude Code's permission gate declined every action it needed",
            reason="; ".join(reasons) if reasons else "denied",
            tokens=tokens,
            cost_usd=cost_usd,
            tool_calls=tool_calls,
        )

    subtype = result_event.get("subtype", "unknown")
    if result_event.get("is_error"):
        return DelegationOutcome(
            kind="failed",
            summary=f"Claude Code did not finish cleanly ({subtype})",
            reason=str(result_event.get("result") or subtype),
            tokens=tokens,
            cost_usd=cost_usd,
            tool_calls=tool_calls,
        )

    result_text = result_event.get("result")
    summary = (
        result_text
        if isinstance(result_text, str) and result_text
        else f"Claude Code finished ({subtype})"
    )
    if edited_paths:
        return DelegationOutcome(
            kind="completed",
            summary=summary,
            edited_paths=edited_paths,
            tokens=tokens,
            cost_usd=cost_usd,
            tool_calls=tool_calls,
        )
    return DelegationOutcome(
        kind="completed", summary=summary, tokens=tokens, cost_usd=cost_usd, tool_calls=tool_calls
    )


#: `result`'s own `usage` fields this module sums into one total token count
#: (KAN-1712/ADR-0017) -- input/output plus both cache categories, since all
#: four are tokens the call actually processed, not only the ones a plain
#: input/output split would count.
_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _usage_from_result(result_event: Mapping[str, Any]) -> tuple[int | None, float | None]:
    """`(tokens, cost_usd)` off one `result` event, or `(None, None)` for either
    when the field isn't there at all -- verified live (2026-09-27, `claude`
    2.1.283) that a real `result` event carries both `usage` and
    `total_cost_usd` directly, so this never has to estimate either.
    """
    usage = result_event.get("usage")
    tokens = (
        sum(int(usage.get(field) or 0) for field in _USAGE_TOKEN_FIELDS)
        if isinstance(usage, Mapping)
        else None
    )
    cost_raw = result_event.get("total_cost_usd")
    cost_usd = float(cost_raw) if isinstance(cost_raw, int | float) else None
    return tokens, cost_usd


def _edited_path_from_block(block: object) -> str | None:
    if not isinstance(block, Mapping) or block.get("type") != "tool_use":
        return None
    if block.get("name") not in _EDIT_TOOL_NAMES:
        return None
    tool_input = block.get("input")
    path = tool_input.get("file_path") if isinstance(tool_input, Mapping) else None
    return path if isinstance(path, str) and path else None


#: How much of a tool call's own args to keep as its `ToolCallRecord.detail`
#: (KAN-1714/ADR-0019) -- `Write`'s own `content` field can be an entire file,
#: and this is a trace log entry, not a second copy of the edit itself (that's
#: `edited_paths`' own job).
_TOOL_CALL_DETAIL_CHARS = 500


def _tool_use_from_block(block: object) -> tuple[str | None, str | None, str | None]:
    """`(tool_use_id, name, detail)` off one `tool_use` content block, or all
    `None` if `block` isn't one."""
    if not isinstance(block, Mapping) or block.get("type") != "tool_use":
        return None, None, None
    call_id = block.get("id")
    name = block.get("name")
    if not isinstance(call_id, str) or not isinstance(name, str):
        return None, None, None
    detail = json.dumps(block.get("input"), sort_keys=True)[:_TOOL_CALL_DETAIL_CHARS]
    return call_id, name, detail


def _relativize(path: str, root: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        # Genuinely outside root (unusual, but not impossible) -- keep the
        # absolute path rather than raising; the caller still gets a real,
        # inspectable answer instead of a crash over an edge case.
        return path


async def run_claude_code(
    *,
    binary: str,
    task_text: str,
    root: str,
    allow: list[list[str]] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run ``binary -p task_text ...`` in `root` and classify what it did.

    Raises :class:`DelegationError` for anything short of a recorded result:
    the binary missing, a non-JSON line, or a stream with no `result` event.
    """
    args = build_claude_code_argv(binary, task_text, allow=allow)

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merge_env(env),
        )
    except FileNotFoundError as exc:
        raise DelegationError(f"Claude Code binary {binary!r} not found") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.gather(process.stdout.read(), process.stderr.read()), timeout=timeout
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise DelegationError(f"Claude Code timed out after {timeout}s") from exc
    await process.wait()

    return classify_claude_code_output(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        returncode=process.returncode,
        root=root,
    )


async def run_claude_code_in_sandbox(
    provider: SandboxProvider,
    handle: SandboxHandle,
    *,
    binary: str,
    task_text: str,
    root: str,
    allow: list[list[str]] | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run Claude Code inside an already-created sandbox and classify what it did.

    `binary` and `root` must already be paths *inside* the sandbox — the
    same contract :func:`~cuttlefish.delegate.kopicode.run_kopicode_in_sandbox`
    gives, now for this backend.
    """
    args = build_claude_code_argv(binary, task_text, allow=allow)
    try:
        result = await provider.exec(handle, args, cwd=root, timeout=timeout)
    except SandboxError as exc:
        raise DelegationError(f"Claude Code sandbox exec failed: {exc}") from exc
    return classify_claude_code_output(
        result.stdout, result.stderr, returncode=result.exit_code, root=root
    )


def classify_claude_code_output(
    stdout_text: str, stderr_text: str, *, returncode: int | None, root: str | None = None
) -> DelegationOutcome:
    """Classify one already-captured stream-json stdout/stderr pair.

    Shared by both invocation paths so parsing and classification never
    diverge between them — only how the raw output was obtained differs.
    """
    events = parse_stream_json(stdout_text)
    if not events:
        raise DelegationError(
            f"Claude Code produced no session events (exit {returncode}); "
            f"stderr: {stderr_text.strip() or '<empty>'}"
        )
    return classify_stream(events, root=root)


def parse_stream_json(stdout_text: str) -> list[Mapping[str, Any]]:
    """Every line of a stream-json stdout stream, parsed as one JSON object."""
    events: list[Mapping[str, Any]] = []
    for line in stdout_text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DelegationError(f"Claude Code emitted a non-JSON line: {line!r}") from exc
        if not isinstance(parsed, dict):
            raise DelegationError(f"Claude Code emitted a non-object JSON line: {line!r}")
        events.append(parsed)
    return events
