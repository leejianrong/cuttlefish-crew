"""Unit: the operational log's one setup (ADR-0029 decision 4)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from cuttlefish import logsetup


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    for handler in [h for h in root.handlers if h not in before]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_a_record_reaches_the_file_with_its_project_team_and_role(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "cuttlefish.log"
    assert logsetup.configure(log_path=log, stream=False) == log

    with logsetup.bind(project="p1", team="t1", role="builder"):
        logging.getLogger("cuttlefish.test").info("hello")
    _flush()

    line = log.read_text().splitlines()[-1]
    assert "INFO cuttlefish.test [project=p1 team=t1 role=builder] hello" in line


def test_a_record_outside_any_binding_says_dash(tmp_path: Path) -> None:
    log = tmp_path / "cuttlefish.log"
    logsetup.configure(log_path=log, stream=False)

    logging.getLogger("cuttlefish.test").info("unbound")
    _flush()

    assert "[project=- team=- role=-] unbound" in log.read_text()


def test_a_records_own_extra_beats_the_bound_context(tmp_path: Path) -> None:
    log = tmp_path / "cuttlefish.log"
    logsetup.configure(log_path=log, stream=False)

    with logsetup.bind(project="p1", role="builder"):
        logging.getLogger("cuttlefish.test").info("x", extra={"role": "reviewer", "team": "t9"})
    _flush()

    assert "[project=p1 team=t9 role=reviewer]" in log.read_text()


def test_configure_twice_does_not_duplicate_lines(tmp_path: Path) -> None:
    log = tmp_path / "cuttlefish.log"
    logsetup.configure(log_path=log, stream=False)
    logsetup.configure(log_path=log, stream=False)

    logging.getLogger("cuttlefish.test").info("once")
    _flush()

    assert log.read_text().count("once") == 1


def test_the_level_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cuttlefish.log"
    monkeypatch.setenv("CUTTLEFISH_LOG_LEVEL", "debug")
    logsetup.configure(log_path=log, stream=False)

    logging.getLogger("cuttlefish.test").debug("fine detail")
    _flush()

    assert "fine detail" in log.read_text()


def test_an_unrecognised_level_falls_back_to_info_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cuttlefish.log"
    monkeypatch.setenv("CUTTLEFISH_LOG_LEVEL", "loud")
    logsetup.configure(log_path=log, stream=False)

    logging.getLogger("cuttlefish.test").debug("hidden")
    _flush()

    text = log.read_text()
    assert "hidden" not in text
    assert "not a log level" in text


def test_a_known_secret_value_is_scrubbed_from_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cuttlefish.log"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-very-secret-value-123")
    logsetup.configure(log_path=log, stream=False)

    logging.getLogger("cuttlefish.test").warning("failed with sk-or-very-secret-value-123")
    _flush()

    text = log.read_text()
    assert "sk-or-very-secret-value-123" not in text
    assert "failed with" in text


def test_an_unwritable_log_path_does_not_stop_startup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")

    assert logsetup.configure(log_path=blocker / "logs" / "cuttlefish.log", stream=False) is None
    assert "cannot write the log file" in capsys.readouterr().err
