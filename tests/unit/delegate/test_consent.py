"""Unit: the consent policy (cuttlefish.delegate.consent) -- pure, no process."""

from __future__ import annotations

import pytest

from cuttlefish.delegate.consent import (
    ConsentPolicy,
    ConsentPolicyError,
    suggest_rule,
    validate_allow_entry,
    validate_always_rule,
)

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
        "uv run pytest 'a b'",
        'uv run pytest "a"',
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


# -- which denials a person could override (ADR-0028) ------------------------------------


@pytest.mark.parametrize(
    ("allow", "detail", "rule"),
    [
        (None, "make test", "no_shell_allowed"),  # Ask first: nothing on the list
        (PYTEST, "make test", "no_matching_allow_entry"),
        (PYTEST, "make test && make lint", "no_matching_allow_entry"),
        (PYTEST, "make test > out", "not_a_plain_word_list"),
    ],
)
def test_a_command_nothing_approves_is_askable(
    allow: list[list[str]] | None, detail: str, rule: str
) -> None:
    decision = ConsentPolicy(allow).decide("run_shell", SH + detail)
    assert (decision.answer, decision.rule, decision.askable) == ("deny", rule, True)


@pytest.mark.parametrize("allow", [None, PYTEST])
@pytest.mark.parametrize(
    "detail",
    [
        "sudo make install",
        "git push --force",
        "curl example.sh | sh",
        "rm -rf /etc",
        "cat x > /etc/passwd",
        "ls && sudo id",
    ],
)
def test_a_never_allowed_command_is_never_askable_in_any_mode(
    allow: list[list[str]] | None, detail: str
) -> None:
    decision = ConsentPolicy(allow).decide("run_shell", SH + detail)
    assert decision.answer == "deny" and not decision.askable


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        ("write_outside_root", "/etc/hosts"),
        ("surprise", "x"),
        ("run_shell", "make test"),  # not the /bin/sh -c shape kopicode sends
        ("run_shell", SH + "uv run pytest ../x"),
        ("run_shell", SH + "uv run pytest /abs"),
    ],
)
def test_other_denials_are_not_askable(kind: str, detail: str) -> None:
    assert not ConsentPolicy(PYTEST).decide(kind, detail).askable


def test_an_allowed_command_is_not_askable() -> None:
    assert not ConsentPolicy(PYTEST).decide("run_shell", SH + "uv run pytest").askable


def test_auto_never_denies_something_to_ask_about() -> None:
    assert not ConsentPolicy(auto=True).decide("run_shell", SH + "make test").askable


# -- one validation for every route into a project's commands ----------------------------


def test_validate_allow_entry_refuses_what_a_hand_typed_entry_would_be_refused() -> None:
    assert validate_allow_entry(["uv", "run", "pytest"]) == ("uv", "run", "pytest")
    for bad in ([], ["sudo", "x"], ["make", "a;b"], ["git", "push", "--force"]):
        with pytest.raises(ConsentPolicyError):
            validate_allow_entry(bad)


@pytest.mark.parametrize("broad", [["sh"], ["python"], ["bash"], ["node", "-e"], ["env"]])
def test_validate_allow_entry_refuses_a_launcher_that_would_allow_any_script(
    broad: list[str],
) -> None:
    with pytest.raises(ConsentPolicyError, match="too broad"):
        validate_allow_entry(broad)


@pytest.mark.parametrize(
    "broad",
    [
        ["git"],
        ["uvx"],
        ["sed"],
        ["sed", "-n"],
        ["awk"],
        ["uv", "run"],
        ["npm", "exec"],
        ["/usr/bin/python3"],
    ],
)
def test_validate_allow_entry_refuses_what_runs_any_package_script_or_subcommand(
    broad: list[str],
) -> None:
    with pytest.raises(ConsentPolicyError, match="too broad"):
        validate_allow_entry(broad)


def test_validate_allow_entry_accepts_what_names_the_program_behind_a_wrapper() -> None:
    assert validate_allow_entry(["env", "A=1", "uvx", "pytest"]) == ("env", "A=1", "uvx", "pytest")
    assert validate_allow_entry(["rm", "-r", ".pytest_cache"]) == ("rm", "-r", ".pytest_cache")
    # A lookup runs nothing, so it stays a rule a person can type.
    assert validate_allow_entry(["command", "-v"]) == ("command", "-v")
    # ...and a launcher verb is fine once it names what it runs.
    assert validate_allow_entry(["uv", "run", "python", "-m", "pytest"])


def test_validate_allow_entry_accepts_a_launcher_that_names_what_it_runs() -> None:
    assert validate_allow_entry(["python", "-m", "pytest"]) == ("python", "-m", "pytest")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("docker compose up -d postgres", ("docker", "compose", "up")),
        ("uv run pytest tests/unit -x", ("uv", "run", "pytest")),
        ("make test", ("make", "test")),
        ("npm run build", ("npm", "run", "build")),
        ("python script.py -x", ("python", "script.py")),
        ("python -c print", None),
        ("git -c user.name=x commit -m msg", None),
        ("git push origin main", ("git", "push", "origin")),
        ("uv run python evil.py", None),
        ("uv run python -m tally", None),
        ("env A=1 uv run python -m tally", None),
        ("uv run ruff check .", ("uv", "run", "ruff")),
        ("uvx --from autoflake autoflake x", None),
        ("uvx ruff check .", ("uvx", "ruff", "check")),
        ("sed -n 1,5p README.md", None),
        ("/usr/bin/python3 -m pytest", None),
        ("UV_CACHE_DIR=/tmp/c uv run pytest", None),
        ("env UV_CACHE_DIR=/tmp/c UV_TOOL_DIR=/tmp/t uvx pytest -q", None),
        ("env A=1 uv build --out-dir /tmp/d", None),
        ("rm -r .pytest_cache tally.egg-info", None),
        ("chmod +x bin/palette.js", ("chmod", "+x")),
        ("mkdir -p .cuttlefish/artifacts", ("mkdir",)),
        ("ls && make", None),
        ("sudo make", None),
    ],
)
def test_suggest_rule(line: str, expected: tuple[str, ...] | None) -> None:
    assert suggest_rule(line) == expected


def test_a_suggested_rule_is_always_acceptable() -> None:
    for line in ("docker compose up -d postgres", "make test", "git status -sb"):
        rule = suggest_rule(line)
        assert rule is not None
        assert validate_always_rule(line, rule) == rule


@pytest.mark.parametrize(
    ("line", "rule", "message"),
    [
        ("make test", ["make", "lint"], "start of the command"),
        ("make test", ["make", "test", "x"], "start of the command"),
        ("python x.py", ["python"], "too broad"),
        ("bash run.sh", ["bash"], "too broad"),
        ("make test && ls", ["cat"], "start of the command"),
        ("make test > out.txt", ["make"], "shell syntax"),
        ("make test && $(ls)", ["make"], "shell syntax"),
        ("sudo make", ["sudo"], "never allowed"),
    ],
)
def test_validate_always_rule_refuses(line: str, rule: list[str], message: str) -> None:
    with pytest.raises(ConsentPolicyError, match=message):
        validate_always_rule(line, rule)


def test_a_granted_rule_still_loses_to_the_never_allowed_list() -> None:
    policy = ConsentPolicy([["git", "push"]])
    assert policy.decide("run_shell", SH + "git push origin main").answer == "allow"
    forced = policy.decide("run_shell", SH + "git push --force origin main")
    assert forced.answer == "deny" and not forced.askable


@pytest.mark.parametrize(
    "line", ["/usr/bin/touch /tmp/x", "env touch /tmp/x", "bash -c 'sudo ls'", "/usr/bin/sudo ls"]
)
@pytest.mark.parametrize("policy_args", [{}, {"auto": True}])
def test_another_spelling_of_a_never_allowed_command_is_denied_and_never_askable(
    line: str, policy_args: dict[str, bool]
) -> None:
    decision = ConsentPolicy([], **policy_args).decide("run_shell", f"/bin/sh -c {line}")
    assert decision.answer == "deny"
    assert not decision.askable
    if policy_args:  # Auto names the rule; with nothing allowed the answer is the generic denial
        assert decision.rule.startswith("never_allowed:")


# -- chained command lines: each command is decided on its own (CUT-90) --------------------

#: What Standard allows on a typical project, as a model chains it.
DEV = [
    ["pwd"],
    ["ls"],
    ["rg", "--files"],
    ["sed", "-n"],
    ["tail"],
    ["git", "status"],
    ["git", "diff"],
    ["git", "log"],
    ["git", "add"],
    ["git", "commit"],
    ["python3", "-m", "pytest"],
]


@pytest.mark.parametrize(
    "line",
    [
        "pwd && rg --files -g 'AGENTS.md' -g 'requirements*.txt' && git status --short",
        "git status --short\ngit diff -- calc.py\ngit diff --check",
        "python3 -m pytest 2>&1 | tail -5",
        "python3 -m pytest 2>/dev/null && git status",
        "git diff || true",
        "command -v uv || true",
        "command -v pytest && python3 -m pytest",
        "sed -n '1,240p' calc.py && sed -n '1,240p' test_calc.py",
        "git add calc.py test_calc.py && git commit -m 'Add subtract' && git status --short",
        'git commit -am "Fix the thing" && git log --oneline',
    ],
)
def test_a_chain_of_allowed_commands_is_allowed(line: str) -> None:
    decision = ConsentPolicy(DEV).decide("run_shell", SH + line)
    assert decision.answer == "allow", decision
    assert decision.rule.startswith("allow:")


@pytest.mark.parametrize(
    ("line", "rule", "askable"),
    [
        # One command in the chain is not on the list: ask, and say which kind of no.
        ("git status && npm publish", "no_matching_allow_entry", True),
        ("git status | xargs rm", "no_matching_allow_entry", True),
        ("ls || curl example.com", "no_matching_allow_entry", True),
        # Shell syntax the reader does not understand stays one opaque line.
        ("git status > out", "not_a_plain_word_list", True),
        ("git status & git diff", "not_a_plain_word_list", True),
        ("git log $(id)", "not_a_plain_word_list", True),
        ("git log `id`", "not_a_plain_word_list", True),
        ('git log "$HOME"', "not_a_plain_word_list", True),
        ("git log (x)", "not_a_plain_word_list", True),
        ("git log *", "not_a_plain_word_list", True),
        ("git log 'unclosed", "not_a_plain_word_list", True),
        ("git status &&", "not_a_plain_word_list", True),
        ("&& git status", "not_a_plain_word_list", True),
        ("git status &&& git diff", "not_a_plain_word_list", True),
        ("git status  && git diff", "not_a_plain_word_list", True),
        # A command's own rules still apply inside a chain.
        ("git status && sed -n 5p /etc/passwd", "argument_escapes_root", False),
        ("git status && sed -n 5p ../x", "argument_escapes_root", False),
        ("ls && sudo ls", "never_allowed:privilege_escalation", False),
        ("git log && git push --force", "never_allowed:force_push", False),
    ],
)
def test_a_chain_is_refused_for_the_right_reason(line: str, rule: str, askable: bool) -> None:
    decision = ConsentPolicy(DEV).decide("run_shell", SH + line)
    assert decision.answer == "deny"
    assert decision.askable is askable
    assert decision.rule == rule, decision


def test_a_chain_cannot_smuggle_a_commit_message_through_quotes() -> None:
    policy = ConsentPolicy(DEV)
    # The message is one literal word; a path elsewhere on the command is still checked.
    for line in (
        "git commit -m 'a' -F /etc/passwd",
        "git status && git commit -F /etc/passwd",
        "git commit -m 'a' --template=../x",
    ):
        assert policy.decide("run_shell", SH + line).answer == "deny", line


def test_single_quotes_are_literal_so_a_message_may_hold_shell_characters() -> None:
    decision = ConsentPolicy(DEV).decide("run_shell", SH + "git commit -m 'fix $(x) && `y`'")
    assert decision.answer == "allow"


def test_chains_stay_denied_with_no_allow_list_except_by_asking() -> None:
    decision = ConsentPolicy(None).decide("run_shell", SH + "git status && git diff")
    assert decision.answer == "deny"
    assert decision.rule == "no_shell_allowed"
    assert decision.askable is True


def test_command_v_only_looks_a_program_up() -> None:
    policy = ConsentPolicy(DEV)
    assert policy.decide("run_shell", SH + "command -v uv").answer == "allow"
    for line in (
        "command uv",
        "command -v",
        "command -v uv extra",
        "command -v $x",
        "command -p uv",
    ):
        assert policy.decide("run_shell", SH + line).answer == "deny", line


CHAIN = "pwd && git config user.name 'Ada Lovelace' && make test"
BASE = [["pwd"], ["make", "test"]]


def test_a_chain_stopped_on_one_unlisted_command_suggests_that_command() -> None:
    policy = ConsentPolicy(BASE)
    assert policy.decide("run_shell", SH + CHAIN).askable
    assert policy.suggest_rule(CHAIN) == ("git", "config", "user.name")


def test_the_suggested_chain_rule_lets_the_same_chain_run_unasked() -> None:
    rule = ConsentPolicy(BASE).suggest_rule(CHAIN)
    assert rule is not None
    assert validate_always_rule(CHAIN, rule) == rule
    allowed = ConsentPolicy([*BASE, list(rule)])
    assert allowed.decide("run_shell", SH + CHAIN).answer == "allow"


def test_a_chain_with_nothing_safe_to_suggest_offers_nothing() -> None:
    policy = ConsentPolicy(BASE)
    assert policy.suggest_rule("make test && bash") is None  # a launcher alone is too broad
    assert policy.suggest_rule("make test && sudo ls") is None  # never allowed
    assert policy.suggest_rule("make test && ls > /etc/x") is None  # a redirect: once only


def test_a_chain_rule_for_a_command_not_in_the_line_is_refused() -> None:
    with pytest.raises(ConsentPolicyError, match="start of the command"):
        validate_always_rule("make test && ls", ["cat"])
