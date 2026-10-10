"""Unit: SecretsStore's own encryption, scoping, and key handling (ADR-0006)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from cuttlefish.secrets.store import (
    DEFAULT_PROJECT,
    SHARED_SCOPE,
    InvalidSecretsKeyError,
    LayeredSecrets,
    MissingSecretsKeyError,
    SecretsStore,
    check_name,
    check_value,
    generate_key,
)


def test_generate_key_is_a_valid_fernet_key() -> None:
    Fernet(generate_key().encode("ascii"))  # raises if invalid


def test_opening_with_no_key_and_none_in_the_environment_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)
    with pytest.raises(MissingSecretsKeyError):
        SecretsStore.open(tmp_path / "secrets.db")


def test_opening_with_a_malformed_key_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(InvalidSecretsKeyError):
        SecretsStore.open(tmp_path / "secrets.db", key="not-a-real-fernet-key")


def test_set_then_get_round_trips_the_plaintext(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set("proj-a", "HUGGINGFACE_TOKEN", "hf_super_secret_value")
    assert store.get("proj-a", "HUGGINGFACE_TOKEN") == "hf_super_secret_value"
    store.close()


def test_get_of_an_unset_name_is_none(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    assert store.get("proj-a", "NOPE") is None
    store.close()


def test_the_ciphertext_on_disk_never_contains_the_plaintext(tmp_path: Path) -> None:
    db_path = tmp_path / "secrets.db"
    store = SecretsStore.open(db_path, key=generate_key())
    store.set(DEFAULT_PROJECT, "GITHUB_TOKEN", "ghp_totally_real_secret_value")
    store.close()

    raw = db_path.read_bytes()
    assert b"ghp_totally_real_secret_value" not in raw


def test_set_overwrites_a_prior_value_for_the_same_scope_and_name(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set("proj-a", "TOKEN", "first")
    store.set("proj-a", "TOKEN", "second")
    assert store.get("proj-a", "TOKEN") == "second"
    store.close()


def test_a_project_scoped_value_does_not_leak_into_another_project(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set("proj-a", "TOKEN", "for-a")
    assert store.get("proj-b", "TOKEN") is None
    store.close()


def test_delete_removes_a_secret_and_reports_whether_it_existed(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set("proj-a", "TOKEN", "value")
    assert store.delete("proj-a", "TOKEN") is True
    assert store.get("proj-a", "TOKEN") is None
    assert store.delete("proj-a", "TOKEN") is False
    store.close()


def test_list_names_returns_only_names_never_values(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set("proj-a", "B_TOKEN", "value-b")
    store.set("proj-a", "A_TOKEN", "value-a")
    store.set("proj-b", "C_TOKEN", "value-c")
    assert store.list_names("proj-a") == ["A_TOKEN", "B_TOKEN"]
    store.close()


def test_resolve_prefers_the_projects_own_scope_over_shared(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set(SHARED_SCOPE, "OPENROUTER_API_KEY", "shared-value")
    store.set("proj-a", "OPENROUTER_API_KEY", "project-value")
    resolved = store.resolve("proj-a", ["OPENROUTER_API_KEY"])
    assert resolved == {"OPENROUTER_API_KEY": "project-value"}
    store.close()


def test_resolve_falls_back_to_shared_scope_when_the_project_has_nothing(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    store.set(SHARED_SCOPE, "OPENROUTER_API_KEY", "shared-value")
    assert store.resolve("proj-a", ["OPENROUTER_API_KEY"]) == {"OPENROUTER_API_KEY": "shared-value"}
    store.close()


def test_resolve_omits_a_name_found_in_neither_scope(tmp_path: Path) -> None:
    store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    assert store.resolve("proj-a", ["NOPE"]) == {}
    store.close()


def test_reopening_the_same_file_with_the_same_key_reads_back_what_was_set(tmp_path: Path) -> None:
    db_path = tmp_path / "secrets.db"
    key = generate_key()
    store = SecretsStore.open(db_path, key=key)
    store.set("proj-a", "TOKEN", "value")
    store.close()

    reopened = SecretsStore.open(db_path, key=key)
    assert reopened.get("proj-a", "TOKEN") == "value"
    reopened.close()


def test_reopening_with_a_different_key_cannot_decrypt_a_prior_value(tmp_path: Path) -> None:
    db_path = tmp_path / "secrets.db"
    store = SecretsStore.open(db_path, key=generate_key())
    store.set("proj-a", "TOKEN", "value")
    store.close()

    reopened = SecretsStore.open(db_path, key=generate_key())
    with pytest.raises(InvalidToken):
        reopened.get("proj-a", "TOKEN")
    reopened.close()


def test_open_creates_the_parent_directory_if_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "secrets.db"
    store = SecretsStore.open(db_path, key=generate_key())
    store.close()
    assert db_path.exists()


def test_secrets_are_stored_as_a_blob_column_not_plain_text(tmp_path: Path) -> None:
    db_path = tmp_path / "secrets.db"
    store = SecretsStore.open(db_path, key=generate_key())
    store.set("proj-a", "TOKEN", "value")
    store.close()

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT ciphertext FROM secrets").fetchone()
    conn.close()
    assert isinstance(row[0], bytes)


@pytest.mark.parametrize("name", ["GITHUB_TOKEN", "_X", "A1_B2"])
def test_a_name_that_can_be_an_environment_variable_is_accepted(name: str) -> None:
    check_name(name)


@pytest.mark.parametrize("name", ["", "github_token", "1TOKEN", "MY-TOKEN", "A B", "X" * 129])
def test_a_name_that_cannot_be_an_environment_variable_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="capital letters"):
        check_name(name)


def test_an_empty_or_huge_value_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a value"):
        check_value("")
    with pytest.raises(ValueError, match="at most"):
        check_value("x" * 16_385)
    check_value("x" * 16_384)


def test_layered_stores_read_the_first_that_has_a_name(tmp_path: Path) -> None:
    key = generate_key()
    central = SecretsStore.open(tmp_path / "central.db", key=key)
    folder = SecretsStore.open(tmp_path / "folder.db", key=key)
    central.set("demo", "GITHUB_TOKEN", "from-central")
    folder.set("demo", "GITHUB_TOKEN", "from-folder")
    folder.set("demo", "HF_TOKEN", "only-in-folder")
    layered = LayeredSecrets(central, folder)
    assert layered.resolve("demo", ["GITHUB_TOKEN", "HF_TOKEN", "NOPE"]) == {
        "GITHUB_TOKEN": "from-central",
        "HF_TOKEN": "only-in-folder",
    }
    layered.close()
