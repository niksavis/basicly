"""CLI for basicly."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from . import (
    __version__,
    agents,
    board_cli,
    board_facts,
    catalog_lint,
    catalog_verify,
    checkout,
    claude_settings,
    comment_rows,
    commit,
    contention,
    decisions,
    decompose,
    dispatch_brief,
    fleet,
    health,
    lane_log,
    lane_split,
    loop,
    loop_state,
    merge,
    owned_store,
    permissions,
    policy,
    projection,
    release,
    review,
    routing_evals,
    rubrics,
    run_record,
    runner,
    state,
    supervise,
    tracker,
    tracker_query,
    tracker_write,
    ui,
    usage_report,
    validate_gate,
    verify,
    working_set,
    worktree,
)
from . import session as session_config
from .catalog import bundled_catalog_root, iter_catalog_files
from .config import (
    AUTONOMY_LEVELS,
    CHECKPOINTS,
    CONFIG_FILE,
    DEFAULT_CONFIG_TOML,
    LOCAL_CONFIG_FILE,
    MODEL_TIERS,
    VERIFY_MODES,
    WORK_TYPES,
    ProjectPaths,
    SizingConfig,
    load_policy_config,
    load_project_paths,
    load_runner_config,
    load_sizing_config,
    load_technology_selection,
    load_verify_config,
    load_worktree_config,
    record_technology_selection,
    unknown_config_keys,
)
from .hooks import (
    AGENT_HOOK_HOSTS,
    PRE_PUSH_STAGE,
    agent_hook_surface_present,
    check_copilot_hooks,
    check_hooks,
    claude_hook_specs,
    copilot_hook_specs,
    git_hook_specs,
    hook_stages,
    install_hooks,
    load_hook_specs,
    missing_hook_installations,
    remove_copilot_hooks,
    remove_managed_hooks,
    selected_hook_specs,
    sync_copilot_hooks,
    sync_hooks,
)
from .loader import load_fragments_from_roots, load_targets
from .planner import plan_outputs
from .renderers.common import sha256_of_text
from .scaffolds import (
    CONSUMER_CI_WORKFLOW,
    OVERLAY_FRAGMENT_STUBS,
    VSCODE_TASKS_JSON,
)
from .schema import (
    CATEGORIES,
    TECHNOLOGIES,
    Fragment,
    OutputDef,
    PlannedOutput,
    Target,
    ValidationError,
    technology_selected,
)
from .skills import (
    DEFAULT_SKILL_ROOTS,
    GENERATED_MARKER,
    RETIRED_SKILL_ROOTS,
    SKILL_FILE_NAME,
    SKILLS_SOURCE_DIR,
    UNMANAGED_REASON_PREFIX,
    check_synced_skills,
    discover_skills,
    resolve_skill_roots,
    sync_skills,
)


def _repo_root() -> Path:
    return Path.cwd()


def _dispatch(
    args: argparse.Namespace,
    dest: str,
    handlers: dict[str, Callable[[argparse.Namespace], int]],
    *,
    group: str = "",
) -> int:
    """Route ``args`` to the handler for the subcommand it selected, loudly on a miss.

    Every subparser in this CLI is ``required=True``, so a name that reaches here is
    one the parser already accepted — meaning a miss is always a registered command
    with no handler, never user error. Returning 0 on a miss made that command print
    nothing and succeed, which is indistinguishable from a command that ran
    (basicly-tcmy.4). Each command group used to spell its own dispatch, so the fix
    landed on one of seven sites; the guard lives here so a group cannot inherit the
    defect by copying its neighbour (basicly-8ry8).
    """
    handler = handlers.get(getattr(args, dest))
    if handler is None:
        name = f"{group} {getattr(args, dest)}".strip()
        print(
            f"internal error: subcommand {name!r} is registered on the parser "
            "but has no handler — this is a bug in basicly, not in your invocation",
            file=sys.stderr,
        )
        return 2
    return handler(args)


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
        ui.say(f"Wrote {_format_path(path, repo_root)}", style="ok")
    if result.written and extra_note:
        ui.say(extra_note, style="warn")
    if not result.written:
        ui.say(f"No {noun} changed.", style="muted")
    ui.say(
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
    ui.fail(stale_message)
    for path, reason in mismatches:
        ui.fail(f"  {_format_path(path, repo_root)}: {reason}")
    return True


def _fragment_roots(paths: ProjectPaths) -> list[tuple[Path, str | None]]:
    roots: list[tuple[Path, str | None]] = [(paths.core_fragments_dir, "core")]

    if paths.legacy_fragments_dir not in {p for p, _ in roots}:
        roots.append((paths.legacy_fragments_dir, None))

    roots.extend((overlay_root, "user") for overlay_root in paths.overlay_fragments_dirs)

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
    selection = load_technology_selection(repo_root)
    fragments = [f for f in fragments if technology_selected(f.technologies, selection)]
    return fragments, targets


def _budget_warnings(
    targets: list[Target], item: PlannedOutput, content: str, repo_root: Path
) -> list[str]:
    """Warnings for a rendered output that overruns its target's always-on budget.

    Two units, because the constraint is stated in both and only one was measured. The
    character cap is ours; the **line** cap is the vendor's — "target under 200 lines per
    CLAUDE.md file. Longer files consume more context and reduce adherence"
    (code.claude.com/docs/en/memory, read 2026-08-08). Measuring characters alone let
    `AGENTS.md` reach 231 lines while reading as merely 1,569 characters over, so a file
    could be comfortably inside one budget and past the other with nothing saying so.

    Warnings, not failures: an always-on file over budget is a cost to weigh, not a broken
    tree, and the eviction that fixes it is authoring work (basicly-a3ab.1).
    """
    out: list[str] = []
    for target in targets:
        if target.name != item.target_name:
            continue
        where = item.output_path.relative_to(repo_root)
        if target.max_size_warning and len(content) > target.max_size_warning:
            out.append(
                f"Warning: {where} exceeds {target.max_size_warning} characters ({len(content)})"
            )
        lines = content.count("\n") + 1
        if target.max_lines_warning and lines > target.max_lines_warning:
            out.append(f"Warning: {where} exceeds {target.max_lines_warning} lines ({lines})")
    return out


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


def _sweep_stale_outputs(
    repo_root: Path, existing_manifest: dict[str, Any], manifest: dict[str, Any]
) -> int:
    """Delete previously manifest-tracked files that dropped out of the new plan.

    Only files the old manifest vouched for are touched, so retiring an output
    (e.g. the .github/instructions twins) converges consumers on re-install
    instead of stranding stale projections.
    """
    existing_outputs = existing_manifest.get("outputs")
    if not isinstance(existing_outputs, dict):
        return 0

    removed = 0
    resolved_root = repo_root.resolve()
    for rel in sorted(set(existing_outputs) - set(manifest["outputs"])):
        stale = _sweepable_path(repo_root, rel)
        if stale is None:
            print(f"Note: skipping unsafe manifest entry: {rel}", file=sys.stderr)
            continue
        if stale.is_symlink() or stale.is_file():
            stale.unlink()
            removed += 1
            print(f"Removed {rel}")
            _remove_empty_parents(stale.parent.resolve(), resolved_root)
    return removed


def cmd_list(_args: argparse.Namespace) -> int:
    """List active fragments in a table."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, _targets = _load_context(repo_root, paths)
    active = [f for f in fragments if f.status == "active"]

    ui.table(
        f"Active fragments ({len(active)})",
        ["id", "category", "priority", "applies_to", "scope", "status"],
        [
            [f.id, f.category, f.priority, ", ".join(f.applies_to), f.scope_summary, f.status]
            for f in sorted(active, key=lambda x: (x.category, -x.priority_value, x.id))
        ],
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
            ui.say(f"Wrote {item.output_path.relative_to(repo_root)}", style="ok")
        for line in _budget_warnings(targets, item, content, repo_root):
            print(line, file=sys.stderr)

    manifest_path = repo_root / paths.manifest_path
    existing_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = {}

    manifest = _build_manifest(rendered, planned, existing_manifest, bool(args.target))
    changed_count += _sweep_stale_outputs(repo_root, existing_manifest, manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    ui.say(f"Updated {_format_path(manifest_path, repo_root)}", style="ok")
    if changed_count == 0:
        ui.say("No files changed.", style="muted")
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
        # Emitted here as well as from build, because build is the command nobody runs on
        # a tree that is already current: measured 2026-08-09, `check` and `agents-check`
        # both reported clean while AGENTS.md sat 1,135 characters and 27 lines past its
        # caps. A budget only the writing command reports is one a reader never sees.
        for line in _budget_warnings(targets, item, content, repo_root):
            print(line, file=sys.stderr)
        rel_path = item.output_path.relative_to(repo_root).as_posix()
        expected_hash = sha256_of_text(content)
        expected_manifest_outputs[rel_path] = {
            "hash": expected_hash,
            "source_fragments": [f.id for f in item.fragments],
        }

        if not item.output_path.exists():
            mismatches.append((item.output_path, expected_hash, "missing"))
            continue

        # read_bytes + decode: no universal-newline translation, so CRLF drift
        # hashes differently — check must see exactly what build compares.
        actual = item.output_path.read_bytes().decode("utf-8")
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

    ui.say("All generated files and manifest are up to date.", style="ok")
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


# Bump only on breaking changes to the `basicly status --json` payload shape —
# fleet loops key on it to detect a schema they do not understand.
STATUS_SCHEMA_VERSION = 1


def _status_report(repo_root: Path, paths: ProjectPaths) -> dict[str, Any]:
    """Assemble the read-only status snapshot; must never write anything."""
    try:
        authoring = bundled_catalog_root().resolve() == (repo_root / paths.core_root).resolve()
    except FileNotFoundError:
        authoring = False

    install_state = None
    state_error: str | None = None
    try:
        install_state = state.read_install_state(repo_root / paths.state_path)
    except ValidationError as exc:
        state_error = str(exc)
    core_drift = (
        state.core_drift(install_state, repo_root / paths.core_root) if install_state else []
    )

    # Same comparison `basicly check` fails on, reported here without failing.
    fragments, targets = _load_context(repo_root, paths)
    planned = plan_outputs(fragments, targets, repo_root)
    stale_outputs: list[str] = []
    expected_manifest_outputs: dict[str, dict[str, Any]] = {}
    for item in planned:
        content = _render_planned(repo_root, paths, item)
        rel_path = item.output_path.relative_to(repo_root).as_posix()
        expected_hash = sha256_of_text(content)
        expected_manifest_outputs[rel_path] = {
            "hash": expected_hash,
            "source_fragments": [f.id for f in item.fragments],
        }
        if (
            not item.output_path.exists()
            or sha256_of_text(item.output_path.read_bytes().decode("utf-8")) != expected_hash
        ):
            stale_outputs.append(rel_path)

    manifest_path = repo_root / paths.manifest_path
    existing_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = {}
    manifest_stale = existing_manifest.get("outputs") != expected_manifest_outputs

    # Per-manager hook state, composed exactly like `basicly hooks-check`.
    selection = load_technology_selection(repo_root)
    all_specs = load_hook_specs()
    selected = selected_hook_specs(all_specs, selection)
    hooks_relpath = _core_hooks_dir(paths).as_posix()

    claude_selected = claude_hook_specs(selected)
    excluded_agent_specs = [
        spec for spec in claude_hook_specs(all_specs) if spec not in claude_selected
    ]
    claude_mismatches = claude_settings.agent_hook_mismatches(
        repo_root, claude_selected, hooks_relpath
    ) + claude_settings.excluded_agent_hooks_present(repo_root, excluded_agent_specs, hooks_relpath)

    stages = hook_stages(selected)

    # Permissions deny-list state, reported like the per-manager hook state.
    deny_patterns = permissions.claude_deny_patterns(permissions.load_deny_rules())
    permission_mismatches = claude_settings.permission_deny_mismatches(repo_root, deny_patterns)

    fragment_overlays = sum(1 for fragment in fragments if fragment.source == "user")
    agent_overlays = sum(
        1
        for agent in agents.discover_agents(agents.default_agent_roots(repo_root))
        if agent.source == "user"
    )

    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "engine_version": __version__,
        "repo_kind": "authoring" if authoring else "consumer",
        "catalog": {
            "installed_version": install_state.basicly_version if install_state else None,
            "installed_at": install_state.installed_at if install_state else None,
            "state_error": state_error,
        },
        "drift": {
            "stale_outputs": stale_outputs,
            "manifest_stale": manifest_stale,
            "core_drift": [{"path": rel_path, "reason": reason} for rel_path, reason in core_drift],
        },
        "hooks": {
            "git": {
                "selected_specs": len(git_hook_specs(selected)),
                "mismatches": len(check_hooks(repo_root, _core_hooks_dir(paths), selection)),
                "stages": stages,
                "missing_stages": missing_hook_installations(repo_root, stages),
            },
            # `host`/`surface_present` only for the agent managers: an agent hook is
            # inert unless its host runs on this machine, so status reports the tier
            # delivered instead of implying parity with the git floor (basicly-0p8n).
            "claude": {
                "selected_specs": len(claude_selected),
                "mismatches": len(claude_mismatches),
                "host": AGENT_HOOK_HOSTS["claude"],
                "surface_present": agent_hook_surface_present("claude"),
            },
            "copilot": {
                "selected_specs": len(copilot_hook_specs(selected)),
                "mismatches": len(
                    check_copilot_hooks(repo_root, _core_hooks_dir(paths), selection)
                ),
                "host": AGENT_HOOK_HOSTS["copilot"],
                "surface_present": agent_hook_surface_present("copilot"),
            },
        },
        "permissions": {
            "claude": {
                "managed_patterns": len(deny_patterns),
                "mismatches": len(permission_mismatches),
            },
        },
        "technologies": sorted(selection) if selection is not None else None,
        "overlays": {"fragments": fragment_overlays, "agents": agent_overlays},
    }


def _say_status_catalog(report: dict[str, Any]) -> None:
    """Print the repo-kind and installed-vs-engine catalog lines."""
    catalog = report["catalog"]
    if report["repo_kind"] == "authoring":
        ui.say("repo: authoring (catalog is the live bundled source; no install state)")
        return
    ui.say("repo: consumer")
    if catalog["state_error"] is not None:
        ui.warn(f"catalog: {catalog['state_error']}")
    elif catalog["installed_version"] is None:
        ui.say("catalog: no install state recorded; run `basicly install`", style="warn")
    else:
        match = catalog["installed_version"] == report["engine_version"]
        note = "matches engine" if match else "run `basicly install` to upgrade"
        ui.say(
            f"catalog: installed by basicly {catalog['installed_version']} "
            f"at {catalog['installed_at']} ({note})",
            style="ok" if match else "warn",
        )


def _say_agent_hook_tier(report: dict[str, Any]) -> None:
    """Name the agent-hook tier this machine actually delivers, active and unavailable.

    Stated rather than left to the reader, because the hosts differ and the difference
    is invisible in a projected file that is present either way (basicly-0p8n).
    """
    managers = ("claude", "copilot")
    active = [name for name in managers if report["hooks"][name]["surface_present"]]
    absent = [name for name in managers if not report["hooks"][name]["surface_present"]]
    delivered = f"active on {', '.join(active)}" if active else "no surface active here"
    if not absent:
        ui.say(f"agent hooks: {delivered}; the git hooks stay the commit-time floor", style="ok")
        return
    ui.say(
        f"agent hooks: {delivered}; unavailable on {', '.join(absent)} (host not on PATH), "
        "so there only the commit-time git hooks gate",
        style="warn",
    )


def _fleet_status(repo_root: Path) -> dict[str, Any]:
    """A single repo's status snapshot, loading that repo's own project paths."""
    return _status_report(repo_root, load_project_paths(repo_root))


def cmd_status(args: argparse.Namespace) -> int:
    """Read-only repo snapshot: versions, drift, hooks, selection, overlays."""
    repo_root = _repo_root()
    if args.fleet:
        root = Path(args.root).expanduser() if args.root else repo_root.parent
        print(json.dumps(fleet.fleet_report(root, _fleet_status), indent=2))
        return 0
    paths = load_project_paths(repo_root)
    report = _status_report(repo_root, paths)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    ui.say(f"engine: basicly {report['engine_version']}")
    _say_status_catalog(report)

    drift = report["drift"]
    stale_count = len(drift["stale_outputs"]) + (1 if drift["manifest_stale"] else 0)
    if stale_count:
        ui.say(f"drift: {stale_count} stale generated file(s); run `basicly build`", style="warn")
    else:
        ui.say("drift: generated files up to date", style="ok")
    if drift["core_drift"]:
        ui.say(
            f"drift: {len(drift['core_drift'])} managed core file(s) differ from the "
            "installed snapshot",
            style="warn",
        )

    rows = []
    for manager in ("git", "claude", "copilot"):
        entry = report["hooks"][manager]
        projection = "in sync" if entry["mismatches"] == 0 else f"{entry['mismatches']} stale"
        if manager == "git":
            activation = (
                "missing: " + ", ".join(entry["missing_stages"])
                if entry["missing_stages"]
                else "installed"
            )
        else:
            activation = (
                "active" if entry["surface_present"] else f"unavailable ({entry['host']} absent)"
            )
        rows.append([manager, str(entry["selected_specs"]), projection, activation])
    ui.table("Hooks", ["manager", "specs", "projection", "activation"], rows)
    _say_agent_hook_tier(report)

    technologies = report["technologies"]
    if technologies is None:
        ui.say("technologies: all (no selection recorded)")
    else:
        ui.say(f"technologies: {', '.join(technologies)}")
    overlays = report["overlays"]
    ui.say(f"overlays: {overlays['fragments']} fragment(s), {overlays['agents']} agent(s)")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Per-agent health scoring and behavioral drift from run-records (read-only)."""
    repo_root = _repo_root()
    if args.window < 1:
        ui.warn("--window must be at least 1")
        return 2
    if args.fleet:
        root = Path(args.root).expanduser() if args.root else repo_root.parent
        print(json.dumps(health.fleet_health(root, window=args.window), indent=2))
        return 0
    report = health.health_report(repo_root, window=args.window)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    agents = report["agents"]
    if not agents:
        ui.say("health: no run-records yet", style="ok")
        return 0
    rows = [
        [
            agent["agent"],
            str(agent["runs"]),
            f"{agent['failure_rate']:.0%}",
            f"{agent['rework_rate']:.0%}",
            f"{agent['health_score']:.2f}",
        ]
        for agent in agents
    ]
    ui.table("Agent health", ["agent", "runs", "fail", "rework", "score"], rows)
    if report["regressions"]:
        ui.say(
            f"drift: behavioral regression flagged for {', '.join(report['regressions'])} "
            f"(recent {args.window}-run failure rate up ≥ {health.REGRESSION_DELTA:.0%})",
            style="warn",
        )
    else:
        ui.say("drift: no behavioral regression against the rolling baseline", style="ok")
    return 0


# --- Session orientation (basicly-askx4j) ------------------------------------
#
# The replacement for a hand-written handover: every line is derived, so there is no
# file to remember to update and none to distrust. D-42 records the analysis behind
# what that can and cannot carry.

# The document whose decision-record index (`architecture.md` §38) carries one status
# cell per decision. Read rather than re-graded: `conventions.md` §3 makes a `[TARGET]`
# passage the document's own statement that it specifies instead of reporting, and a
# second grading in engine code would be a second answer to one question.
DECISION_RECORDS_DOC = Path("docs") / "architecture" / "architecture.md"

# One `architecture.md` §38 row: `| D-01 | Authority is asymmetric | accepted | ... |`.
_DECISION_ROW = re.compile(r"^\| (D-\d+) \| (.+?) \| (.+?) \| (.+?) \|[ \t]*$", re.MULTILINE)

# A decision the tree fully holds says exactly this. Every other cell — `**proposed**`,
# `**superseded by D-36**`, `accepted, unbuilt` — names something the tree does not hold,
# which is what a session about to change the tree needs in front of it.
DECISION_IN_FORCE = "accepted"

# Deliberately wider than either grant marker (`... grant level=L3`, `... grant revoked`):
# this only chooses which records `policy.active_grant` is then asked about, so a filter
# that is too wide costs one comment walk and one that is too narrow hides a live grant.
_GRANT_MARKER = f"{policy.MARKER} grant"

# The engine's own closed test, spelled as `policy._issue_is_closed` spells it. Used here
# only to skip a walk `active_grant` would answer None for anyway — that function stays the
# authority on whether a grant is live.
_CLOSED_STATUS = "closed"

# The tag a session writes at the head of its closing note on the root it worked, and the
# discriminator `_latest_handover` reads. The producer writes it (`session-finish`), so an
# untagged note is a note and not a handover that the report missed. D-42's rule made
# concrete: what the next session needs is written to the ledger, on a record, stamped by
# the event that carries it — never to a file beside the repository.
HANDOVER_MARKER = "[session handover"

# Rows per table before the report says how many more there are and which command prints
# them. The ready set alone is 208 records [measured 2026-08-28, `basicly tracker ready`],
# which is a backlog dump rather than an orientation.
SESSION_ROWS = 10


def _decision_targets(repo_root: Path) -> dict[str, Any]:
    """The decision records whose `architecture.md` §38 status cell is not ``accepted``.

    The one section not derived from the ledger, because a decision record is not a
    tracker record: 41 of them live in the architecture document and 13 carry a status
    that is not ``accepted`` [measured 2026-08-28]. The status cell travels verbatim, so
    the reader gets the document's own words rather than this function's reading of them.
    """
    path = repo_root / DECISION_RECORDS_DOC
    if not path.is_file():
        return {"present": False, "document": DECISION_RECORDS_DOC.as_posix(), "records": []}
    rows = _DECISION_ROW.findall(path.read_text(encoding="utf-8"))
    targets = [
        {"record": record, "title": title.strip(), "status": status.strip()}
        for record, title, status, _governs in rows
        if status.strip() != DECISION_IN_FORCE
    ]
    return {
        "present": True,
        "document": DECISION_RECORDS_DOC.as_posix(),
        "decisions": len(rows),
        "records": targets,
    }


def _latest_handover(repo_root: Path) -> dict[str, Any]:
    """The newest note tagged :data:`HANDOVER_MARKER`, on whichever record carries it.

    Where the last session stopped: the one line the derived report could not print, and
    the reason a hand-written file survived D-42 as a pointer at this note. Newest by the
    event's own stamp rather than by any date the text claims, so a session that closed
    on a different root than the one before still wins.
    """
    latest: dict[str, Any] | None = None
    for record, rows in tracker.all_comment_rows(repo_root).items():
        for row in rows:
            text = str(row[tracker.COMMENT_TEXT_KEY])
            if not text.startswith(HANDOVER_MARKER):
                continue
            stamp = str(row[comment_rows.STAMP_KEY])
            if latest is None or stamp > latest["at"]:
                latest = {"record": record, "at": stamp, "text": text}
    return {"present": latest is not None, "marker": HANDOVER_MARKER, "note": latest}


def _live_grants(repo_root: Path, views: dict[str, Any]) -> dict[str, Any]:
    """Every live autonomy grant, with what is left of it where this checkout can tell.

    A grant is a marker on its root and dies when that root closes, so the population is
    every open record carrying one — 18 of 1154 live records here [measured 2026-08-28,
    `basicly session start --json`]. The marker scan is one whole-tracker read;
    :func:`policy.active_grant` then answers which of those are live, because
    last-marker-wins and the closed-root rule are its contract and not this function's.

    ``remaining`` is ``None`` where the checkout cannot see the spend rather than the
    budget: no dispatch under the root is a spend nobody can report, where a full budget
    left would be a number this checkout does not have (:func:`board_facts.grant_split`).

    The two spend stores are read once for the whole table rather than once per grant,
    which is 5.18 s of the 6.14 s this command took over eighteen of them [measured
    2026-09-01]. Each row carries what each store says as well as the figure drawn from
    both, because a checkout whose file and ledger disagree must not be shown one of them.
    """
    texts = tracker.all_comment_texts(repo_root)
    sources = board_facts.spend_sources(repo_root)
    rows: list[dict[str, Any]] = []
    for record in sorted(views):
        if str(getattr(views[record], "status", "")) == _CLOSED_STATUS:
            continue
        if not any(text.strip().startswith(_GRANT_MARKER) for text in texts.get(record, ())):
            continue
        grant = policy.active_grant(repo_root, record)
        if grant is None:
            continue
        split = board_facts.grant_split(repo_root, record, grant, sources=sources)
        spent = None if split is None else split.tokens
        budget = grant.token_budget
        remaining = None if spent is None or budget is None else budget - spent
        rows.append({
            "record": record,
            "level": grant.level,
            "budget": budget,
            "spent": spent,
            "remaining": remaining,
            "spent_local": None if split is None else split.local,
            "spent_ledger": None if split is None else split.ledger,
        })
    return {"count": len(rows), "records": rows}


def _session_report(repo_root: Path) -> dict[str, Any]:
    """The orientation snapshot, assembled from readers that all already exist.

    Read-only by construction: every call below is a fold or a file read, and the
    command's whole contract is that running it changes nothing.

    ``tracker.present`` is False for a repository with no owned ledger — a consumer that
    installed the harness and has not filed anything reaches this before it reaches an
    empty ready set, and the two need different sentences.
    """
    report: dict[str, Any] = {"decisions": _decision_targets(repo_root)}
    try:
        views = tracker.all_views(repo_root)
    except owned_store.TrackerDivergenceError, OSError, ValueError:
        report["tracker"] = {"present": False, "records": 0}
        return report
    report["tracker"] = {"present": True, "records": len(views)}
    if not views:
        return report
    ready = tracker_query.ready_report(repo_root)
    blocked = tracker_query.blocked_report(repo_root)
    report["handover"] = _latest_handover(repo_root)
    report["ready"] = ready
    report["blocked"] = blocked
    report["grants"] = _live_grants(repo_root, views)
    report["tracker"].update({"ready": ready["count"], "blocked": blocked["count"]})
    return report


def _say_session_handover(report: dict[str, Any]) -> None:
    """Where the last session stopped, before anything ranked — or that no session said."""
    handover = report["handover"]
    note = handover["note"]
    if note is None:
        ui.say(
            f"handover: none - no note starts with `{handover['marker']}`; "
            "the `session-finish` skill writes one on the root the session worked"
        )
        return
    ui.say(f"Handover ({note['record']}, {note['at']}) - `basicly tracker show {note['record']}`")
    ui.say(f"        {note['text']}")


def _say_session_ready(report: dict[str, Any], rows: int) -> None:
    """The ranked ready set, with the ranking policy that produced it in the title."""
    ready = report["ready"]
    if not ready["count"]:
        ui.say("ready: none - every open record is blocked or already in flight")
        return
    ui.table(
        f"Ready ({ready['count']}, {ready['sort']})",
        ["rank", "score", "record", "title"],
        [
            [str(row["rank"]), str(row["score"]), str(row["record"]), str(row["title"])]
            for row in ready["records"][:rows]
        ],
    )
    if ready["count"] > rows:
        ui.say(f"        {ready['count'] - rows} more: `basicly tracker ready`")


def _say_session_blocked(report: dict[str, Any], rows: int) -> None:
    """What is not ready and what holds it — an empty blocker list means its children do."""
    blocked = report["blocked"]
    if not blocked["count"]:
        ui.say("blocked: none - nothing dispatchable is waiting on another record")
        return
    ui.table(
        f"Blocked ({blocked['count']})",
        ["record", "status", "blocked by", "children"],
        [
            [
                str(row["record"]),
                str(row["status"]),
                ", ".join(f"{held['record']} ({held['status']})" for held in row["blocked_by"]),
                str(len(row["children"])) if row["children"] else "",
            ]
            for row in blocked["records"][:rows]
        ],
    )
    if blocked["count"] > rows:
        ui.say(f"        {blocked['count'] - rows} more: `basicly tracker blocked`")


def _say_session_grants(report: dict[str, Any]) -> None:
    """Live grants and their remaining budget, saying so where the spend is unknowable."""
    grants = report["grants"]
    if not grants["count"]:
        ui.say("grants: none live - every dispatch needs `basicly policy grant` first")
        return
    ui.table(
        f"Grants ({grants['count']} live)",
        ["root", "level", "budget", "spent", "remaining"],
        [
            [
                str(row["record"]),
                str(row["level"]),
                "-" if row["budget"] is None else f"{row['budget']:,}",
                "unknown" if row["spent"] is None else f"{row['spent']:,}",
                "unknown" if row["remaining"] is None else f"{row['remaining']:,}",
            ]
            for row in grants["records"]
        ],
    )
    if any(row["spent"] is None for row in grants["records"]):
        ui.say("        spend is unknown where this checkout holds no dispatch for the root")
    for row in grants["records"]:
        # Both figures, never the one this checkout happens to prefer: the file holds
        # dispatches not yet committed and the ledger holds what other machines ran, and a
        # single number would make one of those invisible.
        if row["spent_local"] is not None and row["spent_local"] != row["spent_ledger"]:
            ui.say(
                f"        {row['record']}: run records say {row['spent_local']:,} and the "
                f"committed ledger says {row['spent_ledger']:,}; spent counts both once"
            )


def _say_session_decisions(report: dict[str, Any]) -> None:
    """Decision records the tree does not fully hold, each with the document's own status."""
    decisions = report["decisions"]
    if not decisions["present"]:
        ui.say(f"decisions: no {decisions['document']} in this repository")
        return
    ui.table(
        f"Decision targets ({len(decisions['records'])} of {decisions['decisions']}, "
        f"status is not `{DECISION_IN_FORCE}`)",
        ["record", "status", "title"],
        [
            [str(row["record"]), str(row["status"]), str(row["title"])]
            for row in decisions["records"]
        ],
    )


def cmd_session_start(args: argparse.Namespace) -> int:
    """Print the read-only orientation a session starts from (D-42).

    The command that makes a hand-written handover unnecessary: what is ready and why it
    ranks there, what is blocked and by what, which grants are live and what is left of
    them, and which decisions the tree does not yet hold. Nothing here is authored — a
    line the ledger stops supporting stops being printed.

    It costs one fold per reader plus two more per granted root, and those dominate: 5.9 s
    over 1154 records and 18 grants [measured 2026-08-28, `cli.main(["session", "start"])`
    under `time.monotonic`]. A once-a-session price for a report nothing else assembles,
    and the way to lower it is a cached ledger fold under `tracker` — `active_grant` reads
    the record and its comments per root, and no reader caches either today.
    """
    repo_root = _repo_root()
    report = _session_report(repo_root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if not report["tracker"]["present"]:
        ui.say("ledger: none - this repository has no owned tracker, so no backlog to read")
        _say_session_decisions(report)
        return 0
    if not report["tracker"]["records"]:
        ui.say("ledger: empty - no records yet, so nothing is ready and nothing is blocked")
        ui.say("        `basicly tracker write -- create ...` files the first one")
        _say_session_decisions(report)
        return 0
    records = report["tracker"]["records"]
    ui.say(
        f"ledger: {records} record{'' if records == 1 else 's'}, "
        f"{report['tracker']['ready']} ready, {report['tracker']['blocked']} blocked"
    )
    _say_session_handover(report)
    _say_session_ready(report, args.rows)
    _say_session_blocked(report, args.rows)
    _say_session_grants(report)
    _say_session_decisions(report)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    """Dispatch the `basicly session` verbs."""
    return _dispatch(args, "session_command", {"start": cmd_session_start}, group="session")


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
    skill (architecture §14). This prunes exactly those, scoped to the managed
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


def _tracker_prefix(repo_root: Path) -> str:
    """Derive a beads issue-id prefix from the repo directory name.

    The commit-msg hook only accepts single-hyphen ``<prefix>-<code>`` ids with
    a lowercase alphanumeric prefix starting with a letter, so the name is
    sanitized to that shape.
    """
    prefix = re.sub(r"[^a-z0-9]", "", repo_root.name.lower())
    if not prefix or not prefix[0].isalpha():
        prefix = f"repo{prefix}"
    return prefix


def _setup_tracker(repo_root: Path) -> None:
    """Create the owned tracker's ledger directory when none exists (idempotent).

    The whole initialization: the ledger *is* the store, so there is nothing to install
    and no process to run (basicly-vkh0.42.7). The directory is created rather than left
    to the first write because its presence is what opts a repository in
    (`tracker_usage.is_enabled`), and because a consumer needs somewhere to commit.

    The id prefix is derived and reported rather than written: only a repository that
    mints a *root* record needs one, and guessing it into a committed file would put a
    namespace in `basicly.toml` that nothing had asked for (`owned_write.create`).
    """
    ledger = repo_root / owned_store.LEDGER_DIR
    if ledger.is_dir():
        print("Tracker ledger exists; left unchanged.")
        return
    ledger.mkdir(parents=True, exist_ok=True)
    print(
        f"Initialized the tracker ledger at {owned_store.LEDGER_DIR.as_posix()}. "
        f'To mint root record ids, set [tracker] prefix = "{_tracker_prefix(repo_root)}" '
        f"in basicly.toml."
    )


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


def _report_missing_config_sections(repo_root: Path) -> None:
    """Name shipped-default sections absent from an existing basicly.toml.

    Install never edits a consumer's config, so a file scaffolded by an older
    basicly silently lacks sections added since; the hint is the upgrade path.
    """
    existing = tomllib.loads((repo_root / CONFIG_FILE).read_text(encoding="utf-8"))
    shipped = tomllib.loads(DEFAULT_CONFIG_TOML)
    missing = [name for name in shipped if name not in existing]
    if not missing:
        return
    rendered = ", ".join(f"[{name}]" for name in missing)
    print(
        f"Note: {CONFIG_FILE} lacks section(s) the shipped default now carries: "
        f"{rendered}. Install never edits your file — copy what you need from the "
        f"default scaffold, or override per machine in the gitignored {LOCAL_CONFIG_FILE}."
    )


def ignore_covers_local_config(ignore_text: str) -> bool:
    """True when *ignore_text* already excludes the per-machine config overlay.

    Public because the property is dual-use: ``basicly install`` scaffolds it
    into a consumer, and basicly's own repo has to satisfy it too — a guarantee
    the harness gives consumers and not itself is exactly the gap dogfooding
    exists to catch (basicly-jr0l.7). One predicate, so the two cannot drift.
    """
    return any(line.strip().lstrip("/") == LOCAL_CONFIG_FILE for line in ignore_text.splitlines())


def _scaffold_local_config_ignore(repo_root: Path) -> None:
    """Ensure .gitignore covers the per-machine config overlay (append-only).

    An existing ignore file gains the one entry when missing; nothing else in
    it is touched.
    """
    ignore_path = repo_root / ".gitignore"
    text = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    if ignore_covers_local_config(text):
        return
    prefix = "" if not text or text.endswith("\n") else "\n"
    entry = f"# Per-machine basicly overrides; harness keys win over {CONFIG_FILE}.\n"
    ignore_path.write_text(text + prefix + entry + LOCAL_CONFIG_FILE + "\n", encoding="utf-8")
    print(f"Added {LOCAL_CONFIG_FILE} to .gitignore")


def _validate_install_technologies(raw: str | None) -> list[str] | None:
    """Parse and vet ``--technologies`` before any install work runs; None on bad input.

    Kept separate from recording the selection so a rejected flag exits before
    the first file write, not after cmd_install's sync/scaffold steps have run.
    """
    if raw is None:
        return []
    technologies = [item.strip() for item in raw.split(",") if item.strip()]
    if not technologies:
        print("--technologies requires at least one value", file=sys.stderr)
        return None
    unknown = sorted(set(technologies) - TECHNOLOGIES)
    if unknown:
        print(
            f"Unknown technology value(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(TECHNOLOGIES))}",
            file=sys.stderr,
        )
        return None
    return technologies


def _record_install_technologies(repo_root: Path, technologies: list[str]) -> bool:
    """Record a pre-validated ``--technologies`` selection in basicly.toml."""
    if not technologies:
        return True
    try:
        record_technology_selection(repo_root, technologies)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return False
    print(f"Recorded technology selection in {CONFIG_FILE}: {', '.join(sorted(set(technologies)))}")
    return True


def cmd_install(args: argparse.Namespace) -> int:
    """Converge a consumer repo: sync core, scaffold, and project everything.

    One idempotent command covers first install and every upgrade (architecture
    §9): sync the managed core to the bundled catalog (provenance-guarded, so
    hand-edits are never silently clobbered), migrate/prune legacy layouts,
    scaffold the overlay + config (never overwriting user content), then
    project fragments, skills, agents, and hooks. Re-running from a newer
    pinned ref is the upgrade path.
    """
    technologies = _validate_install_technologies(getattr(args, "technologies", None))
    if technologies is None:
        return 1

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
        _report_missing_config_sections(repo_root)
    else:
        config_path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        print(f"Wrote {CONFIG_FILE}")
    _scaffold_local_config_ignore(repo_root)

    if not _record_install_technologies(repo_root, technologies):
        return 1

    _setup_tracker(repo_root)
    _scaffold_vscode_tasks(repo_root)
    _scaffold_ci_workflow(repo_root)

    steps: list[tuple[str, Any, argparse.Namespace]] = [
        ("build", cmd_build, argparse.Namespace(target=None, verify=False)),
        (
            "skills-build",
            cmd_skills_build,
            argparse.Namespace(roots=None, all_default_roots=True),
        ),
        ("agents-build", cmd_agents_build, argparse.Namespace()),
        ("hooks-build", cmd_hooks_build, argparse.Namespace(no_install=False)),
        ("permissions-build", cmd_permissions_build, argparse.Namespace()),
    ]
    for step, handler, namespace in steps:
        ui.heading(f"\n== basicly {step} ==")
        rc = handler(namespace)
        if rc != 0:
            print(f"basicly install: {step} failed (exit {rc})", file=sys.stderr)
            return rc

    ui.say(
        "\nbasicly install complete: repo converged. Re-run the same command to upgrade.",
        style="ok",
    )
    return 0


def _sweepable_path(repo_root: Path, rel: str) -> Path | None:
    """The un-resolved in-repo path for a manifest entry, or None when unsafe.

    The entry itself is unlinked — never its symlink target — and anything
    absolute, traversing (``..``), or under ``.git/`` is refused: a manifest
    entry must not be able to delete repository internals or out-of-repo
    files through a planted link.
    """
    entry = Path(rel)
    if entry.is_absolute() or ".." in entry.parts or not entry.parts:
        return None
    if entry.parts[0] == ".git":
        return None
    candidate = repo_root / entry
    if not candidate.parent.resolve().is_relative_to(repo_root.resolve()):
        return None
    return candidate


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
        target = _sweepable_path(repo_root, rel)
        if target is None:
            print(f"Note: skipping unsafe manifest entry: {rel}", file=sys.stderr)
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
            removed += 1
            print(f"Removed {rel}")
            _remove_empty_parents(target.parent.resolve(), resolved_root)

    manifest_path.unlink()
    print(f"Removed {_format_path(manifest_path, repo_root)}")
    return removed + 1


def _remove_generated_skills(repo_root: Path, roots: tuple[Path, ...]) -> int:
    """Delete projected skill directories under *roots* (generated-marker SKILL.md only).

    A basicly-projected skill directory bundles the rendered SKILL.md plus any
    hand-authored resources (references/scripts/assets); the whole directory is
    removed when its SKILL.md carries the generated marker. A user's own skill
    directory (no marker) is left untouched.
    """
    removed = 0
    for root in roots:
        base = repo_root / root
        if not base.is_dir():
            continue
        for skill_md in sorted(base.rglob(SKILL_FILE_NAME)):
            if GENERATED_MARKER not in skill_md.read_text(encoding="utf-8"):
                continue
            skill_dir = skill_md.parent
            shutil.rmtree(skill_dir)
            removed += 1
            print(f"Removed {_format_path(skill_dir, repo_root)}")
            _remove_empty_parents(skill_dir.parent, repo_root)
    return removed


def _remove_projected_skills(repo_root: Path) -> int:
    """Delete projected SKILL.md files (generated marker only; user skills stay)."""
    return _remove_generated_skills(repo_root, (*DEFAULT_SKILL_ROOTS, *RETIRED_SKILL_ROOTS))


def _remove_projected_agents(repo_root: Path) -> int:
    """Delete projected agent files in every output root (generated marker only)."""
    removed = 0
    for out_root in agents.AGENTS_OUTPUT_ROOTS:
        base = repo_root / out_root.path
        if not base.is_dir():
            continue
        for agent_md in sorted(base.glob(f"*{out_root.suffix}")):
            if agents.GENERATED_MARKER not in agent_md.read_text(encoding="utf-8"):
                continue
            agent_md.unlink()
            removed += 1
            print(f"Removed {_format_path(agent_md, repo_root)}")
            _remove_empty_parents(agent_md.parent, repo_root)
    return removed


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
    generated files the manifest lists, projected skills and agents
    (generated-marker files only), and the managed pre-commit block. The
    overlay and ``basicly.toml`` are the user's and survive unless ``--purge``.
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
    removed += _remove_projected_agents(repo_root)

    note = remove_managed_hooks(repo_root)
    if note:
        removed += 1
        print(note)

    if claude_settings.remove_agent_hooks(
        repo_root, claude_hook_specs(load_hook_specs()), _core_hooks_dir(paths).as_posix()
    ):
        removed += 1
        print(f"Removed managed agent hooks from {claude_settings.CLAUDE_SETTINGS_PATH}")

    copilot_removed = remove_copilot_hooks(repo_root)
    if copilot_removed:
        removed += copilot_removed
        print(f"Removed {copilot_removed} managed Copilot hook file(s) from .github/hooks/")

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
        ui.say("basicly uninstall complete.", style="ok")
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
    selection = load_technology_selection(repo_root)
    config_path = repo_root / ".pre-commit-config.yaml"
    config_existed = config_path.exists()
    result = sync_hooks(repo_root, _core_hooks_dir(paths), selection)

    rewrite_note = None
    if config_existed and config_path in result.written:
        rewrite_note = (
            "Note: .pre-commit-config.yaml was rewritten to update managed hooks; "
            "comments/formatting outside them may have been normalized."
        )
    _report_sync(result, repo_root, noun="hook files", label="Hooks", extra_note=rewrite_note)

    all_agent_specs = claude_hook_specs(load_hook_specs())
    agent_specs = selected_hook_specs(all_agent_specs, selection)
    excluded_agent_specs = [spec for spec in all_agent_specs if spec not in agent_specs]
    if all_agent_specs:
        hooks_relpath = _core_hooks_dir(paths).as_posix()
        if claude_settings.sync_agent_hooks(
            repo_root, agent_specs, hooks_relpath, excluded_agent_specs
        ):
            print(f"Wrote {claude_settings.CLAUDE_SETTINGS_PATH} (managed agent hooks)")
        else:
            print(f"Agent hooks in {claude_settings.CLAUDE_SETTINGS_PATH} are up to date.")

    copilot_result = sync_copilot_hooks(repo_root, _core_hooks_dir(paths), selection)
    _report_sync(copilot_result, repo_root, noun="copilot hook files", label="Copilot hooks")

    stages = hook_stages(selected_hook_specs(load_hook_specs(), selection))
    if getattr(_args, "no_install", False):
        stage_flags = " ".join(f"-t {stage}" for stage in stages)
        print(
            "Skipped activation (--no-install). Run "
            f"`uvx pre-commit install --install-hooks {stage_flags}`."
        )
        return 0

    ok, message = install_hooks(repo_root, stages)
    if ok:
        print(f"Activated git hooks for stages: {', '.join(stages)}.")
    else:
        print(f"Could not auto-activate git hooks: {message}", file=sys.stderr)
    if not any((repo_root / owned_store.LEDGER_DIR).glob("events-*.jsonl")):
        print(
            f"Note: no tracker found ({owned_store.LEDGER_DIR.as_posix()}/); the "
            f"tracker-commit-msg hook will skip its issue-id check. Create a first "
            f"record with `basicly tracker write -- create ...`."
        )
    return 0


HOOKS_WIRING_REMEDY = "Stale hook projection detected. Run `basicly hooks-build` to sync hooks."
# `hooks-build` deliberately does not copy hook scripts (they are core content owned
# by `basicly install`, provenance-guarded), so naming it here sent the reader at a
# command that cannot fix a script mismatch — and `basicly install` overwrites the
# local script, which for a deliberate hook-script edit destroys the change while
# turning the gate green. Say which command actually applies, and to what
# (basicly-9o6s).
HOOKS_SCRIPT_REMEDY = (
    "Hook scripts differ from the installed basicly catalog; `basicly hooks-build` does not "
    "copy hook scripts and will not fix this. `basicly install` re-materializes them from the "
    "installed catalog, overwriting the local copies — so if the local version is the change "
    "you want, edit the catalog source it is built from instead of running it."
)


def _hooks_stale_message(mismatches: list[tuple[Path, str]], core_hooks_dir: Path) -> str:
    """Pick the hooks-check remedy from what actually drifted: scripts, wiring, or both."""
    resolved = core_hooks_dir.resolve()
    scripts = [path for path, _ in mismatches if path.resolve().is_relative_to(resolved)]
    if not scripts:
        return HOOKS_WIRING_REMEDY
    if len(scripts) == len(mismatches):
        return HOOKS_SCRIPT_REMEDY
    return f"{HOOKS_SCRIPT_REMEDY} The remaining wiring drift is fixed by `basicly hooks-build`."


def cmd_hooks_check(_args: argparse.Namespace) -> int:
    """Check that projected hooks and their wiring are up to date."""
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    selection = load_technology_selection(repo_root)
    mismatches = check_hooks(repo_root, _core_hooks_dir(paths), selection)

    all_agent_specs = claude_hook_specs(load_hook_specs())
    agent_specs = selected_hook_specs(all_agent_specs, selection)
    excluded_agent_specs = [spec for spec in all_agent_specs if spec not in agent_specs]
    settings_path = repo_root / claude_settings.CLAUDE_SETTINGS_PATH
    for reason in claude_settings.agent_hook_mismatches(
        repo_root, agent_specs, _core_hooks_dir(paths).as_posix()
    ):
        mismatches.append((settings_path, reason))
    for reason in claude_settings.excluded_agent_hooks_present(
        repo_root, excluded_agent_specs, _core_hooks_dir(paths).as_posix()
    ):
        mismatches.append((settings_path, reason))

    mismatches.extend(check_copilot_hooks(repo_root, _core_hooks_dir(paths), selection))

    if _report_mismatches(
        mismatches,
        repo_root,
        stale_message=_hooks_stale_message(mismatches, repo_root / _core_hooks_dir(paths)),
    ):
        return 1

    # Advisory (non-fatal): projected files can be in sync yet the gates inert
    # because pre-commit was never installed — the exact gap behind unguarded
    # commits. Report it without failing, since CI runs the scripts directly and
    # does not install git hooks.
    missing = missing_hook_installations(
        repo_root, hook_stages(selected_hook_specs(load_hook_specs(), selection))
    )
    if missing:
        stage_flags = " ".join(f"-t {stage}" for stage in missing)
        remedy = (
            f"Run `basicly hooks-build` or `uvx pre-commit install --install-hooks "
            f"{stage_flags}` to activate them locally."
        )
        if PRE_PUSH_STAGE in missing:
            # `pre-commit install` alone would leave this note firing: the pre-push hook
            # additionally carries the ledger guard, which only `hooks-build` writes.
            remedy = (
                "Run `basicly hooks-build` to activate them locally — pre-push also carries "
                "a ledger guard that `pre-commit install` does not write."
            )
        print(
            f"Note: git hooks are not installed for stages: {', '.join(missing)}. {remedy}",
            file=sys.stderr,
        )

    # Advisory (non-fatal): every projected hook entry runs `uv run python ...`,
    # so a committer without uv hits an opaque failure at commit time — diagnose
    # it here, at check time, instead.
    if shutil.which("uv") is None:
        print(
            "Note: uv is not on PATH. The projected git hooks run `uv run python ...`, "
            "so every committer to this repo needs uv (and Python 3.14+) installed — "
            "without it, commits fail with a command-not-found error. "
            "Install uv: https://docs.astral.sh/uv/",
            file=sys.stderr,
        )

    ui.say("Projected hooks are up to date.", style="ok")
    return 0


def cmd_permissions_build(_args: argparse.Namespace) -> int:
    """Project the catalog deny-list into the agent permissions config."""
    repo_root = _repo_root()
    patterns = permissions.claude_deny_patterns(permissions.load_deny_rules())
    if claude_settings.sync_permission_deny(repo_root, patterns):
        print(f"Wrote {claude_settings.CLAUDE_SETTINGS_PATH} (managed permissions deny-list)")
    else:
        print(f"Permissions deny-list in {claude_settings.CLAUDE_SETTINGS_PATH} is up to date.")
    return 0


def cmd_permissions_check(_args: argparse.Namespace) -> int:
    """Check that the projected permissions deny-list is up to date."""
    repo_root = _repo_root()
    patterns = permissions.claude_deny_patterns(permissions.load_deny_rules())
    settings_path = repo_root / claude_settings.CLAUDE_SETTINGS_PATH
    mismatches = [
        (settings_path, reason)
        for reason in claude_settings.permission_deny_mismatches(repo_root, patterns)
    ]
    if _report_mismatches(
        mismatches,
        repo_root,
        stale_message=(
            "Stale permissions projection detected. "
            "Run `basicly permissions-build` to sync the deny-list."
        ),
    ):
        return 1
    ui.say("Projected permissions deny-list is up to date.", style="ok")
    return 0


def _resolve_skill_output_roots(args: argparse.Namespace, repo_root: Path) -> list[Path]:
    roots_arg = getattr(args, "roots", None)
    use_default_roots = bool(getattr(args, "all_default_roots", False))
    return resolve_skill_roots(
        repo_root=repo_root,
        roots=roots_arg,
        use_default_roots=use_default_roots,
    )


def _cmd_usage_lane_split(_args: argparse.Namespace) -> int:
    """Report each lane's spend split into context acquisition and implementation.

    The instrument `basicly-ejdm`'s causal claim never had (`basicly-ejdm.2`). Shares lead
    and tokens follow, because per-turn stream usage is in a different denomination from
    the run record the grant is metered in — the report says so rather than leaving a
    reader to compare two numbers that do not compare.
    """
    splits = lane_split.lane_splits(_repo_root())
    if not splits:
        ui.say("no lane transcript is persisted, so there is no split to report")
        return 0
    ui.say("claude only: no other family emits the per-tool event this reads")
    ui.say("tokens are stream-denominated and over-report the run record by 1.46x-1.79x")
    for split in splits:
        if split.unclassifiable:
            ui.say(f"{split.issue}: unclassifiable - {split.unclassifiable}")
            continue
        shares = "  ".join(
            f"{name} {split.share(name):.0%} ({split.tokens.get(name, 0)})"
            for name in (
                lane_split.ACQUISITION,
                lane_split.IMPLEMENTATION,
                lane_split.UNCLASSIFIED,
                lane_split.UNATTRIBUTED,
            )
        )
        ui.say(f"{split.issue}: {shares}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """Print the brief the loop would dispatch for one issue, without dispatching it.

    The assembler is shared rather than re-spelled: a second rendering of the brief
    would drift from the one the engine actually sends, and a preview that differs
    from the dispatch is worse than none.

    It is the base prompt plus the scope fence, and the remaining omissions are named
    because a preview that hid them would be the drift it exists to prevent: cross-lane
    records and answered decisions are folded in at dispatch time against the session's
    live bead set; a role's declared skills are prepended by
    :func:`dispatch_brief.with_skills`; and a lane that failed a gate is re-dispatched
    from a repair brief instead of this one.

    The id is checked against the tracker first, because the base prompt is a pure
    function of it: a typo renders a complete, plausible brief pointing at nothing — the
    one failure a preview exists to stop a human reading past.
    """
    repo_root = _repo_root()
    if tracker.read_record(repo_root, args.issue_id) is None:
        ui.fail(f"No tracked issue {args.issue_id}")
        return 1
    prompt = dispatch_brief.dispatch_prompt(args.issue_id)
    ui.say(contention.with_scope_fence(repo_root, args.issue_id, prompt))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    """Dispatch the usage telemetry subcommands (report / forecast / tuning)."""
    handlers = {
        "report": usage_report.cmd_report,
        "forecast": usage_report.cmd_forecast,
        "tuning": usage_report.cmd_tuning,
        "lane-split": _cmd_usage_lane_split,
        "outcomes": usage_report.cmd_outcomes,
    }
    return _dispatch(args, "usage_command", handlers, group="usage")


def cmd_tracker(args: argparse.Namespace) -> int:
    """Dispatch the owned tracker's read verbs and its cutover subcommands."""
    handlers = {"write": tracker_write.cmd_write, **tracker_query.HANDLERS}
    return _dispatch(args, "tracker_command", handlers, group="tracker")


def cmd_skills_list(_args: argparse.Namespace) -> int:
    """List skills available in the source collection."""
    repo_root = _repo_root()
    skills = discover_skills(repo_root)
    if not skills:
        print("No skills found in .basicly/core/skills")
        return 0

    ui.table(
        f"Catalog skills ({len(skills)})",
        ["slug", "technologies", "description"],
        [
            [skill.slug, ", ".join(skill.technologies) or "universal", skill.description]
            for skill in skills
        ],
    )
    return 0


def cmd_skills_build(args: argparse.Namespace) -> int:
    """Project skills from .basicly/core/skills into one or more destination roots."""
    repo_root = _repo_root()
    roots = _resolve_skill_output_roots(args, repo_root)
    result, pruned = sync_skills(repo_root, roots, selection=load_technology_selection(repo_root))
    for path in pruned:
        print(f"Removed {_format_path(path, repo_root)} (excluded by technology selection)")
    _report_sync(result, repo_root, noun="skill files", label="Skill")
    return 0


SKILLS_STALE_REMEDY = (
    "Stale skill projection detected. Run `basicly skills-build` to sync skill files."
)
SKILLS_UNMANAGED_REMEDY = (
    "Unmanaged files under a projected skills root. Move each one into a "
    "`.basicly/core/skills/<slug>/skill.yaml` source and rebuild, or delete it — "
    "`basicly skills-build` will not, since nothing describes it."
)


def _skills_stale_message(mismatches: list[tuple[Path, str]]) -> str:
    """Pick the skills-check remedy from what drifted: a rebuild fixes only projections."""
    unmanaged = sum(1 for _, reason in mismatches if reason.startswith(UNMANAGED_REASON_PREFIX))
    if not unmanaged:
        return SKILLS_STALE_REMEDY
    if unmanaged == len(mismatches):
        return SKILLS_UNMANAGED_REMEDY
    return f"{SKILLS_UNMANAGED_REMEDY} The remaining drift is fixed by `basicly skills-build`."


def cmd_skills_check(args: argparse.Namespace) -> int:
    """Check that projected skill roots are synchronized with source skills."""
    repo_root = _repo_root()
    roots = _resolve_skill_output_roots(args, repo_root)
    mismatches = check_synced_skills(
        repo_root, roots, selection=load_technology_selection(repo_root)
    )
    if _report_mismatches(mismatches, repo_root, stale_message=_skills_stale_message(mismatches)):
        return 1

    ui.say("Projected skills are up to date.", style="ok")
    return 0


# `invocation: model` is the scaffold default because it is the reversible mistake:
# a model-invoked entry that should be user-invoked only wastes a description's
# worth of context, while the opposite silently removes the entry from the agent's
# reach. Author it down to `user` — and delete the description — once the entry's
# audience is settled.
_SKILL_TEMPLATE = """\
# yaml-language-server: $schema=../../schemas/skill.schema.json
schema_version: 1
name: {slug}
invocation: model
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


def cmd_agents_list(_args: argparse.Namespace) -> int:
    """List agents available in the core and overlay sources."""
    repo_root = _repo_root()
    found = agents.discover_agents(agents.default_agent_roots(repo_root))
    if not found:
        print(f"No agents found in {agents.CORE_AGENTS_DIR}")
        return 0

    print(f"{'slug':<24} {'source':<8} description")
    print("-" * 96)
    for agent in found:
        print(f"{agent.slug:<24} {agent.source:<8} {agents.compose_description(agent)}")
    return 0


def cmd_agents_build(_args: argparse.Namespace) -> int:
    """Project agents from the core and overlay sources into every agent root.

    There is no root-selection flag on purpose (unlike `skills-build`): the roots
    are a fixed pair and `agents-check` compares both, so a partial build could
    only ever manufacture drift (basicly-8sxf).
    """
    repo_root = _repo_root()
    result, pruned = agents.sync_agents(repo_root, load_technology_selection(repo_root))
    for path in pruned:
        print(f"Removed {_format_path(path, repo_root)} (excluded by technology selection)")
    _report_sync(result, repo_root, noun="agent files", label="Agent")
    return 0


def cmd_agents_check(_args: argparse.Namespace) -> int:
    """Check that projected agents are synchronized with their sources."""
    repo_root = _repo_root()
    mismatches = agents.check_synced_agents(repo_root, load_technology_selection(repo_root))
    stale = "Stale agent projection detected. Run `basicly agents-build` to sync agent files."
    if _report_mismatches(mismatches, repo_root, stale_message=stale):
        return 1

    ui.say("Projected agents are up to date.", style="ok")
    return 0


_AGENT_TEMPLATE = """\
# yaml-language-server: $schema=../../schemas/agent.schema.json
schema_version: 1
name: {slug}
purpose: {description}
triggers: TODO when to delegate ('Use proactively after ...').
returns: TODO what it hands back, so the caller can delegate without reading dumps.
posture: Read-only.
tools: [Read, Grep, Glob]
slots:
  role:
    - text: |
        You are TODO: role plus epistemic stance, not a resume.
  startup:
    - text: |
        When invoked:

        1. TODO: the first concrete command to run.
  process:
    - text: |
        TODO: the method — checkable steps in priority order, no aspirational metrics.
  output_contract:
    - text: |
        TODO: the deliverable shape, ideally with a literal sample. If clean, say so
        in one line and stop.
  constraints:
    - text: |
        TODO: never-do list with alternatives, and what to do when blocked.
"""


def cmd_agents_new(args: argparse.Namespace) -> int:
    """Scaffold a new agent.yaml source under .basicly/core/agents/<slug>."""
    repo_root = _repo_root()
    path = repo_root / agents.CORE_AGENTS_DIR / args.slug / agents.AGENT_SOURCE_FILE
    if path.exists():
        print(f"Error: {_format_path(path, repo_root)} already exists.", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _AGENT_TEMPLATE.format(
            slug=args.slug, description=args.description or "TODO what this agent does."
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
    for warning in catalog_lint.skill_warnings(repo_root):
        print(f"catalog lint: warning: {warning}", file=sys.stderr)
    # The Tier-2 CI metric, printed whether the gate passes or fails: a floor is
    # only raisable by someone who can see how much headroom the catalog has.
    print(f"catalog lint: {routing_evals.routing_outcome(repo_root).summary()}")
    violations = catalog_lint.lint_catalog(repo_root)
    if violations:
        print("catalog lint: FAILED", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("catalog lint: OK")
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
    if _report_gate_failures("catalog verify: FAILED", _deterministic_gate(repo_root, fragments)):
        return 1
    print("catalog verify: OK")
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
    # Read-only review agent: a helper on the best-effort remainder.
    with runner.process_budget().slot(runner.HELPER):
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


def _issue_record(repo_root: Path, issue_id: str) -> dict[str, object] | None:
    """The br record for *issue_id*, or None when it cannot be read."""
    return tracker.read_record(repo_root, issue_id)


def _issue_work_type(repo_root: Path, issue_id: str) -> str | None:
    """The br work type of *issue_id*, or None when it cannot be read."""
    record = _issue_record(repo_root, issue_id)
    if record is None:
        return None
    work_type = record.get("issue_type") or record.get("type")
    return work_type if isinstance(work_type, str) and work_type else None


def _issue_description(repo_root: Path, issue_id: str) -> str:
    """The br description body for *issue_id*, empty when it cannot be read."""
    record = _issue_record(repo_root, issue_id)
    body = record.get("description") if record else None
    return body if isinstance(body, str) else ""


def _cmd_rubric_eval(args: argparse.Namespace) -> int:
    """Evaluate the work-type rubric for an issue and report the advisory gate."""
    repo_root = _repo_root()
    work_type = _issue_work_type(repo_root, args.issue)
    if work_type is None:
        print(f"could not read the work type for {args.issue!r}", file=sys.stderr)
        return 1
    selected = rubrics.select_rubrics(rubrics.load_rubrics(), work_type)
    if not selected:
        ui.say(f"No rubric applies to work type {work_type!r}; nothing to evaluate.", style="muted")
        return 0

    if args.dry_run:
        for rubric in selected:
            judged = [c for c in rubric.checks if c.kind == rubrics.JUDGED]
            if judged:
                print(rubrics.build_judge_prompt(args.issue, rubric, judged))
        return 0

    verdicts: list[rubrics.CheckVerdict] = []
    for rubric in selected:
        rubric_verdicts = rubrics.evaluate(args.issue, rubric, repo_root, args.runner)
        verdicts.extend(rubric_verdicts)
        for verdict in rubric_verdicts:
            print(
                f"  [{rubric.id}] {verdict.check_id}: {verdict.answer} "
                f"({verdict.kind}) — {verdict.evidence}"
            )

    guard = verify.linked_worktree_guard(repo_root)
    if guard:
        print(f"rubric gate not recorded: {guard}", file=sys.stderr)
        return 0
    ok, message = rubrics.report_gate(repo_root, args.issue, verdicts)
    (ui.say if ok else ui.fail)(message)
    ui.say(
        "[rubric] advisory gate reported (non-blocking unless 'rubric' is in "
        "[policy] required_gates).",
        style="muted",
    )
    return 0


def cmd_rubric(args: argparse.Namespace) -> int:
    """Dispatch the ``rubric`` subcommands (eval)."""
    return _dispatch(args, "rubric_command", {"eval": _cmd_rubric_eval}, group="rubric")


def cmd_release(args: argparse.Namespace) -> int:
    """Produce a release up to the annotated tag, or report why it refused.

    Exit codes are the loop's convention: 0 when the release was produced (or a
    dry run computed cleanly), 1 when preconditions refused it. The push is never
    performed and the final line says so — see :mod:`basicly.release` for why that
    boundary is where it is.
    """
    repo_root = _repo_root()
    if args.root and not args.autonomous:
        print("release: --root only applies with --autonomous", file=sys.stderr)
        return 1
    plan = release.plan_release(repo_root, args.version, date=args.date)
    # Printed and flushed *before* the work starts: the run commits and tags, and a
    # header emitted afterwards leaves the operator with no idea what a failure was
    # in the middle of (and lands after the stderr refusals in any captured log).
    print(f"release:  {plan.current_tag} -> {plan.tag} on {plan.date}")
    sys.stdout.flush()
    result = release.run_release(
        repo_root,
        plan,
        issue_id=args.issue,
        dry_run=args.dry_run,
        root_issue=args.root,
        autonomous=args.autonomous,
        shipping=args.shipping,
    )
    if result.refused:
        for reason in result.refusals:
            print(f"refused:  {reason}", file=sys.stderr)
        return 1
    for step in result.steps:
        print(f"step:     {step}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    """Dispatch the ``catalog`` subcommands (lint / verify / review / new / list / dump)."""
    handlers = {
        "lint": cmd_catalog_lint,
        "verify": cmd_catalog_verify,
        "review": cmd_review,
        "new": _cmd_catalog_new,
        "list": _cmd_catalog_list,
        "dump": _cmd_catalog_dump,
    }
    return _dispatch(args, "catalog_command", handlers, group="catalog")


def _cmd_catalog_new(args: argparse.Namespace) -> int:
    """Scaffold a new source, routing on ``kind`` to the per-kind scaffolder."""
    if args.kind == "fragment":
        args.id = args.name
        return cmd_fragment_new(args)
    args.slug = args.name
    return cmd_skills_new(args) if args.kind == "skill" else cmd_agents_new(args)


def _cmd_catalog_list(args: argparse.Namespace) -> int:
    """List catalog sources, routing on ``kind`` to the per-kind lister."""
    listers = {
        "fragment": cmd_list,
        "skill": cmd_skills_list,
        "agent": cmd_agents_list,
    }
    return listers[args.kind](args)


def _cmd_catalog_dump(_args: argparse.Namespace) -> int:
    """Print the composed selection: every planned item, its origin, and what selected it.

    ``build`` composes core and overlay sources over four axes and then prints only
    the files it wrote, so an operator debugging a wrong projection had to read the
    sources by hand and re-derive the selection (basicly-8kqkxy). Derived by calling
    :func:`plan_outputs`, never by respelling its filters, so the dump cannot claim a
    selection the build would not make.
    """
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, targets = _load_context(repo_root, paths)
    planned = plan_outputs(fragments, targets, repo_root)
    declared = {(t.name, o.name): o for t in targets for o in t.outputs}

    for line in _dump_preamble(repo_root, paths, planned):
        ui.say(line)
    for line in _dump_overrides(fragments, repo_root):
        ui.say(line)
    for item in planned:
        ui.heading(_dump_output_line(item, declared[item.target_name, item.output_name], repo_root))
        for fragment in item.fragments:
            ui.say(f"  {_dump_item_line(fragment, repo_root)}")
    return 0


def _dump_preamble(repo_root: Path, paths: ProjectPaths, planned: list[PlannedOutput]) -> list[str]:
    """The composition inputs: the technology axis, then every fragment root in load order."""
    selection = load_technology_selection(repo_root)
    roots = ", ".join(
        f"{_format_path(repo_root / root, repo_root)} [{hint or 'inferred'}]"
        + ("" if (repo_root / root).is_dir() else " (absent)")
        for root, hint in _fragment_roots(paths)
    )
    return [
        f"technologies: {', '.join(sorted(selection)) if selection else 'unrestricted'}",
        f"roots: {roots}",
        f"composed: {len(planned)} outputs, "
        f"{sum(len(item.fragments) for item in planned)} selected items",
        "",
    ]


def _dump_overrides(fragments: list[Fragment], repo_root: Path) -> list[str]:
    """Each overlay-over-core replacement, naming the override and the source it shadows.

    Mirrors :func:`planner._apply_user_replacements`: an active overlay fragment
    shadows, and only a core fragment is shadowed. Printed before the outputs because
    a shadowed source is absent from every one of them, so a reader looking for why an
    item vanished would otherwise have to reach the end of the dump to find out.
    """
    active = {f.id: f for f in fragments if f.status == "active"}
    lines = [
        f"  {shadowed.id} ({_dump_origin(shadowed, repo_root)}) shadowed by "
        f"{fragment.id} ({_dump_origin(fragment, repo_root)})"
        for fragment in active.values()
        if fragment.source == "user"
        for replaced_id in fragment.replaces
        if (shadowed := active.get(replaced_id)) is not None and shadowed.source == "core"
    ]
    return [f"overridden by the overlay: {len(lines)}", *sorted(lines), ""] if lines else []


def _dump_output_line(item: PlannedOutput, output: OutputDef, repo_root: Path) -> str:
    """One planned output: its path, its target, and the two axes the output itself declares."""
    scoped_only = output.path_template is not None or output.has_scope
    rule = (
        "scoped only" if scoped_only else "unscoped only" if output.exclude_scoped else "any scope"
    )
    return (
        f"{_format_path(item.output_path, repo_root)} [{item.target_name}/{item.output_name}] "
        f"filter.applies_to={','.join(output.applies_to_filter) or 'none'} {rule} "
        f"({len(item.fragments)})"
    )


def _dump_item_line(fragment: Fragment, repo_root: Path) -> str:
    """One selected item: its id, the axis values that selected it, and where it was read."""
    return (
        f"{fragment.id} applies_to={','.join(fragment.applies_to)} "
        f"scope={fragment.scope_summary} "
        f"technologies={','.join(fragment.technologies) or 'any'} "
        f"<- {_dump_origin(fragment, repo_root)} [{fragment.source}]"
    )


def _dump_origin(fragment: Fragment, repo_root: Path) -> str:
    """A fragment's source file, or a named placeholder for one built in memory."""
    if fragment.source_path is None:
        return "<no source file>"
    return _format_path(fragment.source_path, repo_root)


def cmd_policy(args: argparse.Namespace) -> int:
    """Dispatch the ``policy`` subcommands (dor / scaffold / gate / checkpoint / rework)."""
    handlers = {
        "dor": _cmd_policy_dor,
        "scaffold": _cmd_policy_scaffold,
        "gate": _cmd_policy_gate,
        "checkpoint": _cmd_policy_checkpoint,
        "grant": _cmd_policy_grant,
        "rework": _cmd_policy_rework,
    }
    return _dispatch(args, "policy_command", handlers, group="policy")


def _cmd_policy_dor(args: argparse.Namespace) -> int:
    """Report Definition-of-Ready; exit 1 (blocking) when sections are missing."""
    repo_root = _repo_root()
    result = policy.definition_of_ready(repo_root, args.issue)
    # Advisory and verdict-independent: a scope that parsed to nothing is an authoring
    # error on a bead that is otherwise perfectly ready, so it is reported on both
    # paths and changes neither the verdict nor the exit code (basicly-tuy6).
    scope_warning = decompose.unparsed_scope_warning(_issue_description(repo_root, args.issue))
    if scope_warning:
        ui.warn(f"scope: {scope_warning}")
    if result.ready:
        print(f"DoR: READY ({args.issue})")
        return 0
    # Name the fix, with the issue's own type filled in: a refusal is exactly when
    # the agent needs the scaffold, and an unreadable type must not swallow it.
    work_type = _issue_work_type(repo_root, args.issue) or "<work-type>"
    print(
        f"DoR: NOT READY ({args.issue}) — missing: {', '.join(result.missing)}\n"
        f"  Emit the required structure: basicly policy scaffold --type {work_type}",
        file=sys.stderr,
    )
    return 1


def _cmd_policy_scaffold(args: argparse.Namespace) -> int:
    """Print a bead body: every section the DoR requires, plus ``## Scope``.

    Structure is derivable from the work type, so it is emitted rather than
    discovered when the classify gate refuses. ``## Scope`` rides along though no
    gate requires it, because an author who is never shown the line format writes
    one that parses to nothing (basicly-tuy6). Prints to stdout so the caller can
    fill the sections in and hand the result to ``br create -d`` (or ``br update
    -d``) — this command never writes to the tracker itself.
    """
    print(policy.scaffold_body(args.type), end="")
    return 0


def _cmd_policy_gate(args: argparse.Namespace) -> int:
    """Show gate status and exit 1 (blocking) when a required gate is not green."""
    repo_root = _repo_root()
    # The unit's own required set, the same one `loop status` derives its phase from:
    # two operator-facing reads disagreeing about whether a gate is owed is worse than
    # either answer alone (basicly-u2hl.54.1).
    config = validate_gate.required_config(repo_root, args.issue, load_policy_config(repo_root))
    status = policy.gate_status(repo_root, args.issue, config)
    print(f"required passed:  {list(status.required_passed)}")
    if status.required_failed:
        print(f"required FAILED:  {list(status.required_failed)}")
    if status.required_missing:
        print(f"required MISSING: {list(status.required_missing)}")
    for verdict in status.advisory:
        state = "pass" if verdict.passed else "fail"
        print(f"advisory: {verdict.gate} [{verdict.provider}] = {state}")
    for verdict in status.disregarded:
        state = "pass" if verdict.passed else "fail"
        print(
            f"DISREGARDED: {verdict.gate} [{verdict.provider}] = {state} — a required "
            "gate counts only the engine's own result; re-run `basicly verify "
            f"--issue {args.issue}` from the base checkout to record one"
        )
    if status.can_advance:
        print("advance: ALLOWED")
        return 0
    print("advance: BLOCKED", file=sys.stderr)
    return 1


def _cmd_policy_checkpoint(args: argparse.Namespace) -> int:
    """Show or record approval of a human checkpoint."""
    repo_root = _repo_root()
    if args.approve:
        return _approve_checkpoint(repo_root, args)
    approved = policy.checkpoint_approved(repo_root, args.issue, args.name)
    print(f"checkpoint {args.name}: {'APPROVED' if approved else 'PENDING'} ({args.issue})")
    return 0 if approved else 1


# What approving each checkpoint actually does, stated at the prompt
# (basicly-jr0l.39). The protocol asks the driver to "say what approving it
# does", which is unanswerable from a bare phase name — and the ship name is
# actively misleading: it reads as *release* or *publish*, and it sits after the
# merge it sounds like it performs. The owner of this harness misread it off a
# live prompt, and a consumer has strictly less context. The word is not
# changed here; the rename to `close` and its read-old/write-new migration are
# deferred to basicly-kjc5.45, before v1.0.0 freezes the CLI surface.
_CHECKPOINT_MEANING = {
    "classify": (
        "Approving records the work type and provisions a worktree. No code changes yet.",
    ),
    "decompose": ("Approving fans out the child beads. Nothing merges and nothing is published.",),
    # The ship wording is the safety-relevant one. The recorded incident:
    # approving before the landing printed `[merged]` short-circuits the derived
    # phase to ship and wedges an unmerged node, and there is no un-approve. An
    # operator who believes ship means publish has no reason to wait for that
    # line, so the prompt has to say it.
    "ship": (
        "The merge to the base branch has ALREADY happened, at the build->verify landing.",
        "Approving tears down the worktree and closes the bead. It publishes nothing,",
        "pushes nothing, and creates no tag or release.",
        "Do not approve unless you have seen a '[merged]' line for this bead: there is",
        "no un-approve, and approving early wedges the node with its work unmerged.",
    ),
}


def _reason_block(reason: str) -> str:
    """The indented line saying why a grant declined this decision, or nothing.

    Nothing when there is no reason: an ungranted session never consulted a
    grant, and its challenge must read exactly as it always has (basicly-5ltn).
    """
    return f"  {reason}\n" if reason else ""


def _print_challenge(
    label: str, issue: str, rerun: str, meaning: str | None = None, reason: str = ""
) -> int:
    """Print the one-time-code challenge and return the caller's non-zero exit.

    The wording is load-bearing (basicly-kjc5.34). This gate exists to force a
    deliberate human *decision*; it was never about whose fingers type the
    command. An earlier message said "a human must re-run with the one-time
    code", which an agent reasonably read as "hand this over and wait" — so it
    did, wasting a round trip and racing the code's TTL. A ship code really did
    expire between the ask and the paste. Say plainly that the caller may run it
    once approval is given, and name the protocol and the deadline.

    *meaning* states what approving this particular checkpoint does, from
    :data:`_CHECKPOINT_MEANING` (basicly-jr0l.39). Optional because the grant
    challenge has no checkpoint name to look up.

    *reason* is why an autonomy grant did not resolve this itself, when one
    existed and declined (basicly-5ltn). It comes first because it is the only
    part an operator can act on: without it a ship refused by a wrinkle in a
    *sibling* issue is indistinguishable from having no grant at all.
    """
    minutes = int(policy.CONFIRM_TTL_SECONDS // 60)
    print(
        f"{label}: CONFIRMATION REQUIRED ({issue})\n"
        f"{_reason_block(reason)}"
        f"{meaning or ''}"
        "  A human must approve this decision. The gate protects the decision, not the\n"
        "  keystrokes, so whoever is driving may run the command themselves once approval\n"
        "  is given: present it, say what approving it does, get an explicit yes, then\n"
        "  run it.\n"
        f"  Ask now — the one-time code expires in {minutes} minutes, whether or not you\n"
        "  are ready for it.\n"
        f"  {rerun}",
        file=sys.stderr,
    )
    return 1


def _meaning_block(lines: tuple[str, ...] | None) -> str | None:
    """Indent *lines* into the block :func:`_print_challenge` takes as its *meaning*."""
    return "".join(f"  {line}\n" for line in lines) if lines else None


def _checkpoint_meaning(name: str) -> str | None:
    """The indented "what approving does" block for checkpoint *name*, if known."""
    return _meaning_block(_CHECKPOINT_MEANING.get(name))


def _approve_checkpoint(repo_root: Path, args: argparse.Namespace) -> int:
    """Approve a checkpoint, gated on an interactive TTY or a one-time confirm code."""
    result = policy.approve_checkpoint_guarded(
        repo_root,
        args.issue,
        args.name,
        interactive=sys.stdin.isatty(),
        confirm=args.confirm,
        grant_root=args.root,
    )
    if result.status == "approved":
        print(f"checkpoint {args.name}: APPROVED ({args.issue})")
        return 0
    if result.status == "challenge":
        rerun = (
            f"basicly policy checkpoint {args.issue} {args.name} --approve --confirm {result.code}"
        )
        return _print_challenge(
            f"checkpoint {args.name}",
            args.issue,
            rerun,
            _checkpoint_meaning(args.name),
            result.detail,
        )
    print(f"checkpoint {args.name}: REFUSED ({args.issue}) - {result.detail}", file=sys.stderr)
    return 1


def _coverage_phrase(repo_root: Path, issue: str) -> str:
    """How many beads the session rooted at *issue* covers, in words.

    Printed on issuance and on the ledger read, because a grant's marker says
    nothing about its reach: an L3 with a large ceiling reads as authority over
    a whole track even when the session is the single bead it sits on
    (basicly-jr0l.40). The single-leaf case names itself rather than leaving
    "covers 1 bead" to be read as a rounding of something larger.
    """
    count = policy.session_coverage(repo_root, issue)
    if count == 1:
        return "covers 1 bead (this issue only)"
    return f"covers {count} beads"


def _report_active_grant(repo_root: Path, issue: str) -> int:
    """Print the active grant, its coverage, and what it delegates; 1 when there is none.

    Non-zero for "no grant" so a script can branch on it: every checkpoint being
    human is the safe state, but it is not the state a caller asking for a grant
    was hoping to find.

    Two lines, because there are two authorities and reading them as one is what
    stalled a P0 for sessions (basicly-u6jq.2). A bare ``delegable: classify,
    decompose, ship`` reads as "the loop classifies and decomposes for you"; it
    only ever meant the *checkpoint approval* over those phases, while the input
    each phase needs had no producer at all. So approving and originating are
    named separately, and an L1 grant — which approves the decompose checkpoint but
    may not propose the plan it would approve — says so out loud.
    """
    grant = policy.active_grant(repo_root, issue)
    if grant is None:
        print(f"grant: NONE ({issue}) - every checkpoint is human (L0)")
        return 1
    budget = f", token budget {grant.token_budget}" if grant.token_budget is not None else ""
    covers = ", ".join(policy.GRANT_COVERAGE[grant.level]) or "(nothing)"
    proposes = ", ".join(policy.PROPOSAL_COVERAGE[grant.level]) or "(nothing)"
    coverage = _coverage_phrase(repo_root, issue)
    print(f"grant: {grant.level} ({issue}){budget}, {coverage}")
    print(f"       approves checkpoints: {covers} - originates proposals: {proposes}")
    return 0


def _cmd_policy_grant(args: argparse.Namespace) -> int:
    """Show, issue, or revoke a session autonomy grant (factory design D3).

    Issuance runs through the same anti-autopilot gate as checkpoint approval:
    interactive TTY, or a one-time confirm code a human must relay — an agent
    can never self-escalate. With no --level and no --revoke, reports the
    active grant.

    ``--autonomy`` raises the grantable ceiling for this one issuance
    (basicly-jr0l.15). The session pin ``loop supervise --autonomy`` cannot reach
    here: a grant is issued by this separate command in a separate process, so
    without the flag a session could be pinned above L0 and still be unable to
    obtain the grant it needs — which also means no token budget, since a spend
    ceiling exists only on a grant.

    It widens nothing the anti-autopilot gate protects: the TTY-or-confirm-code
    challenge below still applies, so raising the ceiling is not a way to
    self-escalate — it only decides which level a human may then authorize.
    """
    repo_root = _repo_root()
    try:
        overrides = _apply_session_overrides(repo_root, args)
    except ValueError as exc:
        print(f"grant: refused - {exc}", file=sys.stderr)
        return 1
    if overrides:
        print(f"override: {', '.join(overrides)}")
    if args.revoke:
        policy.revoke_grant(repo_root, args.issue)
        print(f"grant: REVOKED ({args.issue})")
        return 0
    if args.level is None:
        return _report_active_grant(repo_root, args.issue)
    result = policy.issue_grant_guarded(
        repo_root,
        args.issue,
        args.level,
        args.token_budget,
        load_policy_config(repo_root),
        interactive=sys.stdin.isatty(),
        confirm=args.confirm,
    )
    if result.status == "approved":
        coverage = _coverage_phrase(repo_root, args.issue)
        print(f"grant: ISSUED {args.level} ({args.issue}) - {coverage}")
        return 0
    if result.status == "challenge":
        # --autonomy has to be carried into the reprinted command: the override is
        # process-local, so a re-run without it is refused at the committed ceiling
        # again and the relay protocol dead-ends (basicly-jr0l.15).
        rerun = (
            f"basicly policy grant {args.issue} --level {args.level}"
            + (f" --token-budget {args.token_budget}" if args.token_budget is not None else "")
            + (f" --autonomy {args.autonomy}" if args.autonomy else "")
            + f" --confirm {result.code}"
        )
        return _print_challenge("grant", args.issue, rerun)
    print(f"grant: REFUSED ({args.issue}) - {result.detail}", file=sys.stderr)
    return 1


def _cmd_policy_rework(args: argparse.Namespace) -> int:
    """Show, record, or forgive a rework attempt, reporting whether the cap escalates."""
    repo_root = _repo_root()
    config = load_policy_config(repo_root)
    if args.record and args.allow_retry:
        print("rework: --record and --allow-retry are opposites", file=sys.stderr)
        return 1
    if args.record:
        charged = policy.record_rework(repo_root, args.issue, args.gate)
        print(f"Recorded rework for gate '{args.gate}'.")
    elif args.allow_retry:
        charged = policy.grant_rework_allowance(repo_root, args.issue, args.gate)
        print(f"Granted one further attempt on gate '{args.gate}'.")
    else:
        charged = policy.rework_charged(repo_root, args.issue, args.gate)
    verdict = "ESCALATE (cap reached)" if charged >= config.max_rework else "may retry"
    print(f"rework: {charged}/{config.max_rework} attempts for gate '{args.gate}' — {verdict}")
    # Only worth the extra tracker read once an allowance exists: it explains why
    # the charged count is behind the raw history.
    if granted := policy.rework_allowances(repo_root, args.issue, args.gate):
        recorded = policy.rework_attempts(repo_root, args.issue, args.gate)
        print(f"  ({recorded} recorded, {granted} forgiven)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Run the configured verify checks for a mode and optionally record a gate."""
    repo_root = _repo_root()
    if args.issue:
        reason = verify.linked_worktree_guard(repo_root)
        if reason is not None:
            ui.fail(f"refusing to record gate for {args.issue}: {reason}")
            return 1
    config = load_verify_config(repo_root)
    if not config.for_mode(args.mode):
        print(f"No verify checks configured for mode '{args.mode}' in {CONFIG_FILE}.")

    if args.fix:
        fixes = verify.apply_fixes(repo_root, args.mode, config)
        for result in fixes.results:
            if result.status == "pass":
                print(f"[fix] applied {result.name}")
            elif result.status == "fail":
                detail = f" — {result.detail}" if result.detail else ""
                print(f"[fix] {result.name} failed{detail}", file=sys.stderr)

    report = verify.run_verify(repo_root, args.mode, config)

    print("\n" + "=" * 60)
    for result in report.results:
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"  {result.name}: {result.status.upper()}{suffix}")

    if args.issue:
        record = run_record.latest_record(repo_root, args.issue)
        ok, message = verify.report_gate(
            repo_root,
            args.issue,
            report,
            gate=args.gate,
            actor=record.agent if record else None,
        )
        print(message if ok else f"Warning: {message}", file=sys.stderr if not ok else sys.stdout)

    if report.passed:
        print(f"[verify] PASS (mode: {args.mode})")
        return 0
    print(f"[verify] FAIL: {', '.join(report.failures)}", file=sys.stderr)
    return 1


def cmd_commit(args: argparse.Namespace) -> int:
    """Assemble the commit envelope from engine state and commit the staged change."""
    repo_root = _repo_root()
    try:
        envelope = commit.assemble(
            repo_root,
            args.description,
            bead=args.issue,
            commit_type=args.type,
            scope=args.scope,
            breaking=args.breaking,
            body=args.body or "",
        )
    except ValueError as exc:
        ui.fail(f"[commit] REJECTED: {exc}")
        return 1

    print(envelope.message)
    if args.dry_run:
        return 0
    if not commit.has_staged_changes(repo_root):
        ui.fail("[commit] nothing staged to commit; stage the change first (git add ...)")
        return 1

    result = commit.run_commit(repo_root, envelope)
    print(result.output)
    if result.committed:
        return 0
    # The hooks are the floor and they just spoke: their output above is the
    # actionable report, so add nothing but the verdict.
    ui.fail("[commit] git rejected the commit (see the hook output above)")
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
        if child.spec.shared:
            print(f"      shared (not owned): {', '.join(child.spec.shared)}")


def _print_collapsing_paths(collapsing: tuple[Any, ...], contended: tuple[str, ...] = ()) -> None:
    """Name the declared paths that are load-bearing for the grouping.

    Printed by the dry run and the real run from the same
    :func:`decompose.collapsing_paths` computation, so the preview cannot report a
    different collapse than the run it is meant to predict (basicly-u6tw's rule
    applied to basicly-jr0l.45). Silent when no single path decides anything.

    *contended* is the configured append-only list, passed through so a path that no
    child declared says where it came from instead of reading as a grouping bug
    (basicly-o8p0).
    """
    if not collapsing:
        return
    print("collapsing paths:")
    for item in collapsing:
        print(f"  {decompose.describe_collapsing_path(item, contended)}")


def _spend_metrics(spend: Any) -> str:
    """One child's predicted spend, with an unpredictable metric named as such."""
    tokens = "tokens unknown" if spend.tokens is None else f"{spend.tokens} tokens"
    cost = "cost unknown" if spend.cost is None else f"${spend.cost:.2f}"
    clock = (
        "wall clock unknown" if spend.wall_clock_s is None else f"{spend.wall_clock_s / 60:.0f} min"
    )
    return f"{tokens}, {cost}, {clock}"


def _spend_sources(calibration: Any) -> str:
    """Where this child's three ratios came from, collapsed when they agree."""
    named = (
        ("tokens", calibration.tokens_per_working_set_token),
        ("cost", calibration.usd_per_million_tokens),
        ("wall clock", calibration.seconds_per_million_tokens),
    )
    sources = {ratio.source for _, ratio in named}
    body = (
        sources.pop()
        if len(sources) == 1
        else ", ".join(f"{name}={ratio.source}" for name, ratio in named)
    )
    return f"{body} ({calibration.pairs} paired record(s) for {calibration.task_class})"


def _print_spend_forecast(children: tuple[Any, ...], spend: tuple[Any, ...]) -> None:
    """Print predicted spend per child, saying for each whether it was measured or seeded.

    A seeded number that reads as measured is worse than no number (basicly-jr0l.21),
    so the source travels with the forecast on the surface a human actually reads —
    not only in the recorded marker.
    """
    if not spend:
        return
    model = spend[0].calibration.model or "unresolved"
    print(f"forecast spend (model {model}):")
    for spec, forecast in zip(children, spend, strict=True):
        sources = _spend_sources(forecast.calibration)
        print(f"  {spec.title}: {_spend_metrics(forecast)} — {sources}")
    print(f"  declared prior: {spend[0].calibration.prior.basis}")


def cmd_decompose(args: argparse.Namespace) -> int:
    """Decompose a feature into child issues + a computed dependency graph."""
    repo_root = _repo_root()
    children = _load_decompose_children(args)
    # The same list the real run loads, read once here so the dry run groups the plan
    # against it too (basicly-o8p0): a preview that ignored the repo's append-only
    # convention would promise parallel groups the run then serializes.
    contended = decompose.append_only_paths(repo_root)

    if args.dry_run:
        planned = decompose.preview(children, contended)
        groups = 1 + max((c.group for c in planned), default=-1)
        print(f"decompose (dry-run): {len(planned)} children in {groups} parallel group(s)")
        _print_planned(planned)
        _print_collapsing_paths(decompose.collapsing_paths(children, contended), contended)
        # The band verdict is the whole point of a dry run: a plan that previews
        # clean and is then refused on the real run is not a preview of anything
        # (basicly-u6tw). Same estimates and same guidance as `decompose` itself,
        # because both read decompose.estimate_plan.
        verdict = decompose.estimate_plan(repo_root, children, feature_id=args.feature)
        print("sizing (D8 working-set band):")
        for spec, estimate in zip(children, verdict.estimates, strict=True):
            print(
                f"  {spec.title}: {estimate.total} tokens "
                f"(scope {estimate.scope_tokens} x build factor + overhead)"
            )
        _print_spend_forecast(children, verdict.spend)
        if verdict.refused:
            print("verdict: REFUSED — the real run would not create these children:")
            for message in verdict.violations:
                print(f"  {message}")
            return 1
        print("verdict: within band")
        return 0

    result = decompose.decompose(repo_root, args.feature, children)
    print(
        f"decompose: created {len(result.children)} children under {result.feature_id} "
        f"in {result.parallel_groups} parallel group(s)"
    )
    for group_index, group in enumerate(result.groups):
        print(f"  group {group_index}: {' -> '.join(group)}")
    print(f"serial order: {' '.join(result.serial_order)}")
    _print_collapsing_paths(result.collapsing, contended)
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    """Dispatch the ``loop`` subcommands (status/advance/run/supervise/session...)."""
    handlers = {
        "status": _cmd_loop_status,
        "advance": _cmd_loop_advance,
        "run": _cmd_loop_run,
        "supervise": _cmd_loop_supervise,
        "preflight": _cmd_loop_preflight,
        "session": _cmd_loop_session,
        "decisions": _cmd_loop_decisions,
        "answer": _cmd_loop_answer,
        "decide": _cmd_loop_decide,
        "kill": _cmd_loop_kill,
        "stop": _cmd_loop_stop,
        "watch": _cmd_loop_watch,
        "improve": _cmd_loop_improve,
    }
    return _dispatch(args, "loop_command", handlers, group="loop")


# The repo's improvement controller, relative to the repo root the command operates
# on. Held as a path rather than imported: `.scripts/` is not a package (the same
# constraint `tracker.PINNED_VERSION` records), and the controller is a repo-local script
# like every `[[verify.checks]]` command rather than engine code.
IMPROVEMENT_CONTROLLER = Path(".scripts") / "improvement_controller.py"


def _cmd_loop_improve(args: argparse.Namespace) -> int:
    """Run the repo's improvement controller: one sensor reading, one lane.

    The scheduled entry point for the second loop shape (basicly-u2hl.27) — the one
    that drives a *property of the codebase* toward a set point rather than shipping a
    requirement. Every decision it makes lives in the controller script, beside the
    sensor it reads; this is the front door a cron or a workflow calls, and it passes
    the script's exit code straight through so a schedule can branch on it.

    A repo that declares no controller is refused by name. The loop is opt-in per repo,
    and an absent script is the one state that would otherwise be indistinguishable
    from a run that measured everything and found nothing to do.
    """
    repo_root = _repo_root()
    script = repo_root / IMPROVEMENT_CONTROLLER
    if not script.is_file():
        print(
            f"improve: refused - this repo declares no improvement controller at "
            f"{IMPROVEMENT_CONTROLLER.as_posix()}",
            file=sys.stderr,
        )
        return 1
    command = [sys.executable, str(script), *(["--dry-run"] if args.dry_run else [])]
    # This process's interpreter plus a repo-declared path: inside the trust boundary,
    # exactly like `verify._run`'s check commands. `shell=False` keeps a repo root with
    # a space in it one argv element.
    return subprocess.run(  # noqa: S603 — repo-declared script, list form, no shell
        command, cwd=repo_root, check=False
    ).returncode


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


def _print_preflight_spend(
    repo_root: Path, state: supervise.SessionState, status: policy.SpendStatus
) -> list[str]:
    """Report what a pass would cost: the live bound, or a forecast for a full fan-out.

    Split from :func:`_cmd_loop_preflight` to keep one statement budget per concern
    rather than widening the lint cap. Returns the blockers the candidate set implies
    (:func:`_provisioning_blockers`), because this is where the set is already read.

    The two branches answer different questions. With lanes already dispatchable, the
    real admission is shown — the same figure the gate will use. With none yet, the
    interesting number is what a *full* fan-out would need, because that is what a
    budget has to be minted for, and discovering it mid-run is how a ceiling gets set
    too low.
    """
    sizing = load_sizing_config(repo_root)
    lanes = supervise.ready_lanes(repo_root, state)
    per_lane, source = decompose.unsized_lane_tokens(repo_root, sizing)
    print(f"lanes:     {len(lanes)} dispatchable now, {len(state.open_children)} open child(ren)")
    print(f"per-lane:  {per_lane} tokens assumed for an unsizeable lane ({source})")
    if lanes:
        working_sets = tuple(
            working_set.admit_working_set(repo_root, lane.issue_id, sizing) for lane in lanes
        )
        pass_spend = supervise.admit_pass_spend(repo_root, working_sets, status, sizing)
        print(f"spend:     {pass_spend.coverage}")
        _print_band_report(working_sets, sizing)
        return []
    cap = load_worktree_config(repo_root).concurrency
    # A root with no children at all is a leaf: seeding provisions the root itself as
    # the single lane (``loop._start_build_leaf``), so price one rather than none.
    open_count = len(state.open_children) or (0 if state.children else 1)
    priced = min(cap, open_count)
    if priced:
        # Bounded by what exists, not by the cap alone. Unbounded, the same output said
        # "0 open child(ren)" on one line and priced five lanes on the next, and a
        # budget minted from that number funds a pass that cannot start (basicly-cdhq).
        print(
            f"forecast:  ~{per_lane * priced} tokens if all {priced} lanes start "
            f"(per-lane x min(cap {cap}, {open_count} open))"
        )
    # That forecast is the unsizeable-lane assumption times the cap whenever the
    # candidates declare no readable scope — a number describing none of them, which
    # reads exactly like one that does. So size the candidates here too: before a budget
    # is minted is the only point where the operator can still act on it (basicly-prnm).
    candidates = tuple(
        working_set.admit_working_set(repo_root, issue_id, sizing)
        for issue_id in state.open_children
    )
    _print_band_report(candidates, sizing)
    return _provisioning_blockers(state, candidates)


def _provisioning_blockers(
    state: supervise.SessionState, candidates: tuple[working_set.WorkingSetAdmission, ...]
) -> list[str]:
    """Refuse a pass that has nothing to provision a lane from, naming which case it is.

    The bead's own Expected line: a command whose job is to check everything a
    supervised run needs must not report ready for a run that cannot dispatch a single
    lane. Two causes reach that state through the candidate set (the third is the root's
    own checkpoint, :func:`_print_preflight_checkpoints`), and they are reported apart
    because the remedies are unrelated — one session is finished, the other needs its
    children split.

    Deliberately *not* written on the dispatchable-lane count: that counts *adopted*
    lanes, and before any worktree exists it reads zero on a pass that is genuinely
    ready. A childless root is not refused either — it is the leaf case, and seeding
    provisions the root itself.
    """
    if not state.open_children:
        if not state.children:
            return []
        print(
            f"provision: NONE - {len(state.children)} child(ren), none open; "
            "nothing left to provision a lane from"
        )
        return ["the session has no open child to provision a lane from"]
    if candidates and all(candidate.refused for candidate in candidates):
        print("provision: NONE - every open child is REFUSED by the band; split them before a pass")
        return ["every open child is too large for the band, so no lane can dispatch"]
    return []


# The checkpoint the root's *own* advance must clear before any lane exists, keyed on
# the phase the root is parked at — the guards in ``loop._on_intake`` and
# ``loop._on_decompose``. It blocks provisioning only when no grant delegates it:
# seeding drives the root through ``loop.run_ceremony``, which does reach
# ``approve_checkpoint_guarded`` (basicly-kjc5.62). Before that it drove
# ``run_until_blocked``, which stopped dead at the checkpoint however high the grant
# was, and this report is where an operator first met that (basicly-cdhq).
_SEEDING_CHECKPOINT = {"intake": "classify", "decompose": "decompose"}


def _preflight_session(repo_root: Path, args: argparse.Namespace) -> supervise.SessionState | None:
    """The session preflight reports on, or None once it has reported why there is none.

    The empty-selector case is *answered* rather than raised: a command whose whole job
    is to report what would block a pass has to report its own lane selector matching
    nothing (basicly-1lpo), and printing the verdict here keeps the caller's one
    statement budget per concern.
    """
    try:
        state = supervise.derive_session(repo_root, args.issue, lane_label=args.label)
    except supervise.LaneSelectionError as exc:
        print(f"select:    INVALID - {exc}")
        print("VERDICT:   not ready - the lane selector names no bead to run")
        return None
    if args.label is not None:
        print(
            f"select:    {len(state.children)} bead(s) carry label {args.label!r}; "
            f"{len(state.open_children)} still open"
        )
    return state


def _print_preflight_coverage(
    repo_root: Path, state: supervise.SessionState, grant: policy.Grant | None
) -> None:
    """Report selected lanes the grant's session does not reach (basicly-1lpo).

    A labelled lane set and a grant's session are two different walks: the grant
    covers the root plus its ``parent-child`` descent and its ``blocks``
    dependencies (:func:`policy.session_issue_ids`), while a label expresses
    membership with no edge at all. An uncovered lane still dispatches, so this is
    a report rather than a blocker — but every checkpoint that lane reaches is a
    human's, which is a pass that reads as lights-out and stalls one checkpoint in.
    The remedy is the edge a release cut already means: the root waits on it.
    """
    if state.lane_label is None or grant is None:
        return
    covered = frozenset(policy.session_issue_ids(repo_root, state.root_issue))
    outside = [issue_id for issue_id, _ in state.children if issue_id not in covered]
    if not outside:
        print(
            f"coverage:  all {len(state.children)} selected lane(s) under the {grant.level} grant"
        )
        return
    print(
        f"coverage:  {len(outside)} of {len(state.children)} selected lane(s) outside the "
        f"{grant.level} grant's session - their checkpoints are a human's"
    )
    print(f"           cover each: br dep add {state.root_issue} <id> -t blocks")
    print(f"           uncovered: {', '.join(outside)}")


def _print_preflight_checkpoints(
    repo_root: Path, root_issue: str, grant: policy.Grant | None, *, seeds_from_root: bool = True
) -> list[str]:
    """Report the root's own unapproved checkpoints; return the ones that refuse a pass.

    Observed 2026-08-04: a clean base, a live L3 grant, a funded forecast and five
    in-band lanes preflighted as ready, and the pass it green-lit dispatched nothing —
    ``seed-blocked ... decompose checkpoint awaiting human approval``. Checkpoint state
    was the one precondition the report omitted, and ``loop status`` reconstructs it
    already, so the cost is one read.

    Only the checkpoint that blocks *provisioning*, and only when nothing delegates it,
    is a blocker. Every unapproved one would be noise, and a grant that covers the
    seeding checkpoint is no longer a blocker at all: seeding resolves it through the
    same guarded predicate the rest of the loop uses (basicly-kjc5.62), so calling it
    one would refuse a pass that runs. Without such a grant it still refuses, which is
    the half of basicly-cdhq that was never wrong.

    *seeds_from_root* is False for a pass whose lanes were selected by label: the root's
    advance is then not the provisioning path (``supervise._seed_selected_lanes``), so
    its own checkpoints refuse nothing and reporting one as a blocker would refuse a
    pass that is ready (basicly-1lpo).
    """
    node = loop_state.read_node_state(repo_root, root_issue)
    blocking = _SEEDING_CHECKPOINT.get(node.phase) if seeds_from_root else None
    # What the grant delegates, named once: it decides both which line each pending
    # checkpoint gets and whether the seeding one refuses the pass at all.
    delegated = policy.GRANT_COVERAGE.get(grant.level, ()) if grant is not None else ()
    if blocking in delegated:
        blocking = None
    delegates = f"the live {grant.level} grant" if grant is not None else ""
    blockers: list[str] = []
    pending = [name for name in CHECKPOINTS if name not in node.checkpoints]
    if not pending:
        print("checkpts:  all approved")
        return blockers
    for name in pending:
        if name != blocking:
            served = f"{delegates} delegates it" if name in delegated else ""
            print(f"checkpts:  {name} pending - {served or 'a human, when the pass reaches it'}")
            continue
        why = "the root's own advance provisions the lanes, and no grant delegates this"
        covers = next(
            (level for level, names in policy.GRANT_COVERAGE.items() if name in names), ""
        )
        print(f"checkpts:  {name} UNAPPROVED - blocks provisioning: {why}")
        print(f"           approve: basicly policy checkpoint {root_issue} {name} --approve")
        if covers:
            print(f"           or delegate it: basicly policy grant {root_issue} --level {covers}")
        blockers.append(f"the root's {name} checkpoint blocks provisioning")
    return blockers


def _print_preflight_calibration(repo_root: Path, sizing: SizingConfig) -> None:
    """Report whether the numbers a pass is about to be sized with are measured.

    Two lines because they are two quantities (:class:`decompose.CalibrationStatus`):
    the spend ratios, which history can replace, and the working-set build factors,
    which nothing measures. An operator minting a budget from the forecast above needs
    to know it rests on a declared prior — and until basicly-tcmy.5 the only way to
    find that out was to read the source (basicly-p8ck: if it must be recalled, it is
    a missing command).
    """
    status = decompose.calibration_status(repo_root, sizing)
    counts = ", ".join(
        f"{name} {count}/{status.min_samples}" for name, count in sorted(status.samples.items())
    )
    verdict = "SEEDS" if status.on_seeds else f"measured for {', '.join(status.measured_classes)}"
    # A null model is not "zero samples for the model in use": the ratios are keyed per
    # (model, class), so an unresolved model means nothing can key in at all and the
    # counts are all the report has to offer. Said outright rather than rendered as
    # "on None", which reads as a model nobody named.
    against = (
        f"paired write dispatches on {status.model}"
        if status.model
        else "no model pinned, so no sample can key in; paired write dispatches"
    )
    print(f"spend cal: {verdict} - {against}: {counts or 'none'}")
    seeded = sorted(
        name
        for name, source in status.build_factor_sources.items()
        if source == decompose.BUILD_FACTOR_SEED
    )
    factors = ", ".join(
        f"{name} {sizing.build_factors[name]:g}"
        for name in sorted(status.build_factor_sources)
        if name in sizing.build_factors
    )
    # Named a seed rather than a calibration: no code path measures a build factor, so
    # a line implying one could be measured would be the misreading this bead removes.
    detail = "all seeds" if len(seeded) == len(status.build_factor_sources) else "some configured"
    print(f"factors:   {detail} (never measured) - {factors}")


def _print_preflight_contention(repo_root: Path, state: supervise.SessionState) -> None:
    """Report the two collisions on paths no bead declares (basicly-o8p0, basicly-lyro).

    Two lines, because :mod:`basicly.contention` answers the merge-queue question two
    ways: `contend:` names the paths that must serialise the pass, `regen:` the ones
    that need not, because the engine rebuilds them.

    Advisory, never a blocker: the remedy is a build order, and refusing the pass would
    turn a predictable conflict into a stopped factory. What it buys is that the
    conflict is named *before* a lane starts, instead of arriving as a merge-queue
    bounce that has already spent a rework cycle.

    Written on the open children rather than on the lanes dispatchable right now,
    because before any worktree exists the dispatchable count reads zero on a pass
    that is genuinely about to start five lanes (the same reason
    :func:`_provisioning_blockers` does not count them). A childless root is the leaf
    case, where seeding provisions the root itself as the one lane — counted as one
    rather than as none, so the line does not report "0 lane(s)" beside a report that
    just said one is dispatchable.
    """
    lanes = state.open_children or ((state.root_issue,) if not state.children else ())
    lines = contention.append_only_report(repo_root, lanes, decompose.append_only_paths(repo_root))
    print(f"contend:   {lines[0]}")
    for line in lines[1:]:
        print(line)

    regen = contention.generated_report(load_worktree_config(repo_root).regenerate_commands)
    print(f"regen:     {regen[0]}")
    for line in regen[1:]:
        print(line)


def _print_band_report(
    working_sets: tuple[working_set.WorkingSetAdmission, ...], sizing: SizingConfig
) -> None:
    """Print the per-lane band table, headed by the band the verdicts are against."""
    lines = working_set.band_report(working_sets)
    if not lines:
        return
    print(f"band:      {sizing.working_set_min}..{sizing.working_set_max} working-set tokens")
    for line in lines:
        print(line)


def _cmd_loop_preflight(args: argparse.Namespace) -> int:
    """Answer, from the repo alone, everything a supervised run needs checked first.

    This exists because the answers used to live in an operator's head. Every line below
    is a question that previously had to be recalled — is the base clean, will the runner
    meter spend, is there a budget, how many lanes would start, what will they cost — and
    a consumer who installs basicly inherits the engine but none of the recollection.
    Deterministic, so it is a command rather than a note (basicly-p8ck).

    Read-only: it dispatches nothing, provisions nothing and writes no tracker state, so
    it is free to run before every session. Exit is non-zero when something would block a
    run, so CI or a wrapper can gate on it.
    """
    repo_root = _repo_root()
    blockers: list[str] = []

    # First, and with an early return: every loader below refuses an unrecognised
    # config name (basicly-1piy), so without this the report would die of an
    # exception on the runner line and answer none of its other questions.
    if unknown := unknown_config_keys(repo_root):
        for problem in unknown:
            print(f"config:    INVALID - {problem}")
        print("VERDICT:   not ready - a config file declares a name this basicly cannot honour")
        return 1
    print("config:    recognised")

    # Not ``.strip()``: that eats the first line's leading status column, shifting its
    # path by one so ``.basicly/x`` reads as ``basicly/x`` and misses the check below.
    dirty = worktree.git(["status", "--porcelain"], cwd=repo_root, check=False).stdout
    # Foreign dirt refuses a landing (basicly-4psl), and the refusal arrives *after* the
    # lanes have already run, which is the expensive place to learn it. The engine's own
    # tracker trees are excluded because the landing sweeps them (basicly-vkh0.25);
    # counting those here refuses a pass over dirt it would have committed itself.
    foreign = [
        line[3:]
        for line in dirty.splitlines()
        if line.strip() and not merge.is_engine_tracker_path(line[3:])
    ]
    if foreign:
        print(f"base:      DIRTY - {len(foreign)} path(s); a landing will refuse until committed")
        blockers.append("base checkout is dirty")
    else:
        print("base:      clean")

    sessions = worktree.list_sessions(repo_root)
    print(f"worktrees: {len(sessions)} live")

    state = _preflight_session(repo_root, args)
    if state is None:
        return 1
    stale = [lane.issue_id for lane in state.adopted if not lane.live]
    if stale:
        print(f"stale:     {', '.join(stale)} - binding outlived its worktree, will be repaired")

    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    print(f"runner:    {spec.name} ({spec.kind}), timeout {config.runner_timeout:.0f}s")

    status = policy.spend_status(repo_root, args.issue)
    grant = status.grant
    if grant is None:
        print("grant:     NONE - every checkpoint is human")
    else:
        spent = policy.tokens_under_grant(status.spent_tokens, grant)
        remaining = (
            "no ceiling" if status.remaining_tokens is None else f"{status.remaining_tokens}"
        )
        print(f"grant:     {grant.level}, spent {spent} under it, remaining {remaining}")
    if status.halted:
        print(f"halted:    {status.detail}")
        blockers.append(
            # Named, not counted: the operator's next move is to fix that runner's
            # usage format, and a bare count sends them looking (basicly-6y0tg5).
            f"could not be metered: {', '.join(status.unmetered_labels)}"
            if status.unmetered_dispatches
            else "the grant's budget is spent"
        )
    if (metered := supervise.metered_without_a_budget(repo_root, status)) is not None:
        print(f"budget:    MISSING - the {metered!r} runner meters spend and no budget covers it")
        blockers.append("a metered runner needs a grant with a token budget")

    _print_preflight_coverage(repo_root, state, grant)
    blockers += _print_preflight_checkpoints(
        repo_root, args.issue, grant, seeds_from_root=state.lane_label is None
    )
    blockers += _print_preflight_spend(repo_root, state, status)
    _print_preflight_contention(repo_root, state)
    _print_preflight_calibration(repo_root, load_sizing_config(repo_root))

    ahead = worktree.git(
        ["rev-list", "--count", "@{upstream}..HEAD"], cwd=repo_root, check=False
    ).stdout.strip()
    if ahead and ahead != "0":
        print(f"unpushed:  {ahead} commit(s)")

    if blockers:
        print(f"VERDICT:   not ready - {'; '.join(blockers)}")
        return 1
    print("VERDICT:   ready")
    return 0


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
    repo_root = _repo_root()
    try:
        overrides = _apply_session_overrides(repo_root, args)
    except ValueError as exc:
        print(f"advance: refused - {exc}", file=sys.stderr)
        return 1
    if overrides:
        print(f"override: {', '.join(overrides)}")
    result = loop.advance(repo_root, args.issue, inputs=_loop_inputs(args))
    print(_format_advance(result))
    return 1 if result.blocked else 0


def _confirm_codes(raw: str | None) -> dict[str, str] | None:
    """Map one ``--confirm`` value onto the per-checkpoint codes the ceremony consumes.

    ``name=code`` targets one checkpoint; a bare ``code`` is offered to whichever
    checkpoint challenges. Offering it widely is safe because a code is minted
    and validated per (issue, checkpoint), so it can only ever satisfy the
    checkpoint it was minted for.
    """
    if not raw:
        return None
    name, _, code = raw.partition("=")
    if code and name in CHECKPOINTS:
        return {name: code}
    return dict.fromkeys(CHECKPOINTS, raw)


def _ceremony_rerun(args: argparse.Namespace, code: str) -> str:
    """The full re-invocation of this same command, carrying the relayed code.

    The point of the ceremony is that a human relays one code and the *whole*
    boundary continues, so the printed line has to be the command they ran plus
    ``--confirm`` — not a bare checkpoint approval that leaves the loop parked.

    ``--runner`` and ``--autonomy`` are carried for the same reason ``policy grant``
    carries ``--autonomy`` (basicly-jr0l.15): both are *process-local* session
    overrides, so a reprint that drops one is not the command the operator ran. For
    ``--runner`` that is not merely untidy — ``[runner] default`` is ``auto``, which
    resolves to a headless agent, so relaying the reprinted line verbatim turned a
    manual handoff into a live metered dispatch. That happened (basicly-1th1), and
    relaying this line exactly is the documented protocol, so the line has to be right.
    """
    parts = ["basicly", "loop", "run", args.issue]
    if args.work_type:
        parts += ["--work-type", args.work_type]
    if args.children:
        parts += ["--children", args.children]
    if args.mode != "full":
        parts += ["--mode", args.mode]
    if args.root:
        parts += ["--root", args.root]
    if runner_name := getattr(args, "runner", None):
        parts += ["--runner", runner_name]
    if autonomy := getattr(args, "autonomy", None):
        parts += ["--autonomy", autonomy]
    return " ".join([*parts, "--confirm", code])


def _cmd_loop_run(args: argparse.Namespace) -> int:
    """Drive the loop across a whole phase boundary; exit non-zero if it stopped short.

    One command per boundary (basicly-kjc5.41, design D10): intake through to
    awaiting-the-agent's-work, and committed work through to shipped. Every
    checkpoint in between is resolved the way ``policy checkpoint --approve``
    resolves one — a TTY, a covering grant, or a relayed one-time code — so this
    collapses the ceremony without widening what may be self-approved.
    """
    repo_root = _repo_root()
    try:
        overrides = _apply_session_overrides(repo_root, args)
    except ValueError as exc:
        print(f"run: refused - {exc}", file=sys.stderr)
        return 1
    if overrides:
        print(f"override: {', '.join(overrides)}")
    result = loop.run_ceremony(
        repo_root,
        args.issue,
        inputs=_loop_inputs(args),
        interactive=sys.stdin.isatty(),
        confirms=_confirm_codes(args.confirm),
        grant_root=args.root,
    )
    for event in result.events:
        if isinstance(event, loop.CheckpointApproval):
            detail = f" - {event.detail}" if event.detail else ""
            print(f"checkpoint {event.checkpoint}: APPROVED ({args.issue}){detail}")
        else:
            print(_format_advance(event))
    # Block-buffered stdout would otherwise let the unbuffered stderr notice
    # below overtake the step lines it is a footnote to, in any piped output.
    sys.stdout.flush()
    if result.challenge is not None:
        name, code = result.challenge
        return _print_challenge(
            f"checkpoint {name}",
            args.issue,
            _ceremony_rerun(args, code),
            _checkpoint_meaning(name),
            result.challenge_reason,
        )
    if result.refused is not None:
        name, why = result.refused
        print(f"checkpoint {name}: REFUSED ({args.issue}) - {why}", file=sys.stderr)
        return 1
    return 1 if result.blocked else 0


def _apply_session_overrides(repo_root: Path, args: argparse.Namespace) -> tuple[str, ...]:
    """Apply ``--runner``/``--autonomy`` for this session; the pairs applied.

    Validated here rather than left to fail deep in a dispatch (basicly-jr0l.8):
    a typo'd runner name would otherwise surface as a confusing adapter miss after
    the lock was taken, and an unknown autonomy level would silently read as the
    default, which is the quiet direction on a permission control. Raises
    ``ValueError`` with the accepted values.

    Both flags are validated before either is applied (basicly-tcmy.22). Validating
    and applying one pair at a time made the refusal only *look* atomic: a valid
    ``--runner`` beside an invalid ``--autonomy`` left the runner override set in a
    process that then raised, and every caller here prints the refusal and returns
    without clearing it — so a second command in the same process would have run
    under half of a rejected pair.
    """
    pending: list[tuple[str, str, str]] = []
    if runner_name := getattr(args, "runner", None):
        known = {spec.name for spec in load_runner_config(repo_root).specs} | {"auto"}
        if runner_name not in known:
            raise ValueError(f"unknown runner {runner_name!r}; configured: {sorted(known)}")
        pending.append(("runner", "default", runner_name))
    if autonomy := getattr(args, "autonomy", None):
        if autonomy not in AUTONOMY_LEVELS:
            raise ValueError(f"unknown autonomy level {autonomy!r}; one of {list(AUTONOMY_LEVELS)}")
        pending.append(("policy", "autonomy", autonomy))
    if tier := getattr(args, "tier", None):
        if tier not in MODEL_TIERS:
            raise ValueError(f"unknown model tier {tier!r}; one of {list(MODEL_TIERS)}")
        pending.append(("runner", "default_tier", tier))
    for section, key, value in pending:
        session_config.set_override(section, key, value)
    return session_config.override_pairs()


def _print_supervise_header(
    repo_root: Path, session_id: str, overrides: tuple[str, ...], say: Callable[[str], None]
) -> None:
    """The session's opening lines: id, any override, and the process budget.

    The override line is printed, not merely recorded: an operator reading a log
    has to be able to see at a glance that this session is not running on the
    repo's committed config (basicly-jr0l.8).
    """
    say(f"session:  {session_id}")
    if overrides:
        say(f"override: {', '.join(overrides)}")
    # D1 puts this one process in charge of the machine's concurrency, so it owns
    # the global agent-process ceiling for the session (component 8).
    budget = supervise.configure_budget(repo_root)
    say(
        f"budget:   {budget.total} agent processes - {budget.lane_slots} lane, "
        f"{budget.decider_slots} decider, {budget.helper_slots} helper"
    )


# Where a detached launch's console output is kept: one file per launch, under the
# self-ignored usage tree the lane transcripts already live in, so it can never enter a
# commit. The narrative the session writes for itself is still `lane_log`'s `pass.log`;
# this file additionally holds what the process printed before that log existed — the
# refusals, which are the launches an operator most needs to read.
DETACHED_LOGS_DIR = run_record.USAGE_DIR / "detached"

# Windows' "this child gets no console at all" flag, read the way
# :data:`runner.CREATE_NEW_PROCESS_GROUP` is: ``subprocess`` defines it only on Windows,
# and the documented value is inert on a platform that has no attribute for it.
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

# The supervise options a detached launch hands to its child, ``dest`` to flag. Argparse
# keeps no such map, so this one is hand-written and pinned equal to the parser by
# `test_every_supervise_flag_reaches_the_detached_process`: a flag added later and missed
# here would be accepted by the launching process and silently never applied by the
# process that does the work — the failure mode of a forwarding table nobody checks.
SUPERVISE_FORWARDED_FLAGS = {
    "label": "--label",
    "max_passes": "--max-passes",
    "runner": "--runner",
    "autonomy": "--autonomy",
    "tier": "--tier",
}


def _detach_isolation(os_name: str) -> tuple[bool, int]:
    """``(start_new_session, creationflags)`` that cut a supervisor loose from its caller.

    POSIX gets a new session, so the process leaves the caller's process group and
    controlling terminal: neither the SIGHUP a closing terminal sends nor the group kill
    an agent tool uses at its ceiling reaches it. Windows has no sessions; the pair of
    flags is the equivalent — ``DETACHED_PROCESS`` withholds the console, so there is no
    console to send the child a CTRL_CLOSE_EVENT, and the new process group keeps a
    stray Ctrl-C from crossing over.

    The platform arrives as an argument rather than being read here, so both branches are
    asserted without patching ``os.name`` — that patch is global and flips ``pathlib``'s
    flavour for the whole process (basicly-xyx556). Same shape as
    :func:`runner._process_isolation`, which answers the neighbouring question for a
    dispatch that must stay killable as a tree.
    """
    if os_name == "nt":
        return False, DETACHED_PROCESS | runner.CREATE_NEW_PROCESS_GROUP
    return True, 0


def _detach_argv(args: argparse.Namespace) -> list[str]:
    """The detached child's command line: this same invocation, minus ``--detach``.

    ``sys.executable -m basicly.cli`` rather than the ``basicly`` console script, for the
    reason the shipped hooks invoke it that way: the interpreter running this process is
    known to have the package importable, while a console script on PATH may belong to a
    different checkout or not exist at all.
    """
    argv = [sys.executable, "-m", "basicly.cli", "loop", "supervise", args.issue]
    for dest, flag in SUPERVISE_FORWARDED_FLAGS.items():
        value = getattr(args, dest, None)
        if value is not None:
            argv += [flag, str(value)]
    return argv


def _detach_log(repo_root: Path, issue: str) -> Path:
    """The file one detached launch's output is appended to, its directory made.

    Named for the root and the launch minute, and opened for append rather than truncate:
    the lock admits one supervisor per root, so a second launch inside the same second is
    a relaunch that will refuse, and its refusal belongs in the file the operator was just
    told to read rather than in a second file nobody was told about.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in issue)
    path = repo_root / DETACHED_LOGS_DIR / f"{safe.strip('.') or 'session'}-{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _spawn_detached(argv: list[str], log: Path, *, cwd: Path) -> int:
    """Start *argv* detached from this terminal with its output in *log*; the child's pid.

    ``stdin`` is the null device on purpose: a background process that keeps the
    terminal's stdin is stopped by SIGTTIN the moment it reads, which is the state the
    hand-written incantation's ``< /dev/null`` avoided. The colour-forcing variables go
    for the reason a dispatched lane drops them (:data:`checkout.COLOUR_ENV_FORCING`) —
    the destination here is a file, where ANSI escapes are noise a reader has to strip.
    """
    new_session, creationflags = _detach_isolation(os.name)
    with log.open("ab") as sink:
        # Popen and not run: the whole point is to *not* wait for this process.
        proc = subprocess.Popen(  # noqa: S603 — argv built above from parsed args, no shell
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            env=checkout.sanitised_colour_env(os.environ),
            start_new_session=new_session,
            creationflags=creationflags,
        )
    return proc.pid


def _detach_supervisor(repo_root: Path, args: argparse.Namespace) -> int:
    """Launch this supervise invocation in its own session and return immediately.

    The engine owning this is the point (basicly-uhrji9). A round runs 20 to 40 minutes
    and an agent tool kills a background job at its own ceiling (600 s on one host); on
    2026-08-28 that took three lanes with it, dirty worktrees and ten uncommitted ledger
    events. The remedy was a per-platform shell incantation carried in a skill bullet,
    which is knowledge every operator had to hold and no consumer inherited.

    Returning 0 says the supervisor *started*, never that its session will succeed —
    that answer takes 20 minutes and is in the log this prints. A launch that refuses
    (another supervisor holds the lock, an empty lane selection) refuses in the child,
    so its reason is on the last line of that file.
    """
    log = _detach_log(repo_root, args.issue)
    pid = _spawn_detached(_detach_argv(args), log, cwd=repo_root)
    print(f"detached: pid {pid}")
    print(f"log:      {log}")
    print(f"watch:    basicly loop session {args.issue}")
    return 0


def _cmd_loop_supervise(args: argparse.Namespace) -> int:
    """The standing supervisor loop: derive, dispatch, route, land — until done.

    The full component-5 composition (basicly-kjc5.5/.6/.7): each iteration
    re-derives the session from ``br``, dispatches ready lanes concurrently
    under the heartbeated singleton lock, routes the outcomes (green lanes
    land through ``loop.advance`` and ship under a covering grant; blocks,
    stalls, and escalations enter the decision queue), and repeats. It exits 0
    when the session's work is done, or 1 when an iteration makes no progress
    — everything remaining waits on a human (see ``loop decisions``).
    """
    repo_root = _repo_root()
    # Before anything this process would hold: the lock, the pass log and the session
    # overrides all belong to the process that runs the rounds, and taking them here
    # would leave the child refused by its own parent.
    if getattr(args, "detach", False):
        return _detach_supervisor(repo_root, args)
    try:
        overrides = _apply_session_overrides(repo_root, args)
    except ValueError as exc:
        print(f"supervise: refused - {exc}", file=sys.stderr)
        return 1
    session_id = supervise.new_session_id(args.issue)
    try:
        lock = supervise.acquire(repo_root, session_id, args.issue)
    except supervise.LockHeldError as exc:
        print(f"supervise: refused - {exc}", file=sys.stderr)
        return 1
    # The pass's narrative, kept (basicly-rrah). Every line below reaches the
    # operator's terminal *and* this session's directory, because the terminal copy
    # is lost with the pane and was the only record of how a pass routed — a claim
    # about a landing depended on somebody having redirected stdout by hand.
    keep = load_runner_config(repo_root).lane_log_sessions
    log = lane_log.open_pass(repo_root, session_id, keep=keep)

    def say(line: str) -> None:
        """Print one narrative line and keep it."""
        print(line)
        log.append(line)

    if log.rotated:
        say(f"rotated:  {len(log.rotated)} lane-log session(s) dropped past the {keep} kept")
    _print_supervise_header(repo_root, session_id, overrides, say)
    # Background beats keep the lock fresh through long landings (verify
    # suites easily outlast the staleness horizon); hb.check raises promptly
    # when a contender took over so no two supervisors ever land concurrently.
    # `board` and `say` turn the beat into the board's producer: the snapshot rides the tick
    # that already runs, so wall mode is current with no second process (basicly-rn0o.7).
    # A failed emission costs one line and never the pass.
    emit = partial(board_facts.emit_tick, repo_root, lane_label=args.label)
    hb = supervise.HeartbeatThread(lock, session_id, board=emit, report=say)
    hb.start()
    try:
        return _supervise_rounds(repo_root, args, hb=hb, say=say, session_id=session_id)
    except supervise.LaneSelectionError as exc:
        # Refused rather than reported blocked: an empty selection is a mistyped
        # selector, not a session with nothing to do.
        log.append(f"refused:  {exc}")
        print(f"supervise: refused - {exc}", file=sys.stderr)
        return 1
    except supervise.LockLostError as exc:
        # On the log as well as on stderr: how a pass ended is the last thing its
        # narrative has to say, and a takeover is not visible anywhere else.
        log.append(f"stopped:  {exc}")
        print(f"supervise: stopped - {exc}", file=sys.stderr)
        return 1
    finally:
        hb.stop()
        supervise.release(lock, session_id)
        log.close()


def _supervise_rounds(
    repo_root: Path,
    args: argparse.Namespace,
    *,
    hb: supervise.HeartbeatThread,
    say: Callable[[str], None],
    session_id: str,
) -> int:
    """The standing loop itself: derive, bound, run a round, route — until it ends.

    Split from the command (basicly-o40x) so the command is the session's lifecycle —
    lock, narrative, exit code — and this is the loop's three exits: done, bounded, or
    nothing left that can progress.
    """
    # Lanes the last pass held: already committed and green, so this pass owes
    # them a landing, not a fresh implement run (basicly-kjc5.18).
    carried: frozenset[str] = frozenset()
    passes = 0
    while True:
        hb.check()
        state = supervise.derive_session(
            repo_root, args.issue, lane_label=args.label, session_id=session_id
        )
        _print_session(state, say)
        if state.done:
            say("done:     yes")
            return 0
        # Plus the lanes whose commits are already on their branch, read from git:
        # the carry above is in-process, so a crash between passes would otherwise
        # re-dispatch work that only needs landing (basicly-pjaudy).
        carried |= supervise.committed_lanes(repo_root, state)
        # Both bounds are read at the round boundary: the previous round's lanes have
        # landed through the routing layer and the next one has seeded nothing, so
        # returning here interrupts no agent (basicly-o40x).
        if ended := supervise.session_end_reason(
            repo_root,
            state,
            passes=passes,
            limit=getattr(args, "max_passes", None),
            carried=carried,
        ):
            say(ended)
            return 1
        routed = _supervise_pass(repo_root, state, hb=hb, carried=carried, say=say)
        passes += 1
        carried = supervise.carried_forward(routed)
        for routing in routed:
            say(f"routed:   {routing.issue_id} -> {routing.route} - {routing.detail}")
        if not supervise.should_continue(routed):
            _print_blocked(repo_root, args.issue, say)
            return 1


def _print_blocked(repo_root: Path, issue: str, say: Callable[[str], None]) -> None:
    """Why a pass that made no progress stopped: a human's queue, or nothing to do."""
    pending = decisions.pending(repo_root, issue)
    if pending:
        say(f"blocked:  {len(pending)} decision(s) await a human (basicly loop decisions)")
    else:
        say("blocked:  no ready lanes and nothing to land")


def _supervise_pass(
    repo_root: Path,
    state: supervise.SessionState,
    *,
    hb: supervise.HeartbeatThread,
    carried: frozenset[str],
    say: Callable[[str], None],
) -> tuple[supervise.RoutedOutcome, ...]:
    """One iteration of the standing loop: delegate, seed, dispatch, route.

    Split out of :func:`_cmd_loop_supervise` so the command is the session's
    lifecycle — lock, narrative, heartbeat, exit code — and this is what a pass
    does. Returns the routed outcomes; the caller decides whether they continue.
    The pass reads its root off *state* rather than off the command's arguments,
    which is the same object every step below already derives from.
    """
    # Read the spend ceiling once per pass and hand it to the dispatcher, so the
    # halt is printed with its numbers instead of looking like an idle pass
    # (basicly-kjc5.23).
    admission = policy.spend_status(repo_root, state.root_issue)
    # Delegate before dispatching (basicly-kjc5.40): a pending item only holds its
    # lane, so an item the decider answers now releases that lane in this same pass
    # instead of the next one.
    delegated = supervise.delegate_decisions(repo_root, state, beat=hb.check, admission=admission)
    supervise.say_delegated(delegated, say)
    # Let the graph learn from discoveries before this pass reads it
    # (basicly-kjc5.24): an edge added now gates dispatch and orders the landings in
    # this same pass, not the next one.
    for bead, coupled_to, dep_type in supervise.propose_coupling_edges(repo_root, state):
        say(f"coupling: {bead} -> {coupled_to} ({dep_type}) - from a found-info record")
    # Dispose of bindings that outlived their worktrees before anything reads the
    # lane set again (basicly-1koh): such a lane derives `build` off the ref alone,
    # so it is invisible to dispatch and to the parked advance, and left alone it is
    # re-adopted and re-discarded every pass forever. Folded into `routed` below so a
    # pass that only repaired still counts as progress and re-derives instead of
    # reporting itself blocked.
    repaired = supervise.repair_stale_bindings(repo_root, state)
    # Then provision lanes if the pass still has nothing to dispatch (basicly-t73d).
    # After the repair, so a cleared binding is re-provisioned in the same pass rather
    # than the next one; before dispatch, because the whole point is that `loop
    # supervise <root>` on a cold root used to report "nothing to land" while its
    # children sat at intake.
    repaired += supervise.seed_lanes(repo_root, state, skip=carried, admission=admission)
    outcomes = supervise.dispatch_lanes(
        repo_root, state, beat=hb.check, skip=carried, admission=admission, report=say
    )
    supervise.say_dispatch(outcomes, carried=carried, admission=admission, say=say)
    routed = repaired + supervise.route_outcomes(
        repo_root, state, outcomes, beat=hb.check, carried=carried
    )
    return routed + supervise.advance_parked(repo_root, state, beat=hb.check)


def _print_session(state: supervise.SessionState, say: Callable[[str], None]) -> None:
    say(f"root:     {state.root_issue} ({state.root_status})")
    open_children = state.open_children
    if state.lane_label is not None:
        # Which session these counts describe, printed for the same reason the config
        # override is: a log has to show that the lanes are a labelled cut and not the
        # root's own children (basicly-1lpo).
        say(f"select:   label {state.lane_label!r}")
    say(f"children: {len(state.children)} total, {len(open_children)} open")
    if state.adopted:
        for lane in state.adopted:
            liveness = "live" if lane.live else "worktree missing"
            say(
                f"adopted:  {lane.issue_id} ({lane.status}) -> "
                f"{lane.binding.name} on {lane.binding.branch} [{liveness}]"
            )
    else:
        say("adopted:  (no in-flight lanes)")


def _cmd_loop_session(args: argparse.Namespace) -> int:
    """Attach to a supervisor session and print its live status (design 7.3).

    The observe half of the client attach protocol: a pure read, so it never
    contends for the lock a running supervisor holds and it is equally valid on
    a root nobody is supervising. Exits 0 whichever it finds — the observation
    itself succeeded; ``loop decisions`` is the command that signals blocked.

    ``--label`` has to be the one the supervisor was started with: the lane set is
    then a labelled cut rather than the root's children, and omitting it observes a
    different session than the one running (basicly-1lpo).

    **The spend pair is windowed, and that is a finding rather than a preference.**
    Settled 2026-08-16 with ``uv run basicly loop session basicly-kjc5 --json`` read
    against ``policy.session_spend``/``policy.active_grant`` on the same root
    (basicly-e2mz.13): its 177970761 spent against a 4000000 budget is a scope
    mismatch, not a ceiling that failed to fire. 3361523 of that total is kjc5's own
    decomposition and all of it predates the grant — the grant's baseline is that
    exact number — while the remaining 174609238 belongs to 28 beads the session walk
    reaches only through ``blocks`` edges: 22 are children of five other epics (jr0l,
    tcmy, u6jq, yc0x, vkh0) and 6 are parentless roots, the track spanning several
    parents and some parentless beads exactly as :func:`policy.session_issue_ids`
    says it does. The ceiling does cover this root: ``policy.spend_status`` reports
    it halted with 0 remaining.

    Subtracting the baseline does not by itself collapse the ratio — 174609238
    against 4000000 is still 43.7x — because the two figures differ in the
    *population* they count and not only in the window: the budget was granted over
    a track whose gated work runs its own dispatches under its own roots' grants. So
    the surface names both, and the surface is what changed here, not the control.

    Read the spend figures on the machine that ran the work: :func:`policy.session_spend`
    reads the self-ignored ``.basicly/usage/`` store, so the same root reports 177970761
    spent and halted at that checkout and 0 spent with the full budget remaining from a
    linked worktree or a fresh clone, where only :func:`run_record.tracker_history` still
    has the record.
    """
    repo_root = _repo_root()
    view = supervise.observe(repo_root, args.issue, lane_label=args.label)
    # One extra tracker read rather than a second session walk: the baseline lives on
    # the grant marker, and re-deriving spend costs a whole ~3s walk of the track.
    grant = policy.active_grant(repo_root, args.issue)
    if args.json:
        # ``supervised`` is a derived property, which asdict drops — and it is the
        # one question a machine client always asks, so emit it explicitly.
        payload = (
            asdict(view)
            | {"supervised": view.supervised}
            | _spend_payload(view.spent_tokens, grant)
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    _print_observation(view, grant)
    return 0


# What each spend figure covers, emitted beside the numbers. ``spent_tokens`` and
# ``token_budget`` are left where they are so a client reading them keeps working, and
# a reader who would otherwise divide one by the other is told they are two windows.
SPEND_WINDOWS = {
    "spent_tokens": (
        "every measured run record on this session's track - its decomposition and the "
        "work it gates - across every grant and all time"
    ),
    "grant_spent_tokens": "spent_tokens since the active grant was issued, null when there is none",
    "token_budget": "the ceiling on grant_spent_tokens alone, never on spent_tokens",
}


def _spend_payload(spent_tokens: int, grant: policy.Grant | None) -> dict[str, object]:
    """The comparable spend figure and the window every reported figure covers."""
    return {
        "grant_spent_tokens": (
            None if grant is None else policy.tokens_under_grant(spent_tokens, grant)
        ),
        "grant_baseline_tokens": None if grant is None else grant.spent_at_issue,
        "spend_windows": SPEND_WINDOWS,
    }


def _cmd_loop_stop(args: argparse.Namespace) -> int:
    """Ask the running supervisor to finish its round and return (basicly-o40x).

    Refused when nobody is supervising this root: the marker outlives the command, so
    an unread one would stop the next session started here before it ran a round.
    """
    repo_root = _repo_root()
    if not args.reason.strip():
        print("stop: refused - --reason must say why the session is stopping", file=sys.stderr)
        return 1
    view = supervise.observe(repo_root, args.issue, lane_label=args.label)
    if not view.supervised or view.holder is None:
        print(
            f"stop: refused - {args.issue} is not supervised: {_supervisor_line(view)}",
            file=sys.stderr,
        )
        return 1
    supervise.request_stop(repo_root, args.issue, requested_by=args.by, reason=args.reason)
    print(f"stop: requested by {args.by} - {args.reason}")
    for lane in view.lanes:
        print(f"landing: {lane.issue_id} ({lane.status}) on {lane.branch}")
    if not view.lanes:
        print("landing: (none in flight)")
    try:
        supervise.await_session_return(repo_root, view.holder.session_id or "")
    except KeyboardInterrupt:
        # The marker outlives the wait, so giving up on watching does not undo it.
        print("stop: still requested - the supervisor returns after its current round")
        return 0
    print("stop: the supervisor returned; its round's lanes landed")
    return 0


def _supervisor_line(view: supervise.Observation) -> str:
    """One line naming who is supervising, and whether their heartbeat is fresh."""
    holder = view.holder
    if holder is None:
        return "(none running - basicly loop supervise <root> starts one)"
    who = f"{holder.session_id or 'unknown'} (pid {holder.pid or '?'})"
    beat = f"heartbeat {holder.age_s:.0f}s old"
    if view.holder_stale:
        beat += f" - STALE (over {supervise.STALE_AFTER_S:.0f}s; a contender may take over)"
    if not view.holder_on_this_root:
        # Both facts matter to a foreign holder: this session is unsupervised,
        # and a stale foreign lock is one a supervisor here could take over.
        other = holder.root_issue or "an unknown root"
        return f"{who} - supervising {other}, not this session; {beat}"
    return f"{who} - {beat}"


def _print_observation(view: supervise.Observation, grant: policy.Grant | None) -> None:
    print(f"root:       {view.root_issue} ({view.root_status})")
    print(f"supervisor: {_supervisor_line(view)}")
    if view.lane_label is not None:
        print(f"select:     label {view.lane_label!r}")
    print(f"children:   {view.children_total} total, {view.children_open} open")
    if view.lanes:
        for lane in view.lanes:
            liveness = "live" if lane.live else "worktree missing"
            print(
                f"lane:       {lane.issue_id} ({lane.status}) -> "
                f"{lane.worktree} on {lane.branch} [{liveness}]"
            )
            if lane.last_outcome is not None:
                tokens = f", {lane.last_tokens} tokens" if lane.last_tokens is not None else ""
                print(
                    f"              last run: {lane.last_agent} {lane.last_outcome} "
                    f"at {lane.last_run_at}{tokens}"
                )
    else:
        print("lane:       (no in-flight lanes)")
    pending = view.pending_decisions
    if pending:
        print(f"decisions:  {len(pending)} pending - answer with basicly loop answer <id> <text>")
        for item in pending:
            print(f"  {_format_decision(item)}")
    else:
        print("decisions:  none pending")
    if grant is None:
        print(
            f"grant:      (none) - {view.spent_tokens} tokens over this session's track, all time"
        )
    else:
        budget = view.token_budget if view.token_budget is not None else "unbounded"
        under = policy.tokens_under_grant(view.spent_tokens, grant)
        # Level and budget off the observation, which already carries them; the grant is
        # read only for the baseline, which it alone has.
        print(
            f"grant:      {view.grant_level}, {under}/{budget} tokens under this grant "
            f"(issued at {grant.spent_at_issue} spent)"
        )
        print(
            f"lifetime:   {view.spent_tokens} tokens over this session's track - its "
            "decomposition and the work it gates - across every grant and all time"
        )
    print(
        f"wait:       {_format_duration(view.human_wait_s)} human, "
        f"{_format_duration(view.delegated_wait_s)} delegated "
        f"(dispatch {_format_duration(view.dispatch_s)})"
    )
    if view.done:
        print("done:       yes")


def _format_duration(seconds: float) -> str:
    """A duration at the precision an operator reads it in: s, then m, then h."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _format_decision(item: decisions.DecisionItem) -> str:
    line = f"{item.decision_id}  [{item.kind}]  {item.question}"
    if item.detail:
        line += f"\n    {item.detail}"
    if not item.pending:
        line += f"\n    answered by {item.answered_by}: {item.answer}"
    return line


def _cmd_loop_decisions(args: argparse.Namespace) -> int:
    """List the session's pending decisions — a pure read over br (design 7.3)."""
    repo_root = _repo_root()
    items = decisions.pending(repo_root, args.issue)
    if args.json:
        print(json.dumps([asdict(item) for item in items], indent=2, sort_keys=True))
        return 0
    if not items:
        print(f"decisions: none pending ({args.issue})")
        return 0
    for item in items:
        print(_format_decision(item))
    return 1  # pending decisions mean the session is blocked on judgment


# An answer that chooses `retry` from "retry, re-dispatch, or park?". Anchored on
# the leading token so a rationale may follow ("retry - the gate flake was
# unrelated"), while `re-dispatch` and `park` are not mistaken for it.
_RETRY_ANSWER = re.compile(r"^\s*retry\b", re.IGNORECASE)


def _carry_out_rework_retry(repo_root: Path, item: decisions.DecisionItem) -> str | None:
    """Grant one further attempt when *item* is a rework escalation answered `retry`.

    The answer is the decision; this is the engine carrying it out, so the
    operator does not have to know that a second command exists (basicly-4tjt —
    the escalation used to offer three choices and implement none of them).

    A decider's answer deliberately does not grant. An autonomy grant may dispose
    of the question, but extending the rework budget that bounds a model's own
    retries is not something that model gets to do for itself; the engine
    disposes. The operator still has `basicly policy rework --allow-retry`.
    """
    if item.kind != policy.REWORK_ESCALATION_KIND:
        return None
    if not _RETRY_ANSWER.match(item.answer or ""):
        return None
    if (item.answered_by or "").startswith(decisions.DECIDER_BY_PREFIX):
        return None
    gate = policy.gate_from_rework_escalation(item.question)
    if gate is None:
        return None
    charged = policy.grant_rework_allowance(repo_root, item.issue_id, gate)
    cap = load_policy_config(repo_root).max_rework
    return f"granted one further attempt on gate '{gate}' (rework now {charged}/{cap})"


# Every question that offers the route ends with it, in five wordings across
# `policy.rework_escalation_question` and `supervise._capped_dispatch` — "retry or
# park?", "re-scope it, serialize it, or park?" and three more. The carrier binds on
# this suffix rather than on a decision kind, which is what let a `stall` offer the
# route and have the answer silently do nothing (basicly-vkjt).
_OFFERS_PARK = "or park?"


def _carry_out_rework_hold(repo_root: Path, item: decisions.DecisionItem) -> str | None:
    """Park the lane when *item* is an escalation answered ``park`` (D3's Hold).

    :func:`_carry_out_rework_retry`'s sibling, and the same defect one verb over:
    every escalation this engine raises offers ``park`` as a route and nothing
    anywhere took it, so an operator who parked a lane watched the next supervised
    pass dispatch it again. The requirements document read that as a status
    fail-open — ``park`` "re-admits the lane" — which it is not: ``deferred`` is
    already outside ``loop_state.DISPATCHABLE_STATUSES`` and
    ``supervise.ready_lanes`` already refuses on it. The missing half was here.

    Bound on the question, not on the decision kind. That distinction is the whole
    of basicly-vkjt: this said "every escalation kind, not only the rework cap" while
    guarding on ``kind == REWORK_ESCALATION_KIND``, so a ``stall`` whose question
    offered ``park`` accepted the answer and did nothing with it. The gate is recorded
    when the question names one, and a stall names none.

    Refused for a delegated answer, like ``land anyway``. A deferred child leaves
    the open-child set, so it stops holding its parent open: a model that could
    park its own lane could drop a requirement and let the package close over the
    hole — the same authority D15 keeps human for Kill.
    """
    if _OFFERS_PARK not in item.question.lower():
        return None
    if not policy.answer_holds(item.answer or ""):
        return None
    if (item.answered_by or "").startswith(decisions.DECIDER_BY_PREFIX):
        return (
            f"note: a delegated answer does not park {item.issue_id} — a human must "
            "park it, or answer with a route that keeps the work"
        )
    gate = policy.gate_from_rework_escalation(item.question)
    policy.hold_lane(repo_root, item.issue_id, item.answer or "", gate=gate)
    return (
        f"parked {item.issue_id}: status {policy.HELD_STATUS} with the reason recorded — "
        "dispatch refuses it until a human reopens it"
    )


def _announce_land_anyway(item: decisions.DecisionItem) -> str | None:
    """Say what an answered `land anyway` will do, or that it will do nothing.

    Unlike ``retry``, this override is carried out by the landing itself
    (``landing_gate.gate_override``) rather than here, because that is where it is
    spent. So this reports instead of granting — but an answer whose whole point is
    that the engine now acts on it must not print the same single line as one that
    changed nothing, which is how this defect stayed invisible (basicly-tcmy.6).

    A delegated answer is told plainly that it authorises nothing: skipping a landing
    gate is not a call a model makes for itself, and the queue would otherwise read as
    disposed while the lane still held.
    """
    if item.kind != policy.REWORK_ESCALATION_KIND:
        return None
    if not policy.answer_lands_anyway(item.answer or ""):
        return None
    gate = policy.gate_from_unreliable_escalation(item.question)
    if gate is None:
        return None
    if (item.answered_by or "").startswith(decisions.DECIDER_BY_PREFIX):
        return (
            f"note: a delegated answer does not override gate '{gate}' — "
            "a human must authorise that, or the flake must be fixed"
        )
    return f"the next landing of {item.issue_id} will skip gate '{gate}', once"


def _cmd_loop_answer(args: argparse.Namespace) -> int:
    """Record a human answer on a queued decision, with attribution."""
    repo_root = _repo_root()
    by = args.by or "human"
    try:
        item = decisions.answer(repo_root, args.decision_id, args.text, by=by)
    except ValueError as exc:
        print(f"answer: refused - {exc}", file=sys.stderr)
        return 1
    print(f"answered {item.decision_id} by {by}")
    if granted := _carry_out_rework_retry(repo_root, item):
        print(granted)
    if parked := _carry_out_rework_hold(repo_root, item):
        print(parked)
    if note := _announce_land_anyway(item):
        print(note)
    return 0


# What killing does, stated at the prompt the way :data:`_CHECKPOINT_MEANING`
# states what approving does. The protocol asks the driver to "say what this
# does", and Kill is the one verb where the answer is that a requirement stops
# existing — so the prompt says it, and says what happens to the code.
_KILL_MEANING = (
    "Killing closes this bead as won't-do-this-way. The requirement is dropped:",
    "nothing re-dispatches it, and it stops holding its parent open — the package",
    "can now close without it. There is no un-kill; re-opening means a new bead.",
    "Its worktree is torn down. Committed work is left on the harness branch, and",
    "--discard deletes that branch and any uncommitted changes with it.",
)


def _tear_down_killed_lane(
    repo_root: Path, binding: loop_state.WorktreeBinding | None, *, discard: bool
) -> str | None:
    """Remove the killed lane's worktree; the refusal text when it still holds work.

    A bead with no *binding* never provisioned a worktree, which is not an error —
    Kill reaches a lane at any phase, including one that never built.

    Without *discard* the teardown keeps everything that could still be wanted:
    ``worktree.cleanup`` refuses on uncommitted changes and leaves an unmerged
    branch in place with a note. So a killed lane's committed work stays
    recoverable from its ``harness/`` branch rather than being deleted by a verb
    whose whole meaning is "not this way" — the requirement is dropped, the
    evidence of the attempt is not. ``--discard`` is the deliberate opposite.

    ``SystemExit`` is caught rather than left to ``main``, which handles
    ``Exception`` and so would let this one out as a bare non-zero exit with the
    bead half-killed and nothing said about why.
    """
    if binding is None:
        return None
    try:
        worktree.cleanup(binding.name, force=discard, repo_root=repo_root)
    except SystemExit as exc:
        return f"{exc}\n  Re-run with --discard to abandon them (a fresh confirm code is minted)."
    return None


def _cmd_loop_kill(args: argparse.Namespace) -> int:
    """Kill a lane: tear its worktree down and close it won't-do-this-way (D3, D15).

    Ordered so no failure can close a bead that still holds a live lane: authorize,
    tear the worktree down, then record the reason and close. A teardown that
    refuses therefore leaves the bead open with the confirm code already spent —
    one re-run and one new challenge — where the other order leaves a closed bead
    bound to a worktree the loop can no longer reach, which has no route back.

    Everything that can refuse without human judgment refuses *before* the
    challenge, so a kill that was never going to be accepted does not cost a code
    relay first: a blank reason, and a bead the tracker cannot produce a record for
    (a typo'd id would otherwise consume the code and then fail on the read).
    """
    repo_root = _repo_root()
    if not args.reason.strip():
        print(
            f"kill: REFUSED ({args.issue}) - --reason must say why this work is not "
            "being done; it is the only record left once the bead closes",
            file=sys.stderr,
        )
        return 1
    binding = loop_state.parse_worktree_ref(
        tracker.require_record(repo_root, args.issue).get("external_ref")
    )
    result = policy.authorize_kill(repo_root, args.issue, confirm=args.confirm)
    if result.status == "challenge":
        rerun = (
            f"basicly loop kill {args.issue} --reason {shlex.quote(args.reason)}"
            + (" --discard" if args.discard else "")
            + f" --confirm {result.code}"
        )
        return _print_challenge("kill", args.issue, rerun, _meaning_block(_KILL_MEANING))
    if result.status != "approved":
        print(f"kill: REFUSED ({args.issue}) - {result.detail}", file=sys.stderr)
        return 1
    if held := _tear_down_killed_lane(repo_root, binding, discard=args.discard):
        print(f"kill: REFUSED ({args.issue}) - {held}", file=sys.stderr)
        return 1
    policy.kill_lane(repo_root, args.issue, args.reason)
    print(f"kill: CLOSED {args.issue} - {args.reason}")
    return 0


def _cmd_loop_decide(args: argparse.Namespace) -> int:
    """Invoke the decider agent on one decision (corpus-bounded, design 7.1)."""
    repo_root = _repo_root()
    already = decisions.get(repo_root, args.decision_id)
    if already is not None and not already.pending:
        print(f"decide: already answered by {already.answered_by}: {already.answer}")
        return 0
    try:
        outcome = decisions.invoke_decider(repo_root, args.decision_id, args.root)
    except ValueError as exc:
        print(f"decide: refused - {exc}", file=sys.stderr)
        return 1
    if isinstance(outcome, decisions.DecisionItem):
        print(f"decided {outcome.decision_id} by {outcome.answered_by}: {outcome.answer}")
        return 0
    print(f"decide: abstained - {outcome.rationale or 'not derivable from the corpus'}")
    return 1


def _cmd_loop_watch(args: argparse.Namespace) -> int:
    """Poll the queue and print newly pending decisions until interrupted."""
    repo_root = _repo_root()
    seen: set[str] = set()
    try:
        while True:
            for item in decisions.pending(repo_root, args.issue):
                if item.decision_id not in seen:
                    seen.add(item.decision_id)
                    print(_format_decision(item))
            if args.once:
                return 1 if seen else 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        # Ctrl-C can land inside the br-subprocess scan, not just the sleep.
        return 0


def cmd_runner(args: argparse.Namespace) -> int:
    """Dispatch the ``runner`` subcommands (list / dry-run / run)."""
    handlers = {
        "list": _cmd_runner_list,
        "dry-run": _cmd_runner_dry_run,
        "run": _cmd_runner_run,
    }
    return _dispatch(args, "runner_command", handlers, group="runner")


def _resolve_runner(args: argparse.Namespace) -> runner.RunnerSpec:
    """Resolve the runner from --runner (or the configured [runner].default)."""
    config = load_runner_config(_repo_root())
    return runner.select_runner(
        config.specs, args.runner or config.default, capable=runner.is_capable
    )


def _cmd_runner_list(_args: argparse.Namespace) -> int:
    """List the configured runner adapters, their availability + capability, and the selection."""
    config = load_runner_config(_repo_root())
    print(f"default: {config.default}")
    for spec in config.specs:
        if spec.kind == runner.HANDOFF:
            print(f"- {spec.name} [{spec.kind}] — always available (work handed off)")
            continue
        model = f" (model: {spec.model})" if spec.model else ""
        if not runner.is_available(spec):
            print(f"- {spec.name} [{spec.kind}] — not on PATH: {shlex.join(spec.command)}{model}")
            continue
        cap = runner.probe_capability(spec)
        capability = "capable" if cap.flag_ok else f"flag unconfirmed — {cap.detail}"
        print(
            f"- {spec.name} [{spec.kind}] — available, {capability}: "
            f"{shlex.join(spec.command)}{model}"
        )
    resolved = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
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
    # Read the model off the resolution, not the spec: a tier is resolved inside
    # run(), so the spec still says None and printing that would contradict the
    # argv below (basicly-kjc5.59). This is the surface the config comment points
    # a consumer at to check a tier before a live run, so it has to show the id.
    resolution = result.model_resolution
    if resolution is not None and resolution.tier:
        print(f"  tier: {resolution.tier} ({resolution.source})")
    if resolution is not None and resolution.model:
        print(f"  model: {resolution.model}")
    elif spec.model:
        print(f"  model: {spec.model}")
    if resolution is not None and not resolution.honoured:
        print(f"  tier not honoured: {resolution.note}")
    if spec.sandbox:
        print(f"  sandbox: {spec.sandbox}")
    if spec.approval:
        print(f"  approval: {spec.approval}")
    print(f"  {shlex.join(result.command)}")
    # A pinned sandbox/approval outside the CLI's enum makes every dispatch exit
    # at argument parsing (basicly-jr0l.38). Dry-run is where that is meant to
    # surface — at authoring time, by name — so it fails rather than printing an
    # argv that cannot run.
    if rejected := runner.probe_guardrails(spec):
        for problem in rejected:
            print(f"  guardrail: {problem}", file=sys.stderr)
        return 1
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
    return _dispatch(args, "worktree_command", handlers, group="worktree")


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
        if queued.bounced:
            coupling = (
                f"; coupling recorded on {', '.join(queued.couplings)}" if queued.couplings else ""
            )
            line += f"  [bounced back to the lane{coupling}]"
        if not outcome.merged and not queued.deferred:
            line += f"  [rework {queued.attempts}: {'ESCALATE' if queued.escalate else 'retry'}]"
        print(line)

    merged = sum(1 for queued in results if queued.result.merged)
    summary = f"merge-queue: {merged}/{len(items)} merged"
    bounced = sum(1 for queued in results if queued.bounced)
    deferred = sum(1 for queued in results if queued.deferred)
    if bounced or deferred:
        summary += f" ({bounced} bounced, {deferred} deferred)"
    print(summary)
    return 0 if merged == len(items) else 1


def _cmd_worktree_create(args: argparse.Namespace) -> int:
    """Create + provision a worktree, honoring the configured base and cap.

    When NAME is a tracked record, this writes the binding too, as the loop's own seeding
    advance does. The two paths used to disagree about the same fact: `derive_phase` reads
    `build` off the binding alone, so a hand-provisioned lane sat at `intake` with work in
    flight, no advance could land a merge that had already happened, and the record missed
    its landing gate, its ship checkpoint and its release record (basicly-i8urje).
    Refusing to provision is the other honest remedy, and is not taken: `worktree-isolation`
    ships this verb as the documented way to isolate work by hand.

    A NAME that is not a record provisions exactly as before, binding nothing.
    """
    repo_root = _repo_root()
    config = load_worktree_config(repo_root)
    record = tracker.read_record(repo_root, args.name)
    bound = loop_state.parse_worktree_ref(record.get("external_ref")) if record else None
    if bound is not None:
        # Before provisioning, not after: a second binding overwrites the first, and the
        # lane it named — whose branch may hold unlanded commits — becomes unreachable
        # from every advance that reads the ref.
        print(
            f"Error: {args.name} is already bound to worktree {bound.name!r} on "
            f"{bound.branch!r}; land or clean up that lane before provisioning another",
            file=sys.stderr,
        )
        return 1
    refusal = worktree.cap_refusal(config.concurrency, repo_root)
    if refusal:
        print(f"Error: {refusal}", file=sys.stderr)
        return 1
    session = worktree.create(args.name, base=args.base or config.base_branch, repo_root=repo_root)
    if record is None:
        return 0
    return _bind_provisioned_worktree(repo_root, args.name, session)


def _bind_provisioned_worktree(repo_root: Path, issue_id: str, session: worktree.Session) -> int:
    """Stash *session* on *issue_id*'s external_ref; non-zero when the write did not land.

    Reported rather than raised: the worktree exists by now, so the tree is in exactly the
    unbound state this command was changed to prevent, and the caller needs the record to
    bind by hand rather than a traceback.
    """
    ref = loop_state.format_worktree_ref(session.name, session.branch)
    try:
        tracker.write(repo_root, ["update", issue_id, "--external-ref", ref])
    except RuntimeError as exc:
        print(
            f"Error: worktree {session.name!r} exists but {issue_id} could not be bound "
            f"to it ({exc}); the loop will read this record as intake until "
            f"`basicly tracker update {issue_id} --external-ref {ref}` lands",
            file=sys.stderr,
        )
        return 1
    print(f"  bound:  {issue_id} -> {ref}")
    return 0


def _cmd_worktree_cleanup(args: argparse.Namespace) -> int:
    """Remove a worktree and delete its merged branch."""
    worktree.cleanup(args.name, force=args.force, repo_root=_repo_root())
    return 0


def _cmd_worktree_list(_args: argparse.Namespace) -> int:
    """List worktree sessions, marking any whose directory has vanished."""
    sessions = worktree.list_sessions(_repo_root())
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


def _add_lifecycle_parsers(subparsers: argparse._SubParsersAction) -> None:
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
    install_parser.add_argument(
        "--technologies",
        help=(
            "Comma-separated technology selection to record in basicly.toml "
            f"(allowed: {', '.join(sorted(TECHNOLOGIES))}); technology-tagged "
            "catalog sources outside it are skipped at projection time"
        ),
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


def _add_agents_parsers(subparsers: argparse._SubParsersAction) -> None:
    roots = ", ".join(root.path.as_posix() for root in agents.AGENTS_OUTPUT_ROOTS)
    subparsers.add_parser(
        "agents-build", help=f"Project agents from .basicly/core/agents into {roots}"
    )
    subparsers.add_parser("agents-check", help=f"Check projected agents are up to date in {roots}")


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


_CATALOG_KINDS = ("fragment", "skill", "agent")


def _add_catalog_parser(subparsers: argparse._SubParsersAction) -> None:
    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Author and inspect the catalog sources (lint/verify/review/new/list/dump)",
    )
    catalog_sub = catalog_parser.add_subparsers(dest="catalog_command", required=True)

    catalog_sub.add_parser(
        "lint",
        help="Validate catalog YAML sources (schema, no .md sources, single extension)",
    )
    catalog_sub.add_parser(
        "verify",
        help="Verify catalog content (lint + duplicate/contradiction/ambiguity/scope checks)",
    )
    c_review = catalog_sub.add_parser(
        "review",
        help="Advisory agent-assisted semantic review of the rendered files (never blocks)",
    )
    c_review.add_argument(
        "--runner", help="Runner name or 'auto' (default: the configured [runner].default)"
    )
    c_review.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the assembled review prompt without invoking any agent",
    )

    c_new = catalog_sub.add_parser("new", help="Scaffold a new fragment/skill/agent source")
    c_new.add_argument("kind", choices=_CATALOG_KINDS, help="Source kind to scaffold")
    c_new.add_argument("name", help="Source name (fragment id, or skill/agent slug)")
    c_new.add_argument(
        "--category",
        default="project",
        choices=sorted(CATEGORIES),
        help="Fragment category (only used when kind is 'fragment')",
    )
    c_new.add_argument("--description", help="One-line description")

    c_list = catalog_sub.add_parser("list", help="List catalog sources of the given kind")
    c_list.add_argument(
        "kind",
        nargs="?",
        default="fragment",
        choices=_CATALOG_KINDS,
        help="Source kind to list (default: fragment)",
    )

    catalog_sub.add_parser(
        "dump",
        help="Print the composed fragment selection: per-item origin and selecting axes",
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
    verify_parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply each check's declared fix_command (mechanical repairs only) before checking",
    )
    verify_parser.add_argument("--issue", help="Record the verdict as a br gate on this issue id")
    verify_parser.add_argument(
        "--gate",
        default=verify.DEFAULT_GATE,
        help=f"Gate name to record (default: {verify.DEFAULT_GATE})",
    )


def _add_commit_parser(subparsers: argparse._SubParsersAction) -> None:
    commit_parser = subparsers.add_parser(
        "commit",
        help="Commit the staged change with an envelope derived from engine state",
        description=(
            "Assemble a conventional-commit message whose type, scope, and trailing bead "
            "id come from engine state, and commit with it. Only the description is "
            "authored input; the commit-msg hooks stay the gate."
        ),
    )
    commit_parser.add_argument(
        "description",
        help="The authored part: lowercase letters, digits, spaces, and hyphens only",
    )
    commit_parser.add_argument(
        "--body", help="Free-form commit body (where capitals, dots, and filenames belong)"
    )
    commit_parser.add_argument(
        "--issue", help="Bead id to reference (default: the bead bound to the current branch)"
    )
    commit_parser.add_argument(
        "--type",
        choices=commit.ALLOWED_TYPES,
        help="Override the type derived from the bead's work class and the staged paths",
    )
    commit_parser.add_argument(
        "--scope", help="Override the scope derived from the staged paths (lowercase-kebab-case)"
    )
    commit_parser.add_argument(
        "--breaking", action="store_true", help="Mark a breaking change (the '!' before the colon)"
    )
    commit_parser.add_argument(
        "--dry-run", action="store_true", help="Print the assembled message without committing"
    )


def _add_decompose_parser(subparsers: argparse._SubParsersAction) -> None:
    decompose_parser = subparsers.add_parser(
        "decompose",
        help="Turn a feature into child br issues + a computed dependency graph",
    )
    decompose_parser.add_argument("feature", help="Parent feature issue id")
    decompose_parser.add_argument(
        "--plan",
        help=(
            "Plan file with a 'children' list of {title, acceptance, scope, shared?, type?} "
            "(.toml or .json); reads JSON on stdin if omitted. 'shared' names literal paths "
            "from that child's 'scope' it only appends to (a manifest, a lockfile), which stops "
            "one path serializing every child that declares it"
        ),
    )
    decompose_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute grouping/serial chains without creating any issues",
    )


def _add_release_parser(subparsers: argparse._SubParsersAction) -> None:
    release_parser = subparsers.add_parser(
        "release",
        help="Produce a release up to the annotated tag (never pushes; component 9)",
    )
    # Positional, not --version: the top-level parser already owns `--version` for
    # printing the engine's own version, and two meanings of one flag on one
    # command line is a trap.
    release_parser.add_argument("version", help="Target semantic version, e.g. 0.6.0")
    release_parser.add_argument(
        "--issue",
        required=True,
        help="Beads issue id for the release commit (the commit-msg hook requires one)",
    )
    release_parser.add_argument(
        "--date", help="Release date YYYY-MM-DD for the changelog heading (default: today)"
    )
    release_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report every step and write nothing (refusal checks still run)",
    )
    release_parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Non-interactive invocation: refused unless --root carries a green L3 grant",
    )
    release_parser.add_argument(
        "--root",
        metavar="ISSUE",
        help="Session root issue the L3 grant is checked against (requires --autonomous)",
    )
    release_parser.add_argument(
        "--shipping",
        metavar="ISSUE",
        help="Node whose required gates must be green for --autonomous "
        "(default: --root; an open epic's own verify gate is never green, kjc5.39)",
    )


def _add_policy_parser(subparsers: argparse._SubParsersAction) -> None:
    policy_parser = subparsers.add_parser(
        "policy", help="Loop gate/checkpoint policy checks (DoR, gates, rework, checkpoints)"
    )
    policy_sub = policy_parser.add_subparsers(dest="policy_command", required=True)
    p_dor = policy_sub.add_parser("dor", help="Check a record against Definition-of-Ready")
    p_dor.add_argument("issue")
    p_scaffold = policy_sub.add_parser(
        "scaffold", help="Print a bead body with every section the DoR requires for a work type"
    )
    p_scaffold.add_argument(
        "--type",
        required=True,
        choices=WORK_TYPES,
        help="br work type whose required sections to emit",
    )
    p_gate = policy_sub.add_parser(
        "gate", help="Show required/advisory gate status and the advance decision"
    )
    p_gate.add_argument("issue")
    p_ck = policy_sub.add_parser("checkpoint", help="Show or approve a human checkpoint")
    p_ck.add_argument("issue")
    p_ck.add_argument("name", choices=CHECKPOINTS)
    p_ck.add_argument("--approve", action="store_true", help="Record human approval")
    p_ck.add_argument(
        "--root",
        metavar="ISSUE",
        help="Session root carrying the grant ledger (default: the issue itself)",
    )
    p_ck.add_argument(
        "--confirm",
        metavar="CODE",
        help="One-time code from a prior non-interactive --approve (required off a TTY)",
    )
    p_gr = policy_sub.add_parser(
        "grant", help="Show, issue, or revoke a session autonomy grant (L1-L3)"
    )
    p_gr.add_argument("issue", help="The session root issue carrying the grant ledger")
    p_gr.add_argument(
        "--level", choices=[lvl for lvl in AUTONOMY_LEVELS if lvl != "L0"], help="Level to issue"
    )
    p_gr.add_argument(
        "--token-budget",
        type=int,
        metavar="TOKENS",
        help="Session spend ceiling in run-record tokens (required for L2+)",
    )
    p_gr.add_argument("--revoke", action="store_true", help="Revoke the active grant")
    p_gr.add_argument(
        "--autonomy",
        choices=AUTONOMY_LEVELS,
        help="Grantable ceiling for this issuance only, overriding [policy] autonomy "
        "without editing any committed config (still gated by the confirm challenge)",
    )
    p_gr.add_argument(
        "--confirm",
        metavar="CODE",
        help="One-time confirm code for non-interactive issuance",
    )
    p_rw = policy_sub.add_parser("rework", help="Show, record, or forgive a rework attempt")
    p_rw.add_argument("issue")
    p_rw.add_argument("--gate", default=verify.DEFAULT_GATE, help="Gate the rework is for")
    p_rw.add_argument("--record", action="store_true", help="Record a new rework attempt")
    p_rw.add_argument(
        "--allow-retry",
        action="store_true",
        help="Permit exactly one further attempt on this node, leaving the repo-wide cap alone",
    )


def _add_rubric_parser(subparsers: argparse._SubParsersAction) -> None:
    rubric_parser = subparsers.add_parser(
        "rubric", help="Evaluate work-type behavioral rubrics (advisory gate)"
    )
    rubric_sub = rubric_parser.add_subparsers(dest="rubric_command", required=True)
    r_eval = rubric_sub.add_parser(
        "eval", help="Evaluate the issue's work-type rubric and report the advisory rubric gate"
    )
    r_eval.add_argument("issue")
    r_eval.add_argument(
        "--runner", help="Runner name or 'auto' for judged checks (default: [runner].default)"
    )
    r_eval.add_argument(
        "--dry-run", action="store_true", help="Print the judged-check prompt without dispatching"
    )


def _add_session_override_args(parser: argparse.ArgumentParser) -> None:
    """Add the per-invocation ``--runner``/``--autonomy``/``--tier`` overrides (basicly-nvm1).

    Shared by every loop subcommand that can dispatch an agent, so the choice is
    answerable per invocation instead of only by the committed config — which is what
    forced one key to serve both the supervised and the interactive mode.

    ``--tier`` joins the pair on the same ground (basicly-pmhmsp): a capability tier is
    the third thing an operator picks for one run, and picking it meant editing
    ``[runner] default_tier`` — which :mod:`basicly.session`'s own docstring names as the
    wrong answer, because it changes behaviour for every consumer. It selects the tier for
    the whole pass and not per lane; ``runner.select_runner`` resolves one spec a round.
    """
    parser.add_argument(
        "--runner",
        help="Agent to dispatch for this invocation only, overriding [runner] default "
        "without editing any committed config ('manual' restores the handoff)",
    )
    parser.add_argument(
        "--autonomy",
        choices=AUTONOMY_LEVELS,
        help="Grantable autonomy ceiling for this invocation only, overriding "
        "[policy] autonomy without editing any committed config",
    )
    parser.add_argument(
        "--tier",
        choices=MODEL_TIERS,
        help="Capability tier every lane of this invocation dispatches at, overriding "
        "[runner] default_tier without editing any committed config; the tier resolves "
        "to a concrete model per vendor and surface, and resolves to nothing rather than "
        "to a neighbouring tier where it has none",
    )


def _add_lane_selector_arg(parser: argparse.ArgumentParser) -> None:
    """Add ``--label``, the pass's explicit lane set (basicly-1lpo).

    Shared by every command that reads a session — supervise, preflight, the client
    attach and the stop — because they must all read the *same* session: a root can be
    supervised over its decomposition or over a labelled cut, and a client that omits
    the selector reports a running label pass as childless.
    """
    parser.add_argument(
        "--label",
        metavar="LABEL",
        help="Fan out over the beads carrying LABEL instead of the root's parent-child "
        "children, so a release cut can be assembled from beads that already have an "
        "epic of origin (br permits one parent); the root then anchors the grant, the "
        "lock and the decision queue only",
    )


def _add_loop_input_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared agent-input flags that map onto a ``loop.Inputs``."""
    parser.add_argument(
        "--work-type",
        choices=WORK_TYPES,
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


def _add_loop_decision_parsers(loop_sub: argparse._SubParsersAction) -> None:
    """Register the four verbs over the session's decision queue.

    List it, answer one item, delegate one to the decider agent, poll for new ones —
    one responsibility, and the surface `tests/test_cli_loop_session.py` calls the
    client's. Split out of :func:`_add_loop_parser` when that function crossed its
    statement cap; the seam is the queue, not an arbitrary halfway point.
    """
    l_dec = loop_sub.add_parser(
        "decisions", help="List the session's pending decisions (pure read over br)"
    )
    l_dec.add_argument("issue", help="Session root issue")
    l_dec.add_argument("--json", action="store_true", help="Machine-readable output")
    l_ans = loop_sub.add_parser("answer", help="Record a human answer on a queued decision")
    l_ans.add_argument("decision_id", help="Decision id as printed by loop decisions")
    l_ans.add_argument("text", help="The answer")
    l_ans.add_argument("--by", metavar="NAME", help="Answerer attribution (default: human)")
    l_dcd = loop_sub.add_parser(
        "decide", help="Invoke the decider agent on one decision (corpus-bounded)"
    )
    l_dcd.add_argument("decision_id", help="Decision id as printed by loop decisions")
    l_dcd.add_argument("--root", required=True, help="Session root issue (the intake corpus)")
    l_watch = loop_sub.add_parser("watch", help="Poll and print newly pending decisions")
    l_watch.add_argument("issue", help="Session root issue")
    l_watch.add_argument("--interval", type=float, default=15.0, help="Poll seconds")
    l_watch.add_argument("--once", action="store_true", help="One pass, then exit")


def _add_loop_parser(subparsers: argparse._SubParsersAction) -> None:
    loop_parser = subparsers.add_parser(
        "loop",
        help="Drive an issue through the harness loop (status / advance / run / supervise)",
    )
    loop_sub = loop_parser.add_subparsers(dest="loop_command", required=True)
    l_status = loop_sub.add_parser(
        "status", help="Show an issue's reconstructed loop state (read-only)"
    )
    l_status.add_argument("issue")
    l_pre = loop_sub.add_parser(
        "preflight",
        help="Check everything a supervised run needs before it starts: clean base, "
        "runner, grant, budget, lane count and forecast spend (read-only)",
    )
    l_pre.add_argument("issue", help="Root issue the session would be bound to")
    _add_lane_selector_arg(l_pre)
    l_advance = loop_sub.add_parser(
        "advance", help="Advance one loop step (exit non-zero when blocked)"
    )
    l_advance.add_argument("issue")
    _add_loop_input_args(l_advance)
    l_run = loop_sub.add_parser(
        "run",
        help="Drive a whole phase boundary in one command, resolving the "
        "checkpoints it is authorized to resolve",
    )
    l_run.add_argument("issue")
    _add_loop_input_args(l_run)
    l_run.add_argument(
        "--confirm",
        metavar="CODE",
        help="One-time confirm code relayed by a human, optionally 'checkpoint=CODE'",
    )
    l_run.add_argument(
        "--root",
        help="Session root issue whose autonomy grant may cover the checkpoints "
        "(default: the issue itself)",
    )
    l_supervise = loop_sub.add_parser(
        "supervise",
        help="Run the standing supervisor loop: dispatch ready lanes, route "
        "outcomes, land green work - until done or blocked on a human",
    )
    l_supervise.add_argument("issue", help="Root issue (feature or epic) the session is bound to")
    _add_lane_selector_arg(l_supervise)
    l_supervise.add_argument(
        "--max-passes",
        type=int,
        metavar="N",
        help="Return after N rounds even with open children left, bounding a "
        "launch's spend up front instead of needing an operator to intervene",
    )
    l_supervise.add_argument(
        "--detach",
        action="store_true",
        help="Start the supervisor in its own session, print its log path and pid, "
        "and return at once - so no terminal that closes and no agent tool that "
        "kills a background job at its own ceiling can take a round with it",
    )
    l_stop = loop_sub.add_parser(
        "stop",
        help="Ask the running supervisor to finish its round and return: every "
        "dispatched lane lands, no further lane is seeded",
    )
    l_stop.add_argument("issue", help="Session root issue the supervisor is bound to")
    _add_lane_selector_arg(l_stop)
    l_stop.add_argument(
        "--reason", required=True, help="Why the session is being stopped (recorded on the marker)"
    )
    l_stop.add_argument(
        "--by", metavar="NAME", default="human", help="Requester attribution (default: human)"
    )
    # Every path that can dispatch takes the session overrides, not only `supervise`
    # (basicly-nvm1). Without them on `advance`/`run`, one committed `[runner] default`
    # had to serve two incompatible modes: a real agent so a supervised pass dispatches
    # at all, and the handoff so an interactive build does not re-implement the node in a
    # second process. The only escape was an uncommitted `basicly.local.toml`, which no
    # consumer inherits — so the committed default could not express the intent.
    for dispatching in (l_advance, l_run, l_supervise):
        _add_session_override_args(dispatching)
    l_sess = loop_sub.add_parser(
        "session",
        help="Attach to a supervisor session and observe its live status "
        "(read-only; takes no lock)",
    )
    l_sess.add_argument("issue", help="Session root issue")
    _add_lane_selector_arg(l_sess)
    l_sess.add_argument("--json", action="store_true", help="Machine-readable output")
    _add_loop_decision_parsers(loop_sub)
    l_kill = loop_sub.add_parser(
        "kill",
        help="Kill a lane: close it won't-do-this-way with a recorded reason and "
        "tear its worktree down (always gated on a human confirm code)",
    )
    l_kill.add_argument("issue", help="The lane to kill")
    l_kill.add_argument(
        "--reason", required=True, help="Why this work is not being done (recorded on the bead)"
    )
    l_kill.add_argument(
        "--confirm",
        metavar="CODE",
        help="One-time confirm code relayed by a human; without it the kill refuses, mints one",
    )
    l_kill.add_argument(
        "--discard",
        action="store_true",
        help="Also discard the lane's uncommitted changes and delete its unmerged "
        "branch; without this the teardown keeps both",
    )
    l_improve = loop_sub.add_parser(
        "improve",
        help="Run the repo's improvement controller: measure one declared property, "
        "select one target, dispatch at most one lane",
    )
    l_improve.add_argument(
        "--dry-run", action="store_true", help="Select and print, but file no lane"
    )


_HELP_EPILOG = """\
command groups:
  consumer (run in a repo that installed basicly, usually via the pinned uvx):
    install            converge the repo: catalog, projections, hooks
                       (re-running install IS the upgrade; no update command)
    uninstall          remove everything basicly manages (--purge: overlay too)
    build / check      regenerate agent instruction files / fail on drift
    status             read-only snapshot: versions, drift, hooks, overlays
                       (--json emits a stable schema for fleet loops)
    skills-build / skills-check, hooks-build / hooks-check,
    agents-build / agents-check
                       project and verify the other catalog kinds

  contributor (author the catalog in the basicly repo itself, under `catalog`):
    catalog list [fragment|skill|agent]      inspect the catalog
    catalog dump                             the composed selection, with each item's origin
    catalog new <fragment|skill|agent> NAME  scaffold a new source
    catalog lint / verify / review           deterministic and semantic gates

  harness (agent-facing development loop, either repo):
    worktree, verify, policy, decompose, loop, runner
"""


def _add_status_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `basicly status` command."""
    status_parser = subparsers.add_parser(
        "status",
        help="Read-only repo snapshot: versions, drift, hooks, technologies, overlays",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the snapshot as JSON (stable schema, for fleet loops)",
    )
    status_parser.add_argument(
        "--fleet",
        action="store_true",
        help="Roll up status + run-records across the housed workspace repos as JSON "
        "(read-only, exit 0); implies JSON output",
    )
    status_parser.add_argument(
        "--root",
        metavar="PATH",
        help="Workspace root to scan for --fleet (default: the parent of this repo)",
    )


def _add_health_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `basicly health` command."""
    health_parser = subparsers.add_parser(
        "health",
        help="Per-agent health scoring and behavioral drift from run-records (read-only)",
    )
    health_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the health report as JSON (stable schema)",
    )
    health_parser.add_argument(
        "--fleet",
        action="store_true",
        help="Roll up per-repo health across the housed workspace repos as JSON "
        "(read-only, exit 0); implies JSON output",
    )
    health_parser.add_argument(
        "--root",
        metavar="PATH",
        help="Workspace root to scan for --fleet (default: the parent of this repo)",
    )
    health_parser.add_argument(
        "--window",
        type=int,
        default=health.DEFAULT_WINDOW,
        metavar="N",
        help=f"Recent-window size for drift (default: {health.DEFAULT_WINDOW})",
    )


def _add_usage_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `basicly usage` command group."""
    usage_parser = subparsers.add_parser(
        "usage", help="Tool/skill usage telemetry recorded by the tool-usage hook"
    )
    usage_sub = usage_parser.add_subparsers(dest="usage_command", required=True)
    usage_sub.add_parser(
        "report", help="Report recorded tool/skill counts and never-used catalog skills"
    )
    usage_sub.add_parser(
        "forecast",
        help="Report the forecast error per dispatch, and the records that cannot be paired",
    )
    usage_sub.add_parser(
        "tuning",
        help="Advise each governed parameter from recorded outcomes (changes no config)",
    )
    usage_sub.add_parser(
        "lane-split",
        help="Split each persisted lane transcript into acquisition and implementation",
    )
    usage_sub.add_parser(
        "outcomes",
        help="Report how every recorded dispatch ended, and the share that failed",
    )


def _add_session_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `basicly session` command group — read-only session orientation."""
    session_parser = subparsers.add_parser(
        "session",
        help="Prepare a session from the ledger: ready, blocked, grants, decision targets",
    )
    session_sub = session_parser.add_subparsers(dest="session_command", required=True)
    start = session_sub.add_parser(
        "start", help="Read-only orientation for a new session (never writes, always exits 0)"
    )
    start.add_argument(
        "--json", action="store_true", help="Emit the orientation as JSON (stable schema)"
    )
    start.add_argument(
        "--rows",
        type=int,
        default=SESSION_ROWS,
        metavar="N",
        help=f"Ready and blocked rows to print before the tail count (default: {SESSION_ROWS})",
    )


def _add_tracker_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `basicly tracker` command group — the owned tracker's cutover."""
    tracker_parser = subparsers.add_parser(
        "tracker",
        help="The owned work tracker: read the backlog, write to it, run its cutover",
    )
    tracker_sub = tracker_parser.add_subparsers(dest="tracker_command", required=True)
    tracker_query.add_parsers(tracker_sub)
    t_write = tracker_sub.add_parser("write", help="Make a tracker write through the engine seam")
    t_write.add_argument("argv", nargs=argparse.REMAINDER, help="The subcommand, after `--`")


def _tolerate_narrow_consoles() -> None:
    """Never crash on unencodable output characters.

    Windows consoles default to a legacy codepage (cp1252), where the
    catalog's unicode (arrows, dashes) raises UnicodeEncodeError on print.
    Degrading the character to ``?`` beats failing the whole command.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _line_buffer_stdout() -> None:
    """Make stdout line-buffered so a long run stays observable through a pipe.

    Python block-buffers a non-TTY stdout, which is exactly the case a long
    headless run is piped into — a log or a pager. A supervised multi-lane run
    showed nothing but its lanes' subprocess noise for twelve minutes and then
    emitted the whole orchestration history at exit (basicly-8veb). The lanes'
    output was never buffered, because it comes from child processes writing to an
    inherited descriptor, so the operator saw everything *except* what the
    supervisor was telling them. That also defeated "watch the run and intervene
    early" as a cost control: the lanes had finished and spent 3.36M tokens before
    the first orchestration line was visible.

    Reconfiguring the stream rather than passing ``flush=True`` at each call site
    is deliberate. Every line the CLI prints is covered by construction, including
    ones added later, and no call site is left to forget. It sits in process setup
    rather than inside the supervise command because the buffering is a property of
    this process's stdout, not of one subcommand — ``loop run`` blocks the same way
    through a long verify.

    ``stderr`` needs nothing: Python already keeps it unbuffered or line-buffered.
    A stream some harness replaced without ``reconfigure`` is skipped, which is
    safe — such a harness collects the output itself.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the full argument parser (also introspected by the docs tripwire)."""
    parser = argparse.ArgumentParser(
        prog="basicly",
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"basicly {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_lifecycle_parsers(subparsers)

    build_parser = subparsers.add_parser("build", help="Build generated files")
    build_parser.add_argument("--target", help="Build only the specified target")
    build_parser.add_argument(
        "--verify",
        action="store_true",
        help="Run the deterministic catalog gate first; write nothing if it fails",
    )

    subparsers.add_parser("check", help="Check generated files are up to date")

    _add_status_parser(subparsers)

    _add_health_parser(subparsers)

    _add_usage_parser(subparsers)

    _add_session_parser(subparsers)

    brief_parser = subparsers.add_parser(
        "brief", help="Print the dispatch brief the loop would send for one issue"
    )
    brief_parser.add_argument("issue_id", help="The tracked issue to brief")

    board_cli.add_parsers(subparsers)
    _add_tracker_parser(subparsers)

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

    _add_agents_parsers(subparsers)

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
        "permissions-build", help="Project the agent-permissions deny-list into agent configs"
    )
    subparsers.add_parser(
        "permissions-check", help="Check the projected permissions deny-list is up to date"
    )

    _add_catalog_parser(subparsers)
    _add_worktree_parser(subparsers)
    _add_verify_parser(subparsers)
    _add_commit_parser(subparsers)
    _add_policy_parser(subparsers)
    _add_decompose_parser(subparsers)
    _add_release_parser(subparsers)
    _add_loop_parser(subparsers)
    _add_runner_parser(subparsers)
    _add_rubric_parser(subparsers)

    return parser


def _handlers() -> dict[str, Callable[[argparse.Namespace], int]]:
    """Every top-level subcommand `_build_parser` registers, mapped to its handler.

    A function and not a module-level dict on purpose: the names are resolved when
    it is called, so a test (or a caller) that substitutes one `cmd_*` still has its
    substitution dispatched. A dict built at import time captures the originals and
    silently ignores the swap.

    The two registries are hand-maintained lists of the same 29 names and nothing
    derives one from the other, so `test_every_registered_subcommand_has_a_handler`
    pins them equal and `main` fails loudly on a name that arrives with no handler
    (basicly-tcmy.4).
    """
    return {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "build": cmd_build,
        "check": cmd_check,
        "status": cmd_status,
        "health": cmd_health,
        "skills-build": cmd_skills_build,
        "skills-check": cmd_skills_check,
        "agents-build": cmd_agents_build,
        "agents-check": cmd_agents_check,
        "hooks-build": cmd_hooks_build,
        "hooks-check": cmd_hooks_check,
        "permissions-build": cmd_permissions_build,
        "permissions-check": cmd_permissions_check,
        "catalog": cmd_catalog,
        "worktree": cmd_worktree,
        "verify": cmd_verify,
        "commit": cmd_commit,
        "policy": cmd_policy,
        "decompose": cmd_decompose,
        "release": cmd_release,
        "loop": cmd_loop,
        "runner": cmd_runner,
        "rubric": cmd_rubric,
        "usage": cmd_usage,
        "brief": cmd_brief,
        "board": board_cli.cmd_board,
        "tracker": cmd_tracker,
        "session": cmd_session,
    }


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the requested command."""
    _tolerate_narrow_consoles()
    _line_buffer_stdout()
    args = _build_parser().parse_args(argv)

    try:
        # `_dispatch` carries the guard for every group as well as this one, so a
        # registered-but-unhandled subcommand is loud wherever it sits in the tree
        # (basicly-tcmy.4, basicly-8ry8).
        return _dispatch(args, "command", _handlers())
    except ValidationError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1
    # The process's last frame. The alternative — letting an unexpected type escape —
    # prints a traceback at a user and exits 1 anyway; this translates instead.
    except Exception as exc:  # noqa: BLE001 — process boundary, reported not swallowed
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
