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

A child stays resident (:class:`ServePool`, one per distinct credential set, since a child
reads its environment once) and every delegation is its own session, ended with
``session.close`` (kopicode >= v0.2.0):

* kopicode writes a session's ``session_ended`` -- the only event whose ``text`` carries the
  real failure (provider HTTP status and body, harness error) -- when the session closes, and
  ``session.close`` does that without ending the process.
* A live session holds the working tree's lock until closed, so closing is also what lets the
  next delegation on the same root start (-32005 otherwise); and a fresh session per
  delegation means one role's conversation never leaks into the next.

If a close cannot be confirmed the child is killed, never left holding a lock; the next
delegation respawns one.

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

from cuttlefish.agents.outcome import (
    ConsentDecisionRecord,
    DelegationError,
    DelegationOutcome,
)
from cuttlefish.delegate.consent import ConsentDecision, ConsentPolicy
from cuttlefish.delegate.kopicode import (
    ENV_PASSTHROUGH as KOPICODE_ENV_PASSTHROUGH,
)
from cuttlefish.delegate.kopicode import _redacted_stderr_tail, classify_stream
from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.requests import CHILD_EXITED
from cuttlefish.sandbox.provider import SandboxError
from cuttlefish.stuck import (
    SHELL_TOOL,
    SessionRecord,
    ShellResult,
    StuckDetector,
    StuckVerdict,
    threshold_from_env,
)

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
    "environment_stuck",  # cuttlefish cancelled it: N shell failures in a row on the environment
    "round_timeout",  # cuttlefish cancelled it: the round passed its wall-clock limit (ADR-0030)
    "open_failed",  # session.start refused: a bad model, a missing credential
    "protocol_error",  # a -32xxx reply, or a stop this client does not know
)

_HTTP_STATUS = re.compile(r"\bhttp (\d{3})\b", re.IGNORECASE)

#: How long a consent decision may take before this client denies on its own -- well
#: inside kopicode's fixed 60s, past which kopicode denies and drops a late reply.
DEFAULT_CONSENT_DEADLINE = 30.0

#: How long to wait, after stdin EOF, for kopicode to close its sessions and exit.
_SHUTDOWN_GRACE = 15.0

#: How long ``session.close`` may take: it queues behind the session's in-flight turn, so
#: this also bounds a cancelled turn's unwinding.
_CLOSE_GRACE = 20.0

#: Longest ``detail`` copied into a consent log line.
_LOG_DETAIL_CHARS = 200

_STDERR_CAP = 64 * 1024

Decider = Callable[[str, str], ConsentDecision | Awaitable[ConsentDecision]]

#: Puts a model's question (and the context it gave) to a person: their answer, or ``None``
#: when nobody answered, which kopicode relays to the model as "no human is present".
AskHandler = Callable[[str, str], Awaitable[str | None]]


@dataclasses.dataclass(frozen=True, slots=True)
class ConsentRecord:
    """One consent decision, for the log. ``detail`` is untrusted model output, capped."""

    session: str
    kind: str
    detail: str
    answer: str
    rule: str
    #: A person answered it (ADR-0028); journaled as the request pair instead.
    asked: bool = False


#: The most a person gets to answer when this binary has no ``--consent-timeout``: kopicode
#: v0.2.0 denies after a fixed 60s, and the answer must land before that.
UNCONFIGURABLE_WINDOW = 45.0

_TIMEOUT_FLAG_SUPPORT: dict[str, bool] = {}


_FEATURES: dict[str, frozenset[str]] = {}


async def serve_features(binary: str) -> frozenset[str]:
    """The ``features`` ``binary`` lists in ``version --json`` (kopicode v0.3.0 and later).
    Probed once per binary; any failure is the empty set, which only leaves a newer capability
    unused."""
    if binary in _FEATURES:
        return _FEATURES[binary]
    features: frozenset[str] = frozenset()
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "version",
            "--json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=merge_env(None),
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5.0)
        except TimeoutError:
            process.kill()
            await process.wait()
            output = b""
        listed = json.loads(output).get("features")
        if isinstance(listed, list):
            features = frozenset(f for f in listed if isinstance(f, str))
    except (OSError, ValueError, AttributeError):
        features = frozenset()
    _FEATURES[binary] = features
    return features


async def serve_supports_ask(binary: str) -> bool:
    """Whether ``binary`` advertises ``ask.request`` (kopicode v0.4.0 and later)."""
    return "ask.request" in await serve_features(binary)


async def serve_supports_limits(binary: str) -> bool:
    """Whether ``binary`` takes ``max_turns`` and ``token_budget`` on ``session.start``."""
    return "session.limits" in await serve_features(binary)


async def serve_supports_consent_timeout(binary: str) -> bool:
    """Whether ``binary serve`` has ``--consent-timeout`` (kopicode#169, after v0.2.0).

    Probed once per binary from ``serve --help`` and remembered; any failure to find out is
    ``False``, which only shortens the window a person gets, never lengthens it."""
    if binary in _TIMEOUT_FLAG_SUPPORT:
        return _TIMEOUT_FLAG_SUPPORT[binary]
    supported = False
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "serve",
            "--help",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=merge_env(None),
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5.0)
        except TimeoutError:
            process.kill()
            await process.wait()
            output = b""
        supported = b"consent-timeout" in output
    except (OSError, ValueError):
        supported = False
    _TIMEOUT_FLAG_SUPPORT[binary] = supported
    return supported


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
        consent_deadline: float,
        on_consent: Callable[[ConsentRecord], None],
    ) -> None:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        self._process = process
        #: session id -> who answers its consent requests; a request for any other
        #: session is denied.
        self._deciders: dict[str, Decider] = {}
        #: session id -> who answers its model's questions (``ask.request``).
        self._askers: dict[str, AskHandler] = {}
        self.loop = asyncio.get_running_loop()
        self._deadline = consent_deadline
        self._on_consent = on_consent
        self._write_lock = asyncio.Lock()
        self._next_id = 0
        self._responses: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        #: consent request id -> the task answering it; an id leaves once replied.
        self._pending_consent: dict[str, asyncio.Task[None]] = {}
        self._replied: set[str] = set()
        self._background: set[asyncio.Task[None]] = set()
        self._killed = False
        self.events: dict[str, list[Mapping[str, Any]]] = {}
        #: session id -> the consent decisions made for it so far, in order.
        self.consents: dict[str, list[ConsentRecord]] = {}
        #: session id -> what watches it for an agent stuck on its environment (ADR-0029).
        self._watches: dict[str, tuple[SessionRecord, StuckDetector]] = {}
        #: session id -> why cuttlefish cancelled it, once the detector fired.
        self.stuck: dict[str, StuckVerdict] = {}
        #: one read of a record at a time, so two checks never consume the same lines.
        self._stuck_lock = asyncio.Lock()
        self.stderr = bytearray()
        self.unparsed_lines = 0
        self._reader = asyncio.create_task(self._read_stdout(process.stdout))
        self._stderr_task = asyncio.create_task(self._read_stderr(process.stderr))

    @classmethod
    async def spawn(
        cls,
        *,
        binary: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        consent_deadline: float = DEFAULT_CONSENT_DEADLINE,
        on_consent: Callable[[ConsentRecord], None] | None = None,
        consent_timeout: float | None = None,
    ) -> ServeChild:
        flags = [] if consent_timeout is None else ["--consent-timeout", f"{int(consent_timeout)}s"]
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "serve",
                *flags,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merge_env(env, root=cwd, passthrough=KOPICODE_ENV_PASSTHROUGH),
            )
        except FileNotFoundError as exc:
            raise DelegationError(f"kopicode binary {binary!r} not found") from exc
        return cls(
            process,
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

    @property
    def alive(self) -> bool:
        return not self._killed and self._process.returncode is None and not self._reader.done()

    def register(self, session: str, decide: Decider, ask: AskHandler | None = None) -> None:
        self._deciders[session] = decide
        if ask is not None:
            self._askers[session] = ask

    def watch(self, session: str, record: SessionRecord, detector: StuckDetector) -> None:
        """Have the child cancel ``session`` if its agent is stuck on the environment."""
        if detector.enabled:
            self._watches[session] = (record, detector)

    def forget(self, session: str) -> list[Mapping[str, Any]]:
        """Drop a finished session's decider and hand back (and free) its events."""
        self._deciders.pop(session, None)
        self._askers.pop(session, None)
        self._watches.pop(session, None)
        return self.events.pop(session, [])

    def take_consents(self, session: str) -> list[ConsentRecord]:
        """Hand back (and free) the consent decisions made for a finished session."""
        return self.consents.pop(session, [])

    async def stderr_text(self) -> str:
        """What the child wrote to stderr, after giving the reader a moment to reach EOF --
        a child that dies at once has usually not been read yet when its death is noticed."""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(self._stderr_task), 2.0)
        return bytes(self.stderr).decode("utf-8", errors="replace").strip()

    def kill(self) -> None:
        # Marked first: the process's exit is only noticed on a later loop iteration, and
        # until then a pool must not hand this child to the next delegation.
        self._killed = True
        with contextlib.suppress(ProcessLookupError):
            self._process.kill()

    async def request(self, method: str, params: Mapping[str, Any]) -> asyncio.Future[Any]:
        if not self.alive:
            raise DelegationError("kopicode serve is not running")
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
            # A request a person is still being asked about has nobody left to answer to.
            for task in list(self._pending_consent.values()):
                task.cancel(CHILD_EXITED)

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
        elif method == "ask.request":
            self._start_ask(request_id, message.get("params"))
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
            if (
                event.get("kind") == "tool_result"
                and event.get("tool") == SHELL_TOOL
                and session in self._watches
                and session not in self.stuck
            ):
                task = asyncio.create_task(self._check_stuck(session))
                self._background.add(task)
                task.add_done_callback(self._background.discard)

    async def _check_stuck(self, session: str) -> None:
        """Read what the session's record gained and cancel the session if its agent has
        failed on the environment N times running. The stream has no command output, so this
        is the one place cuttlefish reads it (ADR-0029). Fails open: any trouble reading is
        "no evidence"."""
        try:
            async with self._stuck_lock:
                watch = self._watches.get(session)
                if watch is None or session in self.stuck:
                    return
                record, detector = watch
                results = await asyncio.to_thread(record.new_shell_results)
                await self._feed_stuck(session, detector, results)
        except Exception:
            _LOG.exception("stuck check for session %s failed", session)

    async def _feed_stuck(
        self, session: str, detector: StuckDetector, results: list[ShellResult]
    ) -> None:
        for result in results:
            verdict = detector.feed(result)
            if verdict is None:
                continue
            self.stuck[session] = verdict
            _LOG.warning(
                "session %s stuck on its environment after %d shell failures (%s)",
                session,
                verdict.count,
                verdict.signature,
            )
            await self.cancel_session(session)
            return

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
        elif kind == "run_shell" and not _argv_is_sh_c(fields):
            decision = ConsentDecision("deny", "not_a_sh_c_command")
        else:
            decide = self._deciders.get(session)
            try:
                if decide is None:
                    outcome: ConsentDecision | Awaitable[ConsentDecision] = ConsentDecision(
                        "deny", "unknown_session"
                    )
                else:
                    outcome = decide(kind, detail)
                if inspect.isawaitable(outcome):
                    deadline = getattr(decide, "deadline", self._deadline)
                    outcome = await asyncio.wait_for(outcome, deadline)
                decision = outcome
            except TimeoutError:
                decision = ConsentDecision("deny", "decider_timeout")
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("consent decider raised; denying")
                decision = ConsentDecision("deny", "decider_error")
        answer = decision.answer if decision.answer in ("allow", "deny") else "deny"
        record = ConsentRecord(
            session, kind, detail[:_LOG_DETAIL_CHARS], answer, decision.rule, decision.asked
        )
        if session in self._deciders:  # an unknown session has no one to collect it
            self.consents.setdefault(session, []).append(record)
        self._on_consent(record)
        await self._reply_consent(str(request_id), answer)

    def _start_ask(self, request_id: object, params: object) -> None:
        if not isinstance(request_id, str | int) or isinstance(request_id, bool):
            return
        key = str(request_id)
        task = asyncio.create_task(self._answer_ask(request_id, params))
        self._pending_consent[key] = task
        task.add_done_callback(functools.partial(self._consent_done, key))

    async def _answer_ask(self, request_id: str | int, params: object) -> None:
        """Relay the model's question to whoever registered for its session. Anything short of
        an answer is replied as unanswered, so the model carries on with "no human is present"."""
        fields = params if isinstance(params, dict) else {}
        session, question, context = (fields.get(k) for k in ("session", "question", "context"))
        text: str | None = None
        ask = self._askers.get(session) if isinstance(session, str) else None
        if ask is not None and isinstance(question, str):
            try:
                text = await ask(question, context if isinstance(context, str) else "")
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("ask handler raised; leaving the question unanswered")
        _LOG.info("ask %s session=%s", "answered" if text is not None else "unanswered", session)
        await self._reply_ask(str(request_id), text)

    async def _reply_ask(self, request_id: str, text: str | None) -> None:
        if request_id in self._replied:
            return
        self._replied.add(request_id)
        if text is None:
            await self._send({"id": request_id, "error": {"code": -32000, "message": "no answer"}})
        else:
            await self._send({"id": request_id, "result": {"text": text}})

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

    async def end_session(self, session: str, *, timeout: float | None = None) -> bool:
        """``session.close`` and wait for it: ``session_ended`` is announced first, and the
        working-tree lock is free once this returns ``True``. On any failure the child is
        killed -- a session that may still hold a lock is not worth keeping a child for."""
        try:
            pending = await self.request("session.close", {"session": session})
            response = await asyncio.wait_for(pending, _CLOSE_GRACE if timeout is None else timeout)
        except (TimeoutError, DelegationError):
            await self._kill_and_reap()
            return False
        error = response.get("error")
        if isinstance(error, dict) and error.get("code") != -32000:  # -32000: already gone
            await self._kill_and_reap()
            return False
        return True

    async def _kill_and_reap(self) -> None:
        self.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._process.wait(), 5.0)

    async def abort_session(self, session: str) -> None:
        """Cancel the in-flight turn, then close the session behind it."""
        await self.cancel_session(session)
        await self.end_session(session)

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


def _argv_is_sh_c(fields: Mapping[str, Any]) -> bool:
    """Whether a ``run_shell`` consent request is exactly ``/bin/sh -c <line>``.

    kopicode v0.3.0 sends the exact ``argv`` (and ``command``, the line) beside ``detail``, which
    is only that argv joined by spaces, so an argv of another shape would read as a line. An
    older kopicode sends neither, and ``detail`` alone is then all there is to go on."""
    argv, command = fields.get("argv"), fields.get("command")
    if argv is None and command is None:
        return True
    return (
        isinstance(argv, list)
        and len(argv) == 3
        and argv[0] == "/bin/sh"
        and argv[1] == "-c"
        and isinstance(argv[2], str)
        and (command is None or argv[2] == command)
    )


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


def _timeout_outcome(
    events: list[Mapping[str, Any]],
    timeout: float | None,
    consents: list[ConsentDecisionRecord],
) -> DelegationOutcome:
    """The round cuttlefish cancelled because it ran past its wall-clock limit. What the agent
    did before then (edits, tool calls, tokens) is kept, so the next round starts from a record
    that says so. A distinct ``failure_kind`` lets a team treat it as a checkpoint (ADR-0030)."""
    base = classify_stream(
        [e for e in events if e.get("kind") != "session_ended"]
        + [{"kind": "session_ended", "reason": "cancelled", "exit_code": 1, "text": ""}]
    )
    limit = "its time limit" if timeout is None else f"{timeout:g} seconds"
    return dataclasses.replace(
        base,
        kind="failed",
        summary="kopicode was stopped: the round ran past its time limit (round_timeout)",
        reason=f"stopped: the round ran longer than {limit}",
        failure_kind="round_timeout",
        consent_decisions=consents,
    )


def _stuck_outcome(
    outcome: DelegationOutcome, verdict: StuckVerdict, env: Mapping[str, str] | None
) -> DelegationOutcome:
    """The round cuttlefish ended because its agent kept failing on the environment: a failure
    that says so, with the last failing output as its ``detail`` (redacted here as every
    failure text is, and again at journal write)."""
    return dataclasses.replace(
        outcome,
        kind="failed",
        summary="kopicode was stopped: it kept failing on its environment (environment_stuck)",
        reason=(
            f"stopped after {verdict.count} shell commands in a row failed on the environment "
            f"(matched {verdict.signature!r})"
        ),
        failure_kind="environment_stuck",
        detail=_redacted_stderr_tail(verdict.evidence, env) or None,
    )


_RPC_FAILURE_KIND = {-32002: "open_failed", -32003: "protocol_error"}


class ServePool:
    """Resident ``kopicode serve`` children, one per (binary, project root, credential set).

    A child reads its credentials, and its shell's environment (the project's own ``.venv`` and
    ``node_modules/.bin`` on ``PATH``, V5-E4), once from the environment it is started with, so
    delegations for different projects, or with different secrets, cannot share one. A child is
    reused while it is alive and belongs to the running event loop; otherwise it is replaced.
    """

    def __init__(self) -> None:
        self._children: dict[
            tuple[str, str | None, tuple[tuple[str, str], ...], float | None], ServeChild
        ] = {}
        self._lock: asyncio.Lock | None = None

    async def child_for(
        self,
        *,
        binary: str,
        env: Mapping[str, str] | None,
        root: str | None = None,
        consent_deadline: float = DEFAULT_CONSENT_DEADLINE,
        on_consent: Callable[[ConsentRecord], None] | None = None,
        consent_timeout: float | None = None,
    ) -> ServeChild:
        loop = asyncio.get_running_loop()
        key = (binary, root, tuple(sorted((env or {}).items())), consent_timeout)
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            child = self._children.get(key)
            if child is not None and child.alive and child.loop is loop:
                return child
            if child is not None:
                child.kill()  # dead, or bound to a loop that is gone
            child = await ServeChild.spawn(
                binary=binary,
                cwd=root,
                env=env,
                consent_deadline=consent_deadline,
                on_consent=on_consent,
                consent_timeout=consent_timeout,
            )
            self._children[key] = child
            return child

    async def aclose(self) -> None:
        children, self._children = list(self._children.values()), {}
        for child in children:
            if child.loop is asyncio.get_running_loop() and child.alive:
                await child.close()
            else:
                child.kill()


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
    consent_timeout: float | None = None,
    session_id: str | None = None,
    pool: ServePool | None = None,
    process_factory: Callable[[], Awaitable[asyncio.subprocess.Process]] | None = None,
    stuck_threshold: int | None = None,
    ask: AskHandler | None = None,
    session_limits: Mapping[str, int] | None = None,
) -> DelegationOutcome:
    """Run one delegation as its own session and classify what it did.

    With a ``pool`` the session runs on a resident child; without one a child is spawned for
    this call alone and shut down after. Either way the session is closed with
    ``session.close``, so its ``session_ended`` (and failure ``text``) is in hand before the
    outcome is classified and its working tree is free for the next delegation.

    ``consent_timeout`` (seconds) is passed to a child this call spawns as ``--consent-timeout``,
    so kopicode waits that long for an answer; a decider that asks a person needs it (ADR-0028).

    ``session_limits`` (``max_turns``, ``token_budget``) go into ``session.start`` as given; the
    caller passes them only for a kopicode that advertises ``session.limits`` (v0.4.0).

    ``ask`` answers the model's own questions live (``ask_mode: "remote"``); the caller passes
    it only for a kopicode that advertises ``ask.request`` (:func:`serve_supports_ask`).

    ``stuck_threshold`` is how many shell commands in a row may fail on the environment before
    the session is cancelled (``None``: ``CUTTLEFISH_STUCK_THRESHOLD``, default 5; ``0``: never).
    The evidence is read from the session's record in ``root``, so a child that runs elsewhere
    (``process_factory``) is not watched.

    ``process_factory`` starts the ``serve`` process somewhere other than this host (a
    sandbox's streaming ``spawn``, KAN-1793); the child it yields is used for this call alone
    and closed after, so it cannot be combined with a ``pool``. ``binary`` and ``env`` then
    only name what to redact from failure text -- nothing is spawned from them.

    Same contract as :func:`~cuttlefish.delegate.kopicode.run_kopicode`: raises
    :class:`DelegationError` for anything short of a recorded turn (binary missing, the
    child dying mid-turn, a timeout); everything kopicode itself recorded is an outcome.
    """
    session = session_id or f"cuttlefish-{uuid.uuid4().hex[:12]}"
    decide: Decider = policy.decide if isinstance(policy, ConsentPolicy) else policy
    if process_factory is not None:
        if pool is not None:
            raise ValueError("a process_factory child is per-delegation; it cannot use a pool")
        try:
            process = await process_factory()
        except SandboxError as exc:
            raise DelegationError(f"kopicode sandbox spawn failed: {exc}") from exc
        child = ServeChild(
            process,
            consent_deadline=consent_deadline,
            on_consent=on_consent or _log_consent,
        )
    elif pool is not None:
        child = await pool.child_for(
            binary=binary,
            env=env,
            root=root,
            consent_deadline=consent_deadline,
            on_consent=on_consent,
            consent_timeout=consent_timeout,
        )
    else:
        child = await ServeChild.spawn(
            binary=binary,
            cwd=root,
            env=env,
            consent_deadline=consent_deadline,
            on_consent=on_consent,
            consent_timeout=consent_timeout,
        )
    timed_out = False
    child.register(session, decide, ask)
    if process_factory is None:
        threshold = threshold_from_env() if stuck_threshold is None else stuck_threshold
        child.watch(session, SessionRecord(root, session), StuckDetector(threshold))
    try:
        try:
            pending = await child.request(
                "session.start",
                {
                    "session": session,
                    "dir": root,
                    "prompt": task_text,
                    "consent_mode": "remote_interactive",
                    **({"ask_mode": "remote"} if ask is not None else {}),
                    **(session_limits or {}),
                },
            )
            response = await asyncio.wait_for(pending, timeout)
        except TimeoutError:
            await asyncio.shield(child.abort_session(session))
            timed_out = True
        except asyncio.CancelledError:
            await asyncio.shield(child.abort_session(session))
            raise
        except DelegationError as exc:
            child.kill()
            tail = await child.stderr_text()
            raise DelegationError(
                f"{exc}; stderr: {_redacted_stderr_tail(tail, env) or '<empty>'}"
            ) from exc
        else:
            if isinstance(response.get("result"), dict):
                await asyncio.shield(child.end_session(session))
    finally:
        events = child.forget(session)
        verdict = child.stuck.pop(session, None)
        consents = [
            ConsentDecisionRecord(
                r.kind, r.detail, "allow" if r.answer == "allow" else "deny", r.rule
            )
            for r in child.take_consents(session)
            if not r.asked
        ]
        if pool is None:
            await asyncio.shield(child.close())

    if process_factory is None:
        # The stream cuts each call's arguments, so a write with real content has no path in it;
        # the session's own record has it in full. Told to the classifier as the edits they are.
        known = {e.get("path") for e in events if e.get("kind") == "edit_applied"}
        written = await asyncio.to_thread(SessionRecord(root, session).written_paths)
        events = [
            *events,
            *({"kind": "edit_applied", "path": p} for p in written if p not in known),
        ]
    if timed_out:
        return _timeout_outcome(events, timeout, consents)
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
            consent_decisions=consents,
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise DelegationError("kopicode serve replied with neither result nor error")
    outcome = classify_turn(events, result, env=env)
    if verdict is not None and outcome.kind != "completed":
        outcome = _stuck_outcome(outcome, verdict, env)
    record = result.get("record")
    return dataclasses.replace(
        outcome,
        consent_decisions=consents,
        record=record if isinstance(record, str) and record else None,
    )
