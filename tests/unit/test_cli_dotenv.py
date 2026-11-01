"""The CLI reads ./.env only, never a parent directory's (CUT-272)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROBE = "import os, cuttlefish.cli; print(os.environ.get('CUT272_PROBE', 'unset'))"


def _probe(cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CUT272_PROBE"}
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_parent_dotenv_is_not_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CUT272_PROBE=from-parent\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    assert _probe(sub) == "unset"


def test_dotenv_in_working_directory_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CUT272_PROBE=here\n")
    assert _probe(tmp_path) == "here"
