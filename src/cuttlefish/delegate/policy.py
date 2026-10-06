"""The declared-allowlist policy file cuttlefish writes for each delegation.

kopicode board KAN-987 landed the policy gate ADR-0003 was written to depend on
(kopicode ADR-0011). Slice 1 (docs/PLAN.md Scope, QUESTIONS.md Q2) used it with
one hardcoded, narrow policy: confine writes to the delegation's own root, and
permit no shell command at all. `write_policy_file`'s `allow` generalises that
into an operator-declared, per-task policy (KAN-1011, docs/SLICES.md V2 step 3)
— `cuttlefish.cli`'s `--allow` and `cuttlefish.workflow`'s `TaskInput.allow` are
what actually let an operator declare one; this module only renders whatever
was declared (or V1's original empty default) into kopicode's grammar.

Declaring a real shell command here, still without the sandbox (V2 step 2,
KAN-1010) that would confine it, is not a new, unnamed risk: ADR-0002's addendum
already reasons about exactly this — "a bounded shell command... rather than an
unconditional refusal" changes what a delegation can do, not who is running it
or what they're running it against, and the same one-operator, own-task,
own-repo trust model covers both. What this module does not do is validate that
a declared command is *sensible* — an operator who declares `rm -rf /` gets
kopicode's permission gate honouring exactly that, the same trust this whole
policy mechanism already rests on.
"""

from __future__ import annotations

import json
from pathlib import Path

#: What a call that declares no policy of its own passes down: an empty declaration.
#: ``presets.resolve_allow`` (ADR-0023) turns that into the built-in dev presets at
#: delegation time; before V4-A it meant no shell command at all. Confining writes to the
#: delegation's own root remains the narrowest capability that still lets
#: kopicode land a real edit unattended.
DEFAULT_SHELL_ALLOWLIST: list[list[str]] = []


def write_policy_file(path: Path, *, root: str, allow: list[list[str]] | None = None) -> None:
    """Write a kopicode declared-allowlist policy file at `path`.

    Grammar is kopicode's own (``internal/permission/allowlist_file.go``): two
    flat top-level keys, `root` (a quoted absolute path) and `allow` (a list of
    argv lists) — hand-written rather than templated through a library, the same
    reasoning kopicode's own file gives for not pulling in a TOML dependency for
    two keys. `json.dumps` happens to render a list of string lists in exactly
    this grammar's array syntax, so it's reused for `allow` rather than
    hand-formatting it.
    """
    resolved_allow = DEFAULT_SHELL_ALLOWLIST if allow is None else allow
    allow_literal = json.dumps(resolved_allow)
    path.write_text(f'root = "{root}"\nallow = {allow_literal}\n')
