"""Integration tests for the CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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

    # A single command projects everything — no separate build/skills/hooks runs.
    assert (consumer / "AGENTS.md").is_file()
    assert (consumer / ".claude" / "CLAUDE.md").is_file()
    assert (consumer / ".github" / "copilot-instructions.md").is_file()
    assert list((consumer / ".claude" / "skills").rglob("SKILL.md"))
    assert (consumer / ".pre-commit-config.yaml").is_file()


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
    assert "0 file(s) written" in result.stdout
    assert "No files changed" in result.stdout
    assert "No skill files changed" in result.stdout
    assert config.read_text(encoding="utf-8") == marker


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
