"""The ``present`` instruction file every backend is pointed at (CUT-194, ADR-0056).

``SKILL.md`` and ``examples/`` are written into a project's ``.cuttlefish/present/`` so any coding
agent can read how to leave a slide deck, wireframe, animation, demo or dashboard in
``.cuttlefish/artifacts/``. A plain file, not a protocol: reading a file is what every backend does,
and the pointer is a sentence in ``artifacts.AGENT_NOTE``. cuttlefish edits nothing of the project's
own and makes no model call. Blocking; meant for ``asyncio.to_thread`` or a task body.
"""

from __future__ import annotations

import logging
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

_LOG = logging.getLogger(__name__)

PRESENT_DIR = Path(".cuttlefish") / "present"
#: What ``AGENT_NOTE`` points at, relative to the project root.
SKILL_PATH = (PRESENT_DIR / "SKILL.md").as_posix()
_SKIP = {"__init__.py", "__pycache__"}


def _files(node: Traversable, prefix: Path) -> list[tuple[Path, Traversable]]:
    found: list[tuple[Path, Traversable]] = []
    for child in node.iterdir():
        if child.name in _SKIP:
            continue
        if child.is_dir():
            found.extend(_files(child, prefix / child.name))
        else:
            found.append((prefix / child.name, child))
    return found


def install(root: str | Path) -> bool:
    """Write the instruction file and examples under ``root``; ``True`` if anything changed.

    Best effort: a missing root or a read-only folder is logged and skipped, never a reason to
    refuse a round.
    """
    base = Path(root)
    if not base.is_dir():
        return False
    changed = False
    try:
        for relative, node in _files(resources.files(__package__), Path()):
            target = base / PRESENT_DIR / relative
            data = node.read_bytes()
            if target.is_file() and target.read_bytes() == data:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            changed = True
    except OSError as exc:
        _LOG.warning("could not write %s under %s: %s", PRESENT_DIR, root, exc)
    return changed
