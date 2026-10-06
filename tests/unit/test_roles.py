"""Unit: built-in roles and team templates (cuttlefish.roles, ADR-0024) -- pure."""

from __future__ import annotations

import pytest

from cuttlefish.projects.store import RoleDefinition
from cuttlefish.roles import (
    BUILTIN_ROLES,
    DEFAULT_TEMPLATE,
    TEMPLATES,
    UnknownTemplateError,
    is_default_prompt,
    reset_to_default,
    role_definition,
    template_roles,
)


def test_every_template_names_only_builtin_roles() -> None:
    for template in TEMPLATES.values():
        assert template.roles
        assert set(template.roles) <= set(BUILTIN_ROLES)


def test_the_default_template_is_a_builder_and_a_reviewer() -> None:
    assert [r.name for r in template_roles()] == ["builder", "reviewer"]
    assert DEFAULT_TEMPLATE in TEMPLATES


def test_reviewer_and_planner_are_read_only_and_the_rest_are_not() -> None:
    access = {name: role_definition(name).access for name in BUILTIN_ROLES}
    assert access == {
        "builder": None,
        "reviewer": "read-only",
        "tester": None,
        "planner": "read-only",
        "docs-writer": None,
    }


def test_prompts_do_not_open_with_the_you_are_line_cuttlefish_prepends() -> None:
    for role in BUILTIN_ROLES.values():
        assert not role.prompt.lower().startswith("you are")


def test_read_only_prompts_say_so() -> None:
    for role in BUILTIN_ROLES.values():
        if role.access == "read-only":
            assert "read-only" in role.prompt


def test_an_unknown_template_names_the_choices() -> None:
    with pytest.raises(UnknownTemplateError, match="solo-builder"):
        template_roles("nope")


def test_a_builtin_is_default_until_its_prompt_is_edited() -> None:
    builder = role_definition("builder")
    assert is_default_prompt(builder)
    edited = RoleDefinition(name="builder", persona="ship it")
    assert not is_default_prompt(edited)


def test_a_custom_role_is_never_default() -> None:
    assert not is_default_prompt(RoleDefinition(name="poet", persona=""))


def test_reset_restores_prompt_and_access_but_keeps_the_backend() -> None:
    edited = RoleDefinition(name="reviewer", persona="x", backend="codex", access=None)
    reset = reset_to_default(edited)
    assert is_default_prompt(reset)
    assert reset.access == "read-only"
    assert reset.backend == "codex"


def test_reset_of_a_custom_role_is_a_no_op() -> None:
    custom = RoleDefinition(name="poet", persona="rhyme")
    assert reset_to_default(custom) == custom
