"""Integration (CUT-215, ADR-0057): the controller's skills, instruction files and MCP config reach
a worker over the channel, nothing that looks like a secret does, and a difference is reported. A
real relay, connector, daemon and worker; the sync folder and Claude's config folder are scratch."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_pairing_and_tiers import OWNER, Rig
from test_worker_join import FAST, _join, _online, _until, _workers

from cuttlefish.worker import run as worker_run

PROVIDER_KEY = (
    "sk-" + "d4" * 15
)  # built from pieces: nothing in this file is shaped like a real key


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _drift(rig: Rig) -> Any:
    async def check() -> list[str] | None:
        [worker] = await _workers(rig)
        return worker["drift"]  # type: ignore[no-any-return]

    return check


async def _until_drift(rig: Rig, wanted: Any) -> list[str]:
    for _ in range(200):
        got = await _drift(rig)()
        if wanted(got):
            return got or []
        await asyncio.sleep(0.05)
    raise AssertionError(f"drift never became what was wanted: {got}")


def _everything_on_disk(root: Path) -> str:
    return "\n".join(p.read_text(errors="ignore") + p.name for p in root.rglob("*") if p.is_file())


async def test_a_skill_added_on_the_controller_appears_on_the_worker_and_secrets_never_do(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = tmp_path / "sync"
    claude = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    rig.link.sync.folder = sync
    _put(sync, "skills/review/SKILL.md", "# Review\nRead the diff.\n")
    _put(sync, "instructions/team.md", "Use small commits.\n")
    _put(sync, "skills/review/.env", "A=1\n")
    _put(sync, "skills/review/notes.md", f"use {PROVIDER_KEY} here\n")
    _put(
        sync,
        "mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "docs": {"command": "npx", "args": ["-y", "docs"], "env": {"K": PROVIDER_KEY}}
                }
            }
        ),
    )
    identity, path = await _join(rig, tmp_path)
    home = path.parent
    runner = asyncio.create_task(
        worker_run.run(identity, path=path, say=lambda _t: None, **FAST, sync_every=1.5)
    )
    try:
        await _until(_online(rig))

        # The skill is on the worker, in its own folder and where Claude Code looks.
        async def synced() -> bool:
            return (home / "synced/skills/review/SKILL.md").exists()

        await _until(synced, 15)
        assert (claude / "skills/review/SKILL.md").read_text() == "# Review\nRead the diff.\n"
        assert (home / "synced/instructions/team.md").read_text() == "Use small commits.\n"
        assert json.loads((home / "synced/mcp.json").read_text()) == {
            "mcpServers": {"docs": {"command": "npx", "args": ["-y", "docs"]}}
        }
        # Nothing secret was copied: not the .env, not the file with a key in it, not the MCP env.
        on_disk = _everything_on_disk(home) + _everything_on_disk(claude)
        assert PROVIDER_KEY not in on_disk and ".env" not in on_disk and "notes.md" not in on_disk
        # Once it has reported what it holds, there is no difference.
        assert (
            await _until_drift(rig, lambda d: d is not None and not any("synced" in x for x in d))
            is not None
        )

        # A skill added now is a difference until the worker syncs it.
        _put(sync, "skills/ship/SKILL.md", "# Ship\n")
        rig.link.sync._at = -1e9  # the controller reads its folder again, not after five seconds
        late = await _until_drift(rig, lambda d: bool(d) and "skills/ship/SKILL.md not synced" in d)
        assert "skills/ship/SKILL.md not synced" in late
        await _until(lambda: _exists(claude / "skills/ship/SKILL.md"), 15)
        await _until_drift(rig, lambda d: d is not None and not any("synced" in x for x in d))

        # Removing it on the controller removes it here, and the machine owner's own skill is left.
        (claude / "skills/mine").mkdir()
        (claude / "skills/mine/SKILL.md").write_text("mine")
        (sync / "skills/ship/SKILL.md").unlink()
        rig.link.sync._at = -1e9
        await _until(lambda: _gone(claude / "skills/ship"), 15)
        assert (claude / "skills/mine/SKILL.md").read_text() == "mine"
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


def _exists(path: Path) -> Any:
    async def check() -> bool:
        return path.exists()

    return check()


def _gone(path: Path) -> Any:
    async def check() -> bool:
        return not path.exists()

    return check()


async def test_a_worker_may_ask_for_the_sync_set_and_a_device_may_not(rig: Rig) -> None:
    refused = await rig.owner.post("/api/worker/sync", headers=OWNER, json={"have": {}})
    assert refused.status_code == 403  # the owner's own token is not a worker
