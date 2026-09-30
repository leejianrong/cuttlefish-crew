"""The kopicode delegation over ``kopicode serve`` (kopicode ADR-0013, ADR-0016).

Replaces ``kopicode run --print`` for a host (unsandboxed) delegation so cuttlefish can be
the *live* consent client: kopicode asks, per shell command, and
:class:`~cuttlefish.delegate.consent.ConsentPolicy` answers -- instead of a declared
allowlist that has to guess every phrasing of a command in advance (kopicode issue #157).

Wire (``docs/kopicode-serve-protocol.md`` in kopicode): NDJSON JSON-RPC 2.0 on the child's
stdio. One reader loop demultiplexes by shape -- ``method`` + ``id`` is a request to answer
(``consent.request``), ``method`` alone is a notification (``session.event``), no
``method`` is a response. ``consent.request`` and ``session.event`` lines arrive *before*
the ``session.start`` response, because the turn is still running.

One child per delegation, deliberately, not one resident child:

* kopicode writes a session's ``session_ended`` -- the only event whose ``text`` carries the
  real failure (provider HTTP status and body, harness error) -- when the session *closes*,
  and there is no ``session.close``; closing means stdin EOF and process shutdown. A resident
  child would never surface it.
* A live session holds the working tree's lock until then, so a second delegation on the same
  root would be refused (-32005), and reusing the session instead would leak one role's
  conversation into the next.

The client itself (:class:`ServeChild`) is not tied to that choice: it drives any number of
sessions, so a resident backend is a change to its caller once kopicode has a
``session.close``.

**Containment.** kopicode does not sandbox what the model's shell does (its ADR-0008/0011);
approving a command here is a decision, not a boundary. This transport is only used for a
delegation with no sandbox provider -- a sandboxed one keeps ``run --print`` inside the
container, where the sandbox is the containment.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import inspect
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.consent import ConsentDecision, ConsentPolicy
from cuttlefish.delegate.kopicode import _redacted_stderr_tail, classify_stream
from cuttlefish.delegate.subprocess_env import merge_env

_LOG = logging.getLogger("cuttlefish.delegate.consent")

#: Why a failed kopicode delegation failed -- ``DelegationOutcome.failure_kind``.
FAILURE_KINDS = (
    "provider_auth",  # exit 3, HTTP 401/403: an expired, revoked or wrong key
    "provider_credits",  # exit 3, HTTP 402: the account is out of credit
    "provider_rate_limit",  # exit 3, HTTP 429 that survived kopicode's retries
    "provider_outage",  # exit 3, HTTP 5xx
    "provider_other",  # exit 3, anything else (transport failure, other 4xx)
    "harness_error",  # exit 4, stop=error: kopicode itself broke
    "max_turns",
    "verification_failed",
    "budget_exhausted",
    "cancelled",
    "open_failed",  # session.start refused: a bad model, a missing credential
    "protocol_error",  # a -32xxx reply, or a stop this client does not know
)

_HTTP_STATUS = re.compile(r"\bhttp (\d{3})\b", re.IGNORECASE)

#: How long a consent decision may take before this client denies on its own -- well
#: inside kopicode's fixed 60s, past which kopicode denies and drops a late reply.
DEFAULT_CONSENT_DEADLINE = 30.0

#: How long to wait, after stdin EOF, for kopicode to close its sessions and exit.
_SHUTDOWN_GRACE = 15.0

#: Longest ``detail`` copied into a consent log line.
_LOG_DETAIL_CHARS = 200

_STDERR_CAP = 64 * 1024

Decider = Callable[[str, str], ConsentDecision | Awaitable[ConsentDecision]]


@dataclasses.dataclass(frozen=True, slots=True)
class ConsentRecord:
    """One consent decision, for the log. ``detail`` is untrusted model output, capped."""

    session: str
    kind: str
    detail: str
    answer: str
    rule: str


def failure_kind_for(stop: object, exit_code: object, text: str) -> str:
    """The distinct failure a non-completed stop is, from ``stop``, ``exit_code`` and the
    ``session_ended`` ``text`` (kopicode's docs/run-print-protocol.md vocabulary)."""
    if stop in ("cancelled", "verification_failed", "budget_exhausted", "max_turns"):
        assert isinstance(stop, str)
        return stop
    if stop == "error" and exit_code == 3:
        match = _HTTP_STATUS.search(text)
        status = int(match.group(1)) if match else 0
        if status in (401, 403):
            return "provider_auth"
        if status == 402:
            return "provider_credits"
        if status == 429:
            return "provider_rate_limit"
        if 500 <= status < 600:
            return "provider_outage"
        return "provider_other"
    if stop == "error" and exit_code == 4:
        return "harness_error"
    return "protocol_error"


class ServeChild:
    """One ``kopicode serve`` process and its demultiplexing reader loop."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        decide: Decider,
        consent_deadline: float,
        on_consent: Callable[[ConsentRecord], None],
    ) -> None:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        self._process = process
        self._decide = decide
        self._deadline = consent_deadline
        self._on_consent = on_consent
        self._write_lock = asyncio.Lock()
        self._next_id = 0
        self._responses: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        #: consent request id -> the task answering it; an id leaves once replied.
        self._pending_consent: dict[str, asyncio.Task[None]] = {}
        self._replied: set[str] = set()
        self._background: set[asyncio.Task[None]] = set()
        self.events: dict[str, list[Mapping[str, Any]]] = {}
        self.stderr = bytearray()
        self.unparsed_lines = 0
        self._reader = asyncio.create_task(self._read_stdout(process.stdout))
        self._stderr_task = asyncio.create_task(self._read_stderr(process.stderr))

    @classmethod
    async def spawn(
        cls,
        *,
        binary: str,
        cwd: str,
        env: Mapping[str, str] | None,
        decide: Decider,
        consent_deadline: float = DEFAULT_CONSENT_DEADLINE,
        on_consent: Callable[[ConsentRecord], None] | None = None,
    ) -> ServeChild:
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "serve",
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merge_env(env),
            )
        except FileNotFoundError as exc:
            raise DelegationError(f"kopicode binary {binary!r} not found") from exc
        return cls(
            process,
            decide=decide,
            consent_deadline=consent_deadline,
            on_consent=on_consent or _log_consent,
        )

    # -- outbound ---------------------------------------------------------------------

    async def _send(self, message: Mapping[str, Any]) -> None:
        line = json.dumps({"jsonrpc": "2.0", **message}, separators=(",", ":")) + "\n"
        stdin = self._process.stdin
        assert stdin is not None
        async with self._write_lock:
            try:
                stdin.write(line.encode())
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the child is gone; the reader's EOF handling reports it

    async def request(self, method: str, params: Mapping[str, Any]) -> asyncio.Future[Any]:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._responses[request_id] = future
        await self._send({"id": request_id, "method": method, "params": params})
        return future

    async def _reply_consent(self, request_id: str, answer: str) -> None:
        if request_id in self._replied:
            return
        self._replied.add(request_id)
        await self._send({"id": request_id, "result": {"answer": answer}})

    # -- inbound ----------------------------------------------------------------------

    async def _read_stderr(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(4096):
            room = _STDERR_CAP - len(self.stderr)
            if room > 0:
                self.stderr += chunk[:room]

    async def _read_stdout(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                try:
                    raw = await stream.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    # kopicode never truncates, so one line can exceed the reader's
                    # limit; consume it rather than lose the stream.
                    self.unparsed_lines += 1
                    continue
                if not raw:
                    return
                self._dispatch(raw)
        finally:
            gone = DelegationError("kopicode serve exited before answering")
            for future in self._responses.values():
                if not future.done():
                    future.set_exception(gone)

    def _dispatch(self, raw: bytes) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self.unparsed_lines += 1
            return
        if not isinstance(message, dict):
            self.unparsed_lines += 1
            return
        method = message.get("method")
        request_id = message.get("id")
        if method is None:  # a response
            future = self._responses.pop(request_id, None) if isinstance(request_id, int) else None
            if future is not None and not future.done():
                future.set_result(message)
        elif request_id is None:  # a notification
            if method == "session.event":
                self._record_event(message.get("params"))
        elif method == "consent.request":
            self._start_consent(request_id, message.get("params"))
        else:  # a server request this client does not implement
            refusal = asyncio.create_task(
                self._send({"id": request_id, "error": {"code": -32601, "message": method}})
            )
            self._background.add(refusal)
            refusal.add_done_callback(self._background.discard)

    def _record_event(self, params: object) -> None:
        if not isinstance(params, dict):
            return
        session, event = params.get("session"), params.get("event")
        if isinstance(session, str) and isinstance(event, dict):
            self.events.setdefault(session, []).append(event)

    def _start_consent(self, request_id: object, params: object) -> None:
        if not isinstance(request_id, str | int) or isinstance(request_id, bool):
            return  # no usable id to echo, so nothing to answer; kopicode's timeout denies
        key = str(request_id)
        task = asyncio.create_task(self._answer_consent(request_id, params))
        self._pending_consent[key] = task
        task.add_done_callback(functools.partial(self._consent_done, key))

    def _consent_done(self, key: str, _task: asyncio.Task[None]) -> None:
        self._pending_consent.pop(key, None)

    async def _answer_consent(self, request_id: str | int, params: object) -> None:
        fields = params if isinstance(params, dict) else {}
        session, kind, detail = (fields.get(k) for k in ("session", "kind", "detail"))
        if not (isinstance(session, str) and isinstance(kind, str) and isinstance(detail, str)):
            decision = ConsentDecision("deny", "malformed_request")
            session, kind, detail = str(session), str(kind), str(detail)
        else:
            try:
                outcome = self._decide(kind, detail)
                if inspect.isawaitable(outcome):
                    outcome = await asyncio.wait_for(outcome, self._deadline)
                decision = outcome
            except TimeoutError:
                decision = ConsentDecision("deny", "decider_timeout")
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("consent decider raised; denying")
                decision = ConsentDecision("deny", "decider_error")
        answer = decision.answer if decision.answer in ("allow", "deny") else "deny"
        self._on_consent(
            ConsentRecord(session, kind, detail[:_LOG_DETAIL_CHARS], answer, decision.rule)
        )
        await self._reply_consent(str(request_id), answer)

    # -- lifecycle --------------------------------------------------------------------

    async def cancel_session(self, session: str) -> None:
        """Cancel the in-flight turn, and deny every consent still outstanding so a blocked
        turn is released at once rather than after kopicode's own 60s."""
        for key, task in list(self._pending_consent.items()):
            task.cancel()
            await self._reply_consent(key, "deny")
            _LOG.info("consent %s denied: session cancelled", key)
        await self._send(
            {"id": self._alloc_id(), "method": "session.cancel", "params": {"session": session}}
        )

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def close(self) -> None:
        """Close stdin (kopicode ends every open session, writing ``session_ended``), drain
        what remains, and reap the child -- killing it if it will not exit."""
        stdin = self._process.stdin
        assert stdin is not None
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            stdin.close()
        try:
            await asyncio.wait_for(self._process.wait(), _SHUTDOWN_GRACE)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()
        for task in (self._reader, self._stderr_task):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 5.0)
        for task in list(self._pending_consent.values()):
            task.cancel()


def _log_consent(record: ConsentRecord) -> None:
    _LOG.info(
        "consent %s session=%s kind=%s rule=%s detail=%r",
        record.answer,
        record.session,
        record.kind,
        record.rule,
        record.detail,
    )


def _session_ended_text(events: list[Mapping[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("kind") == "session_ended":
            text = event.get("text")
            return text if isinstance(text, str) else ""
    return ""


def classify_turn(
    events: list[Mapping[str, Any]],
    result: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None,
) -> DelegationOutcome:
    """One turn's outcome: the events the session emitted plus the ``session.start``
    ``result`` (``stop``/``exit_code``).

    ``classify_stream`` does the event work (edits, denials, tool calls, tokens) exactly as
    for ``run --print``; this adds what serve alone gives -- a distinct ``failure_kind`` --
    and stops an edit that landed from hiding a failed stop.
    """
    stop, exit_code = result.get("stop"), result.get("exit_code")
    text = _session_ended_text(events)
    ended = [e for e in events if e.get("kind") != "session_ended"]
    ended.append({"kind": "session_ended", "reason": stop, "exit_code": exit_code, "text": text})
    base = classify_stream(ended)
    if exit_code == 0 and stop == "completed":
        return base
    if base.kind == "refused":
        return base
    kind = failure_kind_for(stop, exit_code, text)
    reason = f"stop={stop} exit_code={exit_code}"
    if text:
        reason += f"; {_redacted_stderr_tail(text, env)}"
    return dataclasses.replace(
        base,
        kind="failed",
        summary=f"kopicode did not finish cleanly ({kind})",
        reason=reason,
        failure_kind=kind,
    )


_RPC_FAILURE_KIND = {-32002: "open_failed", -32003: "protocol_error"}


async def run_kopicode_serve(
    *,
    binary: str,
    task_text: str,
    root: str,
    policy: ConsentPolicy | Decider,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    consent_deadline: float = DEFAULT_CONSENT_DEADLINE,
    on_consent: Callable[[ConsentRecord], None] | None = None,
    session_id: str | None = None,
) -> DelegationOutcome:
    """Run one delegation through a fresh ``kopicode serve`` and classify what it did.

    Same contract as :func:`~cuttlefish.delegate.kopicode.run_kopicode`: raises
    :class:`DelegationError` for anything short of a recorded turn (binary missing, the
    child dying mid-turn, a timeout); everything kopicode itself recorded is an outcome.
    """
    session = session_id or f"cuttlefish-{uuid.uuid4().hex[:12]}"
    decide: Decider = policy.decide if isinstance(policy, ConsentPolicy) else policy
    child = await ServeChild.spawn(
        binary=binary,
        cwd=root,
        env=env,
        decide=decide,
        consent_deadline=consent_deadline,
        on_consent=on_consent,
    )
    try:
        pending = await child.request(
            "session.start",
            {
                "session": session,
                "dir": root,
                "prompt": task_text,
                "consent_mode": "remote_interactive",
            },
        )
        try:
            response = await asyncio.wait_for(pending, timeout)
        except TimeoutError as exc:
            await child.cancel_session(session)
            raise DelegationError(f"kopicode timed out after {timeout}s") from exc
        except asyncio.CancelledError:
            await asyncio.shield(child.cancel_session(session))
            raise
        except DelegationError as exc:
            tail = bytes(child.stderr).decode("utf-8", errors="replace").strip()
            raise DelegationError(
                f"{exc}; stderr: {_redacted_stderr_tail(tail, env) or '<empty>'}"
            ) from exc
    finally:
        await asyncio.shield(child.close())

    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        kind = (
            _RPC_FAILURE_KIND.get(code, "protocol_error")
            if isinstance(code, int)
            else ("protocol_error")
        )
        message = error.get("message")
        detail = _redacted_stderr_tail(str(message), env)
        return DelegationOutcome(
            kind="failed",
            summary=f"kopicode serve refused the session ({kind})",
            reason=f"rpc error {code}: {detail}",
            failure_kind=kind,
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise DelegationError("kopicode serve replied with neither result nor error")
    return classify_turn(child.events.get(session, []), result, env=env)
