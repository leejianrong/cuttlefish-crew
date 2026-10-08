"""Shared pytest configuration.

Registers satay's own testing fixtures (``ManualClock``, ``SeededRng``,
``FaultInjector``, temp data dirs) as a plugin — the same seam satay-runtime's own
test suite is driven through (docs/PLAN.md "Testing approach").

Also implements the ``requires_kopicode``, ``requires_live_credential``,
``requires_e2b_credential``, ``requires_docker``, ``requires_claude_code``,
and ``requires_claude_code_live`` markers (all declared in pyproject.toml):
the kopicode delegation is tested against kopicode's own headless surface
directly, never a mock, so a test needing the real binary or a real model
credential skips itself, rather than fails, when either is missing — CI's
separate ``kopicode-integration`` job builds the binary but has no live
credential, so ``requires_live_credential`` tests skip there too; both run
locally once ``.env`` (or the shell) has a real key. ``requires_e2b_credential``
is never run in CI at all (docs/SLICES.md V2 test plan, the same cost-bearing
posture kopicode's own ``make bench`` takes) — it only runs locally, and only
once an operator has deliberately set a real ``E2B_API_KEY``.
``requires_docker`` needs no credential and costs nothing (ADR-0002's
2026-08-26 addendum) — it self-skips only on a host with no local Docker
daemon, which GitHub's own `ubuntu-latest` CI runners already have, so those
tests run for real there too, unlike the E2B ones.

``requires_claude_code`` (ADR-0005) mirrors ``requires_kopicode``: free,
binary-on-PATH only, so CI (which never installs `claude`) skips it the same
way it skips a missing kopicode. ``requires_claude_code_live`` is different in
kind from every other credential marker here: Claude Code's own headless
auth can be an OAuth session with no environment variable to detect at all
(verified live, 2026-09-20 — this build's own `claude` authenticates with no
`ANTHROPIC_API_KEY` set), so presence can't be inferred the way
``requires_live_credential``/``requires_e2b_credential`` infer it from an env
var. It costs real money on every run once enabled, the same posture
``requires_e2b_credential`` takes, so it is never inferred from environment
state at all — only ``CUTTLEFISH_TEST_CLAUDE_CODE_LIVE=1``, set deliberately,
turns it on.

``requires_codex``/``requires_codex_live`` (ADR-0018) mirror
``requires_claude_code``/``requires_claude_code_live`` exactly, for the
identical reason: Codex's own headless auth is a persisted
``~/.codex/auth.json`` (ChatGPT OAuth or an explicit API-key login), not an
environment variable ``codex exec`` reads at invocation time (verified live,
2026-09-28 — see ``cuttlefish.delegate.codex``'s own module doc comment), so
presence can't be inferred from env state either. Only
``CUTTLEFISH_TEST_CODEX_LIVE=1``, set deliberately, turns on the paid tests.

Tests marked ``requires_live_credential`` (a real kopicode run against a real model) are opt-in too:
only ``CUTTLEFISH_TEST_LIVE=1`` (``make test-live``) runs them, whatever ``.env`` holds.

``load_dotenv()`` runs once here, at collection time, for the same reason
``cli.py`` loads it once at import rather than per call: a test file that only
imports ``cuttlefish.delegate`` never imports ``cli``, so nothing else in the test
session would otherwise load ``.env`` at all.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Sequence

import pytest
from dotenv import load_dotenv

pytest_plugins = ["satay.testing.fixtures"]

load_dotenv()


@pytest.fixture(autouse=True)
def _let_the_fake_agent_see_its_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent is given an allowlisted environment (V5-E4), so the scripted fake `kopicode
    serve` would not see the `FAKE_KOPICODE_*` variables that tell it what to do. Name them in
    the operator's passthrough, as a person with a custom setting would; a test of the
    allowlist itself sets its own value."""
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_KOPICODE_*")


@pytest.fixture(autouse=True)
async def _end_resident_kopicode_children() -> AsyncIterator[None]:
    """A test that drives `cli._run` directly bypasses `cli.main`'s cleanup of the shared
    `kopicode serve` pool; end whatever it left so no child outlives its event loop."""
    yield
    from cuttlefish.agents.kopicode import close_shared_pool

    await close_shared_pool()


def pytest_collection_modifyitems(items: Sequence[pytest.Item]) -> None:
    skip_kopicode = pytest.mark.skip(
        reason="kopicode is not on PATH (docs/PLAN.md Testing approach)"
    )
    has_kopicode = shutil.which("kopicode") is not None

    # A real model costs real money, and a key in `.env` used to be enough to spend it on every
    # `make test-all`. Like the Claude Code and Codex live tests, it is opt-in: `make test-live`.
    skip_credential = pytest.mark.skip(
        reason="CUTTLEFISH_TEST_LIVE=1 not set -- a real model costs money, opt-in only "
        "(make test-live; needs OPENROUTER_API_KEY or ANTHROPIC_API_KEY)"
    )
    has_credential = os.environ.get("CUTTLEFISH_TEST_LIVE") == "1" and bool(
        os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    )

    skip_e2b = pytest.mark.skip(reason="no live E2B_API_KEY available (a real one starts e2b_)")
    # E2B rejects a key without its ``e2b_`` prefix with a 401, so a placeholder in .env must
    # skip the live test rather than fail it.
    has_e2b_credential = os.environ.get("E2B_API_KEY", "").startswith("e2b_")

    skip_docker = pytest.mark.skip(reason="no docker binary on PATH")
    has_docker = shutil.which("docker") is not None

    skip_claude_code = pytest.mark.skip(reason="claude is not on PATH (ADR-0005)")
    has_claude_code = shutil.which("claude") is not None

    skip_claude_code_live = pytest.mark.skip(
        reason="CUTTLEFISH_TEST_CLAUDE_CODE_LIVE=1 not set -- costs real money, opt-in only"
    )
    claude_code_live_enabled = os.environ.get("CUTTLEFISH_TEST_CLAUDE_CODE_LIVE") == "1"

    skip_codex = pytest.mark.skip(reason="codex is not on PATH (ADR-0018)")
    has_codex = shutil.which("codex") is not None

    skip_codex_live = pytest.mark.skip(
        reason="CUTTLEFISH_TEST_CODEX_LIVE=1 not set -- costs real money, opt-in only"
    )
    codex_live_enabled = os.environ.get("CUTTLEFISH_TEST_CODEX_LIVE") == "1"

    for item in items:
        if not has_kopicode and item.get_closest_marker("requires_kopicode") is not None:
            item.add_marker(skip_kopicode)
        if not has_credential and item.get_closest_marker("requires_live_credential") is not None:
            item.add_marker(skip_credential)
        e2b_marker = item.get_closest_marker("requires_e2b_credential")
        if not has_e2b_credential and e2b_marker is not None:
            item.add_marker(skip_e2b)
        if not has_docker and item.get_closest_marker("requires_docker") is not None:
            item.add_marker(skip_docker)
        if not has_claude_code and item.get_closest_marker("requires_claude_code") is not None:
            item.add_marker(skip_claude_code)
        claude_live_marker = item.get_closest_marker("requires_claude_code_live")
        if not claude_code_live_enabled and claude_live_marker is not None:
            item.add_marker(skip_claude_code_live)
        if not has_codex and item.get_closest_marker("requires_codex") is not None:
            item.add_marker(skip_codex)
        codex_live_marker = item.get_closest_marker("requires_codex_live")
        if not codex_live_enabled and codex_live_marker is not None:
            item.add_marker(skip_codex_live)
