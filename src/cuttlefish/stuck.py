"""Telling an agent that is stuck on its environment from one that is merely failing (ADR-0029).

kopicode's stream says that a shell command failed but not what it printed; the output is in
the session's own record (``<root>/.kopicode/sessions/<session>/events.jsonl``, large outputs
as blobs under ``<root>/.kopicode/blobs``). :class:`SessionRecord` reads that file as it
grows, and :class:`StuckDetector` counts consecutive shell failures whose output matches an
environment signature. Both are plain and synchronous so they are testable without a child;
the serve transport decides when to call them and what to do on a verdict.

Reading the record is best effort. A missing file, a half-written line or an output kopicode
spilled to a blob that cannot be read all mean "no evidence", never an error: the detector
fails open to the old behaviour of letting the round run.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

#: What an environment failure looks like in a shell command's output. Data, not code: add a
#: line here for a toolchain that is not covered. Each is searched, case-insensitively, in the
#: whole output of one failed command.
SIGNATURES: tuple[str, ...] = (
    r"No module named",  # Python: a package that is not installed
    r"ModuleNotFoundError",
    r"command not found",  # a shell: the tool is not on PATH
    r"not found in PATH|executable file not found",
    r"\bENOENT\b",  # Node and others: a file or binary that is not there
    r"Cannot find module",
    r"is not recognized as an internal or external command",
    r"cannot find package",  # Go
    r"could not find .*(?:crate|Cargo\.toml)",  # Rust
    r"Could not find a version that satisfies the requirement",
    r"No matching distribution found",
)

#: Consecutive matching failures that end a round. ``CUTTLEFISH_STUCK_THRESHOLD`` overrides it;
#: ``0`` turns the detector off.
DEFAULT_THRESHOLD = 5

#: How much of the last failing output is kept as evidence.
EVIDENCE_CHARS = 2000

#: The tool whose failures count. Another tool's failure neither counts nor resets.
SHELL_TOOL = "run_shell"

#: The tools that write or delete a whole file, whose path the record keeps in full.
_WRITE_TOOLS = frozenset({"write_file", "delete_file"})

#: The most of a spilled output read back; what matters (the error) is at the end.
_BLOB_TAIL_BYTES = 16 * 1024


def threshold_from_env(environ: dict[str, str] | None = None) -> int:
    """``CUTTLEFISH_STUCK_THRESHOLD``: a whole number, ``0`` for off, else the default."""
    raw = (environ if environ is not None else os.environ).get("CUTTLEFISH_STUCK_THRESHOLD")
    if raw is None or not raw.strip():
        return DEFAULT_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_THRESHOLD
    return max(value, 0)


@dataclasses.dataclass(frozen=True, slots=True)
class ShellResult:
    """One finished ``run_shell`` call as the session record tells it."""

    exit_code: int | None
    output: str


@dataclasses.dataclass(frozen=True, slots=True)
class StuckVerdict:
    """The agent has failed ``count`` shell commands in a row on its environment."""

    count: int
    signature: str
    evidence: str


class StuckDetector:
    """Counts consecutive environment-signature failures of shell commands."""

    def __init__(
        self, threshold: int = DEFAULT_THRESHOLD, signatures: Iterable[str] = SIGNATURES
    ) -> None:
        self.threshold = threshold
        self._signatures = [re.compile(s, re.IGNORECASE) for s in signatures]
        self._count = 0

    @property
    def enabled(self) -> bool:
        return self.threshold > 0

    def feed(self, result: ShellResult) -> StuckVerdict | None:
        """Take one shell result; return a verdict the moment the run of failures is long enough."""
        if not self.enabled:
            return None
        matched = None
        if result.exit_code not in (0, None):
            matched = next((s for s in self._signatures if s.search(result.output)), None)
        if matched is None:
            self._count = 0
            return None
        self._count += 1
        if self._count < self.threshold:
            return None
        return StuckVerdict(self._count, matched.pattern, result.output[-EVIDENCE_CHARS:])


class SessionRecord:
    """Incremental reader of one kopicode session's ``events.jsonl``."""

    def __init__(self, root: str, session: str) -> None:
        self._root = Path(root) / ".kopicode"
        self._path = self._root / "sessions" / session / "events.jsonl"
        self._offset = 0

    def new_shell_results(self) -> list[ShellResult]:
        """Shell results written since the last call. Only whole lines are consumed, so a line
        kopicode is still writing is picked up next time."""
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self._offset += end + 1
        results: list[ShellResult] = []
        for raw in data[: end + 1].splitlines():
            result = self._shell_result(raw)
            if result is not None:
                results.append(result)
        return results

    def written_paths(self) -> list[str]:
        """Files the session wrote or deleted whole (``write_file``, ``delete_file``) and the
        tool did not fail on, in order, read from the whole record.

        The serve stream's ``tool_call_parsed`` carries the call's arguments cut at 120
        characters, so a write with real content loses its ``path`` there; the record holds the
        parsed call in full. Fails soft: any trouble reading is "none found" (ADR-0030)."""
        try:
            data = self._path.read_bytes()
        except OSError:
            return []
        calls: dict[str, str] = {}
        paths: list[str] = []
        for raw in data.splitlines():
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            payload = event.get("payload") if isinstance(event, dict) else None
            if not isinstance(payload, dict):
                continue
            call_id = payload.get("call_id")
            if event.get("type") == "ToolCallParsed" and payload.get("tool") in _WRITE_TOOLS:
                args = payload.get("args")
                path = args.get("path") if isinstance(args, dict) else None
                if isinstance(call_id, str) and isinstance(path, str) and path:
                    calls[call_id] = path
            elif event.get("type") == "ToolResult" and isinstance(call_id, str):
                path = calls.pop(call_id, None)
                failed = payload.get("error_kind") or payload.get("error") or payload.get("reason")
                if path is not None and not failed and path not in paths:
                    paths.append(path)
        return paths

    def _shell_result(self, raw: bytes) -> ShellResult | None:
        try:
            event = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(event, dict) or event.get("type") != "ToolResult":
            return None
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("tool") != SHELL_TOOL:
            return None
        code = payload.get("exit_code")
        output = payload.get("output")
        text = ""
        if isinstance(output, dict):
            inline, blob = output.get("inline"), output.get("blob")
            if isinstance(inline, str):
                text = inline
            elif isinstance(blob, str):
                text = self._read_blob(blob)
        return ShellResult(code if isinstance(code, int) else None, text)

    def _read_blob(self, name: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]+", name):  # a name, never a path
            return ""
        try:
            with (self._root / "blobs" / name).open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - _BLOB_TAIL_BYTES))
                return handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
