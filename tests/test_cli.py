"""Integration tests for the CLI."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from basicly import cli
from basicly.agents import GENERATED_MARKER as AGENT_GENERATED_MARKER
from basicly.config import (
    CONFIG_FILE,
    CONSUMER_CI_WORKFLOW,
    DEFAULT_CONFIG_TOML,
    LOCAL_CONFIG_FILE,
    VSCODE_TASKS_JSON,
    load_project_paths,
)
from basicly.skills import GENERATED_MARKER

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def work_repo(tmp_path: Path) -> Path:
    """Return an isolated copy of the repo so tests never mutate real repo state."""
    work = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, work, ignore=shutil.ignore_patterns(".git", ".venv"))
    return work


def run_basicly(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the basicly CLI with the given arguments in the given working directory."""
    # Inherit the real environment (PATH included) so the CLI's own subprocess
    # calls — e.g. `git` in status — resolve; a bare env has no PATH fallback on
    # Windows, only on POSIX.
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
    """Run the CLI in a consumer dir, importing basicly from the real repo's src."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_install_converges_fresh_consumer(tmp_path: Path) -> None:
    """One install produces catalog, overlay, config, and every projected artifact."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr

    assert (consumer / "basicly.toml").is_file()
    assert (consumer / ".basicly-local" / "fragments" / "user").is_dir()
    assert list((consumer / ".basicly" / "core" / "fragments").rglob("*.fragment.yaml"))
    assert (consumer / ".basicly" / "core" / "targets" / "claude.yaml").is_file()

    # The overview/commands overlay stubs are seeded as drafts: present as
    # sources, absent from projections until the consumer activates them.
    overlay_user = consumer / ".basicly-local" / "fragments" / "user"
    overview = overlay_user / "project" / "project-overview.fragment.yaml"
    commands = overlay_user / "commands" / "commands.fragment.yaml"
    assert "status: draft" in overview.read_text(encoding="utf-8")
    assert "status: draft" in commands.read_text(encoding="utf-8")
    claude_md = (consumer / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Project Overview" not in claude_md

    # A single command projects everything — no separate build/skills/hooks runs.
    assert (consumer / "AGENTS.md").is_file()
    assert (consumer / ".claude" / "CLAUDE.md").is_file()
    assert (consumer / ".github" / "copilot-instructions.md").is_file()
    assert list((consumer / ".claude" / "skills").rglob("SKILL.md"))
    assert (consumer / ".pre-commit-config.yaml").is_file()
    assert '"label": "basicly: build"' in (consumer / ".vscode" / "tasks.json").read_text(
        encoding="utf-8"
    )


def test_cli_install_honors_custom_core_paths(tmp_path: Path) -> None:
    """Install must materialize into the basicly.toml core root, not a hardcoded one.

    Regression: init hardcoded .basicly/core while build read the configured
    paths, so a custom-path consumer got a successful scaffold followed by a
    build that silently generated nothing.
    """
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
    assert not (consumer / ".basicly").exists()
    assert (consumer / "AGENTS.md").is_file()
    config_text = (consumer / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "conf/basicly/core/hooks/pre-commit.py" in config_text


def test_cli_install_is_idempotent_and_preserves_edits(tmp_path: Path) -> None:
    """A second install converges with no changes and never clobbers user content."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    # A user edit to the config must survive re-running install.
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
    """--help carries the consumer/contributor/harness grouping (and no update)."""
    result = run_basicly_consumer(tmp_path, "--help")
    assert result.returncode == 0
    for marker in ("command groups:", "consumer (", "contributor (", "harness ("):
        assert marker in result.stdout
    assert "re-running install IS the upgrade" in result.stdout


def test_cli_piped_output_stays_plain_text(work_repo: Path) -> None:
    """Piped/CI output carries no ANSI styling and keeps the exact wording."""
    result = run_basicly(work_repo, "check")
    assert result.returncode == 0, result.stderr
    assert "\x1b" not in result.stdout
    assert "All generated files and manifest are up to date." in result.stdout

    listing = run_basicly(work_repo, "catalog", "list", "skill")
    assert listing.returncode == 0
    assert "\x1b" not in listing.stdout
    assert "tool-ripgrep" in listing.stdout


def test_cli_install_technology_selection_filters_and_prunes(tmp_path: Path) -> None:
    """A recorded selection keeps tagged sources out and re-narrowing prunes them."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = run_basicly_consumer(consumer, "install", "--technologies", "zsh")
    assert result.returncode == 0, result.stderr
    assert 'technologies = ["zsh"]' in (consumer / "basicly.toml").read_text(encoding="utf-8")
    # Universal skills ship; python/tmux-tagged skills are filtered out.
    assert (consumer / ".claude" / "skills" / "tool-git" / "SKILL.md").is_file()
    assert (consumer / ".claude" / "skills" / "tool-zsh" / "SKILL.md").is_file()
    assert not (consumer / ".claude" / "skills" / "tool-uv").exists()
    assert not (consumer / ".claude" / "skills" / "tool-tmux").exists()
    # Core sync stays full: the filtered skill's source is still materialized.
    assert (consumer / ".basicly" / "core" / "skills" / "tool-uv" / "skill.yaml").is_file()

    # Widening the selection ships the tagged skill; narrowing again prunes it.
    result = run_basicly_consumer(consumer, "install", "--technologies", "python")
    assert result.returncode == 0, result.stderr
    assert (consumer / ".claude" / "skills" / "tool-uv" / "SKILL.md").is_file()
    result = run_basicly_consumer(consumer, "install", "--technologies", "zsh")
    assert result.returncode == 0, result.stderr
    assert not (consumer / ".claude" / "skills" / "tool-uv").exists()

    # An out-of-vocabulary flag value fails loudly before anything is recorded.
    result = run_basicly_consumer(consumer, "install", "--technologies", "pyton")
    assert result.returncode == 1
    assert "Unknown technology value" in result.stderr


def test_record_install_technologies_rejects_empty_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A flag value that parses to nothing is an error, not an empty selection."""
    assert cli._record_install_technologies(tmp_path, None) is True
    assert cli._record_install_technologies(tmp_path, ",") is False
    assert "at least one value" in capsys.readouterr().err
    assert not (tmp_path / "basicly.toml").exists()


def test_setup_beads_initializes_with_derived_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh repo gets `br init` with a prefix sanitized from its dir name."""
    repo = tmp_path / "My-Terminal.2"
    repo.mkdir()
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.setattr(
        cli.br,
        "try_run_br",
        lambda root, args: (
            calls.append((args, root)) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )

    cli._setup_beads(repo)

    assert calls == [(["init", "--prefix", "myterminal2", "--quiet"], repo)]
    assert "issue prefix: myterminal2" in capsys.readouterr().out


def test_setup_beads_skips_existing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An existing .beads workspace is never re-initialized."""
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "config.yaml").write_text("issue_prefix: kept\n", encoding="utf-8")
    monkeypatch.setattr(
        cli.br, "try_run_br", lambda *_a, **_kw: pytest.fail("must not call br init")
    )

    cli._setup_beads(tmp_path)

    assert "left unchanged" in capsys.readouterr().out


def test_setup_beads_degrades_without_br(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No br on PATH: actionable guidance, no failure, no subprocess call."""
    monkeypatch.setattr(cli.br, "try_run_br", lambda *_a, **_kw: None)

    cli._setup_beads(tmp_path)

    assert "br init --prefix" in capsys.readouterr().out


def test_beads_prefix_enforces_leading_letter(tmp_path: Path) -> None:
    """A digit-leading or empty name is padded to a letter-leading prefix."""
    assert cli._beads_prefix(tmp_path / "42tools") == "repo42tools"
    assert cli._beads_prefix(tmp_path / "---") == "repo"


def test_scaffold_vscode_tasks_never_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The tasks scaffold is written once; an existing file is the user's."""
    cli._scaffold_vscode_tasks(tmp_path)
    tasks_path = tmp_path / ".vscode" / "tasks.json"
    assert tasks_path.read_text(encoding="utf-8") == VSCODE_TASKS_JSON

    tasks_path.write_text("{ /* mine */ }", encoding="utf-8")
    cli._scaffold_vscode_tasks(tmp_path)
    assert tasks_path.read_text(encoding="utf-8") == "{ /* mine */ }"
    assert "left unchanged" in capsys.readouterr().out


def test_purge_removes_only_pristine_vscode_tasks(tmp_path: Path) -> None:
    """--purge deletes tasks.json only while byte-identical to the scaffold."""
    paths = load_project_paths(tmp_path)
    tasks_path = tmp_path / ".vscode" / "tasks.json"

    tasks_path.parent.mkdir(parents=True)
    tasks_path.write_text(VSCODE_TASKS_JSON, encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert not tasks_path.exists()

    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(VSCODE_TASKS_JSON + "// edited\n", encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert tasks_path.exists()  # user-modified file survives purge


def test_scaffold_ci_workflow_writes_once_and_parses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CI workflow scaffold is valid YAML, written once, then the user's."""
    cli._scaffold_ci_workflow(tmp_path)
    workflow_path = tmp_path / ".github" / "workflows" / "basicly-gates.yml"
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    assert set(data["jobs"]) == {"commit-messages", "gates"}

    workflow_path.write_text("name: mine\n", encoding="utf-8")
    cli._scaffold_ci_workflow(tmp_path)
    assert workflow_path.read_text(encoding="utf-8") == "name: mine\n"
    assert "left unchanged" in capsys.readouterr().out


def test_scaffold_local_config_ignore_appends_once(tmp_path: Path) -> None:
    """The ignore entry is created, appended without clobbering, and idempotent."""
    cli._scaffold_local_config_ignore(tmp_path)
    ignore_path = tmp_path / ".gitignore"
    assert LOCAL_CONFIG_FILE in ignore_path.read_text(encoding="utf-8").splitlines()

    ignore_path.write_text("node_modules/\n", encoding="utf-8")
    cli._scaffold_local_config_ignore(tmp_path)
    lines = ignore_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "node_modules/"  # existing content survives the append
    assert LOCAL_CONFIG_FILE in lines

    before = ignore_path.read_text(encoding="utf-8")
    cli._scaffold_local_config_ignore(tmp_path)
    assert ignore_path.read_text(encoding="utf-8") == before  # second run is a no-op


def test_scaffold_local_config_ignore_accepts_rooted_entry(tmp_path: Path) -> None:
    """A user's /basicly.local.toml spelling counts as covered — no duplicate."""
    ignore_path = tmp_path / ".gitignore"
    ignore_path.write_text(f"/{LOCAL_CONFIG_FILE}\n", encoding="utf-8")
    cli._scaffold_local_config_ignore(tmp_path)
    assert ignore_path.read_text(encoding="utf-8") == f"/{LOCAL_CONFIG_FILE}\n"


def test_this_repo_satisfies_the_local_config_ignore_it_scaffolds() -> None:
    """This repo carries the local-config ignore entry that `basicly install` scaffolds.

    The dual-use constraint (factory design §1) says every guarantee ships as
    engine behaviour a consumer gets — so a property the harness scaffolds for
    everyone else and does not hold for itself is a real gap, and this one bit:
    basicly is never installed into basicly, so it was the only repo whose
    local override file was still tracked. It is not cosmetic. A landing
    refuses any dirt outside `.beads/`, so an untracked `basicly.local.toml`
    blocks every landing in the very repo that ships the override mechanism.

    Asserted through the engine's own predicate rather than a copy of it, so
    the check cannot drift from the scaffold it mirrors.
    """
    repo_root = Path(__file__).resolve().parents[1]
    ignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert cli.ignore_covers_local_config(ignore_text), (
        f"this repo's .gitignore does not cover {LOCAL_CONFIG_FILE}; "
        "an untracked local override would block every landing"
    )


def test_install_hints_missing_config_sections_without_editing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An older basicly.toml gets its missing sections named, never edited."""
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
    """A config carrying every shipped section produces no hint."""
    (tmp_path / CONFIG_FILE).write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    cli._report_missing_config_sections(tmp_path)
    assert capsys.readouterr().out == ""


def test_ci_workflows_ignore_tracker_only_pushes() -> None:
    """Tracker-only pushes must not trigger builds: .beads/** is paths-ignored.

    The harness loop necessarily makes tracker-only commits (basicly-flp), so
    both the authoring workflows and the consumer scaffold skip CI for them.
    """
    sources = [
        (REPO_ROOT / ".github" / "workflows" / "basicly.yml").read_text(encoding="utf-8"),
        (REPO_ROOT / ".github" / "workflows" / "quality-gates.yml").read_text(encoding="utf-8"),
        CONSUMER_CI_WORKFLOW,
    ]
    for text in sources:
        data = yaml.safe_load(text)
        triggers = data.get("on", data.get(True))  # bare `on:` parses as YAML boolean
        for event in ("push", "pull_request"):
            assert triggers[event]["paths-ignore"] == [".beads/**"], text[:200]


def test_purge_removes_only_pristine_ci_workflow(tmp_path: Path) -> None:
    """--purge deletes the workflow only while byte-identical to the scaffold."""
    paths = load_project_paths(tmp_path)
    workflow_path = tmp_path / ".github" / "workflows" / "basicly-gates.yml"

    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(CONSUMER_CI_WORKFLOW, encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert not workflow_path.exists()

    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(CONSUMER_CI_WORKFLOW + "# edited\n", encoding="utf-8")
    cli._purge_user_content(tmp_path, paths)
    assert workflow_path.exists()  # user-modified file survives purge


def _record_in_state(consumer: Path, rel_path: str) -> None:
    """Rewrite install.json so the on-disk core file at rel_path reads as installed."""
    state_path = consumer / ".basicly" / "state" / "install.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256((consumer / ".basicly" / "core" / rel_path).read_bytes()).hexdigest()
    payload["core"][rel_path] = f"sha256:{digest}"
    state_path.write_text(json.dumps(payload), encoding="utf-8")


def test_cli_install_upgrade_overwrites_upstream_changed_core_file(tmp_path: Path) -> None:
    """A core file whose on-disk state matches the snapshot is synced to the bundle."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    # Simulate an older installed version: rewrite a core file AND record that
    # content as installed, so the bundled catalog now differs from both.
    target = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    bundled_content = target.read_text(encoding="utf-8")
    target.write_text("# older shipped version\n", encoding="utf-8")
    _record_in_state(consumer, "hooks/pre-commit.py")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "1 updated" in result.stdout
    assert target.read_text(encoding="utf-8") == bundled_content


def test_cli_install_upgrade_deletes_upstream_removed_core_file(tmp_path: Path) -> None:
    """A snapshot-tracked core file the bundle no longer ships is deleted."""
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
    """A hand-edited core file is warned about and kept; --force overwrites it."""
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
    """A file of unknown origin in the managed core is never deleted."""
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
    """An upgrade sync never touches the overlay or basicly.toml."""
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

    # Simulate an upstream change so the sync actually rewrites a core file.
    target = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    target.write_text("# older shipped version\n", encoding="utf-8")
    _record_in_state(consumer, "hooks/pre-commit.py")

    result = run_basicly_consumer(consumer, "install")
    assert result.returncode == 0, result.stderr
    assert "1 updated" in result.stdout
    assert overlay_fragment.read_text(encoding="utf-8").startswith("schema_version: 1")
    assert config.read_text(encoding="utf-8") == config_content


def test_cli_install_writes_provenance_state(tmp_path: Path) -> None:
    """Install snapshots the materialized core into .basicly/state/install.json."""
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
    """A second install re-snapshots the state file."""
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
    """The authoring repo (core == bundled source) records no provenance."""
    result = run_basicly(work_repo, "install")
    assert result.returncode == 0, result.stderr
    assert "its own authoring source" in result.stdout
    assert not (work_repo / ".basicly" / "state").exists()


def test_cli_check_reports_core_drift_note(tmp_path: Path) -> None:
    """A hand-edited managed core file surfaces as an advisory note, exit 0."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    # Edit a managed file that does not feed the generated outputs, so the
    # byte-for-byte staleness contract stays green while provenance drifts.
    hook = consumer / ".basicly" / "core" / "hooks" / "pre-commit.py"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# hand edit\n", encoding="utf-8")

    result = run_basicly_consumer(consumer, "check")
    assert result.returncode == 0, result.stderr
    assert "differs from the installed snapshot" in result.stderr
    assert "hooks/pre-commit.py: modified" in result.stderr


def test_cli_check_reports_version_mismatch_note(tmp_path: Path) -> None:
    """An install recorded by another basicly version surfaces as a note, exit 0."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    state_path = consumer / ".basicly" / "state" / "install.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["basicly_version"] = "0.0.0"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_basicly_consumer(consumer, "check")
    assert result.returncode == 0, result.stderr
    assert "installed by basicly 0.0.0" in result.stderr


def test_cli_uninstall_removes_everything_managed(tmp_path: Path) -> None:
    """After install then uninstall, no basicly-managed file remains."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr

    assert not (consumer / ".basicly").exists()
    assert not (consumer / "AGENTS.md").exists()
    assert not (consumer / ".claude" / "CLAUDE.md").exists()
    assert not (consumer / ".github" / "copilot-instructions.md").exists()
    for root in (".claude", ".github", ".agents"):
        base = consumer / root
        assert not (list(base.rglob("SKILL.md")) if base.exists() else [])
    assert not (consumer / ".pre-commit-config.yaml").exists()

    # User content survives.
    assert (consumer / "basicly.toml").is_file()
    assert (consumer / ".basicly-local" / "fragments" / "user").is_dir()


def test_cli_uninstall_preserves_foreign_hooks(tmp_path: Path) -> None:
    """Only the managed pre-commit block is removed; foreign hooks stay."""
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
    """--purge also removes the overlay and basicly.toml."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")

    result = run_basicly_consumer(consumer, "uninstall", "--purge")
    assert result.returncode == 0, result.stderr
    assert not (consumer / ".basicly-local").exists()
    assert not (consumer / "basicly.toml").exists()


def test_cli_uninstall_keeps_hand_written_skill(tmp_path: Path) -> None:
    """A SKILL.md without the generated marker is user content and survives."""
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
    """A second uninstall reports nothing to remove and exits 0."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run_basicly_consumer(consumer, "install")
    run_basicly_consumer(consumer, "uninstall")

    result = run_basicly_consumer(consumer, "uninstall")
    assert result.returncode == 0, result.stderr
    assert "Nothing to remove" in result.stdout


def test_cli_uninstall_refuses_in_authoring_repo(work_repo: Path) -> None:
    """The dogfood repo's catalog source must never be deletable by uninstall."""
    result = run_basicly(work_repo, "uninstall")
    assert result.returncode == 1
    assert "authoring source" in result.stderr
    assert (work_repo / ".basicly" / "core").is_dir()


def test_cli_build_idempotent(work_repo: Path) -> None:
    """Two build runs with no source changes should produce no diff."""
    result1 = run_basicly(work_repo, "build")
    assert result1.returncode == 0
    result2 = run_basicly(work_repo, "build")
    assert result2.returncode == 0
    assert "No files changed" in result2.stdout


def test_cli_check_passes_after_build(work_repo: Path) -> None:
    """Check should pass immediately after a build."""
    run_basicly(work_repo, "build")
    result = run_basicly(work_repo, "check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_cli_check_fails_after_manual_edit(work_repo: Path) -> None:
    """Check should fail after a generated file is edited manually."""
    run_basicly(work_repo, "build")
    agents = work_repo / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = run_basicly(work_repo, "check")
    assert result.returncode == 1
    assert "Stale generated files detected" in result.stderr


def test_cli_build_target_only(work_repo: Path) -> None:
    """Build --target should only touch that target's outputs but preserve the manifest."""
    run_basicly(work_repo, "build")
    result = run_basicly(work_repo, "build", "--target", "claude")
    assert result.returncode == 0
    assert "copilot-instructions.md" not in result.stdout
    # Manifest must still list outputs from other targets so check passes.
    result_check = run_basicly(work_repo, "check")
    assert result_check.returncode == 0


def test_cli_build_sweeps_stale_manifest_outputs(work_repo: Path) -> None:
    """A full build deletes manifest-tracked files no target plans anymore.

    Regression for the retired .github/instructions twins: a consumer
    re-running install must converge on the single-source layout instead of
    keeping stale projections around.
    """
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
    assert not stale_file.parent.exists()  # emptied directory is cleaned up too
    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stale_rel not in manifest_after["outputs"]


def test_cli_build_target_keeps_other_targets_files(work_repo: Path) -> None:
    """A partial --target build must not sweep other targets' manifest entries."""
    run_basicly(work_repo, "build")
    copilot_baseline = work_repo / ".github" / "copilot-instructions.md"
    assert copilot_baseline.is_file()

    result = run_basicly(work_repo, "build", "--target", "claude")
    assert result.returncode == 0, result.stderr
    assert "Removed" not in result.stdout
    assert copilot_baseline.is_file()


def test_cli_unknown_target(work_repo: Path) -> None:
    """Build --target with an unknown target should fail cleanly."""
    result = run_basicly(work_repo, "build", "--target", "unknown")
    assert result.returncode == 1
    assert "Unknown target" in result.stderr


def _add_duplicate_fragments(work_repo: Path) -> None:
    """Add two core fragments with identical bodies to trip catalog-verify."""
    frag_dir = work_repo / ".basicly/core/fragments/project"
    frag_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "schema_version: 1\nid: {id}\ndescription: dup {id}\ncategory: project\n"
        "applies_to: [all]\nbody: |\n  This fragment body is intentionally duplicated.\n"
    )
    (frag_dir / "dup-one.fragment.yaml").write_text(body.format(id="dup-one"), encoding="utf-8")
    (frag_dir / "dup-two.fragment.yaml").write_text(body.format(id="dup-two"), encoding="utf-8")


def test_cli_catalog_verify_passes(work_repo: Path) -> None:
    """The real catalog passes content verification."""
    result = run_basicly(work_repo, "catalog", "verify")
    assert result.returncode == 0, result.stderr
    assert "catalog verify: OK" in result.stdout


def test_cli_catalog_verify_flags_duplicate_bodies(work_repo: Path) -> None:
    """catalog-verify fails when two fragments share a body."""
    _add_duplicate_fragments(work_repo)
    result = run_basicly(work_repo, "catalog", "verify")
    assert result.returncode == 1
    assert "identical bodies" in result.stderr


def test_cli_build_verify_blocks_and_writes_nothing(work_repo: Path) -> None:
    """Build --verify fails the gate before writing, leaving the manifest untouched."""
    manifest = work_repo / ".basicly/generated-manifest.json"
    manifest.unlink()
    _add_duplicate_fragments(work_repo)
    result = run_basicly(work_repo, "build", "--verify")
    assert result.returncode == 1
    assert "nothing written" in result.stderr
    assert not manifest.exists()


def test_cli_build_verify_passes_on_clean_catalog(work_repo: Path) -> None:
    """Build --verify builds normally when the catalog is clean."""
    result = run_basicly(work_repo, "build", "--verify")
    assert result.returncode == 0, result.stderr


def test_cli_review_dry_run_prints_prompt_without_agent(work_repo: Path) -> None:
    """Review --dry-run assembles the prompt from the rendered files, no agent invoked."""
    result = run_basicly(work_repo, "catalog", "review", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "advisory semantic review" in result.stdout
    assert "===== FILE: AGENTS.md =====" in result.stdout
    assert "under review" in result.stdout


def test_cli_review_handoff_is_advisory(work_repo: Path) -> None:
    """With the manual handoff runner, review reports the handoff and still exits 0."""
    result = run_basicly(work_repo, "catalog", "review", "--runner", "manual")
    assert result.returncode == 0, result.stderr
    assert "handoff" in result.stdout
    assert "Advisory only" in result.stdout


def test_cli_install_migrates_legacy_fragments(work_repo: Path) -> None:
    """Install migrates legacy .basicly/fragments into core and overlay roots."""
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
    """Install removes pre-migration SKILL.md/*.fragment.md sources from the managed core."""
    consumer = tmp_path / "consumer"
    skill_dir = consumer / ".basicly" / "core" / "skills" / "tool-x"
    frag_dir = consumer / ".basicly" / "core" / "fragments" / "project"
    skill_dir.mkdir(parents=True)
    frag_dir.mkdir(parents=True)

    # Pre-migration hand-copied sources (must be pruned).
    legacy_skill = skill_dir / "SKILL.md"
    legacy_skill.write_text(
        "---\nname: tool-x\ninvocation: model\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    legacy_frag = frag_dir / "y.fragment.md"
    legacy_frag.write_text("---\nid: y\n---\n\nbody\n", encoding="utf-8")

    # New YAML sources (must survive).
    kept_skill = skill_dir / "skill.yaml"
    kept_skill.write_text(
        "schema_version: 1\nname: tool-x\ninvocation: model\n"
        "description: d\ninstructions: |\n  body\n",
        encoding="utf-8",
    )

    # Overlay content — even a legacy-named .md here must be left untouched.
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
    """A pre-src-layout vendored engine tree in the core root is removed.

    Regression (basicly-u9o): hand installs vendored the engine into
    .basicly/basicly/; install migrated fragment/skill sources but left the
    stale engine copy behind (observed in the terminal repo).
    """
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
    """Two skills-build runs with no source changes should produce no diff."""
    result1 = run_basicly(work_repo, "skills-build")
    assert result1.returncode == 0
    result2 = run_basicly(work_repo, "skills-build")
    assert result2.returncode == 0
    assert "No skill files changed" in result2.stdout


def test_cli_skills_check_passes_after_build(work_repo: Path) -> None:
    """skills-check should pass immediately after a skills-build run."""
    run_basicly(work_repo, "skills-build")
    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_cli_skills_check_fails_after_manual_edit(work_repo: Path) -> None:
    """skills-check should fail after an edited projected skill file."""
    run_basicly(work_repo, "skills-build")

    projected_skill = work_repo / ".claude" / "skills" / "tool-ripgrep" / "SKILL.md"
    projected_skill.write_text(
        projected_skill.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 1
    assert "Stale skill projection detected" in result.stderr


def test_cli_agents_new_build_check_roundtrip(work_repo: Path) -> None:
    """Scaffolding via `catalog new agent` yields a source that builds, then goes stale."""
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
    """Uninstall removes marker-bearing agent files; hand-authored ones stay."""
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
    """Generated skills in the retired .github/skills root are pruned on install.

    Copilot reads .claude/skills and .agents/skills too, so the .github copy
    only tripled its discovery (basicly-sqn); user-authored skills there stay.
    """
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
    # New projections land only in the two live roots.
    assert list((consumer / ".claude" / "skills").rglob("SKILL.md"))
    assert list((consumer / ".agents" / "skills").rglob("SKILL.md"))
    assert not list((consumer / ".github" / "skills").rglob("SKILL.md"))[1:]


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_cli_build_sweep_never_follows_symlinks_or_git_paths(work_repo: Path) -> None:
    """A symlinked manifest entry unlinks the link only; .git entries are refused."""
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
    assert victim.exists()  # the symlink target survives
    assert not (work_repo / link_rel).exists()  # the link itself is swept
    assert (work_repo / git_rel).exists()  # .git/ is never sweepable
    assert "skipping unsafe manifest entry" in result.stderr


def test_cli_check_sees_crlf_drift(work_repo: Path) -> None:
    """A newline-only change to a generated file is drift, same as build sees it."""
    run_basicly(work_repo, "build")
    target = work_repo / "AGENTS.md"
    content = target.read_bytes()
    target.write_bytes(content.replace(b"\n", b"\r\n"))

    result = run_basicly(work_repo, "check")
    assert result.returncode == 1


def test_cli_survives_a_narrow_console_encoding(work_repo: Path) -> None:
    """Unicode output degrades to ? instead of crashing under a legacy codepage.

    Regression for the first windows-latest CI run: cp1252 stdout raised
    UnicodeEncodeError on the catalog's arrows and failed every command.
    """
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


def test_cli_usage_report_tables_counters_and_flags_unused_skills(work_repo: Path) -> None:
    """Usage report joins the counters against the catalog's skills."""
    usage_dir = work_repo / ".basicly" / "usage"
    # The fixture copies the live repo, which may carry real telemetry.
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "tool-usage.json").write_text(
        json.dumps({
            "rg": {"count": 7, "last_used": "2026-07-16"},
            "skill:conventional-commits": {"count": 2, "last_used": "2026-07-16"},
        }),
        encoding="utf-8",
    )
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    assert "rg" in result.stdout and "7" in result.stdout
    assert "conventional-commits" in result.stdout
    assert "Never-used catalog skills" in result.stdout


def test_cli_usage_report_notes_missing_data(work_repo: Path) -> None:
    """A repo without the hook's counter file gets a note, not an error."""
    shutil.rmtree(work_repo / ".basicly" / "usage", ignore_errors=True)
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    assert "No usage data" in result.stdout


def _run_records(work_repo: Path, records: dict) -> None:
    """Seed the whole dispatch history the report reads, and nothing else.

    Both halves are replaced: `dispatch_history` unions the local log with the
    committed tracker markers (D11), and the fixture copies this repo's real export,
    so leaving it in place would mix ~90 live dispatches into the counts.
    """
    usage_dir = work_repo / ".basicly" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "run-records.json").write_text(json.dumps(records), encoding="utf-8")
    beads = work_repo / ".beads"
    # The redirect is why blanking the export alone is not enough: `br` follows it to
    # the base checkout's tracker, so a fixture copied out of a harness worktree reads
    # the live repo's ~90 dispatches however empty its own export is.
    (beads / "redirect").unlink(missing_ok=True)
    (beads / "issues.jsonl").write_text("", encoding="utf-8")


def test_cli_usage_forecast_reports_the_ratio_per_paired_dispatch(work_repo: Path) -> None:
    """The forecast error report, over a dispatch that carries both halves (jr0l.34)."""
    _run_records(
        work_repo,
        {
            "b-1": [
                {
                    "agent": "claude",
                    "outcome": "executed",
                    "timestamp": "2026-07-26T09:00:00+00:00",
                    "forecast_tokens": 50_000,
                    "tokens": 200_000,
                    "task_class": "task",
                    "forecast_source": "dispatch",
                }
            ]
        },
    )
    result = run_basicly(work_repo, "usage", "forecast")
    assert result.returncode == 0, result.stderr
    assert "b-1" in result.stdout and "4.00x" in result.stdout
    assert "Median actual/forecast" in result.stdout
    # The ratio must never be presented as pure estimator error: the actual is total
    # spend and the forecast is a working set, so the turn multiplier is in there too.
    assert "turn multiplier" in result.stdout


def test_cli_usage_forecast_explains_an_empty_report(work_repo: Path) -> None:
    """An empty table alone would read as a healthy forecast; the counts say otherwise."""
    _run_records(
        work_repo,
        {
            "b-1": [
                {
                    "agent": "claude",
                    "outcome": "executed",
                    "timestamp": "2026-07-26T09:00:00+00:00",
                    "tokens": 200_000,
                }
            ]
        },
    )
    result = run_basicly(work_repo, "usage", "forecast")
    assert result.returncode == 0, result.stderr
    assert "no forecast error is computable yet" in result.stdout
    assert "1 actual with no forecast" in result.stdout


def test_cli_status_reports_authoring_repo(work_repo: Path) -> None:
    """In the authoring repo, status names the repo kind and skips install state."""
    result = run_basicly(work_repo, "status")
    assert result.returncode == 0, result.stderr
    assert "engine: basicly" in result.stdout
    assert "repo: authoring" in result.stdout
    assert "drift: generated files up to date" in result.stdout


def test_cli_permissions_build_and_check_are_idempotent(work_repo: Path) -> None:
    """permissions-build converges the deny-list; permissions-check then passes."""
    settings = json.loads((work_repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    # The authoring repo already carries its deny-list, so build is a no-op...
    build = run_basicly(work_repo, "permissions-build")
    assert build.returncode == 0, build.stderr
    assert "up to date" in build.stdout
    # ...and check confirms every managed pattern is present.
    check = run_basicly(work_repo, "permissions-check")
    assert check.returncode == 0, check.stderr

    # Drop a managed pattern: check must now fail, build must restore it.
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
    """A fresh consumer install inherits the catalog deny-list, not just the repo."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    assert run_basicly_consumer(consumer, "install").returncode == 0

    settings = json.loads((consumer / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    assert "Bash(rm -rf*)" in deny
    assert "Read(.env)" in deny
    # A consumer's own deny entry survives a re-projection.
    settings["permissions"]["deny"].append("Bash(sudo*)")
    (consumer / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    assert run_basicly_consumer(consumer, "permissions-build").returncode == 0
    after = json.loads((consumer / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Bash(sudo*)" in after["permissions"]["deny"]
    assert "Bash(rm -rf*)" in after["permissions"]["deny"]


def test_cli_status_json_authoring_schema(work_repo: Path) -> None:
    """The --json payload keeps its stable schema; authoring has no install state."""
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
    assert report["schema_version"] == 1  # additive "permissions" section is not breaking
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
    # The authoring repo dogfoods its own deny-list, so it is fully in sync.
    assert report["permissions"]["claude"]["managed_patterns"] > 0
    assert report["permissions"]["claude"]["mismatches"] == 0
    assert set(report["overlays"]) == {"fragments", "agents"}


def test_cli_status_fleet_rolls_up_the_workspace(work_repo: Path) -> None:
    """`status --fleet` aggregates the housed repos under the workspace root as JSON.

    The workspace is `work_repo`'s parent: the real authoring copy yields a proper
    snapshot; an empty `.basicly` sibling is captured, not crashed — exit 0 either way.
    """
    workspace = work_repo.parent
    (workspace / "other-repo" / ".basicly").mkdir(parents=True)
    result = run_basicly(work_repo, "status", "--fleet")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["workspace_root"] == str(workspace)
    by_name = {r["name"]: r for r in report["repos"]}
    assert {work_repo.name, "other-repo"} <= set(by_name)
    # The real repo produces a proper status snapshot...
    assert by_name[work_repo.name]["status"]["repo_kind"] == "authoring"
    # ...and every entry carries a run-record summary and a status payload.
    assert "runs" in by_name["other-repo"] and "status" in by_name["other-repo"]
    assert report["totals"]["repos"] >= 2


def test_cli_status_json_consumer_reports_install_and_drift(tmp_path: Path) -> None:
    """In a consumer repo, status reports the install provenance and any drift."""
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
    """Both output modes leave every file in the repo byte-identical."""
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


def test_cli_hooks_check_warns_when_uv_is_missing(
    work_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A committer machine without uv gets a diagnosis at check time, not commit time."""
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
    """With uv installed (the test environment), the diagnostic does not fire."""
    monkeypatch.chdir(work_repo)
    assert cli.main(["hooks-check"]) == 0
    assert "uv is not on PATH" not in capsys.readouterr().err


# --- A long run stays observable through a pipe (basicly-8veb) ----------------

# Printed by the child, which then blocks on stdin. Nothing sleeps: the child
# cannot reach its own exit until the parent writes to it, so a line the parent
# manages to read was provably observable *before* exit rather than just soon
# after. That makes this a state check rather than a race against a duration —
# the select below returns the moment the data lands.
_OBSERVABLE_CHILD = (
    "import sys; from basicly import cli; "
    "cli._line_buffer_stdout(); "
    "print('session:  demo'); "
    "sys.stdin.readline()"
)


def _child_env() -> dict[str, str]:
    """Environment pinning the child to the same ``basicly`` this test imported.

    A bare ``sys.executable -c "import basicly"`` resolves against whatever
    interpreter runs pytest, which is not necessarily the source under test: the
    harness lands a worktree by running verify with the *base* checkout's
    interpreter, so these tests imported the installed package and failed on a
    function that existed only on the branch. Pointing PYTHONPATH at the package
    actually imported here makes the child hermetic wherever pytest is invoked
    from.
    """
    package_parent = Path(cli.__file__).resolve().parent.parent
    return {**os.environ, "PYTHONPATH": str(package_parent)}


def test_a_printed_line_is_observable_before_exit_when_stdout_is_a_pipe() -> None:
    """The defect: a piped supervised run showed nothing until the process exited.

    The control for this is
    :func:`test_line_buffer_stdout_sets_line_buffering_on_the_real_stream`, which
    proves the stream is block-buffered without the call. Asserting the negative
    *here* would mean waiting out a timeout to prove an absence, which costs
    seconds of suite time to learn nothing the control does not already give.

    The read is bounded by joining a reader thread rather than by ``selectors``:
    ``DefaultSelector`` is ``SelectSelector`` on Windows and ``select()`` there
    accepts only sockets, so registering a pipe raised ``WinError 10038`` on that
    leg alone (basicly-jr0l.23). The thread is also the stronger assertion — it
    proves the *line* arrived while the child was still blocked, not merely that
    the descriptor had become readable.
    """
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
    """The mechanism, and the control: piped stdout is block-buffered until the call."""
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
    assert proc.stderr == "False True"  # piped: block-buffered before, line after


def test_line_buffer_stdout_tolerates_a_stream_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A harness that replaced stdout collects output itself; skipping is safe."""
    monkeypatch.setattr(cli.sys, "stdout", object())
    cli._line_buffer_stdout()  # must not raise


def test_main_line_buffers_stdout_before_dispatching(monkeypatch: pytest.MonkeyPatch) -> None:
    """The setup runs in main(), so every subcommand benefits, not just supervise."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "_line_buffer_stdout", lambda: calls.append("buffered"))
    monkeypatch.setattr(cli, "cmd_status", lambda _a: calls.append("dispatched") or 0)
    cli.main(["status"])
    assert calls == ["buffered", "dispatched"]
