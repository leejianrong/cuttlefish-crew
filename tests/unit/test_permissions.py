"""Unit: permission modes and effective access (cuttlefish.permissions, ADR-0025) -- pure."""

from __future__ import annotations

from cuttlefish.permissions import ACCESS_LEVELS, DEFAULT_MODE, MODES, effective_access


def test_the_default_mode_is_standard_and_is_a_mode() -> None:
    assert DEFAULT_MODE == "standard"
    assert DEFAULT_MODE in MODES


def test_a_role_may_be_read_only_but_a_project_may_not() -> None:
    assert "read-only" in ACCESS_LEVELS
    assert "read-only" not in MODES


def test_a_roles_own_access_wins_over_the_projects_mode() -> None:
    assert effective_access("auto", "read-only") == "read-only"
    assert effective_access("ask-first", "standard") == "standard"


def test_a_role_with_no_access_inherits_the_projects_mode() -> None:
    assert effective_access("auto", None) == "auto"
    assert effective_access(None, None) == "standard"
