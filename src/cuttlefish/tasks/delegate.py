"""The coding-agent delegation, wrapped as a satay task (ADR-0001, ADR-0005).

``side_effect=True``: invoking a coding-agent backend genuinely has real-world
effects (it can edit files, and run shell commands once a policy allows it,
ADR-0002's addendum), and satay's own execution guarantees exist precisely to
make a retried side-effecting call safe rather than repeated — see
``cuttlefish.episodic.store``'s module docstring for the one race this
doesn't close.

Which backend actually runs a given call, and whether it runs inside a
sandbox, are both resolved from the process-wide ``runtime.Runtime``
(``cuttlefish.runtime``) at call time, not baked into this task's own
identity. Everything backend-specific — its own policy mechanics, its own
sandbox mounts and credential forwarding — lives on the
:class:`~cuttlefish.agents.backend.AgentBackend` itself
(``cuttlefish.agents``), not here. This replaces V1/V2's kopicode-hardcoded
``delegate_to_kopicode`` (ADR-0005).

``project``/``secret_names`` (ADR-0006) resolve into an actual secrets
mapping *inside* this call, never before or after it: ``secret_names`` (a
list of names) is a safe satay task argument, durably journaled like any
other, but the *values* those names resolve to are not — they're a local
variable here, handed straight to ``backend.delegate()`` and never returned
from this task or passed as another task's argument. See ADR-0006 for why
that boundary matters (satay's own journal has no redaction for a value it
was never told to look for).
"""

from __future__ import annotations

from typing import Any

import satay

from cuttlefish import runtime
from cuttlefish.agents.outcome import DelegationOutcome
from cuttlefish.agents.registry import resolve_backend
from cuttlefish.delegate.presets import read_only_allow, resolve_allow
from cuttlefish.permissions import READ_ONLY
from cuttlefish.secrets.store import DEFAULT_PROJECT


@satay.task(side_effect=True)
async def delegate_to_agent_backend(
    task_text: str,
    root: str,
    allow: list[list[str]] | None = None,
    project: str = DEFAULT_PROJECT,
    secret_names: list[str] | None = None,
    agent_backend: str | None = None,
    access: str | None = None,
    presets: list[str] | None = None,
) -> DelegationOutcome:
    """``agent_backend`` (KAN-1809) is a per-call override of the runtime's default;
    callers pass it only when set, so a call that names none has the identical
    recorded arguments it always had (replay-safe for an in-flight run).

    ``allow`` is what the operator *declared*; the built-in presets are added here, inside
    the side-effecting task, so the recorded arguments stay the raw declaration (V4-A).

    ``access`` (V4-B/V4-C, ADR-0024/0025) is the effective permission level, passed only when
    it is not ``standard`` so every other call's recorded arguments are unchanged:
    ``read-only`` is inspection-only commands, ``ask-first`` is no command at all,
    ``auto`` is the presets for backends that need a list (the backend widens it itself).
    ``auto`` and ``read-only`` also reach the backend as ``mode``.

    ``presets`` (V4-F) is the project's chosen command groups, passed only when it differs from
    the defaults, for the same replay reason."""
    runtime_ = runtime.current()
    backend = resolve_backend(
        agent_backend or runtime_.agent_backend,
        kopicode_binary=runtime_.kopicode_binary,
        claude_code_binary=runtime_.claude_code_binary,
        codex_binary=runtime_.codex_binary,
    )
    names = sorted(set(secret_names or []) | set(backend.CREDENTIAL_ENV_VARS))
    secrets_store = runtime_.secrets_store
    resolved_secrets = secrets_store.resolve(project, names) if secrets_store is not None else {}
    if access == READ_ONLY:
        effective_allow = read_only_allow()
    elif access == "ask-first":
        effective_allow = []
    else:
        effective_allow = resolve_allow(allow, presets)
    mode_kwargs: dict[str, Any] = {"mode": access} if access in ("auto", READ_ONLY) else {}
    # A person can be asked (ADR-0028) only inside the fleet daemon, only of kopicode (the one
    # backend that can pause for a consent), and never for Auto or a read-only role. It rides
    # the runtime, not the task arguments, so recorded calls and replay are unchanged.
    requests = runtime_.requests
    if requests is not None and backend.NAME == "kopicode" and access not in ("auto", READ_ONLY):
        mode_kwargs["asker"] = requests.asker(
            role=requests.role_for(task_text), backend=backend.NAME
        )
    return await backend.delegate(
        task_text=task_text,
        root=root,
        allow=effective_allow,
        secrets=resolved_secrets,
        sandbox_provider=runtime_.sandbox_provider,
        **mode_kwargs,
    )
