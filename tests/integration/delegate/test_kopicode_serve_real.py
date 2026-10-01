"""Integration tests: the serve transport against the real kopicode binary.

No model credential is spent: an invalid key is one of kopicode's real, documented
outcomes, and it exercises the whole real wire -- session.start with a required
``consent_mode``, session.event notifications, the turn response, and the shutdown-time
``session_ended`` whose ``text`` carries the provider's real HTTP status. Needs a kopicode
built from a commit that has ``serve``'s consent mode (kopicode ADR-0016) and outbound
network access to the provider.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cuttlefish.delegate.consent import ConsentPolicy
from cuttlefish.delegate.kopicode_serve import ServePool, run_kopicode_serve

_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


@pytest.mark.requires_kopicode
async def test_an_invalid_key_is_a_distinct_provider_auth_failure(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)],
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    outcome = await run_kopicode_serve(
        binary="kopicode",
        task_text="add a .gitignore entry",
        root=str(tmp_path),
        policy=ConsentPolicy(),
        env={"OPENROUTER_API_KEY": _INVALID_OPENROUTER_KEY},
        timeout=120,
    )

    assert outcome.kind == "failed"
    assert outcome.failure_kind == "provider_auth"
    assert outcome.reason is not None and "401" in outcome.reason
    assert _INVALID_OPENROUTER_KEY not in outcome.reason


@pytest.mark.requires_kopicode
async def test_back_to_back_delegations_on_one_tree_share_a_child_via_session_close(
    tmp_path: Path,
) -> None:
    """The second delegation on the same directory only starts if the first session's
    `session.close` really released the working-tree lock (-32005 otherwise)."""
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)],
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    pool = ServePool()
    try:
        outcomes = [
            await run_kopicode_serve(
                binary="kopicode",
                task_text="add a .gitignore entry",
                root=str(tmp_path),
                policy=ConsentPolicy(),
                env={"OPENROUTER_API_KEY": _INVALID_OPENROUTER_KEY},
                timeout=120,
                pool=pool,
            )
            for _ in range(2)
        ]
    finally:
        await pool.aclose()

    assert [o.failure_kind for o in outcomes] == ["provider_auth", "provider_auth"]
    assert all(o.reason is not None and "401" in o.reason for o in outcomes)
