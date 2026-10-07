"""The one env-building helper every delegation backend shares (ADR-0006, ADR-0029).

Not a wire-format concern ADR-0003/ADR-0005's "no shared protocol between
backends" discipline is about — kopicode's, Claude Code's and Codex's own subprocess
invocations still speak their own native CLI language. Building the `env=`
kwarg `asyncio.create_subprocess_exec` takes is identical logic either way,
so it lives once here rather than three times.

What a child process gets (V5-E4, Q58): **an allowlist, not a copy of the daemon's
environment.** The daemon's own environment holds everything `load_dotenv()` read from `.env`
(`CUTTLEFISH_SECRETS_KEY`, `E2B_API_KEY`, ...) and whatever `uv run` set; an agent's shell
inherits all of it. Instead the child gets:

1. the names in `_BASE` and `_BASE_PREFIXES` (``HOME``, ``LANG``, proxies, CA bundles, toolchain
   locations: nothing secret by itself);
2. what the operator names in ``CUTTLEFISH_AGENT_ENV_PASSTHROUGH`` (comma-separated, ``*``
   as a suffix matches a prefix), and what the backend asks for (`passthrough`);
3. for installs only (`tools=True`), the package tools' own settings (``NPM_*``, ``UV_*``,
   ``PIP_*``, ...), which is where a private registry's token lives;
4. ``PATH`` without cuttlefish's own venv and, on WSL, without ``/mnt/...`` (Windows Python
   and Node), unless ``CUTTLEFISH_KEEP_WINDOWS_PATH=1``;
5. the project's own environment first on ``PATH`` (`root`): its ``.venv`` (with
   ``VIRTUAL_ENV``) and ``node_modules/.bin``;
6. `env`, the credentials a backend declared, last, so they always win.

A variable that is not here is simply absent: an agent that needs one says so, and the fix is
one name in the passthrough, never going back to inheriting everything.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

_LOG = logging.getLogger(__name__)

PASSTHROUGH_ENV = "CUTTLEFISH_AGENT_ENV_PASSTHROUGH"
KEEP_WINDOWS_PATH_ENV = "CUTTLEFISH_KEEP_WINDOWS_PATH"

_BASE = frozenset(
    {
        # who and where
        "HOME", "USER", "LOGNAME", "SHELL", "HOSTNAME", "TMPDIR", "TEMP", "TMP", "TZ",
        # how to talk to a terminal and which language
        "LANG", "LANGUAGE", "TERM", "COLORTERM", "NO_COLOR",
        # the network
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "FTP_PROXY",
        "http_proxy", "https_proxy", "no_proxy", "all_proxy", "ftp_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        # where a toolchain keeps its things (locations, never credentials)
        "GOPATH", "GOROOT", "GOMODCACHE", "GOCACHE", "GOFLAGS",
        "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "JAVA_HOME", "PYENV_ROOT",
    }
)  # fmt: skip
_BASE_PREFIXES = ("LC_", "XDG_")

#: Only for an install (`tools=True`): the package tools' own settings, where a private
#: registry's token lives. Not given to an agent: an install is the person's own `npm ci`.
_TOOL_PREFIXES = (
    "NPM_", "npm_config_", "NODE_", "YARN_", "PNPM_", "BUN_", "COREPACK_", "UV_", "PIP_",
)  # fmt: skip


def _matches(name: str, exact: frozenset[str], prefixes: Iterable[str]) -> bool:
    return name in exact or any(name.startswith(prefix) for prefix in prefixes)


def _operator_passthrough() -> tuple[frozenset[str], tuple[str, ...]]:
    return _split_patterns(os.environ.get(PASSTHROUGH_ENV, "").split(","))


def _split_patterns(patterns: Iterable[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    exact: set[str] = set()
    prefixes: list[str] = []
    for raw in patterns:
        pattern = raw.strip()
        if not pattern:
            continue
        if pattern.endswith("*"):
            prefixes.append(pattern[:-1])
        else:
            exact.add(pattern)
    return frozenset(exact), tuple(prefixes)


def _own_venv() -> Path | None:
    """The virtualenv cuttlefish itself runs from, or None when it isn't in one."""
    if sys.prefix == sys.base_prefix:
        return None
    return Path(sys.prefix).resolve()


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def scrub_own_venv(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of `environ` without cuttlefish's own virtualenv (ADR-0029 decision 2.1).

    `uv run cuttlefish serve` puts cuttlefish's `.venv/bin` first on `PATH` and sets
    `VIRTUAL_ENV`; an agent inheriting that runs `python`/`pip` against cuttlefish's
    packages, not the project's (the failure that started ADR-0029). Only cuttlefish's
    *own* venv goes: a `VIRTUAL_ENV` naming some other environment is left alone.
    """
    env = dict(environ)
    own = _own_venv()
    if own is None:
        return env
    virtual_env = env.get("VIRTUAL_ENV")
    if virtual_env and Path(virtual_env).resolve() == own:
        del env["VIRTUAL_ENV"]
        env.pop("VIRTUAL_ENV_PROMPT", None)
    if "PATH" in env:
        kept = [
            entry
            for entry in env["PATH"].split(os.pathsep)
            if not (entry and _under(Path(entry).resolve(), own))
        ]
        env["PATH"] = os.pathsep.join(kept)
    return env


def _clean_path(path: str) -> str:
    """`path` without cuttlefish's own venv and, unless asked to keep them, WSL's Windows
    directories (`/mnt/c/...`: an agent could run Windows `python` or `npm` by accident)."""
    entries = scrub_own_venv({"PATH": path})["PATH"].split(os.pathsep)
    if os.environ.get(KEEP_WINDOWS_PATH_ENV) != "1":
        entries = [e for e in entries if not (e == "/mnt" or e.startswith("/mnt/"))]
    return os.pathsep.join(e for e in entries if e)


def project_overlay(root: str | Path, path: str) -> dict[str, str]:
    """The project's own environment on top of `path` (ADR-0029, layer 4): its `.venv` (and
    `VIRTUAL_ENV`) and `node_modules/.bin`, each first on `PATH` only when it exists."""
    base = Path(root)
    front: list[str] = []
    overlay: dict[str, str] = {}
    venv = base / ".venv"
    if (venv / "pyvenv.cfg").is_file() and (venv / "bin").is_dir():
        overlay["VIRTUAL_ENV"] = str(venv)
        front.append(str(venv / "bin"))
    node_bin = base / "node_modules" / ".bin"
    if node_bin.is_dir():
        front.append(str(node_bin))
    if front:
        overlay["PATH"] = os.pathsep.join([*front, path]) if path else os.pathsep.join(front)
    return overlay


def merge_env(
    env: Mapping[str, str] | None = None,
    *,
    root: str | Path | None = None,
    passthrough: Sequence[str] = (),
    tools: bool = False,
) -> dict[str, str]:
    """The `env=` kwarg to hand `asyncio.create_subprocess_exec`: an allowlisted copy of the
    daemon's environment (module doc), the project's own environment on `PATH` when `root` is
    given, then `env` (the credentials a backend declared) on top.

    Never `None`: `None` means "inherit everything", which is what leaked cuttlefish's venv and
    its `.env`. An empty `env` still yields the allowlisted environment, not an empty one (an
    empty dict would leave the child no `PATH` at all)."""
    operator_exact, operator_prefixes = _operator_passthrough()
    backend_exact, backend_prefixes = _split_patterns(passthrough)
    exact = _BASE | operator_exact | backend_exact
    prefixes = (
        *_BASE_PREFIXES,
        *operator_prefixes,
        *backend_prefixes,
        *(_TOOL_PREFIXES if tools else ()),
    )
    declared = env or {}
    kept: dict[str, str] = {}
    withheld: list[str] = []
    for name, value in os.environ.items():
        if name == "PATH":
            continue
        if _matches(name, exact, prefixes):
            kept[name] = value
        elif name not in declared:  # a declared credential is passed, so it is not withheld
            withheld.append(name)
    path = _clean_path(os.environ.get("PATH", ""))
    if root is not None:
        overlay = project_overlay(root, path)
        path = overlay.pop("PATH", path)
        kept.update(overlay)
    if path:
        kept["PATH"] = path
    if withheld and _LOG.isEnabledFor(logging.DEBUG):
        _LOG.debug(
            "agent environment: withheld %d names: %s", len(withheld), ", ".join(sorted(withheld))
        )
    return {**kept, **(env or {})}


def withheld_names(environ: Mapping[str, str] | None = None) -> list[str]:
    """The names an agent would not be given from `environ` (default: this process's), sorted.
    Names only, never values: `cuttlefish doctor` shows them so a missing variable is easy to
    explain."""
    source = os.environ if environ is None else environ
    operator_exact, operator_prefixes = _operator_passthrough()
    exact = _BASE | operator_exact
    prefixes = (*_BASE_PREFIXES, *operator_prefixes)
    return sorted(n for n in source if n != "PATH" and not _matches(n, exact, prefixes))
