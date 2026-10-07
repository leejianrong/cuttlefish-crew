"""Episodic memory: a durable, tagged-union event log in its own SQLite store (ADR-0004).

See ``cuttlefish.episodic.events`` for the tagged union and ``cuttlefish.episodic.store``
for the append-only journal it's written to.
"""

from __future__ import annotations

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EventPayload,
    HandoverWritten,
    LlmCallCompleted,
    LlmCallFailed,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    TeamResumed,
    TeamStopped,
    UnknownPayload,
    decode_payload,
    encode_payload,
)
from cuttlefish.episodic.redact import Redactor
from cuttlefish.episodic.store import EpisodicEvent, EpisodicStore

__all__ = [
    "DelegationCompleted",
    "DelegationFailed",
    "DelegationRefused",
    "DelegationStarted",
    "EpisodicEvent",
    "EpisodicStore",
    "EventPayload",
    "HandoverWritten",
    "LlmCallCompleted",
    "LlmCallFailed",
    "Redactor",
    "TaskCompleted",
    "TaskFailed",
    "TaskSubmitted",
    "TeamResumed",
    "TeamStopped",
    "UnknownPayload",
    "decode_payload",
    "encode_payload",
]
