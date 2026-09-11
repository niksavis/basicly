"""`basicly install --overwrite-scaffolds` (basicly-lc2bd3v).

Written-once-then-yours is the right default for an upgrade and the wrong one for a
deliberate reinstall: a consumer who wiped `.basicly/` to install fresh kept scaffolds
calling a hook this release renamed, and had no supported way to ask for the current
ones. Replacing a hand-edited CI workflow is still destructive, so the old bytes are
kept beside it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import __version__, cli
from basicly.scaffolds import (
    CONSUMER_CI_WORKFLOW,
    DIST_SOURCE,
    VSCODE_TASKS_JSON,
    repin,
)

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


# --- the pin moves without the flag (basicly-jdpzlwj) --------------------------

OLD_PIN = DIST_SOURCE.replace(f"v{__version__}", "v0.0.1")


def test_an_upgrade_moves_a_stale_pin_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin left at the old tag runs the old engine against the new catalog.

    The version-skew guard then refuses four of the five CI steps, in a file the
    consumer had no reason to look at and that install promised not to touch.
    """
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    cli._scaffold_ci_workflow(tmp_path)
    workflow.write_text(CONSUMER_CI_WORKFLOW.replace(DIST_SOURCE, OLD_PIN), encoding="utf-8")

    cli._scaffold_ci_workflow(tmp_path)

    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW
    assert "Re-pinned 5 basicly reference(s)" in capsys.readouterr().out


def test_the_repin_leaves_everything_but_the_pin_alone(tmp_path: Path) -> None:
    """It is still the consumer's file: only the value that names our engine moves."""
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    cli._scaffold_ci_workflow(tmp_path)
    edited = CONSUMER_CI_WORKFLOW.replace(DIST_SOURCE, OLD_PIN) + "\n# my own step\n"
    workflow.write_text(edited, encoding="utf-8")

    cli._scaffold_ci_workflow(tmp_path)

    after = workflow.read_text(encoding="utf-8")
    assert after.endswith("# my own step\n"), "the consumer's edit did not survive"
    assert OLD_PIN not in after


def test_a_scaffold_already_at_this_version_is_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: the re-pin reports what it moved, never that it ran."""
    cli._scaffold_ci_workflow(tmp_path)
    capsys.readouterr()

    cli._scaffold_ci_workflow(tmp_path)

    assert "left unchanged" in capsys.readouterr().out


def test_a_pin_that_is_not_ours_is_never_rewritten() -> None:
    """Built from `DIST_SOURCE`, so a consumer's other pinned tools cannot match."""
    foreign = "uvx --from git+https://github.com/someone/other@v0.0.1 other run\n"

    assert repin(foreign) == (foreign, 0)
