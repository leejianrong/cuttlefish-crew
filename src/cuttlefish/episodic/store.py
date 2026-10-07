"""The episodic journal's own SQLite store (ADR-0004, QUESTIONS.md Q7).

``.cuttlefish/episodic.db``, never a table inside satay's own ``.satay/`` database:
satay owns its own schema and migration policy, and writing into it from outside
would be an unversioned change to a schema this project doesn't own — the same
argument satay itself makes for staying core-dependency-free.

Append-only: there is no update or delete path, only :meth:`EpisodicStore.append` and
:meth:`EpisodicStore.read`. One file holds every task's events, discriminated by
``task_id`` (the satay run id, QUESTIONS.md Q6); ``seq`` is monotonic per task, 1-based.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cuttlefish.episodic.events import (
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EnvironmentPrepared,
    EnvironmentPrepareStarted,
    EventPayload,
    HandoverWritten,
    LlmCallCompleted,
    LlmCallFailed,
    RequestRaised,
    RequestResolved,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    TeamResumed,
    TeamStopped,
    ToolCallRecorded,
    decode_payload,
    encode_payload,
)
from cuttlefish.episodic.redact import Redactor

_LOG = logging.getLogger(__name__)

#: Stamped on every event this build writes. Bump when the envelope shape changes;
#: a new payload type does not require a bump, since an unrecognised one already
#: survives a round trip as ``UnknownPayload`` (events.py).
SCHEMA_VERSION = 1

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS episodic_events (
    task_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
)
"""


def _log_event(task_id: str, payload: EventPayload) -> None:
    """Project a journal event into the operational log (ADR-0029 decision 4).

    Built from the *redacted* event just written, so the log is redacted at write time and
    can never say more than the journal does. Lifecycle events are INFO, a failure or
    refusal WARNING, per-call detail DEBUG. This is a projection, not a second record: it
    holds no state and the journal stays the only transcript (ADR-0004).
    """
    role = getattr(payload, "role", None)
    extra = {"team": task_id, "role": role or ""}
    level, message = _describe(payload)
    if _LOG.isEnabledFor(level):
        name = type(payload).__name__
        _LOG.log(level, "%s: %s", name, message, extra=extra)


def _describe(payload: EventPayload) -> tuple[int, str]:
    match payload:
        case TaskSubmitted(text=text):
            return logging.INFO, f"{len(text)} chars"
        case DelegationStarted(backend=backend, root=root):
            return logging.INFO, f"backend={backend} root={root}"
        case DelegationCompleted(summary=summary, edited_paths=paths, tokens=tokens):
            return logging.INFO, f"files={len(paths)} tokens={tokens} {_clip(summary)}"
        case DelegationRefused(reason=reason):
            return logging.WARNING, reason
        case DelegationFailed(reason=reason, failure_kind=kind, record=record, tokens=tokens):
            tail = "".join([f" kind={kind}" if kind else "", f" record={record}" if record else ""])
            return logging.WARNING, f"{reason} tokens={tokens}{tail}"
        case TaskCompleted(result=result):
            return logging.INFO, _clip(result)
        case TaskFailed(error=error):
            return logging.WARNING, error
        case HandoverWritten(covers_seq_from=start, covers_seq_to=end):
            return logging.INFO, f"events {start}..{end}"
        case LlmCallCompleted(model=model, input_tokens=i, output_tokens=o):
            return logging.INFO, f"model={model} in={i} out={o}"
        case LlmCallFailed(model=model, error=error):
            return logging.WARNING, f"model={model} {error}"
        case RequestRaised(request_id=rid, kind=kind, title=title):
            return logging.INFO, f"{rid} kind={kind} {title}"
        case RequestResolved(request_id=rid, resolution=resolution, by=by):
            return logging.INFO, f"{rid} {resolution} by={by}"
        case EnvironmentPrepareStarted(ecosystem=ecosystem, commands=commands, reason=reason):
            lines = " && ".join(" ".join(c) for c in commands)
            return logging.INFO, f"{ecosystem}: {lines} ({reason})"
        case EnvironmentPrepared(ok=True, ecosystem=ecosystem, duration_s=seconds):
            return logging.INFO, f"{ecosystem}: ok in {seconds:.1f}s"
        case EnvironmentPrepared(
            ecosystem=ecosystem, exit_code=code, failure=failure, duration_s=seconds, tail=tail
        ):
            how = f"exit code {code}" if failure == "exit" else str(failure)
            return (
                logging.WARNING,
                f"{ecosystem}: failed ({how}) after {seconds:.1f}s: {_clip(tail)}".rstrip(": "),
            )
        case TeamStopped() | TeamResumed():
            return logging.INFO, ""
        case ToolCallRecorded(tool=tool, status=status, detail=detail):
            return logging.DEBUG, f"{tool} {status} {_clip(detail)}"
        case ConsentDecided(kind=kind, answer=answer, rule=rule, detail=detail):
            return logging.DEBUG, f"{kind} {answer} rule={rule} {_clip(detail)}"
        case _:
            return logging.DEBUG, ""


def _clip(text: str, limit: int = 200) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


@dataclass(frozen=True, slots=True)
class EpisodicEvent:
    """One envelope: a task's id, its position in that task's log, and its payload."""

    task_id: str
    seq: int
    schema_version: int
    ts: datetime
    payload: EventPayload


class EpisodicStore:
    """Append-only episodic journal, one SQLite file shared by every task."""

    def __init__(self, connection: sqlite3.Connection, *, redactor: Redactor | None = None) -> None:
        self._conn = connection
        self._redactor = redactor if redactor is not None else Redactor()
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    @classmethod
    def open(cls, path: Path, *, redactor: Redactor | None = None) -> EpisodicStore:
        """Open (creating if needed) the SQLite file at `path`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Manual transaction control (BEGIN IMMEDIATE in append()) needs autocommit
        # mode; sqlite3's default isolation level would open its own implicit
        # transaction around the first DML statement and conflict with it.
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        return cls(connection, redactor=redactor)

    def close(self) -> None:
        self._conn.close()

    def append(self, task_id: str, payload: EventPayload) -> EpisodicEvent:
        """Append one event to `task_id`'s log and return it as actually written.

        Returned, not just accepted, so a caller never has to guess what was
        recorded after redaction — the same discipline kopicode's own
        ``Journal.Append`` holds to.
        """
        event_type, data = encode_payload(payload)
        line = json.dumps(data, sort_keys=True)
        redacted, _ = self._redactor.scrub(line)
        ts = datetime.now(UTC)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM episodic_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            seq = int(row[0])
            self._conn.execute(
                "INSERT INTO episodic_events "
                "(task_id, seq, schema_version, event_type, ts, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, seq, SCHEMA_VERSION, event_type, ts.isoformat(), redacted),
            )
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
        recorded = decode_payload(event_type, json.loads(redacted))
        _log_event(task_id, recorded)
        return EpisodicEvent(
            task_id=task_id, seq=seq, schema_version=SCHEMA_VERSION, ts=ts, payload=recorded
        )

    def read(self, task_id: str) -> Iterator[EpisodicEvent]:
        """Yield every event for `task_id`, in seq order."""
        cursor = self._conn.execute(
            "SELECT seq, schema_version, event_type, ts, payload_json "
            "FROM episodic_events WHERE task_id = ? ORDER BY seq ASC",
            (task_id,),
        )
        for seq, schema_version, event_type, ts, payload_json in cursor:
            yield EpisodicEvent(
                task_id=task_id,
                seq=seq,
                schema_version=schema_version,
                ts=datetime.fromisoformat(ts),
                payload=decode_payload(event_type, json.loads(payload_json)),
            )
