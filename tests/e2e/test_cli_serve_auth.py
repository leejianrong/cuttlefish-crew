"""End-to-end: `cuttlefish serve --host <non-loopback>` refuses to start without
`CUTTLEFISH_SERVE_PASSWORD` (ADR-0011, KAN-1706) -- the actual CLI entry point,
not just `run_daemon` in isolation. The success path (a real non-loopback bind
serving real requests) is covered at the `create_app`/`run_daemon` layer in
`tests/integration/test_fleet_server_auth.py`; `cuttlefish serve` itself blocks
forever on success, so only the fail-fast path is exercised end-to-end here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import cli


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CUTTLEFISH_SERVE_PASSWORD", raising=False)


def test_non_loopback_host_with_no_password_env_var_fails_fast(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["serve", "--host", "0.0.0.0", "--port", "0"])
    assert exit_code == cli.EXIT_TASK_FAILED
    assert "CUTTLEFISH_SERVE_PASSWORD" in capsys.readouterr().err


def test_non_loopback_host_with_a_weak_password_fails_fast(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_SERVE_PASSWORD", "short")
    exit_code = cli.main(["serve", "--host", "0.0.0.0", "--port", "0"])
    assert exit_code == cli.EXIT_TASK_FAILED
    assert "CUTTLEFISH_SERVE_PASSWORD" in capsys.readouterr().err
