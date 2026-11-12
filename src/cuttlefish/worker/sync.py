"""What this worker holds of the controller's sync set (CUT-215): skills, instruction files, MCP
config.

The controller decides the set and strips what must not travel (`fleet/worker_sync.py`); this module
only writes it down, after checking each path and digest again, in three places:

- `~/.cuttlefish/synced/`: every file, as sent, and a manifest of their digests, which the worker
  reports so the controller can say what differs.
- `~/.claude/skills/<name>/`: each skill, for Claude Code, only where the folder is new or was
made by
  this module (it holds a `.cuttlefish-synced` marker), so a skill of the machine owner's own
  with the same
  name is never overwritten or removed.
- `<checkout>/.cuttlefish/instructions/`: the instruction files, put in each project's checkout
when a
  team starts there (`place_instructions`); `.cuttlefish/` is already out of the project's git
  status.

Codex and kopicode have no skill folder this writes to, and the MCP config is kept in the synced
folder
and merged into no agent's own configuration (`known-gaps.md`).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from cuttlefish.fleet.worker_sync import MAX_FILE_BYTES, allowed_path

MANIFEST = ".manifest.json"
MARKER = ".cuttlefish-synced"
EVERY_SECONDS = 60.0


def claude_skills_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "skills"


class Synced:
    def __init__(self, folder: Path, skills: Path | None = None) -> None:
        self.folder = folder
        self._skills = skills

    def have(self) -> dict[str, str]:
        try:
            body = json.loads((self.folder / MANIFEST).read_text())
        except (OSError, ValueError):
            return {}
        return {p: s for p, s in body.items() if isinstance(p, str) and isinstance(s, str)}

    def apply(self, files: list[dict[str, Any]], remove: list[str]) -> dict[str, str]:
        """Write what the controller sent and delete what it dropped; returns the manifest now
        held."""
        have = self.have()
        for path in remove:
            if isinstance(path, str) and allowed_path(path):
                self._remove(path)
                have.pop(path, None)
        for item in files:
            path = str(item.get("path", ""))
            try:
                data = base64.b64decode(str(item.get("content", "")), validate=True)
            except (ValueError, binascii.Error):
                continue
            digest = hashlib.sha256(data).hexdigest()
            if not allowed_path(path) or len(data) > MAX_FILE_BYTES or digest != item.get("sha256"):
                continue  # a path or digest the controller would never send: ignore, do not write
            self._write(self.folder / path, data)
            self._install_skill(path, data)
            have[path] = digest
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / MANIFEST).write_text(json.dumps(have, sort_keys=True))
        return have

    def _write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".part")
        temp.write_bytes(data)
        temp.replace(target)

    def _skill_target(self, path: str) -> Path | None:
        parts = path.split("/")
        if self._skills is None or parts[0] != "skills":
            return None
        return self._skills / "/".join(parts[1:])

    def _install_skill(self, path: str, data: bytes) -> None:
        target = self._skill_target(path)
        if target is None:
            return
        root = self._skills / path.split("/")[1]  # type: ignore[operator]
        if root.exists() and not (root / MARKER).exists():
            return  # the machine owner's own skill of that name
        self._write(target, data)
        (root / MARKER).touch()

    def _remove(self, path: str) -> None:
        (self.folder / path).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            for parent in (self.folder / path).parents:
                if parent == self.folder:
                    break
                parent.rmdir()
        target = self._skill_target(path)
        if target is None or self._skills is None:
            return
        root = self._skills / path.split("/")[1]
        if (root / MARKER).exists():
            target.unlink(missing_ok=True)
            if not any(p.is_file() and p.name != MARKER for p in root.rglob("*")):
                shutil.rmtree(root, ignore_errors=True)


def place_instructions(folder: Path, checkout: Path) -> None:
    """Put the synced instruction files in a project's checkout, for the agent to read."""
    source = folder / "instructions"
    if not source.is_dir():
        return
    target = checkout / ".cuttlefish" / "instructions"
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(target.glob("*.md")):  # one the controller dropped goes too
        if not (source / name.name).exists():
            name.unlink()
    for file in source.glob("*.md"):
        shutil.copyfile(file, target / file.name)
