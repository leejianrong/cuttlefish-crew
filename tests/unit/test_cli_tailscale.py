"""Unit: `cuttlefish.cli._resolve_tailscale_host` (ADR-0013, KAN-1708) -- every
real failure mode of shelling out to `tailscale ip -4` gets a clear,
actionable `RuntimeError`, not a bare `CalledProcessError`/`FileNotFoundError`.
No real `tailscale` binary is involved -- `subprocess.run` is monkeypatched.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from cuttlefish.cli import _resolve_tailscale_host


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return run


def test_returns_the_stripped_ip_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, stdout="100.64.1.2\n"))
    assert _resolve_tailscale_host() == "100.64.1.2"


def test_raises_a_clear_error_when_the_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="PATH"):
        _resolve_tailscale_host()


def test_raises_a_clear_error_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=5)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="timed out"):
        _resolve_tailscale_host()


def test_raises_a_clear_error_when_not_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(1, stderr="not logged in"))
    with pytest.raises(RuntimeError, match="tailscale up"):
        _resolve_tailscale_host()


def test_raises_a_clear_error_when_output_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, stdout="   \n"))
    with pytest.raises(RuntimeError, match="no address"):
        _resolve_tailscale_host()
