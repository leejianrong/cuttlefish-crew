"""Integration: cuttlefish's own policy file, parsed by the real kopicode binary.

docs/PLAN.md "Testing approach", no mock kopicode. kopicode parses --policy-file
before it even checks for a provider credential (cmd/kopicode/print.go), so a
well-formed policy reaches exactly the same "no credential" outcome a call with
no policy at all reaches — proving the file round-trips through the real parser
without needing a live model call. A malformed one is refused immediately, at a
different, distinctive exit code, before credential resolution ever runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.delegate.kopicode import DelegationError, run_kopicode
from cuttlefish.delegate.policy import write_policy_file
from cuttlefish.delegate.presets import PRESETS, preset_allow


@pytest.mark.requires_kopicode
async def test_a_written_policy_file_parses_cleanly_against_the_real_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    root = tmp_path / "scratch"
    root.mkdir()
    policy_path = tmp_path / "policy.toml"
    write_policy_file(policy_path, root=str(root))

    with pytest.raises(DelegationError, match=r"no session events"):
        await run_kopicode(
            binary="kopicode",
            task_text="add a .gitignore entry",
            root=str(root),
            policy_file=str(policy_path),
        )


@pytest.mark.requires_kopicode
async def test_a_declared_allowlist_parses_cleanly_against_the_real_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAN-1011: a non-empty, operator-declared allowlist is still valid kopicode
    policy grammar, not just V1's original empty one. Every preset at once, so one entry
    kopicode refuses in ``allow_commands`` (``find``, ``npx``) would fail the whole file."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    root = tmp_path / "scratch"
    root.mkdir()
    policy_path = tmp_path / "policy.toml"
    write_policy_file(policy_path, root=str(root), allow=preset_allow(list(PRESETS)))

    with pytest.raises(DelegationError, match=r"no session events"):
        await run_kopicode(
            binary="kopicode",
            task_text="add a .gitignore entry",
            root=str(root),
            policy_file=str(policy_path),
        )


@pytest.mark.requires_kopicode
async def test_a_malformed_policy_file_is_refused_before_credential_resolution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text("not a valid policy file at all\n")

    with pytest.raises(DelegationError, match=r"not a key = value line"):
        await run_kopicode(
            binary="kopicode",
            task_text="add a .gitignore entry",
            root=str(root),
            policy_file=str(policy_path),
        )
