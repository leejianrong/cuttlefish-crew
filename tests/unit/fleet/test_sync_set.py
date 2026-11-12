"""CUT-215: what is sent to workers, and what is never sent (secrets)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuttlefish.fleet.worker_sync import (
    SyncCache,
    allowed_path,
    differences,
    load,
    secret_reason,
    strip_mcp,
)

# Built from pieces so this file holds nothing shaped like a real key (a secret scan reads source).
PROVIDER_KEY = "sk-" + "a1" * 15
GITHUB_TOKEN = "ghp_" + "b2" * 20
ASSIGNED = "api_key = " + "C3" * 12


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


def test_a_skill_instruction_file_and_mcp_config_are_sent(tmp_path: Path) -> None:
    mcp = {
        "mcpServers": {"docs": {"command": "npx", "args": ["-y", "docs-server"], "type": "stdio"}}
    }
    found = load(
        _tree(
            tmp_path,
            {
                "skills/review/SKILL.md": "# Review\nRead the diff.\n",
                "skills/review/refs/checklist.md": "- tests\n",
                "instructions/team.md": "Use small commits.\n",
                "mcp.json": json.dumps(mcp),
            },
        )
    )
    assert sorted(found.files) == [
        "instructions/team.md",
        "mcp.json",
        "skills/review/SKILL.md",
        "skills/review/refs/checklist.md",
    ]
    assert found.refused == [] and found.notes == []
    assert list(found.manifest()) == sorted(found.files)


def test_nothing_that_looks_like_a_secret_is_sent_and_no_value_is_in_the_reason(
    tmp_path: Path,
) -> None:
    found = load(
        _tree(
            tmp_path,
            {
                "skills/ok/SKILL.md": "fine\n",
                "skills/ok/.env": "A=1\n",
                "skills/ok/deploy.pem": "x",
                "skills/ok/id_ed25519": "x",
                "skills/ok/credentials.json": "{}",
                "skills/ok/notes.md": f"use {PROVIDER_KEY} for the model\n",
                "skills/ok/gh.md": f"token: {GITHUB_TOKEN}\n",
                "skills/ok/cfg.yaml": f"{ASSIGNED}\n",
                "skills/ok/key.md": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc\n",
                "skills/ok/blob.bin": b"\x00\x01\x02",
                "instructions/team.md": "Use small commits.\n",
            },
        )
    )
    assert sorted(found.files) == ["instructions/team.md", "skills/ok/SKILL.md"]
    refused = dict(found.refused)
    assert set(refused) == {
        "skills/ok/.env", "skills/ok/deploy.pem", "skills/ok/id_ed25519",
        "skills/ok/credentials.json",
        "skills/ok/notes.md", "skills/ok/gh.md", "skills/ok/cfg.yaml", "skills/ok/key.md",
        "skills/ok/blob.bin",
    }  # fmt: skip
    everything = " ".join(refused.values())
    assert PROVIDER_KEY not in everything and GITHUB_TOKEN not in everything
    assert refused["skills/ok/.env"] == "named like a credential file"
    assert refused["skills/ok/blob.bin"] == "not a text file"


def test_prose_about_tokens_is_not_mistaken_for_a_secret() -> None:
    assert (
        secret_reason("a.md", b"Pass the token as an argument; never paste a password here.")
        is None
    )
    assert secret_reason("a.md", b"max_tokens: 4096\nsecret_name: DB_URL\n") is None


def test_mcp_config_loses_its_env_and_headers_and_says_so(tmp_path: Path) -> None:
    mcp = {
        "mcpServers": {
            "search": {
                "command": "npx",
                "args": ["-y", "search"],
                "env": {"SEARCH_KEY": PROVIDER_KEY},
                "headers": {"authorization": "Bearer " + GITHUB_TOKEN},
                "alwaysAllow": ["x"],
            }
        }
    }
    found = load(_tree(tmp_path, {"mcp.json": json.dumps(mcp)}))
    sent = json.loads(found.files["mcp.json"])
    assert sent == {"mcpServers": {"search": {"command": "npx", "args": ["-y", "search"]}}}
    assert PROVIDER_KEY.encode() not in found.files["mcp.json"]
    assert found.notes == ["mcp.json: dropped env and headers from the servers"]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("not json", "not an MCP config"),
        ('{"servers": {}}', "not an MCP config"),
        ('{"mcpServers": []}', "not an MCP config"),
        ('{"mcpServers": {"a b": {"command": "x"}}}', "plain name"),
        (
            json.dumps({"mcpServers": {"a": {"command": "x", "args": [PROVIDER_KEY]}}}),
            "holds a secret",
        ),
    ],
)
def test_a_bad_mcp_config_is_refused(text: str, why: str) -> None:
    sent, reason = strip_mcp(text.encode())
    assert sent is None and why in reason


def test_only_known_places_are_synced_and_links_and_odd_paths_are_not(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        {
            "skills/ok/SKILL.md": "x",
            "stray.txt": "x",
            "instructions/sub/deep.md": "x",
            "instructions/notes.txt": "x",
            "skills/solo.md": "x",
            "hooks/pre.sh": "x",
        },
    )
    (tmp_path / "skills" / "ok" / "link.md").symlink_to(tmp_path / "stray.txt")
    found = load(tmp_path)
    assert list(found.files) == ["skills/ok/SKILL.md"]
    assert {p for p, _ in found.refused} == {
        "stray.txt", "instructions/sub/deep.md",
        "instructions/notes.txt", "skills/solo.md", "hooks/pre.sh",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("mcp.json", True),
        ("skills/a/SKILL.md", True),
        ("skills/a/b/c.md", True),
        ("instructions/a.md", True),
        ("skills/../x", False),
        ("skills/a/../../x", False),
        ("/etc/passwd", False),
        ("skills/a", False),
        ("instructions/a/b.md", False),
        ("skills/a/b c", False),
        ("skills/.hidden/x", False),
    ],
)
def test_allowed_paths(path: str, ok: bool) -> None:
    assert allowed_path(path) is ok


def test_a_missing_folder_is_an_empty_set_and_size_limits_hold(tmp_path: Path) -> None:
    assert load(tmp_path / "nope").files == {}
    _tree(tmp_path, {"skills/a/big.md": "x" * 300_000, "skills/a/SKILL.md": "ok"})
    found = load(tmp_path)
    assert list(found.files) == ["skills/a/SKILL.md"]
    assert found.refused == [("skills/a/big.md", "too large, or too many files")]


def test_differences_are_named_and_bounded() -> None:
    want = {"skills/a/SKILL.md": "1" * 64, "mcp.json": "2" * 64}
    assert differences(want, want) == []
    have = {"skills/a/SKILL.md": "9" * 64, "skills/gone/SKILL.md": "3" * 64}
    assert differences(have, want) == [
        "mcp.json not synced",
        "skills/a/SKILL.md out of date",
        "skills/gone/SKILL.md was removed on the controller",
    ]
    many = {f"skills/s{i}/SKILL.md": "1" * 64 for i in range(20)}
    shown = differences({}, many)
    assert len(shown) == 9 and shown[-1] == "and 12 more files"


def test_the_cache_rereads_after_its_time() -> None:
    folder = Path(__file__).parent / "does-not-exist"
    cache = SyncCache(folder, keep=0)
    assert cache.get().files == {}


def test_the_cli_lists_what_is_sent_and_what_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuttlefish import cli

    _tree(tmp_path, {"skills/a/SKILL.md": "ok", "skills/a/.env": "A=1", "stray.txt": "x"})
    monkeypatch.setenv("CUTTLEFISH_WORKER_SYNC_DIR", str(tmp_path))
    assert cli.main(["worker", "sync"]) == 0
    out = capsys.readouterr().out
    assert "sent     skills/a/SKILL.md" in out
    assert "REFUSED  skills/a/.env: named like a credential file" in out
    assert "REFUSED  stray.txt" in out
