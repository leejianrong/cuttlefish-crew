"""CUT-214: a worker reports what it has of the profile; `drift` names what a machine lacks."""

from __future__ import annotations

import pytest

from cuttlefish.worker.inventory import KEEP_SECONDS, Inventory, probe
from cuttlefish.worker.profile import drift, load, parse

PROFILE = parse(
    "version = 1\n"
    '[[tool]]\nname = "git"\nmin = "2.30"\napt = ["git"]\n'
    '[[tool]]\nname = "docker"\napt = ["docker.io"]\n'
    '[[tool]]\nname = "docker-compose"\ncommand = "docker"\nversion = ["compose", "version"]\n'
    'min = "2.0"\napt = ["docker-compose-v2"]\n'
    '[[tool]]\nname = "claude"\ngroup = "agent"\nhow = "agent-cli"\n'
    '[[tool]]\nname = "codex"\ngroup = "agent"\nhow = "agent-cli"\n'
)


def test_a_machine_with_everything_matches() -> None:
    have = {"git": "2.43.0", "docker": "27.1", "docker-compose": "2.29", "claude": "2.0"}
    assert drift(PROFILE, have) == []


def test_removing_docker_names_it() -> None:
    have = {"git": "2.43.0", "docker-compose": "2.29", "codex": ""}
    assert drift(PROFILE, have) == ["docker missing"]


def test_a_missing_tool_a_stale_version_and_no_agent_are_each_named() -> None:
    have = {"git": "2.20.1", "docker": ""}
    assert drift(PROFILE, have) == [
        "git 2.20.1 is older than 2.30",
        "docker-compose missing",
        "none of claude, codex",
    ]


def test_a_version_a_tool_would_not_print_is_not_judged() -> None:
    assert drift(PROFILE, {"git": "", "docker": "", "docker-compose": "", "codex": ""}) == []


def test_probe_finds_tools_on_path_and_reads_their_versions() -> None:
    on_path = {"git": "/usr/bin/git", "docker": "/usr/bin/docker", "claude": "/bin/claude"}
    ran: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        ran.append(argv)
        if argv[1:] == ["compose", "version"]:
            return 1, "docker: 'compose' is not a docker command"  # the plugin is not installed
        return 0, {"git": "git version 2.43.0", "claude": "no number here"}.get(
            argv[0].rsplit("/")[-1], "Docker version 27.1.1, build abc"
        )

    found = probe(PROFILE, on_path.get, run)
    assert found == {"git": "2.43.0", "docker": "27.1.1", "claude": ""}
    assert drift(PROFILE, found) == ["docker-compose missing"]
    assert ["/usr/bin/docker", "compose", "version"] in ran


def test_a_program_that_cannot_be_run_counts_as_present_unless_it_is_a_subcommand() -> None:
    found = probe(PROFILE, lambda _c: "/x/tool", lambda _a: (1, ""))
    assert "git" in found and "docker" in found and "docker-compose" not in found


def test_the_probe_is_kept_for_a_minute() -> None:
    calls = [0]
    now = [0.0]

    def which(_command: str) -> str | None:
        calls[0] += 1
        return None

    inventory = Inventory(PROFILE, which=which, now=lambda: now[0])
    inventory.tools()
    first = calls[0]
    inventory.tools()
    assert calls[0] == first
    now[0] = KEEP_SECONDS
    inventory.tools()
    assert calls[0] == 2 * first


def test_the_standard_profile_is_matched_by_a_machine_that_has_everything_in_it() -> None:
    standard = load()
    have = {tool.name: "99.0" for tool in standard.tools}
    assert drift(standard, have) == []


def test_verify_exits_1_and_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuttlefish import cli

    monkeypatch.setattr("cuttlefish.worker.inventory.probe", lambda _p: {"git": "2.43.0"})
    assert cli.main(["worker", "verify"]) == 1
    out = capsys.readouterr().out
    assert "ok      git 2.43.0" in out and "DRIFT   docker missing" in out
    everything = {tool.name: "99.0" for tool in load().tools}
    monkeypatch.setattr("cuttlefish.worker.inventory.probe", lambda _p: everything)
    assert cli.main(["worker", "verify"]) == 0
    assert "matches the worker profile" in capsys.readouterr().out


def test_tmux_is_asked_with_dash_v_because_its_usage_line_is_not_a_version() -> None:
    usage = "usage: tmux [-2CDlNuVv] [-c shell-command] [-f file] [-L socket-name]"

    def run(argv: list[str]) -> tuple[int, str]:
        if argv[1:] == ["-V"]:
            return 0, "tmux next-3.5"
        return 1, usage  # anything else, `--version` included, gets the usage line

    tmux = next(t for t in load().tools if t.name == "tmux")
    assert tmux.version_args == ("-V",)
    found = probe(
        parse(
            'version = 1\n[[tool]]\nname = "tmux"\nversion = ["-V"]\nmin = "3.0"\napt = ["tmux"]\n'
        ),
        {"tmux": "/usr/bin/tmux"}.get,
        run,
    )
    assert found == {"tmux": "3.5"}
    assert drift(load(), {**{t.name: "99" for t in load().tools}, "tmux": found["tmux"]}) == []
    # What the old `--version` probe saw: the "2" in the usage line, which fails min 3.0.
    from cuttlefish.worker.profile import parse_version

    assert parse_version(usage) == (2,)
    assert parse_version("tmux 3.4") == (3, 4)
