from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.cli import (
    ConfigError,
    _parse_allow,
    _parse_roles,
    _resolve_dashboard_dir,
    build_parser,
)


def test_parse_allow_defaults_to_empty() -> None:
    assert _parse_allow(None) == []


def test_parse_allow_splits_each_shell_quoted_value() -> None:
    assert _parse_allow(["go test", "npm test"]) == [["go", "test"], ["npm", "test"]]


def test_parse_allow_handles_a_single_quoted_argument() -> None:
    assert _parse_allow(["git commit -m 'fix bug'"]) == [["git", "commit", "-m", "fix bug"]]


def test_run_parser_accepts_repeated_allow_flags() -> None:
    args = build_parser().parse_args(
        ["run", "add a test", "--allow", "go test", "--allow", "npm test"]
    )
    assert args.allow == ["go test", "npm test"]


def test_run_parser_allow_defaults_to_none_when_omitted() -> None:
    args = build_parser().parse_args(["run", "add a test"])
    assert args.allow is None


def test_run_parser_accepts_repeated_secret_flags() -> None:
    args = build_parser().parse_args(
        ["run", "add a test", "--secret", "HUGGINGFACE_TOKEN", "--secret", "GITHUB_TOKEN"]
    )
    assert args.secret == ["HUGGINGFACE_TOKEN", "GITHUB_TOKEN"]


def test_run_parser_project_and_secret_default_to_none_when_omitted() -> None:
    args = build_parser().parse_args(["run", "add a test"])
    assert args.project is None
    assert args.secret is None


def test_secrets_set_requires_a_scope() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["secrets", "set", "TOKEN"])


def test_secrets_set_rejects_both_project_and_shared_together() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["secrets", "set", "--project", "demo", "--shared", "TOKEN"])


def test_secrets_set_accepts_a_project_scope() -> None:
    args = build_parser().parse_args(["secrets", "set", "--project", "demo", "TOKEN"])
    assert args.secrets_command == "set"
    assert args.project == "demo"
    assert args.name == "TOKEN"


def test_secrets_list_accepts_the_shared_scope() -> None:
    args = build_parser().parse_args(["secrets", "list", "--shared"])
    assert args.secrets_command == "list"
    assert args.shared is True


def test_secrets_generate_key_needs_no_scope() -> None:
    args = build_parser().parse_args(["secrets", "generate-key"])
    assert args.secrets_command == "generate-key"


def test_run_team_accepts_repeated_role_flags() -> None:
    args = build_parser().parse_args(
        ["run-team", "--role", "builder:implement it", "--role", "reviewer:review it"]
    )
    assert args.role == ["builder:implement it", "reviewer:review it"]


def test_serve_allow_origin_defaults_to_none_when_omitted() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.allow_origin is None


def test_serve_accepts_repeated_allow_origin_flags() -> None:
    args = build_parser().parse_args(
        ["serve", "--allow-origin", "https://a.example", "--allow-origin", "https://b.example"]
    )
    assert args.allow_origin == ["https://a.example", "https://b.example"]


def test_serve_host_defaults_to_loopback() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.host == "127.0.0.1"


def test_serve_dashboard_dir_defaults_to_none_when_omitted() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.dashboard_dir is None


def test_serve_accepts_an_explicit_dashboard_dir() -> None:
    args = build_parser().parse_args(["serve", "--dashboard-dir", "frontend/dist"])
    assert args.dashboard_dir == "frontend/dist"


def test_resolve_dashboard_dir_returns_the_explicit_path_verbatim(tmp_path: Path) -> None:
    # Deliberately not asserting existence here -- an explicit, missing path is
    # `run_daemon`'s own job to reject (ADR-0012), not this resolver's.
    missing = tmp_path / "nope"
    assert _resolve_dashboard_dir(str(missing)) == missing


def test_resolve_dashboard_dir_auto_detects_frontend_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<html></html>")
    monkeypatch.chdir(tmp_path)
    assert _resolve_dashboard_dir(None) == Path("frontend/dist")


def test_resolve_dashboard_dir_is_none_when_no_build_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _resolve_dashboard_dir(None) is None


def test_serve_tailscale_defaults_to_false() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.tailscale is False


def test_serve_accepts_the_tailscale_flag() -> None:
    args = build_parser().parse_args(["serve", "--tailscale"])
    assert args.tailscale is True


def test_run_team_role_defaults_to_none_when_omitted() -> None:
    args = build_parser().parse_args(["run-team"])
    assert args.role is None


def test_parse_roles_splits_name_and_task_text_on_the_first_colon() -> None:
    roles = _parse_roles(["builder:implement it", "reviewer:review it: carefully"])
    assert roles == [
        {"name": "builder", "text": "implement it"},
        {"name": "reviewer", "text": "review it: carefully"},
    ]


def test_parse_roles_requires_at_least_one() -> None:
    with pytest.raises(ConfigError, match="at least one"):
        _parse_roles(None)
    with pytest.raises(ConfigError, match="at least one"):
        _parse_roles([])


def test_parse_roles_rejects_a_value_with_no_colon() -> None:
    with pytest.raises(ConfigError, match="NAME:TASK_TEXT"):
        _parse_roles(["builder-implement-it"])


def test_parse_roles_rejects_an_empty_name_or_text() -> None:
    with pytest.raises(ConfigError, match="NAME:TASK_TEXT"):
        _parse_roles([":implement it"])
    with pytest.raises(ConfigError, match="NAME:TASK_TEXT"):
        _parse_roles(["builder:"])


def test_parse_roles_rejects_a_duplicate_name() -> None:
    with pytest.raises(ConfigError, match="declared more than once"):
        _parse_roles(["builder:implement it", "builder:implement it again"])


def test_role_backend_parsing() -> None:
    from cuttlefish.cli import _parse_role_backends, _parse_role_definitions

    assert _parse_role_backends(["a=codex"], {"a", "b"}) == {"a": "codex"}
    for bad in (["a"], ["z=codex"], ["a=nope"]):
        with pytest.raises(ConfigError):
            _parse_role_backends(bad, {"a"})
    roles = _parse_role_definitions(["a:voice", "b"], ["b=claude-code"])
    assert [(r.name, r.persona, r.backend) for r in roles] == [
        ("a", "voice", None),
        ("b", "", "claude-code"),
    ]


def test_resolve_agent_backend_override_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cuttlefish.config import resolve_agent_backend

    monkeypatch.setenv("CUTTLEFISH_AGENT_BACKEND", "codex")
    assert resolve_agent_backend() == "codex"
    assert resolve_agent_backend("claude-code") == "claude-code"
    with pytest.raises(ConfigError):
        resolve_agent_backend("nope")


def test_mode_defaults_to_standard_and_is_omitted_from_a_default_runs_input() -> None:
    from cuttlefish.cli import _mode_input, build_parser

    assert build_parser().parse_args(["run", "t"]).mode == "standard"
    assert _mode_input("standard") == {}
    assert _mode_input("auto") == {"access": "auto"}
    assert _mode_input("ask-first") == {"access": "ask-first"}


def test_mode_rejects_a_value_that_is_not_a_project_mode() -> None:
    import pytest

    from cuttlefish.cli import build_parser

    for bad in ("read-only", "yolo"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "t", "--mode", bad])


def test_serve_ctrl_c_stops_cleanly_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """uvicorn drains on SIGINT, then asyncio.run re-raises it as KeyboardInterrupt."""
    from cuttlefish import cli

    def _interrupted(coro: object) -> int:
        close = getattr(coro, "close", None)
        if close is not None:
            close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.logsetup, "configure", lambda **_: None)
    monkeypatch.setattr(cli, "_serve", lambda args: None)
    monkeypatch.setattr(cli.asyncio, "run", _interrupted)
    assert cli.main(["serve"]) == cli.EXIT_OK
    assert "stopped" in capsys.readouterr().out
