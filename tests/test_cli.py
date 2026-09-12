from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
import yaml

from basicly import cli
from basicly.agents import GENERATED_MARKER as AGENT_GENERATED_MARKER
from basicly.config import (
    CONFIG_FILE,
    DEFAULT_CONFIG_TOML,
    LOCAL_CONFIG_FILE,
    load_project_paths,
)
from basicly.scaffolds import CONSUMER_CI_WORKFLOW, GENERATED_IGNORES, VSCODE_TASKS_JSON
from basicly.skills import GENERATED_MARKER

REPO_ROOT = Path(__file__).parent.parent


def run_basicly(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(cwd / "src")}
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_basicly_consumer(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_work_repo_fixture_copies_all_and_only_the_tracked_files(work_repo: Path) -> None:

    listing = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = {name for name in listing.stdout.split("\0") if name}
    copied = {
        path.relative_to(work_repo).as_posix() for path in work_repo.rglob("*") if path.is_file()
    }

    assert copied == tracked
    assert (work_repo / "src" / "basicly" / "cli.py").is_file()


def test_the_work_repo_fixture_leaves_out_the_state_that_differed_per_machine(
    work_repo: Path,
) -> None:

    for offender in ("node_modules", ".venv", "basicly.local.toml"):
        assert not (work_repo / offender).exists(), offender
    assert list(work_repo.rglob("__pycache__")) == []
    assert not (work_repo / cli.owned_store.LEDGER_DIR / "redirect").exists()
    assert list((work_repo / cli.owned_store.LEDGER_DIR).glob("events-*.jsonl"))


def test_cli_install_converges_fresh_consumer(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr

    assert (consumer / "basicly.toml").is_file()
    assert (consumer / ".basicly-local" / "fragments" / "user").is_dir()
    assert list((consumer / ".basicly" / "core" / "fragments").rglob("*.fragment.yaml"))
    assert (consumer / ".basicly" / "core" / "targets" / "claude.yaml").is_file()

    overlay_user = consumer / ".basicly-local" / "fragments" / "user"
    overview = overlay_user / "project" / "project-overview.fragment.yaml"
    commands = overlay_user / "commands" / "commands.fragment.yaml"
    assert "status: draft" in overview.read_text(encoding="utf-8")
    assert "status: draft" in commands.read_text(encoding="utf-8")
    claude_md = (consumer / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Project Overview" not in claude_md

    assert (consumer / "AGENTS.md").is_file()
    assert (consumer / ".claude" / "CLAUDE.md").is_file()
    assert (consumer / ".github" / "copilot-instructions.md").is_file()
    assert list((consumer / ".claude" / "skills").rglob("SKILL.md"))
    assert (consumer / ".pre-commit-config.yaml").is_file()
    assert '"label": "basicly: build"' in (consumer / ".vscode" / "tasks.json").read_text(
        encoding="utf-8"
    )


def test_cli_install_honors_custom_core_paths(tmp_path: Path) -> None:

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "basicly.toml").write_text(
        "[paths]\n"
        'core_fragments = "conf/basicly/core/fragments"\n'
        'overlay_fragments = ["conf/basicly-local/fragments"]\n'
        'targets = "conf/basicly/core/targets"\n'
        'templates = "conf/basicly/core/templates"\n'
        'manifest = "conf/basicly/generated-manifest.json"\n',
        encoding="utf-8",
    )

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert (consumer / "conf" / "basicly" / "core" / "targets" / "claude.yaml").is_file()
    assert not (consumer / ".basicly" / "core").exists()
    assert (consumer / "AGENTS.md").is_file()
    config_text = (consumer / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "conf/basicly/core/hooks/pre-commit.py" in config_text


def test_cli_install_is_idempotent_and_preserves_edits(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    config = consumer / "basicly.toml"
    marker = config.read_text(encoding="utf-8") + "\n# user note\n"
    config.write_text(marker, encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "already exists; left unchanged" in result.stdout
    assert "0 new, 0 updated, 0 removed" in result.stdout
    assert "No files changed" in result.stdout
    assert "No skill files changed" in result.stdout
    assert config.read_text(encoding="utf-8") == marker


def test_cli_help_groups_commands_by_audience(tmp_path: Path) -> None:
    result = run_basicly_consumer(tmp_path, "--help")
    assert result.returncode == 0
    for marker in ("command groups:", "consumer (", "contributor (", "harness ("):
        assert marker in result.stdout
    assert "re-running install IS the upgrade" in result.stdout


def test_cli_piped_output_stays_plain_text(work_repo: Path) -> None:
    result = run_basicly(work_repo, "check")
    assert result.returncode == 0, result.stderr
    assert "\x1b" not in result.stdout
    assert "All generated files and manifest are up to date." in result.stdout

    listing = run_basicly(work_repo, "catalog", "list", "skill")
    assert listing.returncode == 0
    assert "\x1b" not in listing.stdout
    assert "tool-ripgrep" in listing.stdout


def test_cli_install_technology_selection_filters_and_prunes(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = run_basicly_consumer(consumer, "install", "--technologies", "zsh")
    assert result.returncode == 0, result.stderr
    assert 'technologies = ["zsh"]' in (consumer / "basicly.toml").read_text(encoding="utf-8")
    assert (consumer / ".claude" / "skills" / "tool-git" / "SKILL.md").is_file()
    assert (consumer / ".claude" / "skills" / "tool-zsh" / "SKILL.md").is_file()
    assert not (consumer / ".claude" / "skills" / "tool-uv").exists()
    assert not (consumer / ".claude" / "skills" / "tool-tmux").exists()
    assert (consumer / ".basicly" / "core" / "skills" / "tool-uv" / "skill.yaml").is_file()

    result = run_basicly_consumer(consumer, "install", "--technologies", "python")
    assert result.returncode == 0, result.stderr
    assert (consumer / ".claude" / "skills" / "tool-uv" / "SKILL.md").is_file()
    result = run_basicly_consumer(consumer, "install", "--technologies", "zsh")
    assert result.returncode == 0, result.stderr
    assert not (consumer / ".claude" / "skills" / "tool-uv").exists()

    result = run_basicly_consumer(consumer, "install", "--technologies", "pyton")
    assert result.returncode == 1
    assert "Unknown technology value" in result.stderr


def test_validate_install_technologies_rejects_empty_selection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli._validate_install_technologies(None) == []
    assert cli._validate_install_technologies(",") is None
    assert "at least one value" in capsys.readouterr().err


def test_cli_install_rejects_unknown_technology_before_writing_any_file(
    tmp_path: Path,
) -> None:

    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = run_basicly_consumer(consumer, "install", "--technologies", "pyton")
    assert result.returncode == 1
    assert "Unknown technology value" in result.stderr
    assert list(consumer.iterdir()) == []


def test_setup_tracker_creates_the_ledger_and_reports_a_derived_prefix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = tmp_path / "My-Terminal.2"
    repo.mkdir()

    cli._setup_tracker(repo)

    assert (repo / cli.owned_store.LEDGER_DIR).is_dir()
    out = capsys.readouterr().out
    assert 'prefix = "myterminal2"' in out
    assert not (repo / "basicly.toml").exists()


def test_setup_tracker_leaves_an_existing_ledger_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / cli.owned_store.LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text('{"record":"kept-1"}\n', encoding="utf-8")

    cli._setup_tracker(tmp_path)

    assert "left unchanged" in capsys.readouterr().out
    assert (ledger / "events-0001.jsonl").read_text(encoding="utf-8") == '{"record":"kept-1"}\n'


def test_tracker_prefix_enforces_leading_letter(tmp_path: Path) -> None:
    assert cli._tracker_prefix(tmp_path / "42tools") == "repo42tools"
    assert cli._tracker_prefix(tmp_path / "---") == "repo"


def test_scaffold_vscode_tasks_never_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._scaffold_vscode_tasks(tmp_path)
    tasks_path = tmp_path / ".vscode" / "tasks.json"
    assert tasks_path.read_text(encoding="utf-8") == VSCODE_TASKS_JSON

    tasks_path.write_text("{ /* mine */ }", encoding="utf-8")
    cli._scaffold_vscode_tasks(tmp_path)
    assert tasks_path.read_text(encoding="utf-8") == "{ /* mine */ }"
    assert "left unchanged" in capsys.readouterr().out


def test_purge_removes_only_pristine_vscode_tasks(tmp_path: Path) -> None:
    paths = load_project_paths(tmp_path)
    tasks_path = tmp_path / ".vscode" / "tasks.json"

    tasks_path.parent.mkdir(parents=True)
    tasks_path.write_text(VSCODE_TASKS_JSON, encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert not tasks_path.exists()

    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(VSCODE_TASKS_JSON + "// edited\n", encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert tasks_path.exists()


def test_scaffold_ci_workflow_writes_once_and_parses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._scaffold_ci_workflow(tmp_path)
    workflow_path = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    assert set(data["jobs"]) == {"commit-messages", "gates"}

    workflow_path.write_text("name: mine\n", encoding="utf-8")
    cli._scaffold_ci_workflow(tmp_path)
    assert workflow_path.read_text(encoding="utf-8") == "name: mine\n"
    assert "left unchanged" in capsys.readouterr().out


def test_scaffold_generated_ignores_appends_once(tmp_path: Path) -> None:
    cli._scaffold_generated_ignores(tmp_path)
    ignore_path = tmp_path / ".gitignore"
    lines = ignore_path.read_text(encoding="utf-8").splitlines()
    assert {pattern for pattern, _ in GENERATED_IGNORES} <= set(lines)

    ignore_path.write_text("node_modules/\n", encoding="utf-8")
    cli._scaffold_generated_ignores(tmp_path)
    lines = ignore_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "node_modules/"
    assert LOCAL_CONFIG_FILE in lines

    before = ignore_path.read_text(encoding="utf-8")
    cli._scaffold_generated_ignores(tmp_path)
    assert ignore_path.read_text(encoding="utf-8") == before


def test_scaffold_generated_ignores_adds_only_what_is_missing(tmp_path: Path) -> None:
    ignore_path = tmp_path / ".gitignore"
    ignore_path.write_text(f"/{LOCAL_CONFIG_FILE}\n", encoding="utf-8")

    cli._scaffold_generated_ignores(tmp_path)

    lines = ignore_path.read_text(encoding="utf-8").splitlines()
    assert lines.count(LOCAL_CONFIG_FILE) == 0, "re-added a rooted entry it already covers"
    assert "*.basicly-bak" in lines
    assert ".basicly/ledger/snapshot.jsonl" in lines


def test_this_repo_satisfies_the_local_config_ignore_it_scaffolds() -> None:

    repo_root = Path(__file__).resolve().parents[1]
    ignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")
    uncovered = [p for p, _ in GENERATED_IGNORES if not cli.ignore_covers(ignore_text, p)]
    assert not uncovered, (
        f"this repo's .gitignore does not cover {', '.join(uncovered)}; "
        "an untracked generated file would block every landing"
    )


def test_install_hints_missing_config_sections_without_editing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = "[worktree]\nconcurrency = 2\n"
    (tmp_path / CONFIG_FILE).write_text(original, encoding="utf-8")

    cli._report_missing_config_sections(tmp_path)

    out = capsys.readouterr().out
    for section in ("[paths]", "[policy]", "[runner]"):
        assert section in out
    assert "[worktree]" not in out
    assert LOCAL_CONFIG_FILE in out
    assert (tmp_path / CONFIG_FILE).read_text(encoding="utf-8") == original


def test_install_hints_stay_quiet_for_a_current_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    cli._report_missing_config_sections(tmp_path)
    assert capsys.readouterr().out == ""


def test_ci_workflows_ignore_tracker_only_pushes() -> None:

    sources = [
        (REPO_ROOT / ".github" / "workflows" / "basicly.yml").read_text(encoding="utf-8"),
        (REPO_ROOT / ".github" / "workflows" / "quality-gates.yml").read_text(encoding="utf-8"),
        CONSUMER_CI_WORKFLOW,
    ]
    for text in sources:
        data = yaml.safe_load(text)
        triggers = data.get("on", data.get(True))
        for event in ("push", "pull_request"):
            assert triggers[event]["paths-ignore"] == [".basicly/ledger/**"], text[:200]


def test_purge_removes_only_pristine_ci_workflow(tmp_path: Path) -> None:
    paths = load_project_paths(tmp_path)
    workflow_path = tmp_path / ".github" / "workflows" / "basicly-gates.yml"

    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(CONSUMER_CI_WORKFLOW, encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert not workflow_path.exists()

    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(CONSUMER_CI_WORKFLOW + "# edited\n", encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert workflow_path.exists()


def _record_in_state(consumer: Path, rel_path: str) -> None:
    state_path = consumer / ".basicly" / "state" / "install.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256((consumer / ".basicly" / "core" / rel_path).read_bytes()).hexdigest()
    payload["core"][rel_path] = f"sha256:{digest}"
    state_path.write_text(json.dumps(payload), encoding="utf-8")


def test_cli_install_upgrade_overwrites_upstream_changed_core_file(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    target = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    bundled_content = target.read_text(encoding="utf-8")
    target.write_text("# older shipped version\n", encoding="utf-8")
    _record_in_state(consumer, "hooks/pre-commit.py")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "1 updated" in result.stdout
    assert target.read_text(encoding="utf-8") == bundled_content


def test_cli_install_upgrade_deletes_upstream_removed_core_file(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    ghost = consumer / ".basicly" / "core" / "fragments" / "project" / "ghost.fragment.yaml"
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text("retired: true\n", encoding="utf-8")
    _record_in_state(consumer, "fragments/project/ghost.fragment.yaml")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "1 removed" in result.stdout
    assert not ghost.exists()


def test_cli_install_keeps_hand_edited_core_file_unless_forced(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    target = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    bundled_content = target.read_text(encoding="utf-8")
    edited = bundled_content + "\n# my local tweak\n"
    target.write_text(edited, encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "hand-edited managed core files" in result.stderr
    assert target.read_text(encoding="utf-8") == edited

    forced = run_basicly_consumer(consumer, "install", "--force")
    assert forced.returncode == 0, forced.stderr
    assert target.read_text(encoding="utf-8") == bundled_content


def test_cli_install_keeps_unknown_core_file_with_warning(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    stray = consumer / ".basicly" / "core" / "notes.txt"
    stray.write_text("mine\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "unknown origin" in result.stderr
    assert stray.exists()


def test_cli_install_upgrade_preserves_overlay_and_config(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    overlay_fragment = consumer / ".basicly-local" / "fragments" / "user" / "mine.fragment.yaml"
    overlay_fragment.write_text(
        "schema_version: 1\n"
        "id: mine\n"
        "description: my rule\n"
        "category: project\n"
        "applies_to: [all]\n"
        "body: |\n"
        "  - My rule.\n",
        encoding="utf-8",
    )
    config = consumer / "basicly.toml"
    config_content = config.read_text(encoding="utf-8") + "\n# my note\n"
    config.write_text(config_content, encoding="utf-8")

    target = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    target.write_text("# older shipped version\n", encoding="utf-8")
    _record_in_state(consumer, "hooks/pre-commit.py")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "1 updated" in result.stdout
    assert overlay_fragment.read_text(encoding="utf-8").startswith("schema_version: 1")
    assert config.read_text(encoding="utf-8") == config_content


def test_cli_install_writes_provenance_state(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "Recorded install state" in result.stdout

    state_path = consumer / ".basicly" / "state" / "install.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["basicly_version"]
    assert payload["installed_at"]
    core_files = [
        path
        for path in (consumer / ".basicly" / "core").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert len(payload["core"]) == len(core_files)


def test_cli_install_refreshes_provenance_state(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    state_path = consumer / ".basicly" / "state" / "install.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["installed_at"] = "1999-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    refreshed = json.loads(state_path.read_text(encoding="utf-8"))
    assert refreshed["installed_at"] != "1999-01-01T00:00:00+00:00"


def test_cli_install_authoring_repo_writes_no_state(work_repo: Path) -> None:
    result = run_basicly(work_repo, "install")
    assert result.returncode == 0, result.stderr
    assert "its own authoring source" in result.stdout
    assert not (work_repo / ".basicly" / "state").exists()


def test_cli_check_refuses_a_rewritten_managed_core(tmp_path: Path) -> None:

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    hook = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# hand edit\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "check")
    assert result.returncode == 1
    assert "up to date" not in result.stdout
    assert "differs from the installed snapshot in 1 file(s)" in result.stderr
    assert "hooks/pre-commit.py: modified" in result.stderr


def test_cli_check_refuses_a_catalog_another_version_installed(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    state_path = consumer / ".basicly" / "state" / "install.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["basicly_version"] = "0.0.0"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_basicly_consumer(consumer, "check")
    assert result.returncode == 1, result.stderr
    assert "installed by basicly 0.0.0" in result.stderr
    assert "basicly install" in result.stderr
    assert "Stale generated files detected" not in result.stderr


def test_cli_uninstall_removes_everything_managed(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr

    assert not (consumer / ".basicly" / "core").exists()
    assert not (consumer / "AGENTS.md").exists()
    assert not (consumer / ".claude" / "CLAUDE.md").exists()
    assert not (consumer / ".github" / "copilot-instructions.md").exists()
    for root in (".claude", ".github", ".agents"):
        base = consumer / root
        assert not (list(base.rglob("SKILL.md")) if base.exists() else [])
    assert not (consumer / ".pre-commit-config.yaml").exists()

    assert (consumer / "basicly.toml").is_file()
    assert (consumer / ".basicly-local" / "fragments" / "user").is_dir()
    assert (consumer / cli.owned_store.LEDGER_DIR).is_dir()


def test_cli_uninstall_preserves_foreign_hooks(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    config = consumer / ".pre-commit-config.yaml"
    data = config.read_text(encoding="utf-8")
    foreign = (
        "repos:\n"
        "- repo: local\n"
        "  hooks:\n"
        "  - id: my-own-hook\n"
        "    name: my-own-hook\n"
        "    entry: echo mine\n"
        "    language: system\n" + data.removeprefix("repos:\n")
    )
    config.write_text(foreign, encoding="utf-8")

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr
    assert config.exists()
    remaining = config.read_text(encoding="utf-8")
    assert "my-own-hook" in remaining
    assert "pre-commit-script" not in remaining


def test_cli_uninstall_purge_removes_user_content_too(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    result = run_basicly_consumer(consumer, "uninstall", "--purge")
    assert result.returncode == 0, result.stderr
    assert not (consumer / ".basicly-local").exists()
    assert not (consumer / "basicly.toml").exists()


def test_cli_uninstall_keeps_hand_written_skill(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    mine = consumer / ".claude" / "skills" / "my-skill" / "SKILL.md"
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text(
        "---\nname: my-skill\ninvocation: model\ndescription: mine\n---\n\nMine.\n",
        encoding="utf-8",
    )

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr
    assert mine.exists()
    assert not (consumer / ".claude" / "skills" / "tool-git").exists()


def test_cli_uninstall_twice_is_a_noop(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")
    run_basicly_consumer(consumer, "uninstall")

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr
    assert "Nothing to remove" in result.stdout


def test_cli_uninstall_refuses_in_authoring_repo(work_repo: Path) -> None:
    result = run_basicly(work_repo, "uninstall")
    assert result.returncode == 1
    assert "authoring source" in result.stderr
    assert (work_repo / ".basicly" / "core").is_dir()


def test_cli_build_idempotent(work_repo: Path) -> None:
    result1 = run_basicly(work_repo, "build")
    assert result1.returncode == 0
    result2 = run_basicly(work_repo, "build")
    assert result2.returncode == 0
    assert "No files changed" in result2.stdout


def test_cli_check_passes_after_build(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    result = run_basicly(work_repo, "check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_cli_check_fails_after_manual_edit(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    agents = work_repo / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = run_basicly(work_repo, "check")
    assert result.returncode == 1
    assert "Stale generated files detected" in result.stderr


def _set_codex_size_cap(work_repo: Path, value: int) -> None:

    codex = work_repo / ".basicly" / "core" / "targets" / "codex.yaml"
    text = codex.read_text(encoding="utf-8")
    rewritten = re.sub(
        r"^max_size_warning: \d+$", f"max_size_warning: {value}", text, flags=re.MULTILINE
    )
    assert rewritten != text, "no max_size_warning line to rewrite in codex.yaml"
    codex.write_text(rewritten, encoding="utf-8")


def test_cli_check_reports_the_always_on_budget_overrun_build_reports(work_repo: Path) -> None:

    run_basicly(work_repo, "build")
    agents_bytes = len((work_repo / "AGENTS.md").read_bytes())
    _set_codex_size_cap(work_repo, agents_bytes - 1)

    result = run_basicly(work_repo, "check")

    assert result.returncode == 0, "an over-budget file is a cost to weigh, not a stale tree"
    assert "up to date" in result.stdout
    assert f"AGENTS.md exceeds {agents_bytes - 1} bytes ({agents_bytes})" in result.stderr


def test_the_codex_budget_is_measured_in_the_unit_codex_enforces(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    text = (work_repo / "AGENTS.md").read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) > len(text), "the two units must differ to tell them apart"
    _set_codex_size_cap(work_repo, len(text))

    result = run_basicly(work_repo, "check")

    assert f"({len(text.encode('utf-8'))})" in result.stderr, (
        "project_doc_max_bytes counts bytes, so a character count would under-report"
    )


def test_cli_check_is_silent_on_a_budget_it_meets(work_repo: Path) -> None:

    run_basicly(work_repo, "build")
    agents_chars = len((work_repo / "AGENTS.md").read_bytes())
    _set_codex_size_cap(work_repo, agents_chars + 1)

    result = run_basicly(work_repo, "check")

    assert result.returncode == 0
    assert "characters" not in result.stderr


def test_cli_build_target_only(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    result = run_basicly(work_repo, "build", "--target", "claude")
    assert result.returncode == 0
    assert "copilot-instructions.md" not in result.stdout
    result_check = run_basicly(work_repo, "check")
    assert result_check.returncode == 0


def test_cli_build_sweeps_stale_manifest_outputs(work_repo: Path) -> None:

    run_basicly(work_repo, "build")
    manifest_path = work_repo / ".basicly/generated-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    stale_rel = ".github/instructions/python-style.instructions.md"
    stale_file = work_repo / stale_rel
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_text("retired projection\n", encoding="utf-8")
    manifest["outputs"][stale_rel] = {"hash": "sha256:0", "source_fragments": ["python-style"]}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = run_basicly(work_repo, "build")
    assert result.returncode == 0, result.stderr
    assert f"Removed {stale_rel}" in result.stdout
    assert not stale_file.exists()
    assert not stale_file.parent.exists()
    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stale_rel not in manifest_after["outputs"]


def test_cli_build_target_keeps_other_targets_files(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    copilot_baseline = work_repo / ".github" / "copilot-instructions.md"
    assert copilot_baseline.is_file()

    result = run_basicly(work_repo, "build", "--target", "claude")
    assert result.returncode == 0, result.stderr
    assert "Removed" not in result.stdout
    assert copilot_baseline.is_file()


def test_cli_unknown_target(work_repo: Path) -> None:
    result = run_basicly(work_repo, "build", "--target", "unknown")
    assert result.returncode == 1
    assert "Unknown target" in result.stderr


def _add_duplicate_fragments(work_repo: Path) -> None:
    frag_dir = work_repo / ".basicly/core/fragments/project"
    frag_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "schema_version: 1\nid: {id}\ndescription: dup {id}\ncategory: project\n"
        "applies_to: [all]\nbody: |\n  This fragment body is intentionally duplicated.\n"
    )
    (frag_dir / "dup-one.fragment.yaml").write_text(body.format(id="dup-one"), encoding="utf-8")
    (frag_dir / "dup-two.fragment.yaml").write_text(body.format(id="dup-two"), encoding="utf-8")


def test_cli_catalog_verify_passes(work_repo: Path) -> None:
    result = run_basicly(work_repo, "catalog", "verify")
    assert result.returncode == 0, result.stderr
    assert "catalog verify: OK" in result.stdout


def test_cli_catalog_verify_flags_duplicate_bodies(work_repo: Path) -> None:
    _add_duplicate_fragments(work_repo)
    result = run_basicly(work_repo, "catalog", "verify")
    assert result.returncode == 1
    assert "identical bodies" in result.stderr


def _write_overlay_fragment(work_repo: Path, fragment_id: str, extra: str = "") -> Path:
    path = work_repo / ".basicly-local/fragments/user" / f"{fragment_id}.fragment.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"schema_version: 1\nid: {fragment_id}\ndescription: overlay {fragment_id}\n"
        f"category: commands\napplies_to: [all]\n{extra}body: |\n  Overlay body.\n",
        encoding="utf-8",
    )
    return path


def test_cli_catalog_dump_names_every_selected_item_with_its_origin_and_axes(
    work_repo: Path,
) -> None:
    _write_overlay_fragment(work_repo, "overlay-only")

    result = run_basicly(work_repo, "catalog", "dump")

    assert result.returncode == 0, result.stderr
    assert (
        ".claude/CLAUDE.md [claude/claude_wrapper] filter.applies_to=all,claude unscoped only"
        in result.stdout
    )
    assert (
        "core-rules applies_to=all scope=** technologies=any "
        "<- .basicly/core/fragments/project/core-rules.fragment.yaml [core]"
    ) in result.stdout
    assert (
        "overlay-only applies_to=all scope=** technologies=any "
        "<- .basicly-local/fragments/user/overlay-only.fragment.yaml [user]"
    ) in result.stdout


def test_cli_catalog_dump_names_both_the_override_and_the_source_it_shadows(
    work_repo: Path,
) -> None:

    control = run_basicly(work_repo, "catalog", "dump")
    assert "git-discipline applies_to=" in control.stdout, control.stderr

    _write_overlay_fragment(work_repo, "mine", extra="override: true\nreplaces: [git-discipline]\n")
    result = run_basicly(work_repo, "catalog", "dump")

    assert result.returncode == 0, result.stderr
    assert (
        "overridden by the overlay: 1\n"
        "  git-discipline (.basicly/core/fragments/commands/git-discipline.fragment.yaml) "
        "shadowed by mine (.basicly-local/fragments/user/mine.fragment.yaml)"
    ) in result.stdout
    assert "git-discipline applies_to=" not in result.stdout


def test_cli_build_verify_blocks_and_writes_nothing(work_repo: Path) -> None:
    manifest = work_repo / ".basicly/generated-manifest.json"
    manifest.unlink()
    _add_duplicate_fragments(work_repo)
    result = run_basicly(work_repo, "build", "--verify")
    assert result.returncode == 1
    assert "nothing written" in result.stderr
    assert not manifest.exists()


def test_cli_build_verify_passes_on_clean_catalog(work_repo: Path) -> None:
    result = run_basicly(work_repo, "build", "--verify")
    assert result.returncode == 0, result.stderr


def test_cli_review_dry_run_prints_prompt_without_agent(work_repo: Path) -> None:
    result = run_basicly(work_repo, "catalog", "review", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "advisory semantic review" in result.stdout
    assert "===== FILE: AGENTS.md =====" in result.stdout
    assert "under review" in result.stdout


def test_cli_review_handoff_is_advisory(work_repo: Path) -> None:
    result = run_basicly(work_repo, "catalog", "review", "--runner", "manual")
    assert result.returncode == 0, result.stderr
    assert "handoff" in result.stdout
    assert "Advisory only" in result.stdout


def test_cli_install_migrates_legacy_fragments(work_repo: Path) -> None:
    legacy_core = work_repo / ".basicly" / "fragments" / "project"
    legacy_core.mkdir(parents=True, exist_ok=True)
    legacy_overlay = work_repo / ".basicly" / "fragments" / "user"
    legacy_overlay.mkdir(parents=True, exist_ok=True)

    legacy_core_file = legacy_core / "legacy-core.fragment.md"
    legacy_core_file.write_text(
        "---\n"
        "id: legacy-core\n"
        "description: legacy core\n"
        "category: project\n"
        "applies_to: [all]\n"
        "---\n\n"
        "legacy core\n",
        encoding="utf-8",
    )
    legacy_user_file = legacy_overlay / "legacy-user.fragment.md"
    legacy_user_file.write_text(
        "---\n"
        "id: legacy-user\n"
        "description: legacy user\n"
        "category: project\n"
        "applies_to: [all]\n"
        "---\n\n"
        "legacy user\n",
        encoding="utf-8",
    )

    result = run_basicly(work_repo, "install")

    assert result.returncode == 0
    assert (
        work_repo / ".basicly" / "core" / "fragments" / "project" / "legacy-core.fragment.md"
    ).exists()
    assert (
        work_repo / ".basicly-local" / "fragments" / "user" / "legacy-user.fragment.md"
    ).exists()


def test_cli_install_prunes_legacy_catalog_sources(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    skill_dir = consumer / ".basicly" / "core" / "skills" / "tool-x"
    frag_dir = consumer / ".basicly" / "core" / "fragments" / "project"
    skill_dir.mkdir(parents=True)
    frag_dir.mkdir(parents=True)

    legacy_skill = skill_dir / "SKILL.md"
    legacy_skill.write_text(
        "---\nname: tool-x\ninvocation: model\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    legacy_frag = frag_dir / "y.fragment.md"
    legacy_frag.write_text("---\nid: y\n---\n\nbody\n", encoding="utf-8")

    kept_skill = skill_dir / "skill.yaml"
    kept_skill.write_text(
        "schema_version: 1\nname: tool-x\ninvocation: model\n"
        "description: d\ninstructions: |\n  body\n",
        encoding="utf-8",
    )

    overlay = consumer / ".basicly-local" / "fragments" / "user"
    overlay.mkdir(parents=True)
    kept_overlay = overlay / "keep.fragment.md"
    kept_overlay.write_text("mine\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")

    assert result.returncode == 0, result.stderr
    assert not legacy_skill.exists()
    assert not legacy_frag.exists()
    assert kept_skill.exists()
    assert kept_overlay.exists()


def test_cli_install_removes_legacy_vendored_engine(tmp_path: Path) -> None:

    consumer = tmp_path / "consumer"
    engine_dir = consumer / ".basicly" / "basicly"
    engine_dir.mkdir(parents=True)
    (engine_dir / "cli.py").write_text("# legacy vendored engine\n", encoding="utf-8")
    (engine_dir / "loader.py").write_text("# legacy\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")

    assert result.returncode == 0, result.stderr
    assert not engine_dir.exists()
    assert "Removed legacy vendored engine" in result.stdout


def test_cli_skills_build_idempotent(work_repo: Path) -> None:
    result1 = run_basicly(work_repo, "skills-build")
    assert result1.returncode == 0
    result2 = run_basicly(work_repo, "skills-build")
    assert result2.returncode == 0
    assert "No skill files changed" in result2.stdout


def test_cli_skills_check_passes_after_build(work_repo: Path) -> None:
    run_basicly(work_repo, "skills-build")
    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_cli_skills_check_fails_after_manual_edit(work_repo: Path) -> None:
    run_basicly(work_repo, "skills-build")

    projected_skill = work_repo / ".claude" / "skills" / "tool-ripgrep" / "SKILL.md"
    projected_skill.write_text(
        projected_skill.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 1
    assert "Stale skill projection detected" in result.stderr


def test_cli_skills_check_fails_on_a_hand_authored_skill(work_repo: Path) -> None:
    run_basicly(work_repo, "skills-build")

    hand_authored = work_repo / ".claude" / "skills" / "no-such-source" / "SKILL.md"
    hand_authored.parent.mkdir(parents=True)
    hand_authored.write_text("---\nname: release-process\n---\n\nbody\n", encoding="utf-8")

    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 1
    assert "Unmanaged files under a projected skills root" in result.stderr
    assert "Stale skill projection detected" not in result.stderr


def test_cli_agents_new_build_check_roundtrip(work_repo: Path) -> None:
    result = run_basicly(
        work_repo, "catalog", "new", "agent", "triage-bot", "--description", "Triages issues."
    )
    assert result.returncode == 0, result.stderr
    assert (work_repo / ".basicly/core/agents/triage-bot/agent.yaml").exists()

    build = run_basicly(work_repo, "agents-build")
    assert build.returncode == 0, build.stderr
    projected = work_repo / ".claude/agents/triage-bot.md"
    text = projected.read_text(encoding="utf-8")
    assert text.startswith("---\nname: triage-bot\n")
    assert "Generated by `basicly agents-build`" in text

    check = run_basicly(work_repo, "agents-check")
    assert check.returncode == 0, check.stderr

    projected.write_text(text + "\n", encoding="utf-8")
    stale = run_basicly(work_repo, "agents-check")
    assert stale.returncode == 1
    assert "Stale agent projection detected" in stale.stderr


def test_cli_uninstall_sweeps_generated_agents_keeps_hand_written(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    agents_dir = consumer / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    generated = agents_dir / "old-agent.md"
    generated.write_text(
        f"---\nname: old-agent\n---\n\n{AGENT_GENERATED_MARKER}\n\nBody.\n", encoding="utf-8"
    )
    mine = agents_dir / "mine.md"
    mine.write_text("---\nname: mine\n---\n\nMine.\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr
    assert not generated.exists()
    assert mine.exists()


def test_cli_install_prunes_retired_github_skills_root(tmp_path: Path) -> None:

    consumer = tmp_path / "consumer"
    generated = consumer / ".github" / "skills" / "tool-x"
    generated.mkdir(parents=True)
    (generated / "SKILL.md").write_text(f"{GENERATED_MARKER}\n\n# x\n", encoding="utf-8")
    user_skill = consumer / ".github" / "skills" / "mine"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("# hand-authored\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "install")

    assert result.returncode == 0, result.stderr
    assert not (generated / "SKILL.md").exists()
    assert (user_skill / "SKILL.md").exists()
    assert not (consumer / ".github" / "skills" / "tool-x").exists()
    assert list((consumer / ".claude" / "skills").rglob("SKILL.md"))
    assert list((consumer / ".agents" / "skills").rglob("SKILL.md"))
    assert not list((consumer / ".github" / "skills").rglob("SKILL.md"))[1:]


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_cli_build_sweep_never_follows_symlinks_or_git_paths(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    manifest_path = work_repo / ".basicly/generated-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    victim = work_repo / "victim.txt"
    victim.write_text("precious consumer file\n", encoding="utf-8")
    link_rel = "stale-link.md"
    (work_repo / link_rel).symlink_to(victim)
    git_rel = ".git/fake-hook"
    (work_repo / ".git").mkdir(exist_ok=True)
    (work_repo / git_rel).write_text("repo internals\n", encoding="utf-8")

    manifest["outputs"][link_rel] = {"hash": "sha256:0", "source_fragments": ["x"]}
    manifest["outputs"][git_rel] = {"hash": "sha256:0", "source_fragments": ["x"]}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = run_basicly(work_repo, "build")
    assert result.returncode == 0, result.stderr
    assert victim.exists()
    assert not (work_repo / link_rel).exists()
    assert (work_repo / git_rel).exists()
    assert "skipping unsafe manifest entry" in result.stderr


def test_cli_check_sees_crlf_drift(work_repo: Path) -> None:
    run_basicly(work_repo, "build")
    target = work_repo / "AGENTS.md"
    content = target.read_bytes()
    target.write_bytes(content.replace(b"\n", b"\r\n"))

    result = run_basicly(work_repo, "check")
    assert result.returncode == 1


def test_cli_survives_a_narrow_console_encoding(work_repo: Path) -> None:

    env = {**os.environ, "PYTHONPATH": str(work_repo / "src"), "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "-m", "basicly.cli", "catalog", "list", "skill"],
        cwd=work_repo,
        env=env,
        capture_output=True,
        encoding="cp1252",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "charmap" not in result.stderr


def test_cli_status_reports_authoring_repo(work_repo: Path) -> None:
    result = run_basicly(work_repo, "status")
    assert result.returncode == 0, result.stderr
    assert "engine: basicly" in result.stdout
    assert "repo: authoring" in result.stdout
    assert "drift: generated files up to date" in result.stdout


def test_cli_permissions_build_and_check_are_idempotent(work_repo: Path) -> None:
    settings = json.loads((work_repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    build = run_basicly(work_repo, "permissions-build")
    assert build.returncode == 0, build.stderr
    assert "up to date" in build.stdout
    check = run_basicly(work_repo, "permissions-check")
    assert check.returncode == 0, check.stderr

    settings["permissions"]["deny"] = [
        p for p in settings["permissions"]["deny"] if p != "Bash(rm -rf*)"
    ]
    (work_repo / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    assert run_basicly(work_repo, "permissions-check").returncode == 1
    restore = run_basicly(work_repo, "permissions-build")
    assert restore.returncode == 0, restore.stderr
    assert "Wrote" in restore.stdout
    restored = json.loads((work_repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Bash(rm -rf*)" in restored["permissions"]["deny"]


def test_cli_install_projects_permissions_deny_list(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    assert run_basicly_consumer(consumer, "install").returncode == 0

    settings = json.loads((consumer / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    assert "Bash(rm -rf*)" in deny
    assert "Read(.env)" in deny
    settings["permissions"]["deny"].append("Bash(sudo*)")
    (consumer / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    assert run_basicly_consumer(consumer, "permissions-build").returncode == 0
    after = json.loads((consumer / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Bash(sudo*)" in after["permissions"]["deny"]
    assert "Bash(rm -rf*)" in after["permissions"]["deny"]


def test_cli_status_json_authoring_schema(work_repo: Path) -> None:
    result = run_basicly(work_repo, "status", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert set(report) == {
        "schema_version",
        "engine_version",
        "repo_kind",
        "catalog",
        "drift",
        "hooks",
        "permissions",
        "technologies",
        "overlays",
    }
    assert report["schema_version"] == 1
    assert report["repo_kind"] == "authoring"
    assert report["catalog"] == {
        "installed_version": None,
        "installed_at": None,
        "state_error": None,
    }
    assert report["drift"] == {"stale_outputs": [], "manifest_stale": False, "core_drift": []}
    assert set(report["hooks"]) == {"git", "claude", "copilot"}
    for entry in report["hooks"].values():
        assert entry["mismatches"] == 0
    assert report["permissions"]["claude"]["managed_patterns"] > 0
    assert report["permissions"]["claude"]["mismatches"] == 0
    assert set(report["overlays"]) == {"fragments", "agents"}


def test_cli_status_fleet_rolls_up_the_workspace(work_repo: Path) -> None:

    workspace = work_repo.parent
    (workspace / "other-repo" / ".basicly").mkdir(parents=True)
    result = run_basicly(work_repo, "status", "--fleet")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["workspace_root"] == str(workspace)
    by_name = {r["name"]: r for r in report["repos"]}
    assert {work_repo.name, "other-repo"} <= set(by_name)
    assert by_name[work_repo.name]["status"]["repo_kind"] == "authoring"
    assert "runs" in by_name["other-repo"] and "status" in by_name["other-repo"]
    assert report["totals"]["repos"] >= 2


def test_cli_status_json_consumer_reports_install_and_drift(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    install = run_basicly_consumer(consumer, "install")
    assert install.returncode == 0, install.stderr

    result = run_basicly_consumer(consumer, "status", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["repo_kind"] == "consumer"
    assert report["catalog"]["installed_version"] == report["engine_version"]
    assert report["drift"] == {"stale_outputs": [], "manifest_stale": False, "core_drift": []}

    state_payload = json.loads(
        (consumer / ".basicly" / "state" / "install.json").read_text(encoding="utf-8")
    )
    tracked = next(iter(sorted(state_payload["core"])))
    core_file = consumer / ".basicly" / "core" / tracked
    core_file.write_text(core_file.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    manifest = json.loads(
        (consumer / ".basicly" / "generated-manifest.json").read_text(encoding="utf-8")
    )
    generated = next(iter(sorted(manifest["outputs"])))
    (consumer / generated).unlink()

    result = run_basicly_consumer(consumer, "status", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert {"path": tracked, "reason": "modified"} in report["drift"]["core_drift"]
    assert generated in report["drift"]["stale_outputs"]


def test_cli_status_never_writes(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    install = run_basicly_consumer(consumer, "install")
    assert install.returncode == 0, install.stderr

    def snapshot() -> dict[str, str]:
        return {
            path.relative_to(consumer).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(consumer.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    assert run_basicly_consumer(consumer, "status").returncode == 0
    assert run_basicly_consumer(consumer, "status", "--json").returncode == 0
    assert snapshot() == before


def test_cli_hooks_check_names_the_command_that_can_fix_script_drift(
    work_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:

    monkeypatch.chdir(work_repo)
    script = work_repo / ".basicly/core/hooks/pre-commit.py"
    script.write_text("# drifted\n", encoding="utf-8")

    assert cli.main(["hooks-check"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "`basicly hooks-build` does not copy hook scripts" in err
    assert "`basicly install` re-materializes them" in err
    assert "edit the catalog source" in err
    assert "Run `basicly hooks-build` to sync hooks" not in err


def test_cli_hooks_check_still_points_wiring_drift_at_hooks_build(
    work_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(work_repo)
    config = work_repo / ".pre-commit-config.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    for repo in data["repos"]:
        if repo.get("repo") == "local":
            repo["hooks"] = [h for h in repo["hooks"] if h["id"] != "pre-push-script"]
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    assert cli.main(["hooks-check"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "Run `basicly hooks-build` to sync hooks" in err
    assert "does not copy hook scripts" not in err


def test_cli_hooks_check_warns_when_uv_is_missing(
    work_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(work_repo)
    real_which = shutil.which
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name, *args, **kwargs: None if name == "uv" else real_which(name, *args, **kwargs),
    )
    assert cli.main(["hooks-check"]) == 0
    err = capsys.readouterr().err
    assert "uv is not on PATH" in err and "every committer" in err


def test_cli_hooks_check_stays_quiet_when_uv_is_present(
    work_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(work_repo)
    assert cli.main(["hooks-check"]) == 0
    assert "uv is not on PATH" not in capsys.readouterr().err


_OBSERVABLE_CHILD = (
    "import sys; from basicly import cli; "
    "cli._line_buffer_stdout(); "
    "print('session:  demo'); "
    "sys.stdin.readline()"
)


def _child_env() -> dict[str, str]:

    package_parent = Path(cli.__file__).resolve().parent.parent
    return {**os.environ, "PYTHONPATH": str(package_parent)}


def test_a_printed_line_is_observable_before_exit_when_stdout_is_a_pipe() -> None:

    proc = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _OBSERVABLE_CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=_child_env(),
    )
    assert proc.stdin and proc.stdout
    try:
        stdout = proc.stdout
        first: list[str] = []
        reader = threading.Thread(target=lambda: first.append(stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout=30)
        assert first, "nothing readable while the child was still blocked on stdin"
        assert first[0] == "session:  demo\n"
    finally:
        proc.stdin.write("\n")
        proc.stdin.close()
        proc.wait(timeout=30)


def test_line_buffer_stdout_sets_line_buffering_on_the_real_stream() -> None:
    proc = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-c",
            "import sys; from basicly import cli; "
            "before = sys.stdout.line_buffering; cli._line_buffer_stdout(); "
            "sys.stderr.write(f'{before} {sys.stdout.line_buffering}')",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=_child_env(),
    )
    assert proc.stderr == "False True"


def test_line_buffer_stdout_tolerates_a_stream_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.sys, "stdout", object())
    cli._line_buffer_stdout()


def test_main_line_buffers_stdout_before_dispatching(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_line_buffer_stdout", lambda: calls.append("buffered"))
    monkeypatch.setattr(cli, "cmd_status", lambda _a: calls.append("dispatched") or 0)
    cli.main(["status"])
    assert calls == ["buffered", "dispatched"]


def test_the_ceremony_reprint_carries_the_session_overrides() -> None:

    args = argparse.Namespace(
        issue="basicly-1th1",
        work_type="bug",
        children=None,
        mode="full",
        root="basicly-jr0l",
        runner="manual",
        autonomy="L3",
    )

    rerun = cli._ceremony_rerun(args, "2ff3e7a2")

    assert rerun == (
        "basicly loop run basicly-1th1 --work-type bug --root basicly-jr0l "
        "--runner manual --autonomy L3 --confirm 2ff3e7a2"
    )


def test_the_ceremony_reprint_omits_overrides_that_were_not_given() -> None:
    args = argparse.Namespace(
        issue="i", work_type=None, children=None, mode="full", root=None, runner=None, autonomy=None
    )

    assert cli._ceremony_rerun(args, "abc123") == "basicly loop run i --confirm abc123"


def _subcommand_choices(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:

    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(actions) == 1, "expected exactly one subparser action on this parser"
    return actions[0]


def _dispatch_sites(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:

    sites = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        sites.append(prefix)
        for name, sub in action.choices.items():
            sites.extend(_dispatch_sites(sub, (*prefix, name)))
    return sites


def _parser_at(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> argparse.ArgumentParser:
    for name in prefix:
        parser = _subcommand_choices(parser).choices[name]
    return parser


DISPATCH_SITES = _dispatch_sites(cli._build_parser())


def test_the_dispatch_site_list_reaches_nested_groups() -> None:

    parser = cli._build_parser()
    nested = {
        (name,)
        for name, sub in _subcommand_choices(parser).choices.items()
        if any(isinstance(a, argparse._SubParsersAction) for a in sub._actions)
    }

    assert nested, "expected the CLI to have at least one command group with subcommands"
    assert () in DISPATCH_SITES
    assert nested <= set(DISPATCH_SITES)


def test_every_registered_subcommand_has_a_handler() -> None:

    choices = set(_subcommand_choices(cli._build_parser()).choices)

    assert choices == set(cli._handlers())


@pytest.mark.parametrize(
    "site", DISPATCH_SITES, ids=[" ".join(s) or "<top level>" for s in DISPATCH_SITES]
)
def test_a_registered_subcommand_with_no_handler_fails_loudly(
    site: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:

    build_parser = cli._build_parser

    def parser_with_an_orphan() -> argparse.ArgumentParser:
        parser = build_parser()
        group = _parser_at(parser, site)
        _subcommand_choices(group).add_parser("orphan", help="registered but unhandled")
        return parser

    monkeypatch.setattr(cli, "_build_parser", parser_with_an_orphan)

    code = cli.main([*site, "orphan"])

    out, err = capsys.readouterr()
    assert code != 0
    assert err.strip() == (
        f"internal error: subcommand {' '.join((*site, 'orphan'))!r} is registered on "
        "the parser but has no handler — this is a bug in basicly, not in your invocation"
    )
    assert out == ""


def test_the_detached_argv_carries_every_flag_the_launch_was_given() -> None:
    args = argparse.Namespace(
        issue="basicly-hnnmk9",
        label="truth",
        max_passes=3,
        runner="claude",
        autonomy="L3",
        tier="high",
    )

    argv = cli._detach_argv(args, "supervise")

    assert argv[0] == sys.executable
    assert " ".join(argv[1:]) == (
        "-m basicly.cli loop supervise basicly-hnnmk9 --label truth --max-passes 3 "
        "--runner claude --autonomy L3 --tier high"
    )
    assert "--detach" not in argv, "the child must not detach again"


def test_a_launch_with_no_flags_forwards_none_of_them() -> None:
    args = argparse.Namespace(
        issue="i", label=None, max_passes=None, runner=None, autonomy=None, tier=None
    )

    assert cli._detach_argv(args, "supervise")[-3:] == ["loop", "supervise", "i"]


def test_the_forwarding_table_is_every_supervise_flag_but_detach() -> None:

    parser = _parser_at(cli._build_parser(), ("loop", "supervise"))

    declared = {
        action.dest: action.option_strings[0]
        for action in parser._actions
        if action.option_strings and action.dest not in {"help", "detach"}
    }

    assert declared == cli.SUPERVISE_FORWARDED_FLAGS


def test_detach_isolation_is_a_new_session_on_posix_and_no_console_on_windows() -> None:
    assert cli._detach_isolation("posix") == (True, 0)
    assert cli._detach_isolation("nt") == (
        False,
        cli.DETACHED_PROCESS | cli.runner.CREATE_NEW_PROCESS_GROUP,
    )


def test_supervise_detach_prints_the_pid_and_log_and_takes_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def never(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the launcher took the lock the child needs")

    monkeypatch.setattr(cli.supervise, "acquire", never)
    spawned: dict[str, object] = {}

    def fake_spawn(argv: list[str], log: Path, *, cwd: Path) -> int:
        spawned.update(argv=argv, log=log, cwd=cwd)
        return 4242

    monkeypatch.setattr(cli, "_spawn_detached", fake_spawn)
    args = argparse.Namespace(
        issue="basicly-hnnmk9", label=None, max_passes=None, runner=None, autonomy=None, tier=None
    )

    code = cli.main(["loop", "supervise", args.issue, "--detach", "--max-passes", "1"])

    out = capsys.readouterr().out
    log = spawned["log"]
    assert code == 0
    assert isinstance(log, Path)
    assert log.parent == tmp_path / cli.DETACHED_LOGS_DIR
    assert "detached: pid 4242" in out
    assert str(log) in out
    assert spawned["argv"] == [*cli._detach_argv(args, "supervise"), "--max-passes", "1"]


def _child_source(tmp_path: Path) -> str:
    return textwrap.dedent(f"""
        import pathlib, time
        release = pathlib.Path({str(tmp_path / "release")!r})
        pathlib.Path({str(tmp_path / "started")!r}).write_text("up")
        for _ in range(600):
            if release.exists():
                print("survived", flush=True)
                break
            time.sleep(0.1)
    """)


def _launcher_source(tmp_path: Path, log: Path, *, then: str) -> str:
    return textwrap.dedent(f"""
        import pathlib, sys, time
        from basicly import cli
        print(cli._spawn_detached([sys.executable, "-c", {_child_source(tmp_path)!r}],
              pathlib.Path({str(log)!r}), cwd=pathlib.Path({str(tmp_path)!r})), flush=True)
        {then}
    """)


def _await(path: Path, why: str, *, contains: str = "", deadline: float = 30.0) -> None:

    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if path.exists() and contains in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.05)
    pytest.fail(f"{why} within {deadline}s")


def _repo_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}


def test_a_detached_child_outlives_the_process_that_launched_it(tmp_path: Path) -> None:
    log = tmp_path / "detached.log"
    launcher = _launcher_source(tmp_path, log, then="")

    parent = subprocess.run(
        [sys.executable, "-c", launcher],
        env=_repo_env(),
        text=True,
        check=True,
        timeout=60,
        capture_output=True,
    )

    _await(tmp_path / "started", "the detached child never started")
    assert parent.stdout.strip().isdigit()
    (tmp_path / "release").write_text("go")
    _await(log, "the detached child left no output", contains="survived")


def test_a_detached_child_survives_the_kill_of_its_launcher_group(tmp_path: Path) -> None:

    if os.name == "nt":
        pytest.skip("process groups and killpg are POSIX")
    log = tmp_path / "detached.log"
    launcher = _launcher_source(tmp_path, log, then="time.sleep(300)")
    proc = subprocess.Popen(
        [sys.executable, "-c", launcher], env=_repo_env(), start_new_session=True
    )
    try:
        _await(tmp_path / "started", "the detached child never started")

        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

        proc.wait(timeout=30)
        (tmp_path / "release").write_text("go")
        _await(log, "the group kill took the detached child with it", contains="survived")
    finally:
        proc.kill()
