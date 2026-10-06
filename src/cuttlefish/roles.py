"""Built-in roles and team templates (docs/SLICES.md V4-B, ADR-0024).

A new project should not need an operator to invent a prompt or a team shape. Each
:class:`BuiltinRole` is a ready-made :class:`~cuttlefish.projects.store.RoleDefinition`
(its ``prompt`` becomes the role's persona), and each :class:`Template` is a named team of
them. A role keeps no link back to its built-in: it counts as "default" simply while its
persona still equals the built-in prompt, so an edited role is just a role.

``cuttlefish.fleet.daemon._compose_role_text`` opens every task with ``You are {name}.``, so
a prompt here starts with the instruction, not with that sentence.

``access="read-only"`` restricts a role's shell to inspection (``presets.READ_ONLY_PRESETS``).
It does **not** yet stop a role editing files: the Claude Code and Codex mappings, and
what kopicode can do at all, land with the permission modes (V4-C, known-gaps.md). Until
then a reviewer's prompt, not a gate, is what keeps it from editing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cuttlefish.projects.store import RoleDefinition

Access = Literal["standard", "read-only"]
ACCESS_LEVELS: tuple[str, ...] = ("standard", "read-only")


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    name: str
    summary: str
    prompt: str
    access: Access = "standard"


@dataclass(frozen=True, slots=True)
class Template:
    name: str
    title: str
    summary: str
    roles: tuple[str, ...]


BUILTIN_ROLES: dict[str, BuiltinRole] = {
    role.name: role
    for role in (
        BuiltinRole(
            name="builder",
            summary="Writes code and runs the tests",
            prompt=(
                "Implement the task you are given in small, working steps.\n\n"
                "Read the code around a change before you make it, and match the style "
                "already there.\n"
                "Run the project's tests and linters after each meaningful change. Fix "
                "failures before moving on.\n"
                "Commit small, focused changes with clear messages. Never push.\n"
                "If the task is ambiguous in a way that would change the design, say what "
                "you assumed, take the most conservative reading, and carry on.\n"
                "When you finish, summarise what changed, how you tested it, and anything "
                "you left undone."
            ),
        ),
        BuiltinRole(
            name="reviewer",
            summary="Reads the diff and flags risk",
            access="read-only",
            prompt=(
                "Review the recent changes in this repository. You are read-only: do not "
                "edit, create or delete files, and do not commit.\n\n"
                "Read the diff (git diff, git log) and the code it touches before you judge "
                "it.\n"
                "Look for correctness bugs, missing or weak tests, unhandled errors, "
                "security problems and changes that do more than the task asked.\n"
                "Report findings most serious first. For each, name the file and line, say "
                "what goes wrong and when, and suggest the fix. Skip style nitpicks.\n"
                "If you find nothing serious, say so plainly and list what you checked."
            ),
        ),
        BuiltinRole(
            name="tester",
            summary="Writes and runs tests",
            prompt=(
                "Make sure the change is covered by tests that would fail without it.\n\n"
                "Read the change and the existing tests first, and follow their layout and "
                "naming.\n"
                "Add tests for the new behaviour, its edge cases and its failure modes. "
                "Prefer small, deterministic tests over broad ones.\n"
                "Run the suite and report exactly what passed and what failed. Do not "
                "change production code to make a test pass; report the bug instead.\n"
                "Commit test changes in small, focused commits. Never push."
            ),
        ),
        BuiltinRole(
            name="planner",
            summary="Breaks a goal into steps",
            access="read-only",
            prompt=(
                "Turn the goal into a short, ordered plan. You are read-only: do not edit, "
                "create or delete files.\n\n"
                "Read the relevant code and docs first so the plan names real files and "
                "functions.\n"
                "List the steps in the order they should be done, each small enough to "
                "review on its own, and say how to tell each one worked.\n"
                "Call out risks, open questions and anything you would cut to ship sooner.\n"
                "Keep it to what a builder needs to start; do not write the code."
            ),
        ),
        BuiltinRole(
            name="docs-writer",
            summary="Keeps README and docs current",
            prompt=(
                "Keep the documentation true to the code. Change documentation files "
                "only; if the code needs fixing, say so instead of fixing it.\n\n"
                "Check each claim you touch against the code, and when the two disagree "
                "the code wins.\n"
                "Write plainly: what it does, how to use it, with an example that runs. "
                "Remove text that is stale rather than adding a caveat.\n"
                "Commit docs changes in small, focused commits. Never push."
            ),
        ),
    )
}

TEMPLATES: dict[str, Template] = {
    template.name: template
    for template in (
        Template(
            name="solo-builder",
            title="Solo builder",
            summary="One agent plans, edits and tests. Fastest for small changes.",
            roles=("builder",),
        ),
        Template(
            name="builder-reviewer",
            title="Builder + reviewer",
            summary="A second agent reads the diff and flags risk before you do.",
            roles=("builder", "reviewer"),
        ),
        Template(
            name="full-crew",
            title="Full crew",
            summary="Plan first, then build, test and review. For larger features.",
            roles=("planner", "builder", "tester", "reviewer"),
        ),
    )
}

DEFAULT_TEMPLATE = "builder-reviewer"


class UnknownTemplateError(LookupError):
    """A template name that is not one of :data:`TEMPLATES`."""


def role_definition(name: str) -> RoleDefinition:
    """The built-in role `name` as a registrable role (persona = its prompt)."""
    builtin = BUILTIN_ROLES[name]
    return RoleDefinition(
        name=builtin.name,
        persona=builtin.prompt,
        access=None if builtin.access == "standard" else builtin.access,
    )


def template_roles(template: str = DEFAULT_TEMPLATE) -> tuple[RoleDefinition, ...]:
    """The roles of template `template`, ready to register."""
    try:
        names = TEMPLATES[template].roles
    except KeyError:
        raise UnknownTemplateError(
            f"unknown template {template!r} (choose from {', '.join(TEMPLATES)})"
        ) from None
    return tuple(role_definition(name) for name in names)


def is_default_prompt(role: RoleDefinition) -> bool:
    """Whether `role` is a built-in still carrying its built-in prompt."""
    builtin = BUILTIN_ROLES.get(role.name)
    return builtin is not None and role.persona == builtin.prompt


def reset_to_default(role: RoleDefinition) -> RoleDefinition:
    """`role` with its built-in prompt and access restored; backend is kept.

    A role with no built-in of that name has nothing to reset to and is returned as is.
    """
    if role.name not in BUILTIN_ROLES:
        return role
    default = role_definition(role.name)
    return RoleDefinition(
        name=role.name, persona=default.persona, backend=role.backend, access=default.access
    )
