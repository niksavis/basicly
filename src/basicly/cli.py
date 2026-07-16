"""CLI for basicly."""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import (
    __version__,
    catalog_lint,
    catalog_verify,
    claude_settings,
    decompose,
    loop,
    loop_state,
    merge,
    policy,
    projection,
    review,
    runner,
    state,
    verify,
    worktree,
)
from .catalog import bundled_catalog_root, iter_catalog_files
from .config import (
    CHECKPOINTS,
    CONFIG_FILE,
    CONSUMER_CI_WORKFLOW,
    DEFAULT_CONFIG_TOML,
    OVERLAY_FRAGMENT_STUBS,
    VERIFY_MODES,
    VSCODE_TASKS_JSON,
    ProjectPaths,
    load_policy_config,
    load_project_paths,
    load_runner_config,
    load_verify_config,
    load_worktree_config,
)
from .hooks import (
    check_hooks,
    hook_stages,
    install_hooks,
    load_hook_specs,
    missing_hook_installations,
    remove_managed_hooks,
    sync_hooks,
)
from .loader import load_fragments_from_roots, load_targets
from .planner import plan_outputs
from .renderers.common import sha256_of_text
from .schema import CATEGORIES, PlannedOutput, ValidationError
from .skills import (
    DEFAULT_SKILL_ROOTS,
    GENERATED_MARKER,
    RETIRED_SKILL_ROOTS,
    SKILL_FILE_NAME,
    SKILLS_SOURCE_DIR,
    check_synced_skills,
    discover_skills,
    resolve_skill_roots,
    sync_skills,
)


def _repo_root() -> Path:
    return Path.cwd()


def _format_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _report_sync(
    result: projection.SyncResult,
    repo_root: Path,
    *,
    noun: str,
    label: str,
    extra_note: str | None = None,
) -> None:
    """Print the shared build-side projection report (written / unchanged / summary)."""
    for path in result.written:
        print(f"Wrote {_format_path(path, repo_root)}")
    if result.written and extra_note:
        print(extra_note)
    if not result.written:
        print(f"No {noun} changed.")
    print(
        f"{label} projection complete: {len(result.written)} written, "
        f"{len(result.unchanged)} unchanged"
    )


def _report_mismatches(
    mismatches: list[tuple[Path, str]],
    repo_root: Path,
    *,
    stale_message: str,
) -> bool:
    """Print the shared check-side stale report; return True when stale (caller exits 1)."""
    if not mismatches:
        return False
    print(stale_message, file=sys.stderr)
    for path, reason in mismatches:
        print(f"  {_format_path(path, repo_root)}: {reason}", file=sys.stderr)
    return True


def _fragment_roots(paths: ProjectPaths) -> list[tuple[Path, str | None]]:
    roots: list[tuple[Path, str | None]] = [(paths.core_fragments_dir, "core")]

    if paths.legacy_fragments_dir not in {p for p, _ in roots}:
        roots.append((paths.legacy_fragments_dir, None))

    for overlay_root in paths.overlay_fragments_dirs:
        roots.append((overlay_root, "user"))

    seen: set[Path] = set()
    deduped: list[tuple[Path, str | None]] = []
    for root, source_hint in roots:
        if root in seen:
            continue
        seen.add(root)
        deduped.append((root, source_hint))

    return deduped


def _load_context(repo_root: Path, paths: ProjectPaths) -> tuple[list[Any], list[Any]]:
    targets = load_targets(repo_root / paths.targets_dir)
    target_names = {t.name for t in targets}
    roots = [(repo_root / root, source_hint) for root, source_hint in _fragment_roots(paths)]
    fragments = load_fragments_from_roots(roots, target_names)
    return fragments, targets


def _render_planned(repo_root: Path, paths: ProjectPaths, planned: PlannedOutput) -> str:
    module_name = f"basicly.renderers.{planned.target_name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"No renderer module for target '{planned.target_name}'") from exc
    return module.render(planned, repo_root / paths.templates_dir, __version__)


def _build_manifest(
    outputs: dict[Path, str],
    planned: list[PlannedOutput],
    existing_manifest: dict[str, Any] | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    planned_by_path = {p.output_path: p for p in planned}
    existing_outputs: dict[str, Any] = {}
    if existing_manifest and isinstance(existing_manifest.get("outputs"), dict):
        existing_outputs = dict(existing_manifest["outputs"])

    new_outputs = {
        path.relative_to(_repo_root()).as_posix(): {
            "hash": sha256_of_text(content),
            "source_fragments": [f.id for f in planned_by_path[path].fragments],
        }
        for path, content in outputs.items()
    }

    merged_outputs = {**existing_outputs, **new_outputs} if partial else new_outputs

    return {
        "version": "1",
        "generated_at": datetime.now(UTC).isoformat(),
        "outputs": merged_outputs,
    }


def cmd_list(_args: argparse.Namespace) -> int:
    """List active fragments in a table."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, _targets = _load_context(repo_root, paths)
    active = [f for f in fragments if f.status == "active"]

    headers = f"{'id':<30} {'category':<15} {'priority':<10} "
    headers += f"{'applies_to':<20} {'scope':<20} {'status':<10}"
    print(headers)
    print("-" * 105)
    for f in sorted(active, key=lambda x: (x.category, -x.priority_value, x.id)):
        applies = ", ".join(f.applies_to)
        print(
            f"{f.id:<30} {f.category:<15} {f.priority:<10} "
            f"{applies:<20} {f.scope_summary:<20} {f.status:<10}"
        )
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Build generated files for all or one target."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, targets = _load_context(repo_root, paths)

    if getattr(args, "verify", False) and _report_gate_failures(
        "build: verification failed, nothing written", _deterministic_gate(repo_root, fragments)
    ):
        return 1

    if args.target:
        target_names = {t.name for t in targets}
        if args.target not in target_names:
            print(
                f"Unknown target '{args.target}'. Known targets: {', '.join(sorted(target_names))}",
                file=sys.stderr,
            )
            return 1
        selected_targets = [t for t in targets if t.name == args.target]
        if not selected_targets or not selected_targets[0].enabled:
            print(f"Target '{args.target}' is disabled or unknown.", file=sys.stderr)
            return 1
        targets = selected_targets

    planned = plan_outputs(fragments, targets, repo_root)
    rendered: dict[Path, str] = {}
    changed_count = 0

    for item in planned:
        content = _render_planned(repo_root, paths, item)
        rendered[item.output_path] = content
        changed = projection.write_if_changed(item.output_path, content.encode("utf-8"))
        if changed:
            changed_count += 1
            print(f"Wrote {item.output_path.relative_to(repo_root)}")
        for target in targets:
            if (
                target.name == item.target_name
                and target.max_size_warning
                and len(content) > target.max_size_warning
            ):
                print(
                    f"Warning: {item.output_path.relative_to(repo_root)} "
                    f"exceeds {target.max_size_warning} characters "
                    f"({len(content)})",
                    file=sys.stderr,
                )

    manifest_path = repo_root / paths.manifest_path
    existing_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = {}

    manifest = _build_manifest(rendered, planned, existing_manifest, bool(args.target))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {_format_path(manifest_path, repo_root)}")
    if changed_count == 0:
        print("No files changed.")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Check generated files and manifest are up to date."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, targets = _load_context(repo_root, paths)
    planned = plan_outputs(fragments, targets, repo_root)

    mismatches: list[tuple[Path, str, str]] = []
    expected_manifest_outputs: dict[str, dict[str, Any]] = {}

    for item in planned:
        content = _render_planned(repo_root, paths, item)
        rel_path = item.output_path.relative_to(repo_root).as_posix()
        expected_hash = sha256_of_text(content)
        expected_manifest_outputs[rel_path] = {
            "hash": expected_hash,
            "source_fragments": [f.id for f in item.fragments],
        }

        if not item.output_path.exists():
            mismatches.append((item.output_path, expected_hash, "missing"))
            continue

        actual = item.output_path.read_text(encoding="utf-8")
        actual_hash = sha256_of_text(actual)
        if actual_hash != expected_hash:
            mismatches.append((item.output_path, expected_hash, actual_hash))

    manifest_path = repo_root / paths.manifest_path
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"Invalid manifest: {exc}", file=sys.stderr)
            return 1
    else:
        existing_manifest = {}

    if existing_manifest.get("outputs") != expected_manifest_outputs:
        mismatches.append((manifest_path, "manifest mismatch", "manifest mismatch"))

    _report_provenance_notes(repo_root, paths)

    if mismatches:
        print("Stale generated files detected. Run `basicly build` to fix.", file=sys.stderr)
        for path, expected, actual in mismatches:
            print(
                f"  {path.relative_to(repo_root)}: expected {expected}, found {actual}",
                file=sys.stderr,
            )
        return 1

    print("All generated files and manifest are up to date.")
    return 0


def _report_provenance_notes(repo_root: Path, paths: ProjectPaths) -> None:
    """Advisory (non-fatal) install-provenance notes for `basicly check` (§9).

    Absent state (authoring repo, or an install predating provenance) reports
    nothing; a corrupt state file and core drift are surfaced but never change
    the exit code — the hard staleness contract stays byte-for-byte generated
    files only.
    """
    state_path = repo_root / paths.state_path
    try:
        install_state = state.read_install_state(state_path)
    except ValidationError as exc:
        print(f"Note: {exc}; re-run `basicly install` to rewrite it.", file=sys.stderr)
        return
    if install_state is None:
        return

    if install_state.basicly_version != __version__:
        print(
            f"Note: core catalog was installed by basicly {install_state.basicly_version}; "
            f"this is basicly {__version__}. Run `basicly install` to upgrade.",
            file=sys.stderr,
        )

    drift = state.core_drift(install_state, repo_root / paths.core_root)
    if drift:
        print(
            "Note: managed core differs from the installed snapshot "
            "(hand-edits belong in the overlay, not the managed core):",
            file=sys.stderr,
        )
        for rel_path, reason in drift:
            print(f"  {rel_path}: {reason}", file=sys.stderr)


def _merge_directories(src: Path, dst: Path) -> tuple[int, int]:
    """Merge src into dst without overwriting existing files."""
    moved = 0
    skipped = 0
    dst.mkdir(parents=True, exist_ok=True)

    for child in sorted(src.iterdir(), key=lambda p: p.name):
        target = dst / child.name
        if child.is_dir():
            if target.exists() and target.is_dir():
                nested_moved, nested_skipped = _merge_directories(child, target)
                moved += nested_moved
                skipped += nested_skipped
                if not any(child.iterdir()):
                    child.rmdir()
            elif target.exists():
                skipped += 1
            else:
                shutil.move(str(child), str(target))
                moved += 1
            continue

        if target.exists():
            skipped += 1
            continue

        shutil.move(str(child), str(target))
        moved += 1

    return moved, skipped


def _prune_legacy_catalog_sources(repo_root: Path, paths: ProjectPaths) -> list[Path]:
    """Remove discoverable-name legacy sources from the managed core.

    Skills and fragments are now authored as YAML (``skill.yaml`` /
    ``*.fragment.yaml``); a leftover ``SKILL.md`` or ``*.fragment.md`` in the
    managed core is a pre-migration source that would let an agent double-load a
    skill (architecture §4.2). This prunes exactly those, scoped to the managed
    core so a consumer's overlay content is never touched.
    """
    core_root = repo_root / paths.core_root
    skills_dir = core_root / "skills"
    fragments_dir = repo_root / paths.core_fragments_dir
    removed: list[Path] = []
    for legacy in sorted(skills_dir.rglob("SKILL.md")):
        legacy.unlink()
        removed.append(legacy)
    for legacy in sorted(fragments_dir.rglob("*.fragment.md")):
        legacy.unlink()
        removed.append(legacy)
    return removed


def _migrate_legacy_layout(repo_root: Path, paths: ProjectPaths) -> None:
    """Migrate a pre-core legacy fragment layout and prune legacy-named sources."""
    pruned = _prune_legacy_catalog_sources(repo_root, paths)
    for legacy in pruned:
        print(f"Pruned legacy source {_format_path(legacy, repo_root)}")

    # Pre-src-layout installs vendored the engine itself next to the core
    # (.basicly/basicly); the packaged engine replaced it, so a leftover copy
    # is stale dead weight.
    legacy_engine = repo_root / paths.core_root.parent / "basicly"
    if legacy_engine.is_dir() and (legacy_engine / "cli.py").exists():
        shutil.rmtree(legacy_engine)
        print(f"Removed legacy vendored engine {_format_path(legacy_engine, repo_root)}/")

    # Skills are no longer projected into retired roots (e.g. .github/skills —
    # Copilot reads .claude/.agents too, so a third copy only tripled its
    # discovery); prune previously generated copies there.
    _remove_generated_skills(repo_root, RETIRED_SKILL_ROOTS)

    legacy_dir = repo_root / paths.legacy_fragments_dir
    if not legacy_dir.exists():
        return

    core_dir = repo_root / paths.core_fragments_dir
    core_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    skipped = 0

    legacy_user = legacy_dir / "user"
    if legacy_user.exists() and paths.overlay_fragments_dirs:
        overlay_user_dir = repo_root / paths.overlay_fragments_dirs[0] / "user"
        user_moved, user_skipped = _merge_directories(legacy_user, overlay_user_dir)
        moved += user_moved
        skipped += user_skipped
        if legacy_user.exists() and not any(legacy_user.iterdir()):
            legacy_user.rmdir()

    core_moved, core_skipped = _merge_directories(legacy_dir, core_dir)
    moved += core_moved
    skipped += core_skipped

    if legacy_dir.exists() and not any(legacy_dir.iterdir()):
        legacy_dir.rmdir()

    print(f"Migrated legacy fragment layout: {moved} item(s) moved, {skipped} left unchanged")


@dataclass
class _CatalogSyncReport:
    """What one core sync did, for the install report and its tests."""

    new: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped_edits: list[str] = field(default_factory=list)
    kept_unknown: list[str] = field(default_factory=list)
    unchanged: int = 0


def _sync_catalog(
    src: Path,
    dst: Path,
    previous: state.InstallState | None,
    force: bool,
) -> _CatalogSyncReport:
    """Sync the managed core at ``dst`` to the bundled catalog at ``src`` (§9).

    Core is managed, so upstream wins — but only where the provenance snapshot
    proves the on-disk file is what install wrote. A file that differs from the
    snapshot (or predates it) is a hand-edit: warn and keep unless ``force``.
    Files on disk that the bundle no longer ships are deleted only when they
    match the snapshot; anything of unknown origin is kept with a warning.
    """
    report = _CatalogSyncReport()
    recorded = previous.core_hashes if previous else {}
    bundled = {path.relative_to(src).as_posix(): path for path in iter_catalog_files(src)}

    for rel_path, src_path in bundled.items():
        target = dst / rel_path
        src_bytes = src_path.read_bytes()
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, target)
            report.new.append(rel_path)
            continue
        if target.read_bytes() == src_bytes:
            report.unchanged += 1
            continue
        if force or state.sha256_of_file(target) == recorded.get(rel_path):
            shutil.copy2(src_path, target)
            report.updated.append(rel_path)
        else:
            report.skipped_edits.append(rel_path)

    if dst.exists():
        for target in iter_catalog_files(dst):
            rel_path = target.relative_to(dst).as_posix()
            if rel_path in bundled:
                continue
            if state.sha256_of_file(target) == recorded.get(rel_path):
                target.unlink()
                report.deleted.append(rel_path)
                parent = target.parent
                while parent != dst and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            else:
                report.kept_unknown.append(rel_path)

    return report


def _report_catalog_sync(report: _CatalogSyncReport, core_dst: Path, repo_root: Path) -> None:
    """Print the core-sync summary and its hand-edit/unknown-file warnings."""
    print(
        f"Synced core catalog at {_format_path(core_dst, repo_root)}: "
        f"{len(report.new)} new, {len(report.updated)} updated, "
        f"{len(report.deleted)} removed, {report.unchanged} unchanged"
    )
    if report.skipped_edits:
        print(
            "Warning: hand-edited managed core files were left as-is "
            "(re-run with --force to overwrite; hand-edits belong in the overlay):",
            file=sys.stderr,
        )
        for rel_path in report.skipped_edits:
            print(f"  {rel_path}", file=sys.stderr)
    if report.kept_unknown:
        print(
            "Warning: files of unknown origin in the managed core were kept "
            "(move yours to the overlay; core is managed by basicly install):",
            file=sys.stderr,
        )
        for rel_path in report.kept_unknown:
            print(f"  {rel_path}", file=sys.stderr)


def _beads_prefix(repo_root: Path) -> str:
    """Derive a beads issue-id prefix from the repo directory name.

    The commit-msg hook only accepts single-hyphen ``<prefix>-<code>`` ids with
    a lowercase alphanumeric prefix starting with a letter, so the name is
    sanitized to that shape.
    """
    prefix = re.sub(r"[^a-z0-9]", "", repo_root.name.lower())
    if not prefix or not prefix[0].isalpha():
        prefix = f"repo{prefix}"
    return prefix


def _setup_beads(repo_root: Path) -> None:
    """Initialize a beads (br) workspace when none exists (idempotent).

    Degrades gracefully: a repo without ``br`` on PATH gets actionable guidance
    instead of a failed install — the tracker is required for the harness loop
    but not for the projections themselves.
    """
    beads_dir = repo_root / ".beads"
    if (beads_dir / "config.yaml").exists() or (beads_dir / "issues.jsonl").exists():
        print("Beads workspace exists; left unchanged.")
        return

    br = shutil.which("br")
    if not br:
        print(
            "br not on PATH; skipped beads init. Enable the harness tracker later "
            "with `br init --prefix <prefix>` (see the tool-br skill)."
        )
        return

    prefix = _beads_prefix(repo_root)
    result = subprocess.run(  # nosec B603
        [br, "init", "--prefix", prefix, "--quiet"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "unknown error"
        print(
            f"br init failed ({detail}); continuing without a beads workspace.",
            file=sys.stderr,
        )
        return
    print(f"Initialized beads workspace (issue prefix: {prefix}).")


def _scaffold_overlay_stubs(repo_root: Path, paths: ProjectPaths) -> None:
    """Seed draft project-overview/commands fragments in the user overlay when absent.

    The two descriptive blocks every agent instruction file needs are per-repo
    content, so install scaffolds fill-me drafts the consumer completes and
    flips to ``status: active`` (drafts never project, so the placeholders stay
    out of generated files). Same contract as the other scaffolds: written
    once, then the file is the user's — install never overwrites it.
    """
    overlay_user = repo_root / paths.overlay_fragments_dirs[0] / "user"
    for rel_path, content in OVERLAY_FRAGMENT_STUBS.items():
        stub_path = overlay_user / rel_path
        if stub_path.exists():
            print(f"{_format_path(stub_path, repo_root)} already exists; left unchanged")
            continue
        stub_path.parent.mkdir(parents=True, exist_ok=True)
        stub_path.write_text(content, encoding="utf-8")
        print(
            f"Wrote {_format_path(stub_path, repo_root)} (draft: fill it in and set status: active)"
        )


def _scaffold_vscode_tasks(repo_root: Path) -> None:
    """Write .vscode/tasks.json with the harness tasks when absent.

    Same contract as the basicly.toml scaffold: written once, then the file is
    the user's — install never overwrites it.
    """
    tasks_path = repo_root / ".vscode" / "tasks.json"
    if tasks_path.exists():
        print(".vscode/tasks.json already exists; left unchanged")
        return
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(VSCODE_TASKS_JSON, encoding="utf-8")
    print("Wrote .vscode/tasks.json (basicly build/skills-build/hooks-build/update/uninstall)")


def _scaffold_ci_workflow(repo_root: Path) -> None:
    """Write the consumer CI gates workflow when absent.

    Same contract as the other scaffolds: written once, then the file is the
    user's — install never overwrites it.
    """
    workflow_path = repo_root / ".github" / "workflows" / "basicly-gates.yml"
    if workflow_path.exists():
        print(".github/workflows/basicly-gates.yml already exists; left unchanged")
        return
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(CONSUMER_CI_WORKFLOW, encoding="utf-8")
    print("Wrote .github/workflows/basicly-gates.yml (commit messages, drift, verify)")


def cmd_install(args: argparse.Namespace) -> int:
    """Converge a consumer repo: sync core, scaffold, and project everything.

    One idempotent command covers first install and every upgrade (architecture
    §9): sync the managed core to the bundled catalog (provenance-guarded, so
    hand-edits are never silently clobbered), migrate/prune legacy layouts,
    scaffold the overlay + config (never overwriting user content), then
    project fragments, skills, and hooks. Re-running from a newer pinned ref is
    the upgrade path.
    """
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)

    core_src = bundled_catalog_root()
    core_dst = repo_root / paths.core_root
    state_path = repo_root / paths.state_path
    authoring_source = core_src.resolve() == core_dst.resolve()
    if authoring_source:
        print("Core catalog is its own authoring source here; left in place.")
    else:
        try:
            previous_state = state.read_install_state(state_path)
        except ValidationError as exc:
            print(
                f"Note: {exc}; treating existing core files as unverified "
                "(diffs are kept unless --force).",
                file=sys.stderr,
            )
            previous_state = None
        report = _sync_catalog(
            core_src, core_dst, previous_state, force=bool(getattr(args, "force", False))
        )
        _report_catalog_sync(report, core_dst, repo_root)

    _migrate_legacy_layout(repo_root, paths)

    if not authoring_source:
        # Snapshot only what this install vouches for: files whose on-disk
        # content equals the bundle (post-migration/prune). Kept hand-edits and
        # unknown-origin files stay out of the snapshot, so the next sync still
        # treats them as user content instead of upstream state (§9).
        bundled_hashes = state.snapshot_core(core_src)
        disk_hashes = state.snapshot_core(core_dst)
        vouched = {
            rel_path: digest
            for rel_path, digest in disk_hashes.items()
            if bundled_hashes.get(rel_path) == digest
        }
        state.write_install_state(state_path, __version__, vouched)
        print(f"Recorded install state in {_format_path(state_path, repo_root)}")

    for overlay in paths.overlay_fragments_dirs:
        user_dir = repo_root / overlay / "user"
        existed = user_dir.exists()
        user_dir.mkdir(parents=True, exist_ok=True)
        verb = "exists" if existed else "created"
        print(f"Overlay {verb}: {_format_path(user_dir, repo_root)}")

    _scaffold_overlay_stubs(repo_root, paths)

    config_path = repo_root / CONFIG_FILE
    if config_path.exists():
        print(f"{CONFIG_FILE} already exists; left unchanged")
    else:
        config_path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        print(f"Wrote {CONFIG_FILE}")

    _setup_beads(repo_root)
    _scaffold_vscode_tasks(repo_root)
    _scaffold_ci_workflow(repo_root)

    steps: list[tuple[str, Any, argparse.Namespace]] = [
        ("build", cmd_build, argparse.Namespace(target=None, verify=False)),
        (
            "skills-build",
            cmd_skills_build,
            argparse.Namespace(roots=None, all_default_roots=True),
        ),
        ("hooks-build", cmd_hooks_build, argparse.Namespace(no_install=False)),
    ]
    for step, handler, namespace in steps:
        print(f"\n== basicly {step} ==")
        rc = handler(namespace)
        if rc != 0:
            print(f"basicly install: {step} failed (exit {rc})", file=sys.stderr)
            return rc

    print("\nbasicly install complete: repo converged. Re-run the same command to upgrade.")
    return 0


def _remove_empty_parents(directory: Path, stop: Path) -> None:
    """Remove now-empty directories left behind by a deletion, up to ``stop``."""
    current = directory
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def _remove_generated_outputs(repo_root: Path, paths: ProjectPaths) -> int:
    """Delete the files the generated manifest lists, then the manifest itself."""
    manifest_path = repo_root / paths.manifest_path
    if not manifest_path.exists():
        return 0

    rel_paths: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outputs = manifest.get("outputs")
        if isinstance(outputs, dict):
            rel_paths = sorted(outputs)
    except json.JSONDecodeError:
        print("Note: generated manifest was unreadable; removing it anyway.", file=sys.stderr)

    removed = 0
    resolved_root = repo_root.resolve()
    for rel in rel_paths:
        target = (repo_root / rel).resolve()
        if not target.is_relative_to(resolved_root):
            print(f"Note: skipping manifest entry outside the repo: {rel}", file=sys.stderr)
            continue
        if target.is_file():
            target.unlink()
            removed += 1
            print(f"Removed {rel}")
            _remove_empty_parents(target.parent, resolved_root)

    manifest_path.unlink()
    print(f"Removed {_format_path(manifest_path, repo_root)}")
    return removed + 1


def _remove_generated_skills(repo_root: Path, roots: tuple[Path, ...]) -> int:
    """Delete projected SKILL.md files under *roots* (generated marker only)."""
    removed = 0
    for root in roots:
        base = repo_root / root
        if not base.is_dir():
            continue
        for skill_md in sorted(base.rglob(SKILL_FILE_NAME)):
            if GENERATED_MARKER not in skill_md.read_text(encoding="utf-8"):
                continue
            skill_md.unlink()
            removed += 1
            print(f"Removed {_format_path(skill_md, repo_root)}")
            _remove_empty_parents(skill_md.parent, repo_root)
    return removed


def _remove_projected_skills(repo_root: Path) -> int:
    """Delete projected SKILL.md files (generated marker only; user skills stay)."""
    return _remove_generated_skills(repo_root, (*DEFAULT_SKILL_ROOTS, *RETIRED_SKILL_ROOTS))


def _purge_user_content(repo_root: Path, paths: ProjectPaths) -> int:
    """Delete the overlay roots and basicly.toml (the --purge extras)."""
    removed = 0
    for overlay in paths.overlay_fragments_dirs:
        overlay_dir = repo_root / overlay
        if overlay_dir.is_dir():
            shutil.rmtree(overlay_dir)
            removed += 1
            print(f"Removed {_format_path(overlay_dir, repo_root)}/ (--purge)")
            _remove_empty_parents(overlay_dir.parent, repo_root)
    config_path = repo_root / CONFIG_FILE
    if config_path.exists():
        config_path.unlink()
        removed += 1
        print(f"Removed {CONFIG_FILE} (--purge)")
    tasks_path = repo_root / ".vscode" / "tasks.json"
    if tasks_path.exists():
        if tasks_path.read_text(encoding="utf-8") == VSCODE_TASKS_JSON:
            tasks_path.unlink()
            removed += 1
            print("Removed .vscode/tasks.json (--purge)")
            _remove_empty_parents(tasks_path.parent, repo_root)
        else:
            print("Kept .vscode/tasks.json (user-modified).")
    workflow_path = repo_root / ".github" / "workflows" / "basicly-gates.yml"
    if workflow_path.exists():
        if workflow_path.read_text(encoding="utf-8") == CONSUMER_CI_WORKFLOW:
            workflow_path.unlink()
            removed += 1
            print("Removed .github/workflows/basicly-gates.yml (--purge)")
            _remove_empty_parents(workflow_path.parent, repo_root)
        else:
            print("Kept .github/workflows/basicly-gates.yml (user-modified).")
    return removed


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove everything basicly manages; keep user content unless --purge.

    The inverse of ``install`` (§9): deletes the managed core, state, the
    generated files the manifest lists, projected skills (generated-marker
    files only), and the managed pre-commit block. The overlay and
    ``basicly.toml`` are the user's and survive unless ``--purge``.
    """
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)

    core_dst = repo_root / paths.core_root
    if bundled_catalog_root().resolve() == core_dst.resolve():
        print(
            "This repo is the catalog authoring source; refusing to uninstall.",
            file=sys.stderr,
        )
        return 1

    removed = _remove_generated_outputs(repo_root, paths)
    removed += _remove_projected_skills(repo_root)

    note = remove_managed_hooks(repo_root)
    if note:
        removed += 1
        print(note)

    for tree in (core_dst, (repo_root / paths.state_path).parent):
        if tree.is_dir():
            shutil.rmtree(tree)
            removed += 1
            print(f"Removed {_format_path(tree, repo_root)}/")
    _remove_empty_parents(core_dst.parent, repo_root)

    if getattr(args, "purge", False):
        removed += _purge_user_content(repo_root, paths)
    else:
        print(f"Kept the overlay and {CONFIG_FILE} (use --purge to remove them too).")

    if removed == 0:
        print("Nothing to remove; basicly is not installed here.")
    else:
        print("basicly uninstall complete.")
    return 0


def _core_hooks_dir(paths: ProjectPaths) -> Path:
    """Location of the on-disk core hooks dir, derived from the core root.

    Must stay repo-relative: the path is baked into the shared
    .pre-commit-config.yaml, so an absolute path would not be portable.
    """
    hooks_dir = paths.core_root / "hooks"
    if hooks_dir.is_absolute():
        raise ValueError(
            f"core hooks dir {hooks_dir} is absolute; set a repo-relative "
            f"core_fragments path in {CONFIG_FILE} so hook wiring stays portable"
        )
    return hooks_dir


def cmd_hooks_build(_args: argparse.Namespace) -> int:
    """Materialize hook scripts and wire them into the pre-commit config."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    config_path = repo_root / ".pre-commit-config.yaml"
    config_existed = config_path.exists()
    result = sync_hooks(repo_root, _core_hooks_dir(paths))

    rewrite_note = None
    if config_existed and config_path in result.written:
        rewrite_note = (
            "Note: .pre-commit-config.yaml was rewritten to update managed hooks; "
            "comments/formatting outside them may have been normalized."
        )
    _report_sync(result, repo_root, noun="hook files", label="Hooks", extra_note=rewrite_note)

    stages = hook_stages(load_hook_specs())
    if getattr(_args, "no_install", False):
        stage_flags = " ".join(f"-t {stage}" for stage in stages)
        print(
            "Skipped activation (--no-install). Run "
            f"`pre-commit install --install-hooks {stage_flags}`."
        )
        return 0

    ok, message = install_hooks(repo_root, stages)
    if ok:
        print(f"Activated git hooks for stages: {', '.join(stages)}.")
    else:
        print(f"Could not auto-activate git hooks: {message}", file=sys.stderr)
    if not (repo_root / ".beads" / "issues.jsonl").exists():
        print(
            "Note: no beads workspace found (.beads/issues.jsonl); the beads-commit-msg "
            "hook will skip its issue-id check. Enable tracking with `br init`."
        )
    return 0


def cmd_hooks_check(_args: argparse.Namespace) -> int:
    """Check that projected hooks and their wiring are up to date."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    mismatches = check_hooks(repo_root, _core_hooks_dir(paths))
    if _report_mismatches(
        mismatches,
        repo_root,
        stale_message="Stale hook projection detected. Run `basicly hooks-build` to sync hooks.",
    ):
        return 1

    # Advisory (non-fatal): projected files can be in sync yet the gates inert
    # because pre-commit was never installed — the exact gap behind unguarded
    # commits. Report it without failing, since CI runs the scripts directly and
    # does not install git hooks.
    missing = missing_hook_installations(repo_root, hook_stages(load_hook_specs()))
    if missing:
        stage_flags = " ".join(f"-t {stage}" for stage in missing)
        print(
            f"Note: git hooks are not installed for stages: {', '.join(missing)}. "
            f"Run `basicly hooks-build` or `pre-commit install --install-hooks {stage_flags}` "
            "to activate them locally.",
            file=sys.stderr,
        )

    print("Projected hooks are up to date.")
    return 0


def _resolve_skill_output_roots(args: argparse.Namespace, repo_root: Path) -> list[Path]:
    roots_arg = getattr(args, "roots", None)
    use_default_roots = bool(getattr(args, "all_default_roots", False))
    return resolve_skill_roots(
        repo_root=repo_root,
        roots=roots_arg,
        use_default_roots=use_default_roots,
    )


def cmd_skills_list(_args: argparse.Namespace) -> int:
    """List skills available in the source collection."""
    repo_root = _repo_root()
    skills = discover_skills(repo_root)
    if not skills:
        print("No skills found in .basicly/core/skills")
        return 0

    print(f"{'slug':<24} {'name':<24} description")
    print("-" * 96)
    for skill in skills:
        print(f"{skill.slug:<24} {skill.name:<24} {skill.description}")
    return 0


def cmd_skills_build(args: argparse.Namespace) -> int:
    """Project skills from .basicly/core/skills into one or more destination roots."""
    repo_root = _repo_root()
    roots = _resolve_skill_output_roots(args, repo_root)
    result = sync_skills(repo_root, roots)
    _report_sync(result, repo_root, noun="skill files", label="Skill")
    return 0


def cmd_skills_check(args: argparse.Namespace) -> int:
    """Check that projected skill roots are synchronized with source skills."""
    repo_root = _repo_root()
    roots = _resolve_skill_output_roots(args, repo_root)
    mismatches = check_synced_skills(repo_root, roots)
    stale = "Stale skill projection detected. Run `basicly skills-build` to sync skill files."
    if _report_mismatches(mismatches, repo_root, stale_message=stale):
        return 1

    print("Projected skills are up to date.")
    return 0


_SKILL_TEMPLATE = """\
# yaml-language-server: $schema=../../schemas/skill.schema.json
schema_version: 1
name: {slug}
description: {description}
instructions: |
  # {title}

  TODO: the skill runbook (markdown, indented two spaces).
"""

_FRAGMENT_TEMPLATE = """\
# yaml-language-server: $schema=../../schemas/fragment.schema.json
schema_version: 1
id: {id}
description: {description}
category: {category}
priority: medium
applies_to: [all]
tags: []
status: active
body: |
  - TODO: the guidance.
"""


def cmd_skills_new(args: argparse.Namespace) -> int:
    """Scaffold a new skill.yaml source under .basicly/core/skills/<slug>."""
    repo_root = _repo_root()
    path = repo_root / SKILLS_SOURCE_DIR / args.slug / "skill.yaml"
    if path.exists():
        print(f"Error: {_format_path(path, repo_root)} already exists.", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    title = args.slug.replace("-", " ").title()
    path.write_text(
        _SKILL_TEMPLATE.format(
            slug=args.slug, title=title, description=args.description or "TODO: one-line trigger."
        ),
        encoding="utf-8",
    )
    print(f"Wrote {_format_path(path, repo_root)}")
    return 0


def cmd_fragment_new(args: argparse.Namespace) -> int:
    """Scaffold a new <id>.fragment.yaml source under the core fragments tree."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    path = repo_root / paths.core_fragments_dir / args.category / f"{args.id}.fragment.yaml"
    if path.exists():
        print(f"Error: {_format_path(path, repo_root)} already exists.", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _FRAGMENT_TEMPLATE.format(
            id=args.id,
            category=args.category,
            description=args.description or "TODO: one-line description.",
        ),
        encoding="utf-8",
    )
    print(f"Wrote {_format_path(path, repo_root)}")
    return 0


def cmd_catalog_lint(_args: argparse.Namespace) -> int:
    """Lint catalog sources: schema-valid, no .md-named sources, single YAML extension."""
    repo_root = _repo_root()
    violations = catalog_lint.lint_catalog(repo_root)
    if violations:
        print("catalog-lint: FAILED", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("catalog-lint: OK")
    return 0


def _deterministic_gate(repo_root: Path, fragments: list[Any]) -> list[str]:
    """Run the full deterministic gate: structural lint plus resolved-content checks."""
    return catalog_lint.lint_catalog(repo_root) + catalog_verify.verify_catalog(fragments)


def _report_gate_failures(header: str, violations: list[str]) -> bool:
    """Print violations under a header when any exist; return True if the gate failed."""
    if not violations:
        return False
    print(header, file=sys.stderr)
    for violation in violations:
        print(f"  {violation}", file=sys.stderr)
    return True


def cmd_catalog_verify(_args: argparse.Namespace) -> int:
    """Verify catalog content: lint plus duplicate/contradiction/ambiguity/scope checks."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, _targets = _load_context(repo_root, paths)
    if _report_gate_failures("catalog-verify: FAILED", _deterministic_gate(repo_root, fragments)):
        return 1
    print("catalog-verify: OK")
    return 0


def _review_materials(repo_root: Path, paths: ProjectPaths) -> list[review.ReviewMaterial]:
    """Render every planned output as review material (the same content build writes)."""
    fragments, targets = _load_context(repo_root, paths)
    return [
        review.ReviewMaterial(
            _format_path(item.output_path, repo_root),
            _render_planned(repo_root, paths, item),
        )
        for item in plan_outputs(fragments, targets, repo_root)
    ]


def cmd_review(args: argparse.Namespace) -> int:
    """Advisory semantic review: an agent reads the rendered files for issues.

    The second, advisory layer of the pipeline (§6/§11.5) — always exits 0, never
    a merge gate. Renders the always-on files, assembles a review prompt, and
    dispatches it to the selected runner, handing off when no agent CLI is on PATH.
    """
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    prompt = review.build_review_prompt(_review_materials(repo_root, paths))

    if args.dry_run:
        print(prompt)
        return 0

    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, args.runner or config.default)
    result = runner.run(spec, prompt, repo_root)
    if result.handoff:
        print(
            f"review [handoff]: no agent CLI available via runner '{spec.name}' — run the "
            "semantic review yourself (see the prompt with --dry-run) and act on the findings. "
            "Advisory only; nothing blocks."
        )
        return 0
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        print(
            f"Warning: review runner '{spec.name}' exited {result.returncode}; "
            "the advisory review may be incomplete (non-blocking).",
            file=sys.stderr,
        )
    print("[review] advisory pass complete (non-blocking)")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    """Dispatch the ``policy`` subcommands (dor / gate / checkpoint / rework)."""
    handlers = {
        "dor": _cmd_policy_dor,
        "gate": _cmd_policy_gate,
        "checkpoint": _cmd_policy_checkpoint,
        "rework": _cmd_policy_rework,
    }
    handler = handlers.get(args.policy_command)
    return handler(args) if handler else 0


def _cmd_policy_dor(args: argparse.Namespace) -> int:
    """Report Definition-of-Ready; exit 1 (blocking) when sections are missing."""
    result = policy.definition_of_ready(_repo_root(), args.issue)
    if result.ready:
        print(f"DoR: READY ({args.issue})")
        return 0
    print(f"DoR: NOT READY ({args.issue}) — missing: {', '.join(result.missing)}", file=sys.stderr)
    return 1


def _cmd_policy_gate(args: argparse.Namespace) -> int:
    """Show gate status and exit 1 (blocking) when a required gate is not green."""
    repo_root = _repo_root()
    status = policy.gate_status(repo_root, args.issue, load_policy_config(repo_root))
    print(f"required passed:  {list(status.required_passed)}")
    if status.required_failed:
        print(f"required FAILED:  {list(status.required_failed)}")
    if status.required_missing:
        print(f"required MISSING: {list(status.required_missing)}")
    for verdict in status.advisory:
        state = "pass" if verdict.passed else "fail"
        print(f"advisory: {verdict.gate} [{verdict.provider}] = {state}")
    if status.can_advance:
        print("advance: ALLOWED")
        return 0
    print("advance: BLOCKED", file=sys.stderr)
    return 1


def _cmd_policy_checkpoint(args: argparse.Namespace) -> int:
    """Show or record approval of a human checkpoint."""
    repo_root = _repo_root()
    if args.approve:
        policy.approve_checkpoint(repo_root, args.issue, args.name)
        print(f"checkpoint {args.name}: APPROVED ({args.issue})")
        return 0
    approved = policy.checkpoint_approved(repo_root, args.issue, args.name)
    print(f"checkpoint {args.name}: {'APPROVED' if approved else 'PENDING'} ({args.issue})")
    return 0 if approved else 1


def _cmd_policy_rework(args: argparse.Namespace) -> int:
    """Show or record a rework attempt, reporting whether the cap forces escalation."""
    repo_root = _repo_root()
    config = load_policy_config(repo_root)
    if args.record:
        attempts = policy.record_rework(repo_root, args.issue, args.gate)
        print(f"Recorded rework for gate '{args.gate}'.")
    else:
        attempts = policy.rework_attempts(repo_root, args.issue, args.gate)
    verdict = "ESCALATE (cap reached)" if attempts >= config.max_rework else "may retry"
    print(f"rework: {attempts}/{config.max_rework} attempts for gate '{args.gate}' — {verdict}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Run the configured verify checks for a mode and optionally record a gate."""
    repo_root = _repo_root()
    config = load_verify_config(repo_root)
    if not config.for_mode(args.mode):
        print(f"No verify checks configured for mode '{args.mode}' in {CONFIG_FILE}.")

    report = verify.run_verify(repo_root, args.mode, config)

    print("\n" + "=" * 60)
    for result in report.results:
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"  {result.name}: {result.status.upper()}{suffix}")

    if args.issue:
        ok, message = verify.report_gate(repo_root, args.issue, report, gate=args.gate)
        print(message if ok else f"Warning: {message}", file=sys.stderr if not ok else sys.stdout)

    if report.passed:
        print(f"[verify] PASS (mode: {args.mode})")
        return 0
    print(f"[verify] FAIL: {', '.join(report.failures)}", file=sys.stderr)
    return 1


def _load_decompose_children(args: argparse.Namespace) -> tuple[Any, ...]:
    """Load child specs from --plan (suffix-detected) or JSON on stdin."""
    if args.plan:
        return decompose.load_plan_file(Path(args.plan))
    return decompose.load_plan_text(sys.stdin.read(), "json")


def _print_planned(planned: tuple[Any, ...]) -> None:
    for index, child in enumerate(planned):
        pred = "" if child.predecessor is None else f" (after child #{child.predecessor})"
        print(f"  [group {child.group}] #{index} {child.spec.title}{pred}")
        print(f"      scope: {', '.join(child.spec.scope)}")


def cmd_decompose(args: argparse.Namespace) -> int:
    """Decompose a feature into child issues + a computed dependency graph."""
    repo_root = _repo_root()
    children = _load_decompose_children(args)

    if args.dry_run:
        planned = decompose.preview(children)
        groups = 1 + max((c.group for c in planned), default=-1)
        print(f"decompose (dry-run): {len(planned)} children in {groups} parallel group(s)")
        _print_planned(planned)
        return 0

    result = decompose.decompose(repo_root, args.feature, children)
    print(
        f"decompose: created {len(result.children)} children under {result.feature_id} "
        f"in {result.parallel_groups} parallel group(s)"
    )
    for group_index, group in enumerate(result.groups):
        print(f"  group {group_index}: {' -> '.join(group)}")
    print(f"serial order: {' '.join(result.serial_order)}")
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    """Dispatch the ``loop`` subcommands (status / advance / run)."""
    handlers = {
        "status": _cmd_loop_status,
        "advance": _cmd_loop_advance,
        "run": _cmd_loop_run,
    }
    handler = handlers.get(args.loop_command)
    return handler(args) if handler else 0


def _loop_inputs(args: argparse.Namespace) -> loop.Inputs:
    """Map the shared agent-input flags onto a :class:`loop.Inputs`."""
    children = decompose.load_plan_file(Path(args.children)) if args.children else None
    return loop.Inputs(work_type=args.work_type, children=children, verify_mode=args.mode)


def _format_advance(result: loop.AdvanceResult) -> str:
    """Render one :class:`loop.AdvanceResult` as a single status line."""
    line = f"[{result.action}] {result.from_phase} -> {result.to_phase}"
    if result.detail:
        line += f": {result.detail}"
    if result.needs_input:
        line += f" (needs input: {result.needs_input})"
    return line


def _cmd_loop_status(args: argparse.Namespace) -> int:
    """Print an issue's reconstructed loop state, re-read from ``br`` on every call."""
    repo_root = _repo_root()
    state = loop_state.read_node_state(repo_root, args.issue)
    print(f"issue:       {state.issue_id} ({state.issue_type}, {state.status})")
    print(f"phase:       {state.phase}")
    if state.worktree is not None:
        print(f"worktree:    {state.worktree.name} on {state.worktree.branch}")
    else:
        print("worktree:    (none)")
    gates = state.gates
    print(f"gates:       advance {'ALLOWED' if gates.can_advance else 'BLOCKED'}")
    if gates.required_passed:
        print(f"  passed:    {', '.join(gates.required_passed)}")
    if gates.required_failed:
        print(f"  failed:    {', '.join(gates.required_failed)}")
    if gates.required_missing:
        print(f"  missing:   {', '.join(gates.required_missing)}")
    print(f"checkpoints: {', '.join(state.checkpoints) or '(none)'}")
    rework = ", ".join(f"{gate}={n}" for gate, n in state.rework.items()) or "(none)"
    print(f"rework:      {rework}")
    ready = loop_state.ready_ranked(repo_root)
    blocked = loop_state.blocked_ids(repo_root)
    print(f"ready set:   {', '.join(node.issue_id for node in ready) or '(none)'}")
    print(f"blocked:     {', '.join(blocked) or '(none)'}")
    return 0


def _cmd_loop_advance(args: argparse.Namespace) -> int:
    """Advance one loop step; exit non-zero when the track blocks so CI can tell."""
    result = loop.advance(_repo_root(), args.issue, inputs=_loop_inputs(args))
    print(_format_advance(result))
    return 1 if result.blocked else 0


def _cmd_loop_run(args: argparse.Namespace) -> int:
    """Advance until the track blocks or finishes; exit non-zero if it ended blocked."""
    results = loop.run_until_blocked(_repo_root(), args.issue, inputs=_loop_inputs(args))
    for result in results:
        print(_format_advance(result))
    return 1 if results and results[-1].blocked else 0


def cmd_runner(args: argparse.Namespace) -> int:
    """Dispatch the ``runner`` subcommands (list / dry-run / run)."""
    handlers = {
        "list": _cmd_runner_list,
        "dry-run": _cmd_runner_dry_run,
        "run": _cmd_runner_run,
    }
    handler = handlers.get(args.runner_command)
    return handler(args) if handler else 0


def _resolve_runner(args: argparse.Namespace) -> runner.RunnerSpec:
    """Resolve the runner from --runner (or the configured [runner].default)."""
    config = load_runner_config(_repo_root())
    return runner.select_runner(config.specs, args.runner or config.default)


def _cmd_runner_list(_args: argparse.Namespace) -> int:
    """List the configured runner adapters, their availability, and the auto-selection."""
    config = load_runner_config(_repo_root())
    print(f"default: {config.default}")
    for spec in config.specs:
        if spec.kind == runner.HANDOFF:
            print(f"- {spec.name} [{spec.kind}] — always available (work handed off)")
            continue
        avail = "available" if runner.is_available(spec) else "not on PATH"
        print(f"- {spec.name} [{spec.kind}] — {avail}: {shlex.join(spec.command)}")
    resolved = runner.select_runner(config.specs, config.default)
    print(f"selected ({config.default}): {resolved.name}")
    return 0


def _cmd_runner_dry_run(args: argparse.Namespace) -> int:
    """Print the exact command the selected runner would execute (no invocation)."""
    spec = _resolve_runner(args)
    result = runner.run(spec, args.prompt, _repo_root(), dry_run=True)
    if result.handoff:
        print(
            f"runner '{spec.name}' [handoff]: no headless command — the work is handed off to the "
            "driving agent/human; nothing is executed."
        )
        return 0
    print(f"runner '{spec.name}':")
    print(f"  {shlex.join(result.command)}")
    return 0


def _cmd_runner_run(args: argparse.Namespace) -> int:
    """Invoke the selected runner headless in --cwd, streaming its captured output."""
    spec = _resolve_runner(args)
    cwd = Path(args.cwd) if args.cwd else _repo_root()
    result = runner.run(spec, args.prompt, cwd)
    if result.handoff:
        print(
            f"runner '{spec.name}' [handoff]: no agent CLI available — do the work described in "
            f"the prompt in {cwd}, then re-invoke the loop."
        )
        return 0
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode if result.returncode is not None else 0


def cmd_worktree(args: argparse.Namespace) -> int:
    """Dispatch the ``worktree`` subcommands (create / cleanup / list / bg-isolation)."""
    handlers = {
        "create": _cmd_worktree_create,
        "cleanup": _cmd_worktree_cleanup,
        "list": _cmd_worktree_list,
        "bg-isolation": _cmd_worktree_bg_isolation,
        "merge": _cmd_worktree_merge,
        "merge-queue": _cmd_worktree_merge_queue,
    }
    handler = handlers.get(args.worktree_command)
    return handler(args) if handler else 0


def _cmd_worktree_merge(args: argparse.Namespace) -> int:
    """Merge one finished worktree back to its base; exit 1 when it does not land."""
    result = merge.merge_worktree(_repo_root(), args.name, bead=args.bead, verify_mode=args.mode)
    print(f"  {result.name}: {result.status.upper()} — {result.detail}")
    return 0 if result.merged else 1


def _cmd_worktree_merge_queue(args: argparse.Namespace) -> int:
    """Merge NAME:BEAD worktrees serially; exit 1 if any node fails to land."""
    items: list[tuple[str, str]] = []
    for raw in args.items:
        name, sep, bead = raw.partition(":")
        if not sep or not name or not bead:
            print(f"Error: expected NAME:BEAD, got {raw!r}", file=sys.stderr)
            return 1
        items.append((name, bead))

    results = merge.merge_queue(_repo_root(), items, verify_mode=args.mode)
    for queued in results:
        outcome = queued.result
        line = f"  {outcome.name}: {outcome.status.upper()} — {outcome.detail}"
        if not outcome.merged:
            line += f"  [rework {queued.attempts}: {'ESCALATE' if queued.escalate else 'retry'}]"
        print(line)

    merged = sum(1 for queued in results if queued.result.merged)
    print(f"merge-queue: {merged}/{len(items)} merged")
    return 0 if merged == len(items) else 1


def _cmd_worktree_create(args: argparse.Namespace) -> int:
    """Create + provision a worktree, honoring the configured base and cap."""
    config = load_worktree_config(_repo_root())
    active = len(worktree.list_sessions())
    if active >= config.concurrency:
        print(
            f"Error: worktree concurrency cap reached ({active}/{config.concurrency}). "
            "Clean up a worktree or raise [worktree].concurrency in basicly.toml.",
            file=sys.stderr,
        )
        return 1
    worktree.create(args.name, base=args.base or config.base_branch)
    return 0


def _cmd_worktree_cleanup(args: argparse.Namespace) -> int:
    """Remove a worktree and delete its merged branch."""
    worktree.cleanup(args.name, force=args.force)
    return 0


def _cmd_worktree_list(_args: argparse.Namespace) -> int:
    """List worktree sessions, marking any whose directory has vanished."""
    sessions = worktree.list_sessions()
    if not sessions:
        print("No worktree sessions.")
        return 0
    for session in sessions:
        marker = "" if session.path.exists() else "  (stale: dir missing)"
        print(f"- {session.name}: {session.branch} (base {session.base}){marker}")
        print(f"    {session.worktree_path}")
    return 0


def _cmd_worktree_bg_isolation(args: argparse.Namespace) -> int:
    """Consent-gated write of Claude's ``worktree.bgIsolation=none`` (Claude only)."""
    repo_root = _repo_root()
    current = claude_settings.current_bg_isolation(repo_root)
    if current == claude_settings.BG_ISOLATION_NONE:
        print("worktree.bgIsolation is already 'none' in .claude/settings.json; nothing to do.")
        return 0

    shown = current if current is not None else "unset (Claude default: enabled)"
    print(
        "Claude Code's worktree.bgIsolation guard forces background agents into "
        ".claude/worktrees/ before editing, which conflicts with basicly's sibling "
        "<repo>.worktrees/ isolation (EnterWorktree cannot target a sibling path).\n"
        "To run the harness under Claude Code it must be 'none' — the harness isolates "
        "itself.\n"
        f"  current: {shown}\n"
        "  proposed: set worktree.bgIsolation='none' in the COMMITTED .claude/settings.json\n"
        "            (team-wide default; any user may override in the gitignored "
        ".claude/settings.local.json).\n"
        "This affects the Claude target only; Codex and Copilot have no such setting."
    )
    if not args.yes:
        print(
            "\nNo change made. Re-run `basicly worktree bg-isolation --yes` to consent to "
            "writing it."
        )
        return 0

    changed = claude_settings.set_bg_isolation_none(repo_root)
    if changed:
        print(
            "\nSet worktree.bgIsolation='none' in .claude/settings.json (committed, team-wide "
            "default). Override locally in the gitignored .claude/settings.local.json if needed."
        )
    return 0


def _add_skill_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Destination skills root. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--all-default-roots",
        action="store_true",
        help="Use .claude/skills and .agents/skills.",
    )


def _add_worktree_parser(subparsers: argparse._SubParsersAction) -> None:
    worktree_parser = subparsers.add_parser(
        "worktree", help="Manage isolated sibling git worktrees"
    )
    worktree_sub = worktree_parser.add_subparsers(dest="worktree_command", required=True)
    wt_create = worktree_sub.add_parser(
        "create", help="Create + provision a sibling worktree on harness/<name>"
    )
    wt_create.add_argument("name")
    wt_create.add_argument(
        "--base",
        default=None,
        help="Base branch to fork from (default: [worktree].base_branch or current)",
    )
    wt_cleanup = worktree_sub.add_parser(
        "cleanup", help="Remove a worktree and delete its merged branch"
    )
    wt_cleanup.add_argument("name")
    wt_cleanup.add_argument(
        "--force",
        action="store_true",
        help="Delete the branch even if it is not fully merged",
    )
    worktree_sub.add_parser("list", help="List worktree sessions (marks stale ones)")
    wt_bg = worktree_sub.add_parser(
        "bg-isolation",
        help="Set Claude's worktree.bgIsolation=none so the harness isolates itself",
    )
    wt_bg.add_argument(
        "--yes",
        action="store_true",
        help="Consent to writing the change to the committed .claude/settings.json",
    )
    wt_merge = worktree_sub.add_parser(
        "merge", help="Merge a finished worktree back to its base (rebase, re-verify, --no-ff)"
    )
    wt_merge.add_argument("name")
    wt_merge.add_argument("--bead", required=True, help="Bead id for the merge commit message")
    wt_merge.add_argument(
        "--mode", choices=VERIFY_MODES, default="full", help="Verify mode to re-run before merge"
    )
    wt_queue = worktree_sub.add_parser(
        "merge-queue", help="Merge several worktrees serially in the given (topological) order"
    )
    wt_queue.add_argument("items", nargs="+", metavar="NAME:BEAD", help="e.g. feat-x:basicly-onb.5")
    wt_queue.add_argument("--mode", choices=VERIFY_MODES, default="full")


def _add_verify_parser(subparsers: argparse._SubParsersAction) -> None:
    verify_parser = subparsers.add_parser(
        "verify", help="Run the configured verify checks and optionally record a br gate"
    )
    verify_parser.add_argument(
        "--mode",
        choices=VERIFY_MODES,
        default="full",
        help="Which configured check set to run (default: full)",
    )
    verify_parser.add_argument("--issue", help="Record the verdict as a br gate on this issue id")
    verify_parser.add_argument(
        "--gate",
        default=verify.DEFAULT_GATE,
        help=f"Gate name to record (default: {verify.DEFAULT_GATE})",
    )


def _add_decompose_parser(subparsers: argparse._SubParsersAction) -> None:
    decompose_parser = subparsers.add_parser(
        "decompose",
        help="Turn a feature into child br issues + a computed dependency graph",
    )
    decompose_parser.add_argument("feature", help="Parent feature issue id")
    decompose_parser.add_argument(
        "--plan",
        help="Plan file with a 'children' list (.toml or .json); reads JSON on stdin if omitted",
    )
    decompose_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute grouping/serial chains without creating any issues",
    )


def _add_policy_parser(subparsers: argparse._SubParsersAction) -> None:
    policy_parser = subparsers.add_parser(
        "policy", help="Loop gate/checkpoint policy checks (DoR, gates, rework, checkpoints)"
    )
    policy_sub = policy_parser.add_subparsers(dest="policy_command", required=True)
    p_dor = policy_sub.add_parser("dor", help="Check Definition-of-Ready via br lint")
    p_dor.add_argument("issue")
    p_gate = policy_sub.add_parser(
        "gate", help="Show required/advisory gate status and the advance decision"
    )
    p_gate.add_argument("issue")
    p_ck = policy_sub.add_parser("checkpoint", help="Show or approve a human checkpoint")
    p_ck.add_argument("issue")
    p_ck.add_argument("name", choices=CHECKPOINTS)
    p_ck.add_argument("--approve", action="store_true", help="Record human approval")
    p_rw = policy_sub.add_parser("rework", help="Show or record a rework attempt")
    p_rw.add_argument("issue")
    p_rw.add_argument("--gate", default=verify.DEFAULT_GATE, help="Gate the rework is for")
    p_rw.add_argument("--record", action="store_true", help="Record a new rework attempt")


def _add_loop_input_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared agent-input flags that map onto a ``loop.Inputs``."""
    parser.add_argument(
        "--work-type",
        choices=("bug", "chore", "task", "feature", "epic"),
        help="Agent-proposed br work type, consumed by the classify phase",
    )
    parser.add_argument(
        "--children",
        help="Child plan file (.toml or .json) with a 'children' list, for decompose",
    )
    parser.add_argument(
        "--mode",
        choices=VERIFY_MODES,
        default="full",
        help="Verify mode used when a phase re-runs the checks (default: full)",
    )


def _add_runner_parser(subparsers: argparse._SubParsersAction) -> None:
    runner_parser = subparsers.add_parser(
        "runner", help="Agent-agnostic headless runner adapters (claude/codex/copilot)"
    )
    runner_sub = runner_parser.add_subparsers(dest="runner_command", required=True)
    runner_sub.add_parser(
        "list", help="List runner adapters, their availability, and the auto-selection"
    )
    r_dry = runner_sub.add_parser(
        "dry-run", help="Print the exact command a runner would execute (no invocation)"
    )
    r_dry.add_argument(
        "--runner", help="Runner name or 'auto' (default: the configured [runner].default)"
    )
    r_dry.add_argument("--prompt", required=True, help="Prompt the runner would send to the agent")
    r_run = runner_sub.add_parser(
        "run", help="Invoke a runner headless and stream its captured output"
    )
    r_run.add_argument(
        "--runner", help="Runner name or 'auto' (default: the configured [runner].default)"
    )
    r_run.add_argument("--prompt", required=True, help="Prompt to send to the agent")
    r_run.add_argument("--cwd", help="Working directory to run in (default: repo root)")


def _add_loop_parser(subparsers: argparse._SubParsersAction) -> None:
    loop_parser = subparsers.add_parser(
        "loop", help="Drive an issue through the harness loop (status / advance / run)"
    )
    loop_sub = loop_parser.add_subparsers(dest="loop_command", required=True)
    l_status = loop_sub.add_parser(
        "status", help="Show an issue's reconstructed loop state (read-only)"
    )
    l_status.add_argument("issue")
    l_advance = loop_sub.add_parser(
        "advance", help="Advance one loop step (exit non-zero when blocked)"
    )
    l_advance.add_argument("issue")
    _add_loop_input_args(l_advance)
    l_run = loop_sub.add_parser("run", help="Advance until the track blocks or finishes")
    l_run.add_argument("issue")
    _add_loop_input_args(l_run)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the requested command."""
    parser = argparse.ArgumentParser(prog="basicly")
    parser.add_argument("--version", action="version", version=f"basicly {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List active fragments")

    install_parser = subparsers.add_parser(
        "install",
        help=(
            "Install or upgrade basicly in this repo "
            "(sync catalog + scaffold + build + skills + hooks)"
        ),
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite hand-edited managed core files instead of keeping them",
    )

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help=(
            "Remove everything basicly manages (core, state, generated files, "
            "projected skills, managed hooks); user content survives"
        ),
    )
    uninstall_parser.add_argument(
        "--purge",
        action="store_true",
        help="Also remove the user overlay and basicly.toml",
    )

    build_parser = subparsers.add_parser("build", help="Build generated files")
    build_parser.add_argument("--target", help="Build only the specified target")
    build_parser.add_argument(
        "--verify",
        action="store_true",
        help="Run the deterministic catalog gate first; write nothing if it fails",
    )

    subparsers.add_parser("check", help="Check generated files are up to date")

    subparsers.add_parser("skills-list", help="List skills in .basicly/core/skills")

    skills_build_parser = subparsers.add_parser(
        "skills-build",
        help="Project skills from .basicly/core/skills",
    )
    _add_skill_root_args(skills_build_parser)

    skills_check_parser = subparsers.add_parser(
        "skills-check",
        help="Check projected skills are up to date",
    )
    _add_skill_root_args(skills_check_parser)

    skills_new_parser = subparsers.add_parser("skills-new", help="Scaffold a new skill.yaml source")
    skills_new_parser.add_argument("slug", help="Skill slug (directory + name)")
    skills_new_parser.add_argument("--description", help="One-line trigger description")

    fragment_new_parser = subparsers.add_parser(
        "fragment-new", help="Scaffold a new <id>.fragment.yaml source"
    )
    fragment_new_parser.add_argument("id", help="Fragment id")
    fragment_new_parser.add_argument(
        "--category", default="project", choices=sorted(CATEGORIES), help="Fragment category"
    )
    fragment_new_parser.add_argument("--description", help="One-line description")

    hooks_build_parser = subparsers.add_parser(
        "hooks-build", help="Project git hooks into .pre-commit-config.yaml"
    )
    hooks_build_parser.add_argument(
        "--no-install",
        action="store_true",
        help="Only write wiring; do not run `pre-commit install` to activate the hooks",
    )
    subparsers.add_parser("hooks-check", help="Check projected hooks are up to date")

    subparsers.add_parser(
        "catalog-lint",
        help="Validate catalog YAML sources (schema, no .md sources, single extension)",
    )

    subparsers.add_parser(
        "catalog-verify",
        help="Verify catalog content (lint + duplicate/contradiction/ambiguity/scope checks)",
    )

    review_parser = subparsers.add_parser(
        "review",
        help="Advisory agent-assisted semantic review of the rendered files (never blocks)",
    )
    review_parser.add_argument(
        "--runner", help="Runner name or 'auto' (default: the configured [runner].default)"
    )
    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the assembled review prompt without invoking any agent",
    )

    _add_worktree_parser(subparsers)
    _add_verify_parser(subparsers)
    _add_policy_parser(subparsers)
    _add_decompose_parser(subparsers)
    _add_loop_parser(subparsers)
    _add_runner_parser(subparsers)

    args = parser.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "build": cmd_build,
        "check": cmd_check,
        "skills-list": cmd_skills_list,
        "skills-build": cmd_skills_build,
        "skills-check": cmd_skills_check,
        "skills-new": cmd_skills_new,
        "fragment-new": cmd_fragment_new,
        "hooks-build": cmd_hooks_build,
        "hooks-check": cmd_hooks_check,
        "catalog-lint": cmd_catalog_lint,
        "catalog-verify": cmd_catalog_verify,
        "review": cmd_review,
        "worktree": cmd_worktree,
        "verify": cmd_verify,
        "policy": cmd_policy,
        "decompose": cmd_decompose,
        "loop": cmd_loop,
        "runner": cmd_runner,
    }

    try:
        handler = handlers.get(args.command)
        if handler is None:
            return 0
        return handler(args)
    except ValidationError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
