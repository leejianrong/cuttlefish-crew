"""Print the tokens each prompt of a Claude Code session cost, from its transcript (CUT-273).

    python scripts/manager_tokens.py <project dir under ~/.claude/projects | one .jsonl> ...

A folder is read file by file, oldest first, so a manager whose context was cleared before each
wake shows one short file per wake. Reads only the usage fields and the first words of a prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cuttlefish.sessions.usage import read_file

_ROW = "{:10} {:>8} {:>8} {:>8} {:>9} {:>11} {:>9}  {}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    files: list[Path] = []
    for arg in argv:
        path = Path(arg)
        if path.is_dir():
            files += sorted(path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        else:
            files.append(path)
    grand = 0
    print(_ROW.format("file", "requests", "in", "out", "cache+", "cache-read", "total", "prompt"))
    for file in files:
        for t in read_file(file):
            grand += t.total
            row = (file.stem[:8], t.requests, t.input, t.output, t.cache_create, t.cache_read)
            print(_ROW.format(*row, t.total, repr(t.prompt[:40])))
    print(f"total tokens: {grand}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
