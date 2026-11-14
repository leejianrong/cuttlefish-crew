"""Unit: answering Claude Code's trust dialog for a goal worktree only (CUT-182)."""

from __future__ import annotations

from pathlib import Path

from cuttlefish.sessions import trust

CURSOR = chr(0x276F)


def _dialog(folder: str, cursor: str = "No, exit") -> str:
    yes = (CURSOR + " " if cursor.startswith("Yes") else "  ") + "Yes, I trust this folder"
    no = (CURSOR + " " if cursor.startswith("No") else "  ") + "No, exit"
    return (
        f" Accessing workspace:\n {folder}\n Quick safety check: Is this a project you created or "
        "one you trust? (Like your own code)\n Claude Code'll be able to read, edit, and execute "
        f"files here.\n {no}\n {yes}\n Enter to confirm · Esc to cancel"
    )


class FakeKeys:
    """A screen that moves the cursor on Down and goes blank on Enter."""

    def __init__(self, folder: str, screens: list[str] | None = None, alive: bool = True) -> None:
        self.folder = folder
        self.screen = _dialog(folder)
        self.waiting = screens or []
        self.sent: list[str] = []
        self.alive = alive
        self.responsive = True
        self.ignore = 0

    def has_session(self, name: str) -> bool:
        return self.alive

    def capture(self, name: str, lines: int = 15) -> str:
        if self.waiting:
            return self.waiting.pop(0)
        return self.screen

    def send_key(self, name: str, key: str) -> None:
        self.sent.append(key)
        if key == "Down" and self.ignore:
            self.ignore -= 1
        elif key == "Down" and self.responsive:
            self.screen = _dialog(self.folder, cursor="Yes")
        if key == "Enter":
            self.screen = "Welcome"


def _root(tmp_path: Path) -> tuple[str, str]:
    root = tmp_path / "proj"
    folder = root / ".cuttlefish" / "worktrees" / "build-1"
    folder.mkdir(parents=True)
    return str(root), str(folder)


class Clock:
    """Time that moves only when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.5)

    def __call__(self) -> float:
        return self.now


def _run(keys: FakeKeys, root: str, folder: str) -> bool:
    clock = Clock()
    return trust.answer(
        keys, "cf-x", root, folder, timeout_s=10, interval_s=0.5, sleep=clock.sleep, clock=clock
    )


def test_a_goal_worktree_is_trusted_by_moving_to_yes_then_enter(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(folder)
    assert _run(keys, root, folder)
    assert keys.sent == ["Down", "Enter"]


def test_it_waits_for_the_dialog_to_appear(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(folder, screens=["", "starting..."])
    assert _run(keys, root, folder)


def test_a_key_the_tui_missed_while_starting_is_sent_again(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(folder)
    keys.ignore = 1
    assert _run(keys, root, folder)
    assert keys.sent == ["Down", "Down", "Enter"]


def test_a_cursor_already_on_yes_gets_only_enter(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(folder)
    keys.screen = _dialog(folder, cursor="Yes")
    assert _run(keys, root, folder)
    assert keys.sent == ["Enter"]


def test_a_wrapped_path_still_matches(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(folder)
    keys.screen = _dialog(folder[:20] + "\n " + folder[20:])
    assert _run(keys, root, folder)


def test_a_folder_that_is_not_a_goal_worktree_is_never_answered(tmp_path: Path) -> None:
    root, _ = _root(tmp_path)
    for elsewhere in (
        root,
        str(tmp_path / "other"),
        str(Path(root) / ".cuttlefish" / "worktrees" / "a" / "b"),
    ):
        keys = FakeKeys(elsewhere)
        assert not _run(keys, root, elsewhere)
        assert keys.sent == []


def test_a_dialog_naming_another_folder_is_left_alone(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(str(tmp_path / "somewhere-else"))
    assert not _run(keys, root, folder)
    assert keys.sent == []


def test_an_unknown_layout_is_left_alone(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    keys = FakeKeys(folder)
    keys.screen = _dialog(folder).replace("Yes, I trust this folder", "Proceed")
    keys.responsive = False
    assert not _run(keys, root, folder)
    assert "Enter" not in keys.sent


def test_no_dialog_and_a_gone_session_end_quietly(tmp_path: Path) -> None:
    root, folder = _root(tmp_path)
    quiet = FakeKeys(folder)
    quiet.screen = "ready"
    assert not _run(quiet, root, folder)
    assert quiet.sent == []
    assert not _run(FakeKeys(folder, alive=False), root, folder)


def test_dialog_folder_reads_the_path() -> None:
    assert trust.dialog_folder(_dialog("/a/b")) == "/a/b"
    assert trust.dialog_folder("nothing here") is None
