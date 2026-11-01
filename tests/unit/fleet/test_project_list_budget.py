"""The project list's cost with a 100-project fleet (CUT-192): one journal read per project, and a
payload that stays small. Counted, not timed, so it cannot flake on a slow machine."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore

_SPEC = importlib.util.spec_from_file_location(
    "seed_fleet", Path(__file__).parents[3] / "scripts" / "seed_fleet.py"
)
assert _SPEC and _SPEC.loader
seed_fleet = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed_fleet)

#: What a 100-project list weighed before the personas were left out: 216 KB. Over a phone's
#: 1.6 Mbps link every 100 KB is half a second before the first card.
MAX_LIST_BYTES = 110_000


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[tuple[TestClient, ProjectStore]]:
    seed_fleet.seed(tmp_path, 100)
    store = ProjectStore.open(tmp_path / ".cuttlefish" / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as client:
        yield client, store
    store.close()


def test_the_list_reads_each_journal_once(
    fleet: tuple[TestClient, ProjectStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = fleet
    opened: list[Path] = []
    real_open = EpisodicStore.open.__func__  # type: ignore[attr-defined]

    def counting_open(cls: type[EpisodicStore], /, path: Path, **kw: object) -> EpisodicStore:
        opened.append(path)
        return real_open(cls, path, **kw)  # type: ignore[no-any-return]

    monkeypatch.setattr(EpisodicStore, "open", classmethod(counting_open))
    listed = client.get("/api/projects").json()["projects"]
    assert len(listed) == 100
    with_journal = [p for p in listed if p["last_team_id"]]
    assert len(opened) == len(with_journal) == 90  # the ten idle ones have none to open


def test_the_list_stays_small_and_leaves_out_persona_text(
    fleet: tuple[TestClient, ProjectStore],
) -> None:
    client, store = fleet
    response = client.get("/api/projects")
    assert len(response.content) <= MAX_LIST_BYTES
    assert all("persona" not in role for p in response.json()["projects"] for role in p["roles"])
    one = store.list()[0]
    detail = client.get(f"/api/projects/{one.id}").json()
    assert all("persona" in role for role in detail["roles"])
