"""Files an agent leaves in ``<root>/.cuttlefish/artifacts/`` for a person to read (CUT-97).

Every backend can write a file, so a plain folder is the one way to show a result that does not
depend on which agent made it (ADR-0032). Markdown, images and video are data. An HTML file
(CUT-175) is code, so this module only serves its bytes, as ``text/plain``: the dashboard fetches
them with its token and runs them in a sandboxed iframe that has an opaque origin and no network
(``frontend/src/lib/artifacts.ts``, ADR-0044), never in the page and never from this server.

The folder is the agent's to write, so every path is treated as hostile: a name is resolved first
(``..`` and symlinks followed) and refused unless the result is still inside the folder.
Blocking calls, meant for ``asyncio.to_thread``.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

ARTIFACTS_DIR = Path(".cuttlefish") / "artifacts"

#: A listing past this many files is cut (newest first) rather than sent whole.
MAX_LISTED = 200
#: How deep below the folder a file is looked for.
MAX_DEPTH = 4

#: extension -> (kind, content type, most bytes served)
_KINDS: dict[str, tuple[str, str, int]] = {
    ".md": ("markdown", "text/markdown; charset=utf-8", 1_000_000),
    ".markdown": ("markdown", "text/markdown; charset=utf-8", 1_000_000),
    ".png": ("image", "image/png", 10_000_000),
    ".jpg": ("image", "image/jpeg", 10_000_000),
    ".jpeg": ("image", "image/jpeg", 10_000_000),
    ".gif": ("image", "image/gif", 10_000_000),
    ".webp": ("image", "image/webp", 10_000_000),
    ".svg": ("image", "image/svg+xml", 2_000_000),
    ".mp4": ("video", "video/mp4", 100_000_000),
    ".webm": ("video", "video/webm", 100_000_000),
    # Served as text, not text/html: the browser never renders it from this server's origin.
    ".html": ("html", "text/plain; charset=utf-8", 2_000_000),
    ".htm": ("html", "text/plain; charset=utf-8", 2_000_000),
}

#: What an agent is told, appended to a round's task and to a session's system prompt.
AGENT_NOTE = (
    "To show a person a result (a plan, a review, a summary, a diagram), write it under "
    ".cuttlefish/artifacts/ in the project root, for example .cuttlefish/artifacts/review.md. "
    "Markdown, png, jpg, gif, webp or svg images, mp4 or webm video and self-contained .html "
    "designs (inline CSS and JavaScript, no network) are shown in the dashboard. Raw HTML inside "
    "markdown is not. For slides, a wireframe, an animation, a demo or a dashboard, read "
    ".cuttlefish/present/SKILL.md first."
)


class ArtifactError(Exception):
    """The base of what :func:`read_artifact` refuses."""


class ArtifactNotFoundError(ArtifactError):
    """No such file in the folder, or its name resolves outside it."""


class ArtifactNotPreviewableError(ArtifactError):
    """A file the dashboard only lists, never serves."""


class ArtifactTooLargeError(ArtifactError):
    """A file over its type's limit."""


@dataclasses.dataclass(frozen=True, slots=True)
class Artifact:
    #: Relative to the artifacts folder, with ``/`` separators.
    path: str
    name: str
    #: ``markdown``, ``image``, ``video``, ``html`` or ``other`` (listed by name only).
    kind: str
    size: int
    #: Seconds since the epoch; the dashboard sorts and detects change on it.
    modified: float

    def to_json(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def folder(root: str) -> Path:
    return Path(root) / ARTIFACTS_DIR


def _classify(path: Path) -> tuple[str, str, int] | None:
    return _KINDS.get(path.suffix.lower())


def _inside(base: Path, candidate: Path) -> Path | None:
    """``candidate`` resolved, when it is still within ``base``; otherwise ``None``."""
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_relative_to(base) else None


def list_artifacts(root: str) -> list[Artifact]:
    """Every file in the folder, newest first. A folder that does not exist yet is an empty one."""
    try:
        base = folder(root).resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    found: list[Artifact] = []
    for current, dirs, files in os.walk(base, followlinks=False):
        depth = len(Path(current).relative_to(base).parts)
        dirs[:] = [d for d in dirs if not d.startswith(".")] if depth < MAX_DEPTH else []
        for name in files:
            if name.startswith("."):
                continue
            real = _inside(base, Path(current) / name)
            if real is None or not real.is_file():
                continue
            try:
                stat = real.stat()
            except OSError:
                continue
            kind = _classify(real)
            found.append(
                Artifact(
                    path=(Path(current) / name).relative_to(base).as_posix(),
                    name=name,
                    kind=kind[0] if kind else "other",
                    size=stat.st_size,
                    modified=stat.st_mtime,
                )
            )
    found.sort(key=lambda a: (-a.modified, a.path))
    return found[:MAX_LISTED]


def read_artifact(root: str, relative: str) -> tuple[bytes, str]:
    """The bytes and content type of one file, or an :class:`ArtifactError`."""
    if not relative or "\0" in relative or Path(relative).is_absolute():
        raise ArtifactNotFoundError(relative)
    try:
        base = folder(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ArtifactNotFoundError(relative) from exc
    real = _inside(base, base / relative)
    if real is None or not real.is_file() or any(p.startswith(".") for p in Path(relative).parts):
        raise ArtifactNotFoundError(relative)
    kind = _classify(real)
    if kind is None:
        raise ArtifactNotPreviewableError(relative)
    _, content_type, limit = kind
    try:
        if real.stat().st_size > limit:
            raise ArtifactTooLargeError(relative)
        return real.read_bytes(), content_type
    except OSError as exc:
        raise ArtifactNotFoundError(relative) from exc
