"""`scripts/install.sh --check` (CUT-224, ADR-0045): names what is missing, changes nothing.

The script runs under a PATH holding only symlinks and stubs we choose, so each case sees
exactly the prerequisites it lists. `--check` never installs, so these need no network.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "install.sh"


def _bin(tmp_path: Path, *, tools: tuple[str, ...], node_version: str = "v22.12.0") -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real = shutil.which("sed")
    assert real is not None
    (bindir / "sed").symlink_to(real)  # the script's one external dependency besides its stubs
    for tool in tools:
        stub = bindir / tool
        body = f"echo {node_version}" if tool == "node" else "exit 0"
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    return bindir


def _check(tmp_path: Path, bindir: Path) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir()
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), "--check"],
        env={"PATH": str(bindir), "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_missing_git_is_named_and_stops_the_install(tmp_path: Path) -> None:
    result = _check(tmp_path, _bin(tmp_path, tools=("curl", "uv", "node", "npm", "tmux", "claude")))
    assert result.returncode == 1
    assert "git" in result.stderr and "Cannot install" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_curl_and_wget_is_named(tmp_path: Path) -> None:
    result = _check(tmp_path, _bin(tmp_path, tools=("git", "uv")))
    assert result.returncode == 1
    assert "curl" in result.stderr


def test_everything_present_passes_with_nothing_to_add(tmp_path: Path) -> None:
    tools = ("git", "curl", "uv", "node", "npm", "tmux", "claude")
    result = _check(tmp_path, _bin(tmp_path, tools=tools))
    assert result.returncode == 0, result.stderr
    assert "Not found" not in result.stdout


def test_soft_prerequisites_are_named_but_do_not_block(tmp_path: Path) -> None:
    result = _check(tmp_path, _bin(tmp_path, tools=("git", "curl", "uv")))
    assert result.returncode == 0
    for needle in ("Node.js", "tmux", "coding-agent CLI"):
        assert needle in result.stdout


@pytest.mark.parametrize("version", ["v18.19.1", "v20.18.0", "v22.11.0"])
def test_a_node_too_old_for_the_dashboard_build_is_named(tmp_path: Path, version: str) -> None:
    tools = ("git", "curl", "uv", "node", "npm", "tmux", "kopicode")
    result = _check(tmp_path, _bin(tmp_path, tools=tools, node_version=version))
    assert result.returncode == 0
    assert version in result.stdout and "20.19" in result.stdout


@pytest.mark.parametrize("version", ["v20.19.0", "v22.12.0", "v24.1.0"])
def test_a_new_enough_node_is_accepted(tmp_path: Path, version: str) -> None:
    tools = ("git", "curl", "uv", "node", "npm", "tmux", "codex")
    result = _check(tmp_path, _bin(tmp_path, tools=tools, node_version=version))
    assert result.returncode == 0
    assert "Node.js" not in result.stdout


def test_check_changes_nothing(tmp_path: Path) -> None:
    result = _check(tmp_path, _bin(tmp_path, tools=("git", "curl")))
    assert result.returncode == 0
    assert list((tmp_path / "home").iterdir()) == []


def test_unknown_option_is_a_usage_error() -> None:
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT), "--bogus"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "usage" in result.stderr
