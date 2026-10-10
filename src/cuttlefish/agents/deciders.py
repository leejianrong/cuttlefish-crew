"""Who answers a backend's live command approvals (ADR-0028), shared by every backend that can
hold a request open (Codex over ``app-server``, V4-M; Claude Code over stream-json, V4-K)."""

from __future__ import annotations

from collections.abc import Sequence

from cuttlefish.agents.outcome import DelegationError
from cuttlefish.delegate.consent import ConsentPolicy, ConsentPolicyError
from cuttlefish.delegate.kopicode_serve import Decider
from cuttlefish.requests import AskingDecider, ShellAsker


def command_decider(
    allow: Sequence[Sequence[str]] | None, mode: str, asker: ShellAsker | None
) -> Decider:
    """The role's policy, and a person for a command no rule approves when ``asker`` is given
    (never in Auto or for a read-only role)."""
    try:
        policy = ConsentPolicy(allow, auto=mode == "auto")
    except ConsentPolicyError as exc:
        raise DelegationError(f"unusable shell allowlist: {exc}") from exc
    if asker is None or mode in ("auto", "read-only"):
        return policy.decide
    return AskingDecider(allow, asker, window_s=asker.window_s)
