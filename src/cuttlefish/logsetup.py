"""The operational log: one setup, one file, one context (ADR-0029 decision 4).

Not a second transcript (ADR-0004): lifecycle lines are projected from the redacted
episodic journal (``EpisodicStore.append``); this module only decides where log records
go and what identifies them. Every line carries ``project``, ``team`` and ``role``,
taken from :data:`_CONTEXT` (a ``contextvars`` object, so concurrent teams in the one
daemon process never see each other's, the same reason as ``cuttlefish.runtime``,
ADR-0009) or from a record's own ``extra``.

Written to ``~/.cuttlefish/logs/cuttlefish.log`` (rotating) and to stderr. Level comes from
``CUTTLEFISH_LOG_LEVEL`` (default INFO). A record's text is scrubbed of the known secret
values in the environment before it is written, as defence in depth: the lines this build
emits are built from already-redacted events and carry no secret.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import logging.handlers
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from cuttlefish.episodic.redact import Redactor

LOG_LEVEL_ENV = "CUTTLEFISH_LOG_LEVEL"
DEFAULT_LEVEL = "INFO"
LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s [project=%(project)s team=%(team)s role=%(role)s] "
    "%(message)s"
)
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5
#: Marks the handlers this module installed, so `configure` is idempotent.
_MARK = "_cuttlefish_log_handler"

_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "cuttlefish_log_context", default=None
)


def default_log_path() -> Path:
    return Path.home() / ".cuttlefish" / "logs" / "cuttlefish.log"


@contextlib.contextmanager
def bind(**fields: str) -> Iterator[None]:
    """Add `fields` (``project``, ``team``, ``role``) to every record logged in this block,
    in this task and the ones it spawns, and nowhere else."""
    token = _CONTEXT.set({**(_CONTEXT.get() or {}), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def set_context(**fields: str) -> None:
    """Like :func:`bind` for the rest of the current task (a daemon task's whole life)."""
    _CONTEXT.set({**(_CONTEXT.get() or {}), **fields})


class ContextFilter(logging.Filter):
    """Stamps ``project``/``team``/``role`` on a record, from its own ``extra`` first, then
    the bound context, else ``-``."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _CONTEXT.get() or {}
        for name in ("project", "team", "role"):
            if getattr(record, name, None) in (None, ""):
                setattr(record, name, context.get(name) or "-")
        return True


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor if redactor is not None else Redactor()

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed, _ = self._redactor.scrub(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
        return True


def resolve_level(raw: str | None) -> int:
    """`CUTTLEFISH_LOG_LEVEL` as a logging level; anything unrecognised is INFO."""
    name = (raw or DEFAULT_LEVEL).strip().upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.INFO


def configure(*, log_path: Path | None = None, stream: bool = True) -> Path | None:
    """Route every record to the log file and stderr. Idempotent; returns the file's path,
    or None when it could not be opened (the stderr stream still works)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(handler)
        handler.close()

    raw_level = os.environ.get(LOG_LEVEL_ENV)
    level = resolve_level(raw_level)
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)
    context, redact = ContextFilter(), RedactingFilter()

    handlers: list[logging.Handler] = []
    path = log_path if log_path is not None else default_log_path()
    opened: Path | None = path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
            )
        )
    except OSError as exc:
        opened = None
        print(f"cuttlefish: cannot write the log file {path}: {exc}", file=sys.stderr)
    if stream:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(context)
        handler.addFilter(redact)
        setattr(handler, _MARK, True)
        root.addHandler(handler)

    if raw_level and not isinstance(logging.getLevelName(raw_level.strip().upper()), int):
        logging.getLogger(__name__).warning(
            "%s=%r is not a log level; using %s", LOG_LEVEL_ENV, raw_level, DEFAULT_LEVEL
        )
    # uvicorn's own loggers propagate to the root handlers above; its access log is
    # a line per request and stays off unless asked for with DEBUG.
    logging.getLogger("uvicorn.access").setLevel(max(level, logging.INFO))
    return opened
