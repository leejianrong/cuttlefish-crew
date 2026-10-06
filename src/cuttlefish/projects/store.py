"""A `Project`'s stable identity, outside any single project's own `.cuttlefish/` (ADR-0009).

``~/.cuttlefish/projects.db`` — deliberately *not* colocated with any project
directory's own state (`secrets.db`, `episodic.db`, satay's own `.satay/`, all still
rooted at that project's own `--root`/cwd, unchanged by this module): a fleet daemon
overseeing many projects needs one registry that outlives, and isn't rooted in, any
single one of them.

A role's own ``persona`` is durable here (docs/QUESTIONS.md Q31); its task text is not
— that stays as ephemeral as today's ``--role NAME:TASK_TEXT`` (ADR-0007), composed
fresh each time a team starts (`cuttlefish.fleet`).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cuttlefish.permissions import DEFAULT_MODE


def default_projects_db() -> Path:
    """``~/.cuttlefish/projects.db`` — hardcoded this slice, no env override yet
    (docs/QUESTIONS.md Q47): a concrete need for more than one registry per
    operator machine hasn't shown up, so this isn't built ahead of one.

    A function, not a module-level constant: `Path.home()` resolved once at import
    time would freeze whatever `HOME` happened to be when `cuttlefish` first
    imported this module, the same reason `cuttlefish.config.secrets_db_path`
    resolves its own default fresh on every call rather than once at import time.
    """
    return Path.home() / ".cuttlefish" / "projects.db"


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT NOT NULL,
    secrets_scope TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    last_team_id TEXT,
    allow_json TEXT NOT NULL DEFAULT '[]',
    last_team_roles_json TEXT NOT NULL DEFAULT '[]',
    last_team_require_approval INTEGER NOT NULL DEFAULT 0,
    max_tokens INTEGER,
    max_cost_usd REAL,
    backend TEXT,
    mode TEXT,
    presets_json TEXT
)
"""

#: `allow_json` was added after slice D1 shipped -- an operator's existing,
#: on-disk `projects.db` predates it. `CREATE TABLE IF NOT EXISTS` alone would
#: leave that column missing on every such file; `ProjectStore.__init__` runs
#: this once, guarded by `PRAGMA table_info`, so a fresh database (which already
#: has the column from `_CREATE_TABLE`) is a harmless no-op.
_ADD_ALLOW_COLUMN = "ALTER TABLE projects ADD COLUMN allow_json TEXT NOT NULL DEFAULT '[]'"

#: `last_team_roles_json` was added for KAN-1703/ADR-0010 -- a daemon restart can
#: only resume `last_team_id` if it can rebuild the *identical* `TeamInput` that
#: run started with (satay's own resume primitive drives on whatever
#: `workflow_input` this call passes, not something it rehydrates from the
#: journal itself); persisting each role's already-composed text/allow at start
#: time is the only durable place that input can come from after a restart.
#: Migrated the same way `allow_json` was.
_ADD_LAST_TEAM_ROLES_COLUMN = (
    "ALTER TABLE projects ADD COLUMN last_team_roles_json TEXT NOT NULL DEFAULT '[]'"
)

#: `last_team_require_approval` was added for KAN-1711 -- the same resume-fidelity
#: reason `last_team_roles_json` was: `TeamInput.require_approval` is team-wide
#: config a daemon restart must rebuild identically, not something the journal
#: itself records. Migrated the same way.
_ADD_LAST_TEAM_REQUIRE_APPROVAL_COLUMN = (
    "ALTER TABLE projects ADD COLUMN last_team_require_approval INTEGER NOT NULL DEFAULT 0"
)

#: `max_tokens`/`max_cost_usd` were added for KAN-1712/ADR-0017 -- a project's own
#: run-scoped usage ceiling (Q53's own precedent: reviewed once here, not retyped
#: per `FleetDaemon.start` call, since a daemon-started team has no CLI flag of its
#: own to carry it). Both nullable and default `NULL` ("no ceiling") rather than a
#: migrated `NOT NULL DEFAULT 0` -- `0` is a real, meaningful configuration here
#: (`cuttlefish.budget.exceeded`'s own docstring), so an existing row predating
#: this column must decode to "unset," never to "stop immediately."
_ADD_MAX_TOKENS_COLUMN = "ALTER TABLE projects ADD COLUMN max_tokens INTEGER"
_ADD_MAX_COST_USD_COLUMN = "ALTER TABLE projects ADD COLUMN max_cost_usd REAL"

#: `backend` was added for KAN-1809 -- a project's own default agent backend,
#: nullable and defaulting to `NULL` ("use `CUTTLEFISH_AGENT_BACKEND`"), so an
#: existing row keeps its exact prior behaviour. A role's own backend lives inside
#: `roles_json`, which needed no migration.
_ADD_BACKEND_COLUMN = "ALTER TABLE projects ADD COLUMN backend TEXT"

#: `mode` was added for V4-C/ADR-0025 -- the project's permission mode, nullable and read as
#: "standard" when `NULL`, so an existing row keeps its exact prior behaviour.
_ADD_MODE_COLUMN = "ALTER TABLE projects ADD COLUMN mode TEXT"

#: `presets_json` was added for V4-F -- the command groups a project has switched on (a JSON
#: list of preset names). `NULL` means the defaults, so an existing row is unchanged.
_ADD_PRESETS_COLUMN = "ALTER TABLE projects ADD COLUMN presets_json TEXT"


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """One role's durable identity: a name and a persona (voice/personality, Q31).

    Never task text — a role's task is supplied fresh each time a team starts.
    """

    name: str
    persona: str = ""
    backend: str | None = None
    #: ``"read-only"`` restricts the role's shell to inspection (roles.py, ADR-0024); ``None``
    #: is the project's normal access.
    access: str | None = None


@dataclass(frozen=True, slots=True)
class PersistedRole:
    """One role's task text and policy exactly as composed when `last_team_id`
    last started (ADR-0010/KAN-1703) — durable only so a daemon restart can
    resume that run with the identical `TeamInput` satay's own resume-by-run_id
    primitive needs, never surfaced as a project's own reusable role definition.
    `RoleDefinition` above stays the persona-only, task-text-free durable role;
    ADR-0009's own reasoning for why task text is ephemeral is unchanged — this
    is a resume checkpoint, not a second place task text lives on purpose.
    """

    name: str
    text: str
    allow: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    backend: str | None = None
    access: str | None = None
    presets: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Project:
    """A project's stable identity (Q38's own deferral target, resolved by ADR-0009).

    ``secrets_scope`` carries forward exactly the meaning `--project NAME` already had
    (docs/QUESTIONS.md Q38) — defaulting to ``name`` so a project registered against an
    existing checkout keeps reading whatever `SecretsStore` scope it already used.

    ``allow`` is durable here for the same reason ``persona`` is (Q31, Q53): a
    project's trusted shell-command set is reviewed once, not retyped per
    `FleetDaemon.start` call — a daemon-launched team has no CLI `--allow` flag
    of its own to carry it. Defaults to ``()``: nothing declared on top of the
    built-in dev presets (ADR-0023).
    """

    id: str
    name: str
    root: str
    secrets_scope: str
    roles: tuple[RoleDefinition, ...] = field(default_factory=tuple)
    last_team_id: str | None = None
    allow: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    last_team_roles: tuple[PersistedRole, ...] = field(default_factory=tuple)
    last_team_require_approval: bool = False
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    backend: str | None = None
    #: The project's permission mode (ask-first, standard or auto; ADR-0025). A role's own
    #: `access` overrides it.
    mode: str = DEFAULT_MODE
    #: The command groups switched on (names from `delegate.presets.PRESETS`), or `None` for
    #: the defaults. `allow` is what is declared on top of them.
    presets: tuple[str, ...] | None = None

    def role(self, name: str) -> RoleDefinition | None:
        """The registered role definition named `name`, or `None` if this project
        never declared one — a start request naming an unregistered role still runs,
        just with no persona prefix (a graceful default, not a rejected request)."""
        for candidate in self.roles:
            if candidate.name == name:
                return candidate
        return None


class ProjectNotFoundError(LookupError):
    """No project with the given id is registered."""


def _encode_roles(roles: tuple[RoleDefinition, ...]) -> str:
    return json.dumps(
        [
            {
                "name": r.name,
                "persona": r.persona,
                **({"backend": r.backend} if r.backend else {}),
                **({"access": r.access} if r.access else {}),
            }
            for r in roles
        ]
    )


def _decode_roles(raw: str) -> tuple[RoleDefinition, ...]:
    return tuple(
        RoleDefinition(
            name=r["name"],
            persona=r.get("persona", ""),
            backend=r.get("backend"),
            access=r.get("access"),
        )
        for r in json.loads(raw)
    )


def _decode_presets(raw: str | None) -> tuple[str, ...] | None:
    return None if raw is None else tuple(json.loads(raw))


def _encode_allow(allow: tuple[tuple[str, ...], ...]) -> str:
    return json.dumps([list(command) for command in allow])


def _decode_allow(raw: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(command) for command in json.loads(raw))


def _encode_persisted_roles(roles: tuple[PersistedRole, ...]) -> str:
    return json.dumps(
        [
            {
                "name": r.name,
                "text": r.text,
                "allow": [list(c) for c in r.allow],
                **({"backend": r.backend} if r.backend else {}),
                **({"access": r.access} if r.access else {}),
                **({"presets": list(r.presets)} if r.presets is not None else {}),
            }
            for r in roles
        ]
    )


def _decode_persisted_roles(raw: str) -> tuple[PersistedRole, ...]:
    return tuple(
        PersistedRole(
            name=r["name"],
            text=r["text"],
            allow=tuple(tuple(c) for c in r.get("allow", [])),
            backend=r.get("backend"),
            access=r.get("access"),
            presets=tuple(r["presets"]) if r.get("presets") is not None else None,
        )
        for r in json.loads(raw)
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        root=row["root"],
        secrets_scope=row["secrets_scope"],
        roles=_decode_roles(row["roles_json"]),
        last_team_id=row["last_team_id"],
        allow=_decode_allow(row["allow_json"]),
        last_team_roles=_decode_persisted_roles(row["last_team_roles_json"]),
        last_team_require_approval=bool(row["last_team_require_approval"]),
        max_tokens=row["max_tokens"],
        max_cost_usd=row["max_cost_usd"],
        backend=row["backend"],
        mode=row["mode"] or DEFAULT_MODE,
        presets=_decode_presets(row["presets_json"]),
    )


class ProjectStore:
    """The `Project` registry — one row per registered project, id-addressed."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(projects)")}
        if "allow_json" not in columns:
            self._conn.execute(_ADD_ALLOW_COLUMN)
        if "last_team_roles_json" not in columns:
            self._conn.execute(_ADD_LAST_TEAM_ROLES_COLUMN)
        if "last_team_require_approval" not in columns:
            self._conn.execute(_ADD_LAST_TEAM_REQUIRE_APPROVAL_COLUMN)
        if "max_tokens" not in columns:
            self._conn.execute(_ADD_MAX_TOKENS_COLUMN)
        if "max_cost_usd" not in columns:
            self._conn.execute(_ADD_MAX_COST_USD_COLUMN)
        if "backend" not in columns:
            self._conn.execute(_ADD_BACKEND_COLUMN)
        if "mode" not in columns:
            self._conn.execute(_ADD_MODE_COLUMN)
        if "presets_json" not in columns:
            self._conn.execute(_ADD_PRESETS_COLUMN)
        self._conn.commit()

    @classmethod
    def open(cls, path: Path | None = None) -> ProjectStore:
        """Open (creating if needed) the SQLite file at `path` (default:
        :func:`default_projects_db`).

        ``check_same_thread=False``: the fleet daemon's FastAPI routes (ADR-0009)
        run this store's synchronous calls from whatever thread the ASGI server
        dispatches a request on -- a real production `cuttlefish serve` process is
        single-threaded (uvicorn's own asyncio loop), but its own `TestClient`
        (used in tests) dispatches through a background thread, and sqlite3's
        default same-thread check would reject that unconditionally. Every actual
        access still goes through this one connection sequentially -- sqlite3's own
        global lock, not Python's same-thread check, is what makes that safe.
        """
        resolved = path if path is not None else default_projects_db()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(resolved, check_same_thread=False)
        return cls(connection)

    def close(self) -> None:
        self._conn.close()

    def register(
        self,
        *,
        name: str,
        root: str,
        secrets_scope: str | None = None,
        roles: tuple[RoleDefinition, ...] = (),
        allow: tuple[tuple[str, ...], ...] = (),
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        backend: str | None = None,
        mode: str = DEFAULT_MODE,
        presets: tuple[str, ...] | None = None,
    ) -> Project:
        """Register a new project. `secrets_scope` defaults to `name` (Q38)."""
        project = Project(
            id=uuid.uuid4().hex,
            name=name,
            root=root,
            secrets_scope=secrets_scope if secrets_scope is not None else name,
            roles=roles,
            allow=allow,
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            backend=backend,
            mode=mode,
            presets=presets,
        )
        self._conn.execute(
            "INSERT INTO projects "
            "(id, name, root, secrets_scope, roles_json, last_team_id, allow_json, "
            "last_team_roles_json, max_tokens, max_cost_usd, backend, mode, presets_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project.id,
                project.name,
                project.root,
                project.secrets_scope,
                _encode_roles(project.roles),
                project.last_team_id,
                _encode_allow(project.allow),
                _encode_persisted_roles(project.last_team_roles),
                project.max_tokens,
                project.max_cost_usd,
                project.backend,
                project.mode,
                None if project.presets is None else json.dumps(list(project.presets)),
            ),
        )
        self._conn.commit()
        return project

    def get(self, project_id: str) -> Project:
        row = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
        return _row_to_project(row)

    def list(self) -> list[Project]:
        cursor = self._conn.execute("SELECT * FROM projects ORDER BY name")
        return [_row_to_project(row) for row in cursor]

    def update_roles(self, project_id: str, roles: tuple[RoleDefinition, ...]) -> Project:
        self.get(project_id)  # raises ProjectNotFoundError if unknown
        self._conn.execute(
            "UPDATE projects SET roles_json = ? WHERE id = ?", (_encode_roles(roles), project_id)
        )
        self._conn.commit()
        return self.get(project_id)

    def update_allow(self, project_id: str, allow: tuple[tuple[str, ...], ...]) -> Project:
        self.get(project_id)  # raises ProjectNotFoundError if unknown
        self._conn.execute(
            "UPDATE projects SET allow_json = ? WHERE id = ?", (_encode_allow(allow), project_id)
        )
        self._conn.commit()
        return self.get(project_id)

    def update_mode(self, project_id: str, mode: str) -> Project:
        """Set this project's permission mode (V4-C/ADR-0025), applied the next time the team starts
        (the daemon composes each role's settings once, at start)."""
        self.get(project_id)  # raises ProjectNotFoundError if unknown
        self._conn.execute("UPDATE projects SET mode = ? WHERE id = ?", (mode, project_id))
        self._conn.commit()
        return self.get(project_id)

    def update_presets(self, project_id: str, presets: tuple[str, ...] | None) -> Project:
        """Set the project's command groups (`None` restores the defaults). Like the mode, it
        applies the next time the team starts."""
        self.get(project_id)  # raises ProjectNotFoundError if unknown
        self._conn.execute(
            "UPDATE projects SET presets_json = ? WHERE id = ?",
            (None if presets is None else json.dumps(list(presets)), project_id),
        )
        self._conn.commit()
        return self.get(project_id)

    def update_budget(
        self, project_id: str, *, max_tokens: int | None, max_cost_usd: float | None
    ) -> Project:
        """Set (or clear, with `None`) this project's own run-scoped usage ceiling
        (KAN-1712/ADR-0017) -- the same "reviewed once, not retyped per start call"
        precedent `update_allow` already holds to."""
        self.get(project_id)  # raises ProjectNotFoundError if unknown
        self._conn.execute(
            "UPDATE projects SET max_tokens = ?, max_cost_usd = ? WHERE id = ?",
            (max_tokens, max_cost_usd, project_id),
        )
        self._conn.commit()
        return self.get(project_id)

    def record_team_started(
        self,
        project_id: str,
        team_id: str,
        roles: Sequence[PersistedRole] = (),
        *,
        require_approval: bool = False,
    ) -> None:
        """`roles` defaults to `()` for a caller with nothing to persist (e.g. a
        plain CLI-driven team, which has no daemon restart to survive) — a project
        started that way just isn't resumable later (`FleetDaemon.resume_pending`
        skips any project with no persisted `last_team_roles`, ADR-0010).
        `require_approval` (KAN-1711) persists team-wide, the same resume-fidelity
        reason `roles` does."""
        self.get(project_id)  # raises ProjectNotFoundError if unknown
        self._conn.execute(
            "UPDATE projects SET last_team_id = ?, last_team_roles_json = ?, "
            "last_team_require_approval = ? WHERE id = ?",
            (team_id, _encode_persisted_roles(tuple(roles)), int(require_approval), project_id),
        )
        self._conn.commit()

    def deregister(self, project_id: str) -> bool:
        """Remove `project_id`'s registry row. Returns whether anything was actually
        deleted (mirroring `SecretsStore.delete`'s own return-what-happened shape).
        Never touches `root` or its own `.cuttlefish/` contents — the same
        non-destructive posture `cuttlefish secrets delete` already holds for one
        name, extended to a whole project entry."""
        cursor = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._conn.commit()
        return cursor.rowcount > 0
