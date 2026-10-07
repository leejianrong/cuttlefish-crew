"""Unit: environment detection reads files and never runs anything (ADR-0029, V5-E2)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cuttlefish import environment


def _write(root: Path, name: str, text: str = "") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _only(root: Path, ecosystem: str) -> environment.EcosystemEnv:
    spec = environment.detect(root)
    return next(e for e in spec.ecosystems if e.ecosystem == ecosystem)


def test_an_empty_root_has_no_ecosystems(tmp_path: Path) -> None:
    spec = environment.detect(tmp_path)

    assert (spec.ecosystems, spec.root_exists) == ((), True)
    assert "no recognised" in spec.summary()


def test_a_missing_root_is_not_the_same_answer_as_an_empty_one(tmp_path: Path) -> None:
    spec = environment.detect(tmp_path / "gone")

    assert (spec.ecosystems, spec.root_exists) == ((), False)
    assert spec.summary() == "the project folder does not exist"
    assert spec.to_json()["root_exists"] is False


def test_a_uv_project_with_a_venv(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\nrequires-python = ">=3.12"\n')
    _write(tmp_path, "uv.lock")
    _write(tmp_path, ".venv/pyvenv.cfg")

    python = _only(tmp_path, "python")

    assert (python.tool, python.lockfile) == ("uv", "uv.lock")
    assert (python.env_dir, python.installed) == (".venv", True)
    assert python.version_hint == ">=3.12"


def test_python_version_hints_win_in_order(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nrequires-python = ">=3.10"\n')
    _write(tmp_path, ".tool-versions", "python 3.11.4\nnodejs 20\n")
    assert _only(tmp_path, "python").version_hint == "3.11.4"
    _write(tmp_path, ".python-version", "# pinned\n3.13\n")
    assert _only(tmp_path, "python").version_hint == "3.13"


def test_a_requirements_project_is_pip_and_a_missing_venv_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "numpy\n")
    _write(tmp_path, "requirements-dev.txt", "pytest\n")

    python = _only(tmp_path, "python")

    assert python.tool == "pip"
    assert python.manifests == ("requirements-dev.txt", "requirements.txt")
    assert (python.env_dir, python.installed) == (None, False)
    assert "missing" in environment.detect(tmp_path).summary()


def test_poetry_and_pipenv_are_told_apart(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.poetry]\nname = 'x'\n")
    assert _only(tmp_path, "python").tool == "poetry"
    other = tmp_path / "other"
    _write(other, "Pipfile")
    assert _only(other, "python").tool == "pipenv"


def test_a_directory_named_venv_without_pyvenv_cfg_is_not_a_venv(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    (tmp_path / ".venv").mkdir()

    assert _only(tmp_path, "python").installed is False


@pytest.mark.parametrize(
    ("lockfile", "tool"),
    [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
    ],
)
def test_the_node_package_tool_comes_from_the_lockfile(
    tmp_path: Path, lockfile: str, tool: str
) -> None:
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, lockfile)

    node = _only(tmp_path, "node")

    assert (node.tool, node.lockfile) == (tool, lockfile)


def test_the_package_manager_field_beats_the_lockfile_and_engines_gives_a_version(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "package.json",
        json.dumps({"packageManager": "pnpm@9.1.0", "engines": {"node": ">=20"}}),
    )
    _write(tmp_path, "package-lock.json")

    node = _only(tmp_path, "node")

    assert node.tool == "pnpm"
    assert node.version_hint == ">=20"
    _write(tmp_path, ".nvmrc", "22\n")
    assert _only(tmp_path, "node").version_hint == "22"


def test_node_without_a_lockfile_says_so_and_node_modules_is_looked_for(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")
    node = _only(tmp_path, "node")
    assert (node.tool, node.notes, node.installed) == ("npm", ("no lockfile",), False)

    (tmp_path / "node_modules").mkdir()
    node = _only(tmp_path, "node")
    assert (node.env_dir, node.installed) == ("node_modules", True)


def test_a_broken_manifest_is_read_as_empty_not_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{ not json")
    _write(tmp_path, "pyproject.toml", "this is = = not toml")

    spec = environment.detect(tmp_path)

    assert {e.ecosystem for e in spec.ecosystems} == {"node", "python"}
    by = {e.ecosystem: e for e in spec.ecosystems}
    assert "package.json could not be read" in by["node"].notes
    assert "pyproject.toml could not be read" in by["python"].notes


def test_go_rust_java_and_ruby_are_detected_with_what_they_say(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", "module x\n\ngo 1.22\n")
    _write(tmp_path, "go.sum")
    _write(tmp_path, "Cargo.toml")
    _write(tmp_path, "rust-toolchain.toml", '[toolchain]\nchannel = "1.80"\n')
    _write(tmp_path, "pom.xml")
    _write(tmp_path, "mvnw")
    _write(tmp_path, "Gemfile")
    _write(tmp_path, ".ruby-version", "3.3.0\n")

    spec = environment.detect(tmp_path)
    by = {e.ecosystem: e for e in spec.ecosystems}

    assert (by["go"].version_hint, by["go"].lockfile) == ("1.22", "go.sum")
    assert by["rust"].version_hint == "1.80"
    assert (by["java"].tool, by["java"].notes) == ("maven", ("has a wrapper script",))
    assert (by["ruby"].tool, by["ruby"].version_hint) == ("bundler", "3.3.0")
    assert [e.ecosystem for e in spec.ecosystems] == ["go", "rust", "java", "ruby"]
    assert by["go"].installed is None


def test_mise_toml_supplies_a_version(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "mise.toml", '[tools]\nnode = "22"\n')

    assert _only(tmp_path, "node").version_hint == "22"


def test_only_the_root_is_read(tmp_path: Path) -> None:
    _write(tmp_path, "frontend/package.json", "{}")

    assert environment.detect(tmp_path).ecosystems == ()


def test_a_huge_marker_file_is_not_read(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"packageManager": "' + "x" * 2_000_000 + '"}')

    node = _only(tmp_path, "node")

    assert node.tool == "npm"  # the oversize file was treated as unreadable, not parsed
    assert "package.json could not be read" in node.notes


def test_detection_never_runs_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("detect() must not execute a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("os.system", forbidden)
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path, "package.json", "{}")

    assert len(environment.detect(tmp_path).ecosystems) == 2


def test_to_json_round_trips_the_shape(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")

    body = environment.detect(tmp_path).to_json()

    assert body["root"] == str(tmp_path)
    assert body["ecosystems"][0]["ecosystem"] == "node"
    assert body["ecosystems"][0]["manifests"] == ["package.json"]
    json.dumps(body)


# --- the note in an agent's brief (V5-E4) -----------------------------------------------------


def test_a_python_uv_project_with_a_venv_is_told_to_use_it_and_uv_run(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path, "uv.lock")
    _write(tmp_path, ".venv/pyvenv.cfg")

    note = environment.brief(environment.detect(tmp_path))

    assert note.startswith("Environment: Python: use the project's own environment")
    assert "`uv run <command>`" in note and "Don't install packages globally" in note


def test_a_missing_install_is_described_as_missing_unless_it_is_about_to_be_made(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "requirements.txt", "numpy\n")
    spec = environment.detect(tmp_path)

    assert "not installed (no `.venv`)" in environment.brief(spec)
    assert "`python` and `pip` are its own" in environment.brief(spec, installing={"python"})


def test_node_gets_its_own_tool_in_the_note(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"packageManager": "pnpm@9.1.0"}')
    (tmp_path / "node_modules").mkdir()

    note = environment.brief(environment.detect(tmp_path))

    assert "Node (pnpm)" in note and "`pnpm run <script>`" in note and "`.bin` is on PATH" in note


def test_other_ecosystems_and_empty_projects_get_no_claims(tmp_path: Path) -> None:
    assert environment.brief(environment.detect(tmp_path)) == ""
    _write(tmp_path, "go.mod", "module x\n\ngo 1.22\n")

    assert environment.brief(environment.detect(tmp_path)) == ""


def test_a_polyglot_project_gets_one_line_per_ecosystem(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "x\n")
    _write(tmp_path, "package.json", "{}")

    note = environment.brief(environment.detect(tmp_path), installing={"python", "node"})

    assert note.count("Python:") == 1 and note.count("Node (") == 1
