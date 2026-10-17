"""The vectors file comes from an independent Noise implementation (see make_vectors.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

VECTORS = Path(__file__).parents[2] / "fixtures" / "relay" / "vectors.json"


@pytest.fixture(scope="session")
def vectors() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(VECTORS.read_text())
    return data
