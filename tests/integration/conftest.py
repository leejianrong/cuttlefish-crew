"""Fixtures for the integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fake_upstream import Upstream, upstream_fixture


@pytest.fixture
async def upstream() -> AsyncIterator[Upstream]:
    async for state in upstream_fixture():
        yield state
