"""CUT-212: the worker profile is data that is parsed and validated."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.worker import profile

GOOD = 'version = 1\n[[tool]]\nname = "git"\napt = ["git"]\n'


def test_the_standard_profile_loads_and_names_what_a_dev_worker_needs() -> None:
    loaded = profile.load()
    names = {tool.name for tool in loaded.tools}
    assert {"git", "tmux", "gh", "docker", "docker-compose", "uv", "node"} <= names
    assert {"kopicode", "claude", "codex"} == {t.name for t in loaded.groups()["agent"]}
    compose = next(t for t in loaded.tools if t.name == "docker-compose")
    assert (compose.command, compose.version_args) == ("docker", ("compose", "version", "--short"))
    assert all(t.apt or t.how for t in loaded.tools)


def test_an_environment_variable_points_at_another_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "p.toml").write_text(GOOD)
    monkeypatch.setenv("CUTTLEFISH_WORKER_PROFILE", str(tmp_path / "p.toml"))
    assert [t.name for t in profile.load().tools] == ["git"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not toml [", "not valid TOML"),
        ('[[tool]]\nname = "git"\napt = ["git"]\n', "version = 1"),
        ("version = 1\n", "lists no"),
        (GOOD + GOOD.split("\n", 1)[1], "listed twice"),
        ('version = 1\n[[tool]]\nname = "Git"\napt = ["git"]\n', "`name`"),
        (GOOD.replace('apt = ["git"]', 'apt = ["git"]\nfoo = 1'), "unknown key 'foo'"),
        (GOOD.replace('apt = ["git"]', 'command = "/bin/git"\napt = ["git"]'), "not a path"),
        (GOOD.replace('apt = ["git"]', 'min = "two"\napt = ["git"]'), "`min`"),
        (GOOD.replace('apt = ["git"]', 'version = ["x; rm"]\napt = ["git"]'), "`version`"),
        (GOOD.replace('apt = ["git"]', 'apt = ["git; reboot"]'), "`apt`"),
        (GOOD.replace('apt = ["git"]', 'how = "curl-sh"'), "`how`"),
        (GOOD.replace('apt = ["git"]', ""), "one way to install"),
        (GOOD.replace('apt = ["git"]', 'apt = ["git"]\nhow = "uv"'), "one way to install"),
    ],
)
def test_a_bad_profile_is_refused_naming_what_is_wrong(text: str, message: str) -> None:
    with pytest.raises(profile.ProfileError, match=message):
        profile.parse(text)


def test_a_missing_file_is_a_profile_error(tmp_path: Path) -> None:
    with pytest.raises(profile.ProfileError, match="cannot read"):
        profile.load(tmp_path / "nope.toml")


def test_versions_are_read_out_of_a_tools_own_words() -> None:
    assert profile.parse_version("git version 2.43.0") == (2, 43, 0)
    assert profile.parse_version("v20.11.1") == (20, 11, 1)
    assert profile.parse_version("no digits") is None
