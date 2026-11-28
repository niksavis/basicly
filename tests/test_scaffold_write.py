from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from basicly import cli, scaffold_write
from basicly.scaffolds import VSCODE_TASKS_JSON

if TYPE_CHECKING:
    from pathlib import Path

BACKUP = ".basicly-bak"


EDITED_TASKS = '{ "version": "2.0.0", "tasks": [] }\n'
TASKS_LABEL = ".vscode/tasks.json"


def _edited_tasks(root: Path) -> Path:
    tasks = root / ".vscode" / "tasks.json"
    tasks.parent.mkdir(parents=True)
    tasks.write_text(EDITED_TASKS, encoding="utf-8")
    return tasks


def test_the_bare_flag_on_an_edited_scaffold_refuses_before_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _edited_tasks(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["install", "--overwrite-scaffolds"])

    err = capsys.readouterr().err
    assert code == 1
    assert tasks.read_text(encoding="utf-8") == EDITED_TASKS
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [
        ".vscode",
        ".vscode/tasks.json",
    ]
    assert TASKS_LABEL in err
    assert f"basicly install --overwrite-scaffolds {TASKS_LABEL}" in err


def test_a_named_scaffold_is_replaced_and_keeps_its_old_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _edited_tasks(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert scaffold_write.overwrite_set(tmp_path, [TASKS_LABEL]) == {TASKS_LABEL}
    assert cli._scaffold_consumer_files(tmp_path, force=frozenset({TASKS_LABEL})) is True

    assert tasks.read_text(encoding="utf-8") == VSCODE_TASKS_JSON
    assert tasks.with_suffix(tasks.suffix + BACKUP).read_text(encoding="utf-8") == EDITED_TASKS
    assert "Replaced .vscode/tasks.json" in capsys.readouterr().out


def test_a_named_scaffold_never_overwrites_an_older_different_backup(tmp_path: Path) -> None:
    tasks = _edited_tasks(tmp_path)
    first = tasks.with_suffix(tasks.suffix + BACKUP)
    first.write_text("an older copy\n", encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL, force=True)

    assert first.read_text(encoding="utf-8") == "an older copy\n"
    second = tasks.with_suffix(tasks.suffix + BACKUP + "-2")
    assert second.read_text(encoding="utf-8") == EDITED_TASKS


def test_the_bare_flag_replaces_a_scaffold_that_holds_the_text_install_wrote(
    tmp_path: Path,
) -> None:
    tasks = tmp_path / ".vscode" / "tasks.json"
    recorded = scaffold_write.record_path(tmp_path)
    recorded.parent.mkdir(parents=True)
    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL)
    assert recorded.is_file()

    assert scaffold_write.edited(tmp_path) == []
    assert scaffold_write.overwrite_set(tmp_path, []) == set(scaffold_write.SCAFFOLD_LABELS)
    tasks.write_text(EDITED_TASKS, encoding="utf-8")
    assert scaffold_write.edited(tmp_path) == [TASKS_LABEL]


def test_without_the_flag_nothing_is_forced(tmp_path: Path) -> None:
    _edited_tasks(tmp_path)

    assert scaffold_write.overwrite_set(tmp_path, None) == frozenset()


def test_the_flag_refuses_a_name_that_is_not_a_scaffold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        cli.main(["install", "--overwrite-scaffolds", "../outside.yml"])

    assert "invalid choice" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
