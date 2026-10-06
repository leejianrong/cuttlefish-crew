"""Real kopicode: the ``--consent-timeout`` flag a held request depends on (ADR-0028, V4-H5).

No model credential is spent. Needs a kopicode that has ``serve --consent-timeout``
(kopicode#169, released in v0.3.0; CI builds kopicode from main, so it is there). On v0.2.0
the probe is correctly ``False`` and the first test would fail: that is the signal to read
ADR-0028's 45-second fallback, not a bug here.
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from cuttlefish.delegate import kopicode_serve
from cuttlefish.delegate.kopicode_serve import ServeChild, serve_supports_consent_timeout


@pytest.mark.requires_kopicode
async def test_the_probe_finds_the_flag_in_the_real_binary() -> None:
    kopicode_serve._TIMEOUT_FLAG_SUPPORT.pop("kopicode", None)
    assert await serve_supports_consent_timeout("kopicode") is True


@pytest.mark.requires_kopicode
async def test_the_probe_agrees_with_what_the_binary_reports_about_itself() -> None:
    """``kopicode version --json`` lists ``consent_timeout.flag`` when the flag exists
    (kopicode#175); the help-text probe must say the same."""
    reported = subprocess.run(
        ["kopicode", "version", "--json"], check=True, capture_output=True, text=True
    )
    features = json.loads(reported.stdout)["features"]
    kopicode_serve._TIMEOUT_FLAG_SUPPORT.pop("kopicode", None)
    assert await serve_supports_consent_timeout("kopicode") is ("consent_timeout.flag" in features)


@pytest.mark.requires_kopicode
@pytest.mark.parametrize("consent_timeout", [None, 90.0])
async def test_a_serve_child_starts_with_and_without_the_flag(
    consent_timeout: float | None,
) -> None:
    """A bad flag value makes kopicode exit at once, so a child still alive after a moment
    accepted ``--consent-timeout 90s`` (and a plain ``serve`` is unchanged)."""
    child = await ServeChild.spawn(binary="kopicode", consent_timeout=consent_timeout)
    try:
        await asyncio.sleep(1.0)
        assert child.alive, await child.stderr_text()
    finally:
        await child.close()
