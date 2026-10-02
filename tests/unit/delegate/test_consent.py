"""Unit: the consent policy (cuttlefish.delegate.consent) -- pure, no process."""

from __future__ import annotations

import pytest

from cuttlefish.delegate.consent import ConsentPolicy, ConsentPolicyError

PYTEST = [["uv", "run", "pytest"]]
#: kopicode sends the argv it will run, joined by spaces: `/bin/sh -c <line>` (live, KAN-1794).
SH = "/bin/sh -c "


@pytest.mark.parametrize(
    "detail",
    [
        "uv run pytest",
        "uv run pytest -q",
        "uv run pytest tests/unit/test_a.py::test_b -x",
        "uv run pytest --rootdir=.",
    ],
)
def test_every_plain_phrasing_of_an_allowed_command_is_allowed(detail: str) -> None:
    assert ConsentPolicy(PYTEST).decide("run_shell", SH + detail).answer == "allow"


@pytest.mark.parametrize(
    "detail",
    [
        "uv run pytest && rm -rf /",
        "uv run pytest; rm x",
        "uv run pytest | tee out",
        "uv run pytest > out",
        "uv run pytest $(id)",
        "uv run pytest `id`",
        "uv run pytest 'a b'",
        'uv run pytest "a"',
        "uv run pytest *",
        "uv run pytest ~/x",
        "uv run pytest\nrm x",
        "uv run pytest\trm",
        "uv run  pytest",
        " uv run pytest",
        "uv run pytest ",
        "uv run pytest /etc/passwd",
        "uv run pytest ../other",
        "uv run pytest --rootdir=/",
        "uv run pytest --rootdir=../..",
        "cd sub uv run pytest",
        "uv run",
        "uv run pytestx",
        "uv run python -c x",
        "",
        "uv run pytest " + "a" * 2000,
    ],
)
def test_anything_that_is_not_a_plain_allowed_word_list_is_denied(detail: str) -> None:
    assert ConsentPolicy(PYTEST).decide("run_shell", SH + detail).answer == "deny"


def test_no_allow_list_denies_every_shell_command() -> None:
    for policy in (ConsentPolicy(), ConsentPolicy([])):
        decision = policy.decide("run_shell", SH + "ls")
        assert (decision.answer, decision.rule) == ("deny", "no_shell_allowed")


def test_a_write_outside_root_is_denied_even_for_a_role_with_a_shell_allowlist() -> None:
    assert ConsentPolicy(PYTEST).decide("write_outside_root", "/etc/x").answer == "deny"


def test_an_unknown_kind_is_denied() -> None:
    assert ConsentPolicy(PYTEST).decide("network", "uv run pytest").answer == "deny"


def test_the_sh_c_shape_kopicode_uses_is_accepted_as_a_declared_entry() -> None:
    policy = ConsentPolicy([["/bin/sh", "-c", "uv run pytest"]])
    assert policy.decide("run_shell", SH + "uv run pytest -q").answer == "allow"


def test_the_decision_names_the_rule_that_allowed_it() -> None:
    assert ConsentPolicy(PYTEST).decide("run_shell", SH + "uv run pytest -q").rule == (
        "allow:uv run pytest"
    )


@pytest.mark.parametrize(
    "entry",
    [[], ["/bin/sh", "-c"], ["/bin/sh", "-c", "a", "b"], ["go", "test;"], ["sh", "-c", "a && b"]],
)
def test_an_unexpressible_declared_entry_is_refused_at_config_time(entry: list[str]) -> None:
    with pytest.raises(ConsentPolicyError):
        ConsentPolicy([entry])


@pytest.mark.parametrize(
    "detail",
    [
        "uv run pytest -q",  # the bare line: not what kopicode sends, so never guessed at
        "sh -c uv run pytest -q",
        "/bin/bash -c uv run pytest -q",
        " /bin/sh -c uv run pytest -q",
        "/bin/sh -cuv run pytest -q",
    ],
)
def test_a_detail_that_is_not_the_sh_c_form_is_denied(detail: str) -> None:
    decision = ConsentPolicy(PYTEST).decide("run_shell", detail)
    assert (decision.answer, decision.rule) == ("deny", "not_a_sh_c_command")


def test_the_prefix_is_stripped_exactly_once() -> None:
    # A line that itself starts with the prefix is just another word list to match.
    decision = ConsentPolicy([["ls"]]).decide("run_shell", SH + "/bin/sh -c ls")
    assert (decision.answer, decision.rule) == ("deny", "no_matching_allow_entry")
