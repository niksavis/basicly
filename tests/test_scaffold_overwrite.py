"""`basicly install --overwrite-scaffolds` (basicly-lc2bd3v).

Written-once-then-yours is the right default for an upgrade and the wrong one for a
deliberate reinstall: a consumer who wiped `.basicly/` to install fresh kept scaffolds
calling a hook this release renamed, and had no supported way to ask for the current
ones. Replacing a hand-edited CI workflow is still destructive, so the old bytes are
kept beside it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import cli
from basicly.scaffolds import CONSUMER_CI_WORKFLOW, VSCODE_TASKS_JSON

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

BACKUP = ".basicly-bak"


def test_an_edited_scaffold_is_replaced_and_its_bytes_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Discarding the edit silently is the failure this backup exists to prevent."""
    tasks = tmp_path / ".vscode" / "tasks.json"
    cli._scaffold_vscode_tasks(tmp_path)
    tasks.write_text("{ /* mine */ }", encoding="utf-8")

    cli._scaffold_vscode_tasks(tmp_path, force=True)

    assert tasks.read_text(encoding="utf-8") == VSCODE_TASKS_JSON
    backup = tasks.with_suffix(tasks.suffix + BACKUP)
    assert backup.read_text(encoding="utf-8") == "{ /* mine */ }"
    assert "Replaced" in capsys.readouterr().out


def test_a_current_scaffold_gets_no_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: an unedited file must not litter a backup on every install."""
    cli._scaffold_vscode_tasks(tmp_path)

    cli._scaffold_vscode_tasks(tmp_path, force=True)

    assert not list((tmp_path / ".vscode").glob(f"*{BACKUP}"))
    assert "already current" in capsys.readouterr().out


def test_without_the_flag_an_edit_still_survives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default contract is unchanged, and this is what says so."""
    tasks = tmp_path / ".vscode" / "tasks.json"
    cli._scaffold_vscode_tasks(tmp_path)
    tasks.write_text("{ /* mine */ }", encoding="utf-8")

    cli._scaffold_vscode_tasks(tmp_path)

    assert tasks.read_text(encoding="utf-8") == "{ /* mine */ }"
    assert "left unchanged" in capsys.readouterr().out


def test_the_ci_workflow_scaffold_honours_the_flag_too(tmp_path: Path) -> None:
    """Both scaffolds go through one writer, and this is what keeps them in step."""
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    cli._scaffold_ci_workflow(tmp_path)
    workflow.write_text("name: mine\n", encoding="utf-8")

    cli._scaffold_ci_workflow(tmp_path, force=True)

    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW
    assert workflow.with_suffix(workflow.suffix + BACKUP).exists()
