"""CLI for basicly."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, claude_settings, verify, worktree
from .catalog import bundled_catalog_root, iter_catalog_files
from .config import (
    CONFIG_FILE,
    DEFAULT_CONFIG_TOML,
    VERIFY_MODES,
    ProjectPaths,
    load_project_paths,
    load_verify_config,
    load_worktree_config,
)
from .hooks import (
    check_hooks,
    hook_stages,
    install_hooks,
    load_hook_specs,
    missing_hook_installations,
    sync_hooks,
)
from .loader import load_fragments_from_roots, load_targets
from .planner import plan_outputs
from .renderers.common import sha256_of_text
from .schema import PlannedOutput, ValidationError
from .skills import (
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


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


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
        changed = _write_if_changed(item.output_path, content)
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


def cmd_update(_args: argparse.Namespace) -> int:
    """Refresh managed core layout and preserve user overlay files."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)

    core_dir = repo_root / paths.core_fragments_dir
    core_dir.mkdir(parents=True, exist_ok=True)

    for overlay in paths.overlay_fragments_dirs:
        (repo_root / overlay / "user").mkdir(parents=True, exist_ok=True)

    legacy_dir = repo_root / paths.legacy_fragments_dir
    if not legacy_dir.exists():
        print("basicly update: core and overlay layout is up to date.")
        return 0

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

    print(
        "basicly update complete: "
        f"{moved} item(s) migrated, {skipped} existing item(s) left unchanged"
    )
    return 0


def _materialize_catalog(src: Path, dst: Path) -> tuple[int, int]:
    """Copy catalog files from ``src`` to ``dst`` without overwriting existing ones.

    Returns ``(written, skipped)``.
    """
    written = 0
    skipped = 0
    for path in iter_catalog_files(src):
        target = dst / path.relative_to(src)
        if target.exists():
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        written += 1
    return written, skipped


def cmd_init(_args: argparse.Namespace) -> int:
    """Scaffold a consumer repo: materialize the core catalog, overlay, and config."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)

    core_src = bundled_catalog_root()
    core_dst = repo_root / paths.core_root
    if core_src.resolve() == core_dst.resolve():
        print("Core catalog is its own authoring source here; left in place.")
    else:
        written, skipped = _materialize_catalog(core_src, core_dst)
        print(
            f"Materialized core catalog into {_format_path(core_dst, repo_root)}: "
            f"{written} file(s) written, {skipped} existing left unchanged"
        )

    for overlay in paths.overlay_fragments_dirs:
        user_dir = repo_root / overlay / "user"
        existed = user_dir.exists()
        user_dir.mkdir(parents=True, exist_ok=True)
        verb = "exists" if existed else "created"
        print(f"Overlay {verb}: {_format_path(user_dir, repo_root)}")

    config_path = repo_root / CONFIG_FILE
    if config_path.exists():
        print(f"{CONFIG_FILE} already exists; left unchanged")
    else:
        config_path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        print(f"Wrote {CONFIG_FILE}")

    print("\nNext steps:")
    print("  basicly build        # generate AGENTS.md / CLAUDE.md / copilot-instructions.md")
    print("  basicly skills-build --all-default-roots   # project skills")
    print("  basicly hooks-build  # wire the git-hook gates into .pre-commit-config.yaml")
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

    for path in result.written:
        print(f"Wrote {_format_path(path, repo_root)}")
    if config_existed and config_path in result.written:
        print(
            "Note: .pre-commit-config.yaml was rewritten to update managed hooks; "
            "comments/formatting outside them may have been normalized."
        )
    if not result.written:
        print("No hook files changed.")

    print(
        f"Hooks projection complete: {len(result.written)} written, "
        f"{len(result.unchanged)} unchanged"
    )

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
    return 0


def cmd_hooks_check(_args: argparse.Namespace) -> int:
    """Check that projected hooks and their wiring are up to date."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    mismatches = check_hooks(repo_root, _core_hooks_dir(paths))

    if mismatches:
        print(
            "Stale hook projection detected. Run `basicly hooks-build` to sync hooks.",
            file=sys.stderr,
        )
        for path, reason in mismatches:
            print(f"  {_format_path(path, repo_root)}: {reason}", file=sys.stderr)
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

    for path in result.written:
        print(f"Wrote {_format_path(path, repo_root)}")

    if not result.written:
        print("No skill files changed.")

    print(
        "Skill projection complete: "
        f"{len(result.written)} written, {len(result.unchanged)} unchanged"
    )
    return 0


def cmd_skills_check(args: argparse.Namespace) -> int:
    """Check that projected skill roots are synchronized with source skills."""
    repo_root = _repo_root()
    roots = _resolve_skill_output_roots(args, repo_root)
    mismatches = check_synced_skills(repo_root, roots)

    if mismatches:
        print(
            "Stale skill projection detected. Run `basicly skills-build` to sync skill files.",
            file=sys.stderr,
        )
        for path, reason in mismatches:
            print(f"  {_format_path(path, repo_root)}: {reason}", file=sys.stderr)
        return 1

    print("Projected skills are up to date.")
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
        print(f"  {result.name}: {result.status.upper()}")

    if args.issue:
        ok, message = verify.report_gate(repo_root, args.issue, report, gate=args.gate)
        print(message if ok else f"Warning: {message}", file=sys.stderr if not ok else sys.stdout)

    if report.passed:
        print(f"[verify] PASS (mode: {args.mode})")
        return 0
    print(f"[verify] FAIL: {', '.join(report.failures)}", file=sys.stderr)
    return 1


def cmd_worktree(args: argparse.Namespace) -> int:
    """Dispatch the ``worktree`` subcommands (create / cleanup / list / bg-isolation)."""
    handlers = {
        "create": _cmd_worktree_create,
        "cleanup": _cmd_worktree_cleanup,
        "list": _cmd_worktree_list,
        "bg-isolation": _cmd_worktree_bg_isolation,
    }
    handler = handlers.get(args.worktree_command)
    return handler(args) if handler else 0


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
        help="Use .claude/skills, .github/skills, and .agents/skills.",
    )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the requested command."""
    parser = argparse.ArgumentParser(prog="basicly")
    parser.add_argument("--version", action="version", version=f"basicly {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List active fragments")

    subparsers.add_parser(
        "init",
        help="Scaffold a consumer repo (materialize core catalog + overlay + basicly.toml)",
    )

    subparsers.add_parser("update", help="Refresh managed core layout")

    build_parser = subparsers.add_parser("build", help="Build generated files")
    build_parser.add_argument("--target", help="Build only the specified target")

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

    hooks_build_parser = subparsers.add_parser(
        "hooks-build", help="Project git hooks into .pre-commit-config.yaml"
    )
    hooks_build_parser.add_argument(
        "--no-install",
        action="store_true",
        help="Only write wiring; do not run `pre-commit install` to activate the hooks",
    )
    subparsers.add_parser("hooks-check", help="Check projected hooks are up to date")

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

    args = parser.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "init": cmd_init,
        "update": cmd_update,
        "build": cmd_build,
        "check": cmd_check,
        "skills-list": cmd_skills_list,
        "skills-build": cmd_skills_build,
        "skills-check": cmd_skills_check,
        "hooks-build": cmd_hooks_build,
        "hooks-check": cmd_hooks_check,
        "worktree": cmd_worktree,
        "verify": cmd_verify,
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
