"""What a project needs to run its code: detected from its files, never by running anything
(ADR-0029 decision 2, layer 2; V5-E2).

``detect(root)`` reads marker files and version hints at the project root and returns an
:class:`EnvironmentSpec`. It never executes a tool: not ``uv``, not ``npm``, not ``python
--version``. A probe that runs the project's own tooling is a side effect that happens before
anyone has agreed to run that project's code, the same rule as kopicode's ``verify.Discover``.
What this therefore cannot know (is the lockfile in sync with what is installed? which Node is
on ``PATH``?) is reported as unknown, never guessed. Preparing an environment is V5-E3.

The project root is read first, then each folder directly under it (V5-E6b), so a
``frontend/package.json`` or ``backend/pyproject.toml`` is found. Deeper folders are not, and a
subfolder of an ecosystem the root already has is skipped: a root ``package.json`` with workspaces
installs its members itself, and a second ``npm ci`` in one would be wrong. Both are named limits
(``agent_docs/known-gaps.md``). ``cuttlefish.fleet.fs`` keeps its own coarser language markers for
the folder picker.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Ecosystem = Literal["python", "node", "go", "rust", "java", "ruby"]

#: A project's ``env_prepare`` setting (V5-E3): ask before installing its dependencies (the
#: default), install automatically when something is stale, or never. Kept here, with no
#: imports of its own, so the project store can use it without a cycle.
PREPARE_MODES = ("ask", "auto", "off")
DEFAULT_PREPARE_MODE = "ask"

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
    #: The folder this was found in, relative to the project root; ``.`` for the root itself.
    #: Every other path on this object (manifests, lockfile, env_dir) is relative to it.
    path: str = "."

    def to_json(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "path": self.path,
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


def brief(spec: EnvironmentSpec, *, installing: Collection[str] = ()) -> str:
    """A short note for an agent's brief on how to run things in this project (ADR-0029,
    decision 2.4): which environment is its own, that it is already on ``PATH``, and not to
    install globally. Says only what is true of what cuttlefish sets up; an ecosystem it does not
    describe gets no line rather than a guess. `installing` names ecosystems
    cuttlefish is about to install, so their note describes the result."""
    lines: list[str] = []
    for env in spec.ecosystems:
        if env.path != ".":
            lines.append(_sub_brief(env))
            continue
        ready = bool(env.installed) or env.ecosystem in installing
        if env.ecosystem == "python":
            if ready:
                how = (
                    "run tools with `uv run <command>`"
                    if env.tool == "uv"
                    else "`python` and `pip` are its own"
                )
                lines.append(
                    "Python: use the project's own environment (`.venv`, already first on "
                    f"PATH); {how}. Don't install packages globally."
                )
            else:
                lines.append(
                    "Python: its dependencies are not installed (no `.venv`). If you need them, "
                    "say so; don't install packages globally."
                )
        elif env.ecosystem == "node":
            tool = env.tool or "npm"
            if ready:
                lines.append(
                    f"Node ({tool}): dependencies are in `node_modules` and its `.bin` is on "
                    f"PATH; run scripts with `{tool} run <script>`. Don't install globally."
                )
            else:
                lines.append(
                    "Node: its dependencies are not installed (no `node_modules`). If you need "
                    "them, say so; don't install globally."
                )
        elif env.ecosystem in _OTHER_BRIEF:
            lines.append(_OTHER_BRIEF[env.ecosystem](env))
    return ("Environment: " + " ".join(lines)) if lines else ""


def _sub_brief(env: EcosystemEnv) -> str:
    """A project in a subfolder: cuttlefish only puts the root's own environment on ``PATH``, so
    say where this one is and to work from that folder."""
    where = f"`{env.path}/`"
    name = ecosystem_name(env.ecosystem)
    if env.ecosystem == "python":
        return (
            f"{name} in {where}: its environment is `{env.path}/.venv`, which is "
            f"not on PATH; `cd {env.path}` and use `{env.path}/.venv/bin/python` (or `uv run`). "
            "Don't install packages globally."
        )
    if env.ecosystem == "node":
        return (
            f"{name} ({env.tool or 'npm'}) in {where}: `cd {env.path}` first; its "
            "`node_modules/.bin` is not on PATH, so run scripts with the package manager. "
            "Don't install globally."
        )
    return f"{name} in {where}: `cd {env.path}` first. " + _OTHER_BRIEF[env.ecosystem](env)


# Go, Rust, Java and Ruby keep their downloads outside the project, so cuttlefish cannot tell from
# the folder whether they are fetched; these say how to run things and claim nothing about that.
def _go_brief(env: EcosystemEnv) -> str:
    return "Go: modules are cached outside the project; use `go test ./...` and `go build ./...`."


def _rust_brief(env: EcosystemEnv) -> str:
    return "Rust: use `cargo test` and `cargo build`; builds go into `target/`."


def _ruby_brief(env: EcosystemEnv) -> str:
    return "Ruby: run tools with `bundle exec <command>`; gems are installed outside the project."


def _java_brief(env: EcosystemEnv) -> str:
    wrapper = any("wrapper" in note for note in env.notes)
    tool = "the project's wrapper script (`./mvnw` or `./gradlew`)" if wrapper else str(env.tool)
    return f"Java ({env.tool}): build and test with {tool}."


_OTHER_BRIEF: dict[Ecosystem, Callable[[EcosystemEnv], str]] = {
    "go": _go_brief,
    "rust": _rust_brief,
    "ruby": _ruby_brief,
    "java": _java_brief,
}


def _describe(env: EcosystemEnv) -> str:
    parts = [env.tool] if env.tool else []
    if env.version_hint:
        parts.append(f"wants {env.version_hint}")
    if env.installed is True:
        parts.append(f"{env.env_dir} present")
    elif env.installed is False:
        parts.append(f"{_DEFAULT_ENV_DIR[env.ecosystem]} missing")
    name = ecosystem_name(env.ecosystem) + (f" in {env.path}/" if env.path != "." else "")
    return f"{name} ({', '.join(parts)})" if parts else name


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


#: Folders never looked into for a nested project: dependencies, build output and tool state.
_SKIP_DIRS = frozenset(
    {
        "node_modules", "vendor", "venv", "target", "dist", "build", "out", "site-packages",
        "__pycache__", "third_party", "bower_components", "coverage",
    }
)  # fmt: skip

#: How many nested projects are reported, so a repository of many examples is not a wall of rows.
MAX_SUBPROJECTS = 12


def _subfolders(root: Path) -> list[Path]:
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if entry.is_dir()
        and not entry.is_symlink()
        and not entry.name.startswith(".")
        and entry.name not in _SKIP_DIRS
    ]


def detect(root: str | Path) -> EnvironmentSpec:
    """The ecosystems the project root's files point to, then those one folder down. A missing
    root is an empty spec."""
    path = Path(root)
    found = [env for detector in _DETECTORS if (env := detector(path)) is not None]
    at_root = {env.ecosystem for env in found}
    nested = 0
    for folder in _subfolders(path):
        for detector in _DETECTORS:
            env = detector(folder)
            if env is None or env.ecosystem in at_root:
                continue  # a workspace member or a second copy: the root's own install covers it
            if nested >= MAX_SUBPROJECTS:
                break
            found.append(dataclasses.replace(env, path=folder.name))
            nested += 1
    return EnvironmentSpec(root=str(path), ecosystems=tuple(found), root_exists=path.is_dir())
