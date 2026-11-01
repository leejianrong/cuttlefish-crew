import importlib.util
from pathlib import Path

from cuttlefish.fleet.status import role_statuses
from cuttlefish.projects.store import ProjectStore

_SPEC = importlib.util.spec_from_file_location(
    "seed_fleet", Path(__file__).parents[2] / "scripts" / "seed_fleet.py"
)
assert _SPEC and _SPEC.loader
seed_fleet = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed_fleet)


def test_seed_registers_every_state_without_a_running_team(tmp_path: Path) -> None:
    assert seed_fleet.main(["seed", "--home", str(tmp_path)]) == 0
    store = ProjectStore.open(tmp_path / ".cuttlefish" / "projects.db")
    projects = store.list()
    assert len(projects) == 100
    # Nothing to resume: a daemon started on this HOME launches no session.
    assert all(not p.last_team_roles for p in projects)
    seen = set()
    for p in projects:
        if p.last_team_id is None:
            seen.add("idle")
            continue
        journal = seed_fleet.EpisodicStore.open(Path(p.root) / ".cuttlefish" / "episodic.db")
        events = list(journal.read(p.last_team_id))
        journal.close()
        seen.add(role_statuses(events, ["builder"])["builder"])
    assert {"idle", "working", "blocked", "done", "failed"} <= seen
    store.close()


def test_clear_removes_only_seeded_projects(tmp_path: Path) -> None:
    seed_fleet.main(["seed", "--home", str(tmp_path), "--count", "10"])
    store = ProjectStore.open(tmp_path / ".cuttlefish" / "projects.db")
    store.register(name="mine", root=str(tmp_path))
    store.close()
    seed_fleet.main(["clear", "--home", str(tmp_path)])
    store = ProjectStore.open(tmp_path / ".cuttlefish" / "projects.db")
    assert [p.name for p in store.list()] == ["mine"]
    assert not (tmp_path / "seed-projects").exists()
    store.close()
