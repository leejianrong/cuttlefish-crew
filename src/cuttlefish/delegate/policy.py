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
import os
from pathlib import Path

#: What a call that declares no policy of its own passes down: an empty declaration.
#: ``presets.resolve_allow`` (ADR-0023) turns that into the built-in dev presets at
#: delegation time; before V4-A it meant no shell command at all. Confining writes to the
#: delegation's own root remains the narrowest capability that still lets
#: kopicode land a real edit unattended.
DEFAULT_SHELL_ALLOWLIST: list[list[str]] = []


#: Command names kopicode refuses in ``allow_commands``: a launcher, wrapper or shell would start
#: commands its matcher never sees, and ``cd`` inside the root is always allowed. One such entry
#: makes kopicode reject the whole file, so they are dropped here (``find``, ``npx tsc`` among the
#: presets) and the command waits to be denied rather than breaking every command.
_UNEXPRESSIBLE = frozenset(
    {
        "env", "command", "exec", "nohup", "nice", "ionice", "time", "timeout", "stdbuf", "xargs",
        "setsid", "watch", "flock", "busybox", "find", "parallel", "unbuffer", "chroot", "strace",
        "npx", "corepack", "sudo", "doas", "su", "pkexec", "runuser", "sh", "bash", "zsh", "dash",
        "ksh", "ash", "csh", "tcsh", "fish", "eval", "source", ".", "cd", "pushd", "popd",
    }
)  # fmt: skip


def write_policy_file(path: Path, *, root: str, allow: list[list[str]] | None = None) -> None:
    """Write a kopicode declared-allowlist policy file at `path`.

    Grammar is kopicode's own (``internal/permission/allowlist_file.go``): flat top-level keys,
    `root` (a quoted absolute path) and the allowlist, hand-written rather than templated
    through a library. `json.dumps` renders a list of string lists in the grammar's array syntax.

    The `allow` argv prefixes go in ``allow_commands`` (kopicode ADR-0018), not ``allow``: the
    latter matches ``/bin/sh -c <line>`` byte for byte, so no real shell command ever matched
    it (CUT-279). ``allow_commands`` tokenizes the line and matches every command in it on whole
    leading words, with ``cd`` inside `root` implicit. kopicode's own never-allowed rules still
    apply on top.
    """
    commands = [e for e in (DEFAULT_SHELL_ALLOWLIST if allow is None else allow)
                if e and os.path.basename(e[0]) not in _UNEXPRESSIBLE]
    text = f'root = "{root}"\nallow = []\n'
    if commands:
        text += f"allow_commands = {json.dumps(commands)}\n"
    path.write_text(text)
