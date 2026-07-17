"""Integration tests for the CLI."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from basicly import cli
from basicly.agents import GENERATED_MARKER as AGENT_GENERATED_MARKER
from basicly.config import CONSUMER_CI_WORKFLOW, VSCODE_TASKS_JSON, load_project_paths
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
    env = {"PYTHONPATH": str(cwd / "src")}
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
    env = {"PYTHONPATH": str(REPO_ROOT / "src")}
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

    listing = run_basicly(work_repo, "skills-list")
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
    mine.write_text("---\nname: my-skill\ndescription: mine\n---\n\nMine.\n", encoding="utf-8")

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
    result = run_basicly(work_repo, "catalog-verify")
    assert result.returncode == 0, result.stderr
    assert "catalog-verify: OK" in result.stdout


def test_cli_catalog_verify_flags_duplicate_bodies(work_repo: Path) -> None:
    """catalog-verify fails when two fragments share a body."""
    _add_duplicate_fragments(work_repo)
    result = run_basicly(work_repo, "catalog-verify")
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
    result = run_basicly(work_repo, "review", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "advisory semantic review" in result.stdout
    assert "===== FILE: AGENTS.md =====" in result.stdout
    assert "under review" in result.stdout


def test_cli_review_handoff_is_advisory(work_repo: Path) -> None:
    """With the manual handoff runner, review reports the handoff and still exits 0."""
    result = run_basicly(work_repo, "review", "--runner", "manual")
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
    legacy_skill.write_text("---\nname: tool-x\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    legacy_frag = frag_dir / "y.fragment.md"
    legacy_frag.write_text("---\nid: y\n---\n\nbody\n", encoding="utf-8")

    # New YAML sources (must survive).
    kept_skill = skill_dir / "skill.yaml"
    kept_skill.write_text(
        "schema_version: 1\nname: tool-x\ndescription: d\ninstructions: |\n  body\n",
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
    """agents-new scaffolds a source that builds, checks clean, then goes stale."""
    result = run_basicly(work_repo, "agents-new", "triage-bot", "--description", "Triages issues.")
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
    env = {"PYTHONPATH": str(work_repo / "src"), "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "-m", "basicly.cli", "skills-list"],
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


def test_cli_status_reports_authoring_repo(work_repo: Path) -> None:
    """In the authoring repo, status names the repo kind and skips install state."""
    result = run_basicly(work_repo, "status")
    assert result.returncode == 0, result.stderr
    assert "engine: basicly" in result.stdout
    assert "repo: authoring" in result.stdout
    assert "drift: generated files up to date" in result.stdout


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
    assert set(report["overlays"]) == {"fragments", "agents"}


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
