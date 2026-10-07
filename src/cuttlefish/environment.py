"""What a project needs to run its code: detected from its files, never by running anything
(ADR-0029 decision 2, layer 2; V5-E2).

``detect(root)`` reads marker files and version hints at the project root and returns an
:class:`EnvironmentSpec`. It never executes a tool: not ``uv``, not ``npm``, not ``python
--version``. A probe that runs the project's own tooling is a side effect that happens before
anyone has agreed to run that project's code, the same rule as kopicode's ``verify.Discover``.
What this therefore cannot know (is the lockfile in sync with what is installed? which Node is
on ``PATH``?) is reported as unknown, never guessed. Preparing an environment is V5-E3.

Only the project root is read. A monorepo's ``frontend/package.json`` is not found from the
root; that is a deliberate, named limit (``agent_docs/known-gaps.md``), not an oversight.
``cuttlefish.fleet.fs`` keeps its own coarser language markers for the folder picker.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Ecosystem = Literal["python", "node", "go", "rust", "java", "ruby"]

#: A marker file is never read past this: a version hint is a few bytes, and a multi-megabyte
#: lockfile or a data file named like a manifest must not make a page load slow.
_MAX_READ = 1_000_000


@dataclass(frozen=True, slots=True)
class EcosystemEnv:
    """One ecosystem found at the project root."""

    ecosystem: Ecosystem
    #: The package tool the files point to (``uv``, ``pnpm``, ``cargo``...), None when unclear.
    tool: str | None = None
    #: Manifest files found, relative to the root.
    manifests: tuple[str, ...] = ()
    lockfile: str | None = None
    #: A version the project asks for (``3.12``, ``22``, ``1.22``), from the first hint found.
    version_hint: str | None = None
    #: Where the project's own install lives (``.venv``, ``node_modules``) when it exists.
    env_dir: str | None = None
    #: True when ``env_dir`` exists, False when this ecosystem keeps one in the project and it
    #: is absent, None when there is nothing in the project to look for (Go, Maven...).
    installed: bool | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "tool": self.tool,
            "manifests": list(self.manifests),
            "lockfile": self.lockfile,
            "version_hint": self.version_hint,
            "env_dir": self.env_dir,
            "installed": self.installed,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    root: str
    ecosystems: tuple[EcosystemEnv, ...]
    #: False when the project's folder is not there at all (moved or deleted), which is not the
    #: same answer as "nothing recognised in it".
    root_exists: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "root_exists": self.root_exists,
            "ecosystems": [e.to_json() for e in self.ecosystems],
        }

    def summary(self) -> str:
        """One line for `cuttlefish doctor`: ``Python (uv, .venv present); Node (pnpm, ...)``."""
        if not self.root_exists:
            return "the project folder does not exist"
        if not self.ecosystems:
            return "no recognised project files at the root"
        return "; ".join(_describe(e) for e in self.ecosystems)


_NAMES: dict[Ecosystem, str] = {
    "python": "Python",
    "node": "Node",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
    "ruby": "Ruby",
}


def ecosystem_name(ecosystem: Ecosystem) -> str:
    return _NAMES[ecosystem]


def _describe(env: EcosystemEnv) -> str:
    parts = [env.tool] if env.tool else []
    if env.version_hint:
        parts.append(f"wants {env.version_hint}")
    if env.installed is True:
        parts.append(f"{env.env_dir} present")
    elif env.installed is False:
        parts.append(f"{_DEFAULT_ENV_DIR[env.ecosystem]} missing")
    return (
        f"{ecosystem_name(env.ecosystem)} ({', '.join(parts)})"
        if parts
        else ecosystem_name(env.ecosystem)
    )


_DEFAULT_ENV_DIR: dict[Ecosystem, str] = {"python": ".venv", "node": "node_modules"}


# --- reading -----------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_READ:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _toml(path: Path) -> Mapping[str, Any] | None:
    """The parsed file; ``{}`` when there is no such file, None when it exists but cannot be
    read (malformed, too large), so a caller can say so instead of guessing."""
    if not path.is_file():
        return {}
    text = _read_text(path)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _json(path: Path) -> Mapping[str, Any] | None:
    """As :func:`_toml`, for JSON (a top-level value that is not an object is unreadable)."""
    if not path.is_file():
        return {}
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _first_line(path: Path) -> str | None:
    text = _read_text(path)
    if text is None:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def _tool_versions(root: Path) -> dict[str, str]:
    """``.tool-versions`` (asdf/mise): ``name version`` per line, first version wins."""
    text = _read_text(root / ".tool-versions")
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        words = line.split("#", 1)[0].split()
        if len(words) >= 2 and words[0] not in found:
            found[words[0]] = words[1]
    return found


def _mise_tool(root: Path, name: str) -> str | None:
    for filename in ("mise.toml", ".mise.toml"):
        tools = (_toml(root / filename) or {}).get("tools")
        if isinstance(tools, dict):
            value = tools.get(name)
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value and isinstance(value[0], str):
                return value[0]
    return None


def _present(root: Path, *names: str) -> tuple[str, ...]:
    return tuple(name for name in names if (root / name).is_file())


# --- one detector per ecosystem ----------------------------------------------------------


def _python(root: Path) -> EcosystemEnv | None:
    requirements = tuple(sorted(p.name for p in root.glob("requirements*.txt") if p.is_file()))
    manifests = _present(root, "pyproject.toml", "setup.py", "setup.cfg", "Pipfile") + requirements
    if not manifests:
        return None
    parsed = _toml(root / "pyproject.toml")
    pyproject: Mapping[str, Any] = parsed or {}
    raw_tools = pyproject.get("tool")
    tool_table: Mapping[str, Any] = raw_tools if isinstance(raw_tools, dict) else {}
    lockfile = next(
        (name for name in ("uv.lock", "poetry.lock", "Pipfile.lock") if (root / name).is_file()),
        None,
    )
    if lockfile == "uv.lock" or "uv" in tool_table:
        tool: str | None = "uv"
    elif lockfile == "poetry.lock" or "poetry" in tool_table:
        tool = "poetry"
    elif "Pipfile" in manifests:
        tool = "pipenv"
    else:
        tool = "pip"
    version = (
        _first_line(root / ".python-version")
        or _tool_versions(root).get("python")
        or _mise_tool(root, "python")
        or _requires_python(pyproject)
    )
    env_dir = next(
        (name for name in (".venv", "venv") if (root / name / "pyvenv.cfg").is_file()), None
    )
    notes: tuple[str, ...] = ()
    if parsed is None:
        notes += ("pyproject.toml could not be read",)
    if lockfile is None and tool == "pip" and not requirements and "pyproject.toml" in manifests:
        notes += ("no lockfile",)
    return EcosystemEnv(
        ecosystem="python",
        tool=tool,
        manifests=manifests,
        lockfile=lockfile,
        version_hint=version,
        env_dir=env_dir,
        installed=env_dir is not None,
        notes=notes,
    )


def _requires_python(pyproject: Mapping[str, Any]) -> str | None:
    project = pyproject.get("project")
    value = project.get("requires-python") if isinstance(project, dict) else None
    return value if isinstance(value, str) else None


_NODE_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
)


def _node(root: Path) -> EcosystemEnv | None:
    if not (root / "package.json").is_file():
        return None
    parsed = _json(root / "package.json")
    package: Mapping[str, Any] = parsed or {}
    lockfile, tool = next(
        ((name, tool) for name, tool in _NODE_LOCKFILES if (root / name).is_file()), (None, None)
    )
    manager = package.get("packageManager")
    if isinstance(manager, str) and manager:
        tool = manager.split("@", 1)[0] or tool
    notes: tuple[str, ...] = ()
    if parsed is None:
        notes += ("package.json could not be read",)
    if tool is None:
        tool = "npm"
        notes += ("no lockfile",)
    engines = package.get("engines")
    engine_node = engines.get("node") if isinstance(engines, dict) else None
    version = (
        _first_line(root / ".nvmrc")
        or _first_line(root / ".node-version")
        or _tool_versions(root).get("nodejs")
        or _tool_versions(root).get("node")
        or _mise_tool(root, "node")
        or (engine_node if isinstance(engine_node, str) else None)
    )
    installed = (root / "node_modules").is_dir()
    return EcosystemEnv(
        ecosystem="node",
        tool=tool,
        manifests=("package.json",),
        lockfile=lockfile,
        version_hint=version,
        env_dir="node_modules" if installed else None,
        installed=installed,
        notes=notes,
    )


def _go(root: Path) -> EcosystemEnv | None:
    text = _read_text(root / "go.mod")
    if text is None:
        return None
    version = None
    for line in text.splitlines():
        words = line.split()
        if len(words) == 2 and words[0] == "go":
            version = words[1]
            break
    return EcosystemEnv(
        ecosystem="go",
        tool="go",
        manifests=("go.mod",),
        lockfile="go.sum" if (root / "go.sum").is_file() else None,
        version_hint=version,
        notes=("modules are cached outside the project",),
    )


def _rust(root: Path) -> EcosystemEnv | None:
    if not (root / "Cargo.toml").is_file():
        return None
    toolchain = (_toml(root / "rust-toolchain.toml") or {}).get("toolchain")
    channel = toolchain.get("channel") if isinstance(toolchain, dict) else None
    version = channel if isinstance(channel, str) else _first_line(root / "rust-toolchain")
    return EcosystemEnv(
        ecosystem="rust",
        tool="cargo",
        manifests=("Cargo.toml",),
        lockfile="Cargo.lock" if (root / "Cargo.lock").is_file() else None,
        version_hint=version,
        notes=("builds into target/",),
    )


def _java(root: Path) -> EcosystemEnv | None:
    manifests = _present(
        root,
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    )
    if not manifests:
        return None
    tool = "maven" if "pom.xml" in manifests else "gradle"
    wrapper = _present(root, "mvnw", "gradlew")
    version = _tool_versions(root).get("java") or _mise_tool(root, "java")
    return EcosystemEnv(
        ecosystem="java",
        tool=tool,
        manifests=manifests,
        version_hint=version,
        notes=("has a wrapper script",) if wrapper else (),
    )


def _ruby(root: Path) -> EcosystemEnv | None:
    if not (root / "Gemfile").is_file():
        return None
    version = _first_line(root / ".ruby-version") or _tool_versions(root).get("ruby")
    return EcosystemEnv(
        ecosystem="ruby",
        tool="bundler",
        manifests=("Gemfile",),
        lockfile="Gemfile.lock" if (root / "Gemfile.lock").is_file() else None,
        version_hint=version,
    )


_DETECTORS: tuple[Callable[[Path], EcosystemEnv | None], ...] = (
    _python,
    _node,
    _go,
    _rust,
    _java,
    _ruby,
)


def detect(root: str | Path) -> EnvironmentSpec:
    """The ecosystems the project root's files point to. A missing root is an empty spec."""
    path = Path(root)
    found = tuple(env for detector in _DETECTORS if (env := detector(path)) is not None)
    return EnvironmentSpec(root=str(path), ecosystems=found, root_exists=path.is_dir())
