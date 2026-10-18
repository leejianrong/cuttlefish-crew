"""Unit: `--tailscale-https` (ADR-0041, CUT-128). No real `tailscale` binary is involved."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.cli import _resolve_tailnet_name, _tailscale_https_notes
from cuttlefish.fleet.relay_link import RelaySettings
from cuttlefish.fleet.server import run_daemon


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    return run


def test_the_name_is_the_first_cert_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"CertDomains": ["box.tail1234.ts.net"]})
    monkeypatch.setattr(subprocess, "run", _fake_run(0, body))
    assert _resolve_tailnet_name() == "box.tail1234.ts.net"


@pytest.mark.parametrize("body", [{}, {"CertDomains": None}, {"CertDomains": []}])
def test_https_off_says_where_to_turn_it_on(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, json.dumps(body)))
    with pytest.raises(RuntimeError, match=r"HTTPS Certificates.*certificate logs"):
        _resolve_tailnet_name()


def test_a_missing_binary_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="PATH"):
        _resolve_tailnet_name()


def test_the_notes_print_commands_and_run_none_of_them() -> None:
    text = _tailscale_https_notes("box.tail1234.ts.net", 8420, 9000)
    assert "tailscale serve --bg --https=443 http://127.0.0.1:8420" in text
    assert "tailscale serve --bg --https=8443 http://127.0.0.1:9000" in text
    assert "https://box.tail1234.ts.net/" in text


def test_the_browser_gets_the_public_relay_address(tmp_path: Path) -> None:
    plain = RelaySettings("ws://127.0.0.1:9000", tmp_path / "k")
    assert plain.browser_url == "ws://127.0.0.1:9000"
    public = RelaySettings("ws://127.0.0.1:9000", tmp_path / "k", public_url="wss://b.ts.net:8443")
    assert public.browser_url == "wss://b.ts.net:8443"


def test_run_daemon_refuses_a_tailnet_name_without_a_password() -> None:
    with pytest.raises(ValueError, match="CUTTLEFISH_SERVE_PASSWORD"):
        asyncio.run(run_daemon(None, tailnet_name="b.ts.net"))  # type: ignore[arg-type]


def test_run_daemon_refuses_a_tailnet_name_on_a_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(
            run_daemon(None, host="0.0.0.0", password="x", tailnet_name="b.ts.net")  # type: ignore[arg-type]
        )
