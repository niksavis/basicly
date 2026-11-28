from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import cli, scaffold_write
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
    tasks = tmp_path / ".vscode" / "tasks.json"
    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL)
    tasks.write_text("{ /* mine */ }", encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL, force=True)

    assert tasks.read_text(encoding="utf-8") == VSCODE_TASKS_JSON
    backup = tasks.with_suffix(tasks.suffix + BACKUP)
    assert backup.read_text(encoding="utf-8") == "{ /* mine */ }"
    assert "Replaced" in capsys.readouterr().out


def test_a_current_scaffold_gets_no_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL)

    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL, force=True)

    assert not list((tmp_path / ".vscode").glob(f"*{BACKUP}"))
    assert "already current" in capsys.readouterr().out


def test_without_the_flag_an_edit_still_survives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks = tmp_path / ".vscode" / "tasks.json"
    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL)
    tasks.write_text("{ /* mine */ }", encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.TASKS_LABEL)

    assert tasks.read_text(encoding="utf-8") == "{ /* mine */ }"
    assert "left unchanged" in capsys.readouterr().out


def test_the_ci_workflow_scaffold_honours_the_flag_too(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    workflow.write_text("name: mine\n", encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL, force=True)

    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW
    assert workflow.with_suffix(workflow.suffix + BACKUP).exists()


OLD_PIN = "git+https://github.com/niksavis/basicly@v0.0.1"
RECORD_HOOK = (
    '            python3 .basicly/core/hooks/tracker-commit-msg.py "${msg_file}" || failed=1\n'
)
OLD_SCAFFOLD = (
    CONSUMER_CI_WORKFLOW
    .replace(DIST_SOURCE, OLD_PIN)
    .replace(" --mode full --ci\n", " --mode full\n")
    .replace('"${msg_file}" || failed=1\n', '"${msg_file}" || failed=1\n' + RECORD_HOOK, 1)
)


def test_an_upgrade_migrates_the_0_46_3_steps_that_read_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(OLD_SCAFFOLD, encoding="utf-8")

    assert scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL) is True

    said = capsys.readouterr().out
    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW
    assert (
        workflow.with_suffix(workflow.suffix + BACKUP).read_text(encoding="utf-8") == OLD_SCAFFOLD
    )
    assert (
        "Migrated .github/workflows/basicly-gates.yml: removed the tracker-commit-msg.py step"
        in said
    )
    assert "Migrated .github/workflows/basicly-gates.yml: added --ci to verify --mode full" in said


def test_a_migration_keeps_the_consumer_own_steps_and_a_second_run_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(OLD_SCAFFOLD + "\n# my own step\n", encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    capsys.readouterr()
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW + "\n# my own step\n"
    assert "left unchanged" in capsys.readouterr().out


def test_a_dry_run_migration_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(OLD_SCAFFOLD, encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL, dry_run=True)

    assert workflow.read_text(encoding="utf-8") == OLD_SCAFFOLD
    assert "Would migrate" in capsys.readouterr().out


def test_install_refuses_a_workflow_that_reads_records_in_a_shape_it_cannot_migrate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    odd = OLD_SCAFFOLD.replace(
        '"${msg_file}" || failed=1\n' + RECORD_HOOK,
        '"${msg_file}" || failed=1\n' + RECORD_HOOK.replace("|| failed=1", "|| exit 1"),
    )
    workflow.write_text(odd, encoding="utf-8")

    assert cli._scaffold_consumer_files(tmp_path, force=frozenset()) is False

    err = capsys.readouterr().err
    assert workflow.read_text(encoding="utf-8") == odd
    assert not workflow.with_suffix(workflow.suffix + BACKUP).exists()
    assert ".github/workflows/basicly-gates.yml:" in err
    assert "runs the record-existence hook" in err
    assert "fix: remove the line" in err


def test_the_overwrite_flag_still_replaces_a_workflow_that_refuses_migration(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("        run: uvx basicly tracker ready\n", encoding="utf-8")

    assert scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL, force=True) is True
    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW


def test_an_upgrade_moves_a_stale_pin_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    workflow.write_text(CONSUMER_CI_WORKFLOW.replace(DIST_SOURCE, OLD_PIN), encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    assert workflow.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW
    assert "Re-pinned 6 basicly reference(s)" in capsys.readouterr().out


def test_a_repin_leaves_the_previous_copy_beside_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    stale = CONSUMER_CI_WORKFLOW.replace(DIST_SOURCE, OLD_PIN)
    workflow.write_text(stale, encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    backup = workflow.with_suffix(workflow.suffix + BACKUP)
    assert backup.read_text(encoding="utf-8") == stale, (
        "the default path rewrites a file a consumer may hold on a confirmation list, so "
        "it leaves the previous bytes the same way the force path does"
    )
    assert backup.name in capsys.readouterr().out


def test_a_run_that_repins_nothing_leaves_no_backup(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    assert not workflow.with_suffix(workflow.suffix + BACKUP).exists(), (
        "the control: a backup on every run would litter the tree and stop meaning "
        "anything, so it is written only when the file is about to change"
    )


def test_the_repin_leaves_everything_but_the_pin_alone(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    edited = CONSUMER_CI_WORKFLOW.replace(DIST_SOURCE, OLD_PIN) + "\n# my own step\n"
    workflow.write_text(edited, encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    after = workflow.read_text(encoding="utf-8")
    assert after.endswith("# my own step\n"), "the consumer's edit did not survive"
    assert OLD_PIN not in after


def test_a_scaffold_already_at_this_version_is_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    capsys.readouterr()

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    assert "left unchanged" in capsys.readouterr().out


def test_a_pin_that_is_not_ours_is_never_rewritten() -> None:
    foreign = "uvx --from git+https://github.com/someone/other@v0.0.1 other run\n"

    assert repin(foreign) == (foreign, 0)


def test_upgrade_repins_release_wheels_without_rewriting_standalone_kits() -> None:
    old = "https://github.com/niksavis/basicly/releases/download/v0.1.0/basicly-0.1.0-py3-none-any.whl"
    kit = "git+https://github.com/niksavis/basicly@v0.1.0#subdirectory=packages/basicly-tracker"
    assert repin(f"uvx --from {old} basicly install\n{kit}") == (
        f"uvx --from {DIST_SOURCE} basicly install\n{kit}",
        1,
    )


def test_upgrade_adds_runtime_setup_without_replacing_consumer_ci_steps(tmp_path: Path) -> None:
    workflow = tmp_path / ".github/workflows/basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    bootstrap = (
        "      - name: Prepare bundled Lefthook runtime\n"
        f"        run: uvx --from {DIST_SOURCE} basicly hooks-build --runtime-only\n"
    )
    original = CONSUMER_CI_WORKFLOW.replace(bootstrap, "") + "\n# consumer customization\n"
    assert original != CONSUMER_CI_WORKFLOW + "\n# consumer customization\n"
    workflow.write_text(original, encoding="utf-8")

    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)

    updated = workflow.read_text(encoding="utf-8")
    assert updated.endswith("\n# consumer customization\n")
    assert "hooks-build --runtime-only" in updated
    assert workflow.with_suffix(".yml.basicly-bak").read_text(encoding="utf-8") == original
    scaffold_write.write_one(tmp_path, scaffold_write.CI_WORKFLOW_LABEL)
    assert workflow.read_text(encoding="utf-8") == updated


def test_install_refuses_before_any_write_when_the_workflow_cannot_be_migrated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("        run: uvx basicly tracker ready\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = cli.main(["install"])

    err = capsys.readouterr().err
    assert code == 1
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [
        ".github",
        ".github/workflows",
        ".github/workflows/basicly-gates.yml",
    ]
    assert ".github/workflows/basicly-gates.yml:1: runs a tracker command" in err
