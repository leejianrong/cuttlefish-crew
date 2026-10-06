"""End-to-end: `cuttlefish projects add|list|remove`, in-process (ADR-0009) -- the
same style `tests/e2e/test_cli.py` already uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuttlefish import cli
from cuttlefish.roles import BUILTIN_ROLES


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ProjectStore`'s default path is `~/.cuttlefish/projects.db` (Q47) -- point
    `Path.home()` at a throwaway location so this test never touches the real
    operator's registry."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


def test_add_then_list_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "projects",
            "add",
            "--name",
            "demo",
            "--root",
            str(tmp_path / "demo"),
            "--role",
            "builder:ships fast, terse commits",
            "--role",
            "reviewer",
        ]
    )
    assert exit_code == cli.EXIT_OK
    added = json.loads(capsys.readouterr().out)
    assert added["name"] == "demo"
    assert added["secrets_scope"] == "demo"
    # A persona on the command line wins; a bare built-in name is that built-in (V4-B).
    assert added["roles"] == [
        {
            "name": "builder",
            "persona": "ships fast, terse commits",
            "backend": None,
            "access": "standard",
        },
        {
            "name": "reviewer",
            "persona": BUILTIN_ROLES["reviewer"].prompt,
            "backend": None,
            "access": "read-only",
        },
    ]

    exit_code = cli.main(["projects", "list"])
    assert exit_code == cli.EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["id"] == added["id"]


def test_add_with_allow_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "projects",
            "add",
            "--name",
            "demo",
            "--root",
            str(tmp_path / "demo"),
            "--allow",
            "uv run pytest",
            "--allow",
            "go test",
        ]
    )
    assert exit_code == cli.EXIT_OK
    added = json.loads(capsys.readouterr().out)
    assert added["allow"] == [["uv", "run", "pytest"], ["go", "test"]]


def test_remove_an_unknown_id_fails(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["projects", "remove", "no-such-id"])
    assert exit_code == cli.EXIT_TASK_FAILED
    assert "no project" in capsys.readouterr().err


def test_remove_a_registered_project_leaves_its_root_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "marker.txt").write_text("still here")

    cli.main(["projects", "add", "--name", "demo", "--root", str(root)])
    project_id = json.loads(capsys.readouterr().out)["id"]

    exit_code = cli.main(["projects", "remove", project_id])
    assert exit_code == cli.EXIT_OK
    assert (root / "marker.txt").exists()


def test_a_duplicate_role_name_is_a_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "projects",
            "add",
            "--name",
            "demo",
            "--root",
            "/tmp/demo",
            "--role",
            "builder",
            "--role",
            "builder:second",
        ]
    )
    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "declared more than once" in capsys.readouterr().err


def test_add_with_a_template_registers_that_teams_roles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["projects", "add", "--name", "demo", "--root", str(tmp_path / "demo")]
    assert cli.main([*args, "--template", "full-crew"]) == cli.EXIT_OK
    added = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in added["roles"]] == ["planner", "builder", "tester", "reviewer"]


def test_add_with_no_role_or_template_gets_builder_and_reviewer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["projects", "add", "--name", "demo", "--root", str(tmp_path / "demo")]
    assert cli.main(args) == cli.EXIT_OK
    added = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in added["roles"]] == ["builder", "reviewer"]


def test_add_rejects_a_template_together_with_a_role(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["projects", "add", "--name", "demo", "--root", str(tmp_path / "demo")]
    assert cli.main([*args, "--template", "solo-builder", "--role", "x"]) == cli.EXIT_CONFIG_ERROR
    assert "cannot be combined" in capsys.readouterr().err


def test_add_rejects_an_unknown_template(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["projects", "add", "--name", "demo", "--root", str(tmp_path / "demo")]
    assert cli.main([*args, "--template", "nope"]) == cli.EXIT_CONFIG_ERROR
    assert "solo-builder" in capsys.readouterr().err
