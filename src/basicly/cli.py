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
    owned_write,
    permissions,
    policy,
    projection,
    release,
    retention,
    review,
    routing_evals,
    rubrics,
    run_record,
    runner,
    state,
    supervise,
    tracker,
    tracker_import,
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
from .output_styles import UNMANAGED_REASON_PREFIX as STYLES_UNMANAGED_REASON_PREFIX
from .output_styles import (
    check_synced_styles,
    resolve_style_roots,
    sync_styles,
)
from .planner import plan_outputs
from .renderers.common import sha256_of_text
from .scaffolds import (
    CONSUMER_CI_WORKFLOW,
    GENERATED_IGNORES,
    OVERLAY_FRAGMENT_STUBS,
    VSCODE_TASKS_JSON,
    install_notes,
    repin,
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

    out: list[str] = []
    for target in targets:
        if target.name != item.target_name:
            continue
        where = item.output_path.relative_to(repo_root)
        measured = target.measure(content)
        if target.max_size_warning and measured > target.max_size_warning:
            out.append(
                f"Warning: {where} exceeds {target.max_size_warning} "
                f"{target.max_size_unit} ({measured})"
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

    manifest_path = repo_root / paths.manifest_path
    existing_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = {}
    recorded = existing_manifest.get("outputs")
    tracked = set(recorded) if isinstance(recorded, dict) else set()

    for item in planned:
        content = _render_planned(repo_root, paths, item)
        rendered[item.output_path] = content
        encoded = content.encode("utf-8")
        rel = item.output_path.relative_to(repo_root).as_posix()
        backup = projection.back_up_unrecognised(item.output_path, encoded, tracked=rel in tracked)
        if backup is not None:
            ui.warn(f"{rel} was not ours to overwrite; your copy is at {backup.name}")
        changed = projection.write_if_changed(item.output_path, encoded)
        if changed:
            changed_count += 1
            ui.say(f"Wrote {item.output_path.relative_to(repo_root)}", style="ok")
        for line in _budget_warnings(targets, item, content, repo_root):
            print(line, file=sys.stderr)

    manifest = _build_manifest(rendered, planned, existing_manifest, bool(args.target))
    changed_count += _sweep_stale_outputs(repo_root, existing_manifest, manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    ui.say(f"Updated {_format_path(manifest_path, repo_root)}", style="ok")
    if changed_count == 0:
        ui.say("No files changed.", style="muted")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    if _report_provenance_notes(repo_root, paths):
        return 1
    fragments, targets = _load_context(repo_root, paths)
    planned = plan_outputs(fragments, targets, repo_root)

    mismatches: list[tuple[Path, str, str]] = []
    expected_manifest_outputs: dict[str, dict[str, Any]] = {}

    for item in planned:
        content = _render_planned(repo_root, paths, item)
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

    if (recorded := existing_manifest.get("outputs")) != expected_manifest_outputs:
        mismatches.append((
            manifest_path,
            _manifest_digest(expected_manifest_outputs),
            _manifest_digest(recorded),
        ))

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


def _manifest_digest(outputs: object) -> str:

    return sha256_of_text(json.dumps(outputs, sort_keys=True, default=str))


CORE_DRIFT_SHOWN = 10


def _report_provenance_notes(repo_root: Path, paths: ProjectPaths) -> bool:

    state_path = repo_root / paths.state_path
    try:
        install_state = state.read_install_state(state_path)
    except ValidationError as exc:
        print(f"Note: {exc}; re-run `basicly install` to rewrite it.", file=sys.stderr)
        return False
    if install_state is None:
        return False

    skewed = install_state.basicly_version != __version__
    if skewed:
        print(
            f"Version skew: the core catalog was installed by basicly "
            f"{install_state.basicly_version} and this is basicly {__version__}. "
            "Run `basicly install` to upgrade — `basicly build` cannot fix this, and a "
            "cross-version file comparison would report drift that is not yours.",
            file=sys.stderr,
        )

    drift = state.core_drift(install_state, repo_root / paths.core_root)
    if drift:
        print(
            f"Managed core differs from the installed snapshot in {len(drift)} file(s). "
            "Hand-edits belong in the overlay; a formatter or linter of your own that "
            "reaches into the managed core will rewrite it on every run. Restore it with "
            "`basicly install`, and exclude the core root from your own tooling:",
            file=sys.stderr,
        )
        for rel_path, reason in drift[:CORE_DRIFT_SHOWN]:
            print(f"  {rel_path}: {reason}", file=sys.stderr)
        if len(drift) > CORE_DRIFT_SHOWN:
            print(f"  … and {len(drift) - CORE_DRIFT_SHOWN} more", file=sys.stderr)
    return skewed or bool(drift)


STATUS_SCHEMA_VERSION = 1


def _status_report(repo_root: Path, paths: ProjectPaths) -> dict[str, Any]:
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
    return _status_report(repo_root, load_project_paths(repo_root))


def cmd_status(args: argparse.Namespace) -> int:
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


DECISION_RECORDS_DOC = Path("docs") / "architecture" / "architecture.md"

_DECISION_ROW = re.compile(r"^\| (D-\d+) \| (.+?) \| (.+?) \| (.+?) \|[ \t]*$", re.MULTILINE)

DECISION_IN_FORCE = "accepted"

_GRANT_MARKER = f"{policy.MARKER} grant"

_CLOSED_STATUS = "closed"

HANDOVER_MARKER = "[session handover"

SESSION_ROWS = 10


def _decision_targets(repo_root: Path) -> dict[str, Any]:

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
        if row["spent_local"] is not None and row["spent_local"] != row["spent_ledger"]:
            ui.say(
                f"        {row['record']}: run records say {row['spent_local']:,} and the "
                f"committed ledger says {row['spent_ledger']:,}; spent counts both once"
            )


def _say_session_decisions(report: dict[str, Any]) -> None:
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
    return _dispatch(args, "session_command", {"start": cmd_session_start}, group="session")


def _merge_directories(src: Path, dst: Path) -> tuple[int, int]:
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
    pruned = _prune_legacy_catalog_sources(repo_root, paths)
    for legacy in pruned:
        print(f"Pruned legacy source {_format_path(legacy, repo_root)}")

    legacy_engine = repo_root / paths.core_root.parent / "basicly"
    if legacy_engine.is_dir() and (legacy_engine / "cli.py").exists():
        shutil.rmtree(legacy_engine)
        print(f"Removed legacy vendored engine {_format_path(legacy_engine, repo_root)}/")

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

    prefix = re.sub(r"[^a-z0-9]", "", repo_root.name.lower())
    if not prefix or not prefix[0].isalpha():
        prefix = f"repo{prefix}"
    return prefix


def _setup_tracker(repo_root: Path) -> None:

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


def _scaffold_ledger_attributes(repo_root: Path) -> None:
    rules = owned_write.ledger_git_rules(repo_root)
    if not rules:
        return
    path = repo_root / ".gitattributes"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        present = {line.strip() for line in text.split("\n")}
        missing = [rule for rule in rules if rule not in present]
        if not missing:
            return
        prefix = "" if not text or text.endswith("\n") else "\n"
        body = text + prefix + _LEDGER_ATTRIBUTE_NOTE + "\n".join(missing) + "\n"
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"basicly install: cannot write {_format_path(path, repo_root)}, and the tracker "
            f"ledger conflicts on every parallel append without it: {exc}"
        ) from exc
    print(f"Added {', '.join(missing)} to .gitattributes")


def _scaffold_overlay_stubs(repo_root: Path, paths: ProjectPaths) -> None:

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


def _write_scaffold(path: Path, content: str, label: str, *, force: bool) -> None:

    if path.exists():
        if not force:
            repinned, moved = repin(path.read_text(encoding="utf-8"))
            if moved:
                path.write_text(repinned, encoding="utf-8")
                print(f"Re-pinned {moved} basicly reference(s) in {label} to v{__version__}")
            else:
                print(f"{label} already exists; left unchanged (--overwrite-scaffolds replaces it)")
            return
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            print(f"{label} already current")
            return
        backup = path.with_suffix(path.suffix + ".basicly-bak")
        backup.write_text(existing, encoding="utf-8")
        path.write_text(content, encoding="utf-8")
        print(f"Replaced {label}; your previous copy is at {backup.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {label}")


def _scaffold_vscode_tasks(repo_root: Path, *, force: bool = False) -> None:
    _write_scaffold(
        repo_root / ".vscode" / "tasks.json", VSCODE_TASKS_JSON, ".vscode/tasks.json", force=force
    )


def _scaffold_ci_workflow(repo_root: Path, *, force: bool = False) -> None:
    _write_scaffold(
        repo_root / ".github" / "workflows" / "basicly-gates.yml",
        CONSUMER_CI_WORKFLOW,
        ".github/workflows/basicly-gates.yml",
        force=force,
    )


def _scaffold_consumer_files(repo_root: Path, *, force: bool) -> None:
    _scaffold_vscode_tasks(repo_root, force=force)
    _scaffold_ci_workflow(repo_root, force=force)


def _report_missing_config_sections(repo_root: Path) -> None:

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


def ignore_covers(ignore_text: str, pattern: str) -> bool:

    return any(line.strip().lstrip("/") == pattern for line in ignore_text.splitlines())


_LEDGER_ATTRIBUTE_NOTE = (
    "# The tracker ledger is append-only, and two branches that each append conflict\n"
    "# without a union merge. `-text` keeps git from rewriting a byte an event id is\n"
    "# derived from. The glob is read off the kit's own `events.LOG_GLOB`. This rule\n"
    "# must sit after any `*` rule to win.\n"
)


def _scaffold_generated_ignores(repo_root: Path) -> None:

    ignore_path = repo_root / ".gitignore"
    text = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    added: list[str] = []
    for pattern, why in GENERATED_IGNORES:
        if ignore_covers(text, pattern):
            continue
        prefix = "" if not text or text.endswith("\n") else "\n"
        text += prefix + (f"# {why}\n" if why else "") + pattern + "\n"
        added.append(pattern)
    if not added:
        return
    ignore_path.write_text(text, encoding="utf-8")
    print(f"Added {', '.join(added)} to .gitignore")


def _validate_install_technologies(raw: str | None) -> list[str] | None:

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
    _scaffold_generated_ignores(repo_root)
    for note in install_notes(repo_root):
        print(note, file=sys.stderr)

    if not _record_install_technologies(repo_root, technologies):
        return 1

    _setup_tracker(repo_root)
    _scaffold_ledger_attributes(repo_root)
    _scaffold_consumer_files(repo_root, force=bool(getattr(args, "overwrite_scaffolds", False)))

    steps: list[tuple[str, Any, argparse.Namespace]] = [
        ("build", cmd_build, argparse.Namespace(target=None, verify=False)),
        (
            "skills-build",
            cmd_skills_build,
            argparse.Namespace(roots=None),
        ),
        ("styles-build", cmd_styles_build, argparse.Namespace(roots=None)),
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
    current = directory
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def _remove_generated_outputs(repo_root: Path, paths: ProjectPaths) -> int:
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
    return _remove_generated_skills(repo_root, (*DEFAULT_SKILL_ROOTS, *RETIRED_SKILL_ROOTS))


def _remove_projected_agents(repo_root: Path) -> int:
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

    hooks_dir = paths.core_root / "hooks"
    if hooks_dir.is_absolute():
        raise ValueError(
            f"core hooks dir {hooks_dir} is absolute; set a repo-relative "
            f"core_fragments path in {CONFIG_FILE} so hook wiring stays portable"
        )
    return hooks_dir


def cmd_hooks_build(_args: argparse.Namespace) -> int:
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
HOOKS_SCRIPT_REMEDY = (
    "Hook scripts differ from the installed basicly catalog; `basicly hooks-build` does not "
    "copy hook scripts and will not fix this. `basicly install` re-materializes them from the "
    "installed catalog, overwriting the local copies — so if the local version is the change "
    "you want, edit the catalog source it is built from instead of running it."
)


def _hooks_stale_message(mismatches: list[tuple[Path, str]], core_hooks_dir: Path) -> str:
    resolved = core_hooks_dir.resolve()
    scripts = [path for path, _ in mismatches if path.resolve().is_relative_to(resolved)]
    if not scripts:
        return HOOKS_WIRING_REMEDY
    if len(scripts) == len(mismatches):
        return HOOKS_SCRIPT_REMEDY
    return f"{HOOKS_SCRIPT_REMEDY} The remaining wiring drift is fixed by `basicly hooks-build`."


def cmd_hooks_check(_args: argparse.Namespace) -> int:
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
            remedy = (
                "Run `basicly hooks-build` to activate them locally — pre-push also carries "
                "a ledger guard that `pre-commit install` does not write."
            )
        print(
            f"Note: git hooks are not installed for stages: {', '.join(missing)}. {remedy}",
            file=sys.stderr,
        )

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
    repo_root = _repo_root()
    patterns = permissions.claude_deny_patterns(permissions.load_deny_rules())
    if claude_settings.sync_permission_deny(repo_root, patterns):
        print(f"Wrote {claude_settings.CLAUDE_SETTINGS_PATH} (managed permissions deny-list)")
    else:
        print(f"Permissions deny-list in {claude_settings.CLAUDE_SETTINGS_PATH} is up to date.")
    return 0


def cmd_permissions_check(_args: argparse.Namespace) -> int:
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


ALL_DEFAULT_ROOTS_DEPRECATED = (
    "`--all-default-roots` is deprecated and does nothing: every default skills root "
    "is written and checked without it. Drop the flag."
)


def _resolve_skill_output_roots(args: argparse.Namespace, repo_root: Path) -> list[Path]:

    if getattr(args, "all_default_roots", False):
        ui.warn(ALL_DEFAULT_ROOTS_DEPRECATED)
    return resolve_skill_roots(repo_root=repo_root, roots=getattr(args, "roots", None))


def _cmd_usage_lane_split(_args: argparse.Namespace) -> int:

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

    repo_root = _repo_root()
    if tracker.read_record(repo_root, args.issue_id) is None:
        ui.fail(f"No tracked issue {args.issue_id}")
        return 1
    prompt = dispatch_brief.dispatch_prompt(args.issue_id)
    ui.say(contention.with_scope_fence(repo_root, args.issue_id, prompt))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    handlers = {
        "report": usage_report.cmd_report,
        "forecast": usage_report.cmd_forecast,
        "tuning": usage_report.cmd_tuning,
        "lane-split": _cmd_usage_lane_split,
        "outcomes": usage_report.cmd_outcomes,
    }
    return _dispatch(args, "usage_command", handlers, group="usage")


def cmd_tracker_import(args: argparse.Namespace) -> int:
    try:
        code, lines = tracker_import.run_import(
            _repo_root(),
            args.export,
            source_name=args.source,
            dry_run=args.dry_run,
            deleted=tuple(args.deleted),
        )
    except ValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line, file=sys.stderr if code else sys.stdout)
    return code


def cmd_tracker_scrub(_args: argparse.Namespace) -> int:

    try:
        changed = tracker.scrub_ledger(_repo_root())
    except (owned_store.TrackerDivergenceError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Scrubbed {changed} event(s) in the ledger. Re-stage .basicly/ledger to commit them.")
    return 0


def cmd_tracker(args: argparse.Namespace) -> int:
    handlers = {
        "write": tracker_write.cmd_write,
        "import": cmd_tracker_import,
        "scrub": cmd_tracker_scrub,
        **tracker_query.HANDLERS,
    }
    return _dispatch(args, "tracker_command", handlers, group="tracker")


def cmd_skills_list(_args: argparse.Namespace) -> int:
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


STYLES_STALE_REMEDY = (
    "Stale output style projection detected. Run `basicly styles-build` to sync them."
)
STYLES_UNMANAGED_REMEDY = (
    "Unmanaged files under a projected output-styles root. Move each one into a "
    "`.basicly/core/output-styles/<slug>/style.yaml` source and rebuild, or delete it — "
    "`basicly styles-build` will not, since nothing describes it."
)


def _styles_stale_message(mismatches: list[tuple[Path, str]]) -> str:
    unmanaged = sum(
        1 for _, reason in mismatches if reason.startswith(STYLES_UNMANAGED_REASON_PREFIX)
    )
    if not unmanaged:
        return STYLES_STALE_REMEDY
    if unmanaged == len(mismatches):
        return STYLES_UNMANAGED_REMEDY
    return f"{STYLES_UNMANAGED_REMEDY} The remaining drift is fixed by `basicly styles-build`."


def cmd_styles_build(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    roots = resolve_style_roots(repo_root, getattr(args, "roots", None))
    result, pruned = sync_styles(repo_root, roots, selection=load_technology_selection(repo_root))
    for path in pruned:
        print(f"Removed {_format_path(path, repo_root)} (excluded by technology selection)")
    _report_sync(result, repo_root, noun="output styles", label="Output style")
    return 0


def cmd_styles_check(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    roots = resolve_style_roots(repo_root, getattr(args, "roots", None))
    mismatches = check_synced_styles(
        repo_root, roots, selection=load_technology_selection(repo_root)
    )
    if _report_mismatches(mismatches, repo_root, stale_message=_styles_stale_message(mismatches)):
        return 1
    checked = ", ".join(_format_path(root, repo_root) for root in roots)
    ui.say(f"Projected output styles are up to date in {checked}.", style="ok")
    return 0


RETENTION_TREND = Path(".basicly/usage/retention.jsonl")

RETENTION_SCOPE = (
    "This probe answers one question: is the instruction file in this session's context? "
    "It does not measure adherence, and it cannot measure gradual forgetting -- while the "
    "file is in context the response is read from it, not recalled. A low score means read "
    "the file; a high score means it is loaded, not that it is being followed. Gates and "
    "hooks are what enforce a rule."
)


def _retention_baseline(repo_root: Path, given: str | None) -> Path:
    if given:
        candidate = Path(given)
        return candidate if candidate.is_absolute() else repo_root / candidate
    for relative in (".claude/CLAUDE.md", "CLAUDE.md", "AGENTS.md"):
        candidate = repo_root / relative
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "no always-on instruction file found at .claude/CLAUDE.md, CLAUDE.md or AGENTS.md; "
        "pass one with --baseline"
    )


def cmd_retention(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    baseline = _retention_baseline(repo_root, getattr(args, "baseline", None))
    rules = retention.derive_rules_from(baseline)
    if not rules:
        raise SystemExit(f"{_format_path(baseline, repo_root)} declares no rules to score against")

    source = getattr(args, "response", None)
    text = sys.stdin.read() if source in (None, "-") else Path(source).read_text(encoding="utf-8")
    report = retention.score_response(rules, text)
    call = report.verdict

    print(
        f"{_format_path(baseline, repo_root)}: {report.retained}/{report.total} rules "
        f"({report.rate:.0%}) -> {call.upper()}"
    )
    print(f"  {retention.VERDICTS[call]}")
    if getattr(args, "verbose", False):
        for match in report.forgotten():
            print(f"  [{match.score:.2f}] {match.rule.rule_id}: {match.rule.text[:90]}")
    print(f"  {RETENTION_SCOPE}")

    trend = repo_root / RETENTION_TREND
    trend.parent.mkdir(parents=True, exist_ok=True)
    with trend.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({
                "at": datetime.now(UTC).isoformat(),
                "baseline": _format_path(baseline, repo_root),
                "rules": report.total,
                "retained": report.retained,
                "rate": round(report.rate, 4),
                "verdict": call,
            })
            + "\n"
        )
    return 1 if call == retention.ABSENT and getattr(args, "strict", False) else 0


def cmd_skills_build(args: argparse.Namespace) -> int:
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
    unmanaged = sum(1 for _, reason in mismatches if reason.startswith(UNMANAGED_REASON_PREFIX))
    if not unmanaged:
        return SKILLS_STALE_REMEDY
    if unmanaged == len(mismatches):
        return SKILLS_UNMANAGED_REMEDY
    return f"{SKILLS_UNMANAGED_REMEDY} The remaining drift is fixed by `basicly skills-build`."


def cmd_skills_check(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    roots = _resolve_skill_output_roots(args, repo_root)
    mismatches = check_synced_skills(
        repo_root, roots, selection=load_technology_selection(repo_root)
    )
    if _report_mismatches(mismatches, repo_root, stale_message=_skills_stale_message(mismatches)):
        return 1

    checked = ", ".join(_format_path(root, repo_root) for root in roots)
    ui.say(f"Projected skills are up to date in {checked}.", style="ok")
    return 0


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

    repo_root = _repo_root()
    result, pruned = agents.sync_agents(repo_root, load_technology_selection(repo_root))
    for path in pruned:
        print(f"Removed {_format_path(path, repo_root)} (excluded by technology selection)")
    _report_sync(result, repo_root, noun="agent files", label="Agent")
    return 0


def cmd_agents_check(_args: argparse.Namespace) -> int:
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
    repo_root = _repo_root()
    violations = catalog_lint.lint_catalog(repo_root)
    try:
        for warning in catalog_lint.skill_warnings(repo_root):
            print(f"catalog lint: warning: {warning}", file=sys.stderr)
        print(f"catalog lint: {routing_evals.routing_outcome(repo_root).summary()}")
    except ValidationError as exc:
        print(f"catalog lint: advisories unavailable ({exc})", file=sys.stderr)
    if violations:
        print("catalog lint: FAILED", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("catalog lint: OK")
    return 0


def _deterministic_gate(repo_root: Path, fragments: list[Any]) -> list[str]:
    return catalog_lint.lint_catalog(repo_root) + catalog_verify.verify_catalog(fragments)


def _report_gate_failures(header: str, violations: list[str]) -> bool:
    if not violations:
        return False
    print(header, file=sys.stderr)
    for violation in violations:
        print(f"  {violation}", file=sys.stderr)
    return True


def cmd_catalog_verify(_args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    fragments, _targets = _load_context(repo_root, paths)
    if _report_gate_failures("catalog verify: FAILED", _deterministic_gate(repo_root, fragments)):
        return 1
    print("catalog verify: OK")
    return 0


def _review_materials(repo_root: Path, paths: ProjectPaths) -> list[review.ReviewMaterial]:
    fragments, targets = _load_context(repo_root, paths)
    return [
        review.ReviewMaterial(
            _format_path(item.output_path, repo_root),
            _render_planned(repo_root, paths, item),
        )
        for item in plan_outputs(fragments, targets, repo_root)
    ]


def cmd_review(args: argparse.Namespace) -> int:

    repo_root = _repo_root()
    paths = load_project_paths(repo_root)
    prompt = review.build_review_prompt(_review_materials(repo_root, paths))

    if args.dry_run:
        print(prompt)
        return 0

    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, args.runner or config.default)
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
    return tracker.read_record(repo_root, issue_id)


def _issue_work_type(repo_root: Path, issue_id: str) -> str | None:
    record = _issue_record(repo_root, issue_id)
    if record is None:
        return None
    work_type = record.get("issue_type") or record.get("type")
    return work_type if isinstance(work_type, str) and work_type else None


def _issue_description(repo_root: Path, issue_id: str) -> str:
    record = _issue_record(repo_root, issue_id)
    body = record.get("description") if record else None
    return body if isinstance(body, str) else ""


def _cmd_rubric_eval(args: argparse.Namespace) -> int:
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
    return _dispatch(args, "rubric_command", {"eval": _cmd_rubric_eval}, group="rubric")


def cmd_release(args: argparse.Namespace) -> int:

    repo_root = _repo_root()
    if args.root and not args.autonomous:
        print("release: --root only applies with --autonomous", file=sys.stderr)
        return 1
    plan = release.plan_release(repo_root, args.version, date=args.date)
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
    if args.kind == "fragment":
        args.id = args.name
        return cmd_fragment_new(args)
    args.slug = args.name
    return cmd_skills_new(args) if args.kind == "skill" else cmd_agents_new(args)


def _cmd_catalog_list(args: argparse.Namespace) -> int:
    listers = {
        "fragment": cmd_list,
        "skill": cmd_skills_list,
        "agent": cmd_agents_list,
    }
    return listers[args.kind](args)


def _cmd_catalog_dump(_args: argparse.Namespace) -> int:

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
    return (
        f"{fragment.id} applies_to={','.join(fragment.applies_to)} "
        f"scope={fragment.scope_summary} "
        f"technologies={','.join(fragment.technologies) or 'any'} "
        f"<- {_dump_origin(fragment, repo_root)} [{fragment.source}]"
    )


def _dump_origin(fragment: Fragment, repo_root: Path) -> str:
    if fragment.source_path is None:
        return "<no source file>"
    return _format_path(fragment.source_path, repo_root)


def cmd_policy(args: argparse.Namespace) -> int:
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
    repo_root = _repo_root()
    result = policy.definition_of_ready(repo_root, args.issue)
    scope_warning = decompose.unparsed_scope_warning(_issue_description(repo_root, args.issue))
    if scope_warning:
        ui.warn(f"scope: {scope_warning}")
    if result.ready:
        print(f"DoR: READY ({args.issue})")
        return 0
    work_type = _issue_work_type(repo_root, args.issue) or "<work-type>"
    print(
        f"DoR: NOT READY ({args.issue}) — missing: {', '.join(result.missing)}\n"
        f"  Emit the required structure: basicly policy scaffold --type {work_type}",
        file=sys.stderr,
    )
    return 1


def _cmd_policy_scaffold(args: argparse.Namespace) -> int:

    print(policy.scaffold_body(args.type), end="")
    return 0


def _cmd_policy_gate(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
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
    repo_root = _repo_root()
    if args.approve:
        return _approve_checkpoint(repo_root, args)
    approved = policy.checkpoint_approved(repo_root, args.issue, args.name)
    print(f"checkpoint {args.name}: {'APPROVED' if approved else 'PENDING'} ({args.issue})")
    return 0 if approved else 1


_CHECKPOINT_MEANING = {
    "classify": (
        "Approving records the work type and provisions a worktree. No code changes yet.",
    ),
    "decompose": ("Approving fans out the child beads. Nothing merges and nothing is published.",),
    "ship": (
        "The merge to the base branch has ALREADY happened, at the build->verify landing.",
        "Approving tears down the worktree and closes the bead. It publishes nothing,",
        "pushes nothing, and creates no tag or release.",
        "Do not approve unless you have seen a '[merged]' line for this bead: there is",
        "no un-approve, and approving early wedges the node with its work unmerged.",
    ),
}


def _reason_block(reason: str) -> str:

    return f"  {reason}\n" if reason else ""


def _print_challenge(
    label: str, issue: str, rerun: str, meaning: str | None = None, reason: str = ""
) -> int:

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
    return "".join(f"  {line}\n" for line in lines) if lines else None


def _checkpoint_meaning(name: str) -> str | None:
    return _meaning_block(_CHECKPOINT_MEANING.get(name))


def _approve_checkpoint(repo_root: Path, args: argparse.Namespace) -> int:
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

    count = policy.session_coverage(repo_root, issue)
    if count == 1:
        return "covers 1 bead (this issue only)"
    return f"covers {count} beads"


def _report_active_grant(repo_root: Path, issue: str) -> int:

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
    if granted := policy.rework_allowances(repo_root, args.issue, args.gate):
        recorded = policy.rework_attempts(repo_root, args.issue, args.gate)
        print(f"  ({recorded} recorded, {granted} forgiven)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
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
    ui.fail("[commit] git rejected the commit (see the hook output above)")
    return 1


def _load_decompose_children(args: argparse.Namespace) -> tuple[Any, ...]:
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

    if not collapsing:
        return
    print("collapsing paths:")
    for item in collapsing:
        print(f"  {decompose.describe_collapsing_path(item, contended)}")


def _spend_metrics(spend: Any) -> str:
    tokens = "tokens unknown" if spend.tokens is None else f"{spend.tokens} tokens"
    cost = "cost unknown" if spend.cost is None else f"${spend.cost:.2f}"
    clock = (
        "wall clock unknown" if spend.wall_clock_s is None else f"{spend.wall_clock_s / 60:.0f} min"
    )
    return f"{tokens}, {cost}, {clock}"


def _spend_sources(calibration: Any) -> str:
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

    if not spend:
        return
    model = spend[0].calibration.model or "unresolved"
    print(f"forecast spend (model {model}):")
    for spec, forecast in zip(children, spend, strict=True):
        sources = _spend_sources(forecast.calibration)
        print(f"  {spec.title}: {_spend_metrics(forecast)} — {sources}")
    print(f"  declared prior: {spend[0].calibration.prior.basis}")


def cmd_decompose(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    children = _load_decompose_children(args)
    contended = decompose.append_only_paths(repo_root)

    if args.dry_run:
        planned = decompose.preview(children, contended)
        groups = 1 + max((c.group for c in planned), default=-1)
        print(f"decompose (dry-run): {len(planned)} children in {groups} parallel group(s)")
        _print_planned(planned)
        _print_collapsing_paths(decompose.collapsing_paths(children, contended), contended)
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


IMPROVEMENT_CONTROLLER = Path(".scripts") / "improvement_controller.py"


def _cmd_loop_improve(args: argparse.Namespace) -> int:

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
    return subprocess.run(  # noqa: S603 — repo-declared script, list form, no shell
        command, cwd=repo_root, check=False
    ).returncode


def _loop_inputs(args: argparse.Namespace) -> loop.Inputs:
    children = decompose.load_plan_file(Path(args.children)) if args.children else None
    return loop.Inputs(work_type=args.work_type, children=children, verify_mode=args.mode)


def _format_advance(result: loop.AdvanceResult) -> str:
    line = f"[{result.action}] {result.from_phase} -> {result.to_phase}"
    if result.detail:
        line += f": {result.detail}"
    if result.needs_input:
        line += f" (needs input: {result.needs_input})"
    return line


def _print_preflight_spend(
    repo_root: Path, state: supervise.SessionState, status: policy.SpendStatus
) -> list[str]:

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
    open_count = len(state.open_children) or (0 if state.children else 1)
    priced = min(cap, open_count)
    candidates = tuple(
        working_set.admit_working_set(repo_root, issue_id, sizing)
        for issue_id in state.open_children
    )
    startable = tuple(item for item in candidates if not item.refused)[:priced]
    if startable:
        forecast = supervise.admit_pass_spend(repo_root, startable, status, sizing)
        print(f"forecast:  {forecast.coverage} ({len(startable)} of {open_count} open, cap {cap})")
    elif candidates:
        print(
            f"forecast:  no lane can start - the working-set band refuses all "
            f"{len(candidates)} candidate(s)"
        )
    elif priced:
        print(
            f"forecast:  ~{per_lane * priced} tokens if all {priced} lanes start "
            f"(per-lane x min(cap {cap}, {open_count} open))"
        )
    _print_band_report(candidates, sizing)
    return _provisioning_blockers(state, candidates)


def _provisioning_blockers(
    state: supervise.SessionState, candidates: tuple[working_set.WorkingSetAdmission, ...]
) -> list[str]:

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


_SEEDING_CHECKPOINT = {"intake": "classify", "decompose": "decompose"}


def _preflight_session(repo_root: Path, args: argparse.Namespace) -> supervise.SessionState | None:

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

    node = loop_state.read_node_state(repo_root, root_issue)
    blocking = _SEEDING_CHECKPOINT.get(node.phase) if seeds_from_root else None
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

    status = decompose.calibration_status(repo_root, sizing)
    counts = ", ".join(
        f"{name} {count}/{status.min_samples}" for name, count in sorted(status.samples.items())
    )
    verdict = "SEEDS" if status.on_seeds else f"measured for {', '.join(status.measured_classes)}"
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
    detail = "all seeds" if len(seeded) == len(status.build_factor_sources) else "some configured"
    print(f"factors:   {detail} (never measured) - {factors}")


def _print_preflight_contention(repo_root: Path, state: supervise.SessionState) -> None:

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
    lines = working_set.band_report(working_sets)
    if not lines:
        return
    print(f"band:      {sizing.working_set_min}..{sizing.working_set_max} working-set tokens")
    for line in lines:
        print(line)


def _cmd_loop_preflight(args: argparse.Namespace) -> int:

    repo_root = _repo_root()
    blockers: list[str] = []

    if unknown := unknown_config_keys(repo_root):
        for problem in unknown:
            print(f"config:    INVALID - {problem}")
        print("VERDICT:   not ready - a config file declares a name this basicly cannot honour")
        return 1
    print("config:    recognised")

    dirty = worktree.git(["status", "--porcelain"], cwd=repo_root, check=False).stdout
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
        unmetered = (
            f"; could not be metered: {', '.join(status.unmetered_labels)}"
            if status.unmetered_dispatches
            else ""
        )
        print(f"halted:    {status.detail}{unmetered}")
    if (metered := supervise.metered_without_a_budget(repo_root, status)) is not None:
        print(f"budget:    MISSING - the {metered!r} runner meters spend and no budget covers it")

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

    if not raw:
        return None
    name, _, code = raw.partition("=")
    if code and name in CHECKPOINTS:
        return {name: code}
    return dict.fromkeys(CHECKPOINTS, raw)


def _ceremony_rerun(args: argparse.Namespace, code: str) -> str:

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

    repo_root = _repo_root()
    if getattr(args, "detach", False):
        if args.confirm:
            print(
                "run: refused - --confirm cannot be combined with --detach; a one-time "
                "code answers a challenge, and relaying it into a process the operator "
                "cannot watch defeats the challenge",
                file=sys.stderr,
            )
            return 1
        return _detach_launch(repo_root, args, "run")
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

    say(f"session:  {session_id}")
    if overrides:
        say(f"override: {', '.join(overrides)}")
    budget = supervise.configure_budget(repo_root)
    say(
        f"budget:   {budget.total} agent processes - {budget.lane_slots} lane, "
        f"{budget.decider_slots} decider, {budget.helper_slots} helper"
    )


DETACHED_LOGS_DIR = run_record.USAGE_DIR / "detached"

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

SUPERVISE_FORWARDED_FLAGS = {
    "label": "--label",
    "max_passes": "--max-passes",
    "runner": "--runner",
    "autonomy": "--autonomy",
    "tier": "--tier",
}

RUN_FORWARDED_FLAGS = {
    "work_type": "--work-type",
    "children": "--children",
    "mode": "--mode",
    "root": "--root",
    "runner": "--runner",
    "autonomy": "--autonomy",
    "tier": "--tier",
}

DETACHABLE_VERBS = {
    "supervise": (SUPERVISE_FORWARDED_FLAGS, "loop session"),
    "run": (RUN_FORWARDED_FLAGS, "loop status"),
}


def _detach_isolation(os_name: str) -> tuple[bool, int]:

    if os_name == "nt":
        return False, DETACHED_PROCESS | runner.CREATE_NEW_PROCESS_GROUP
    return True, 0


def _detach_argv(args: argparse.Namespace, verb: str) -> list[str]:

    forwarded, _watch = DETACHABLE_VERBS[verb]
    argv = [sys.executable, "-m", "basicly.cli", "loop", verb, args.issue]
    for dest, flag in forwarded.items():
        value = getattr(args, dest, None)
        if value is not None:
            argv += [flag, str(value)]
    return argv


def _detach_log(repo_root: Path, issue: str) -> Path:

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in issue)
    path = repo_root / DETACHED_LOGS_DIR / f"{safe.strip('.') or 'session'}-{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _spawn_detached(argv: list[str], log: Path, *, cwd: Path) -> int:

    new_session, creationflags = _detach_isolation(os.name)
    with log.open("ab") as sink:
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


def _detach_launch(repo_root: Path, args: argparse.Namespace, verb: str) -> int:

    log = _detach_log(repo_root, args.issue)
    pid = _spawn_detached(_detach_argv(args, verb), log, cwd=repo_root)
    _forwarded, watch = DETACHABLE_VERBS[verb]
    print(f"detached: pid {pid}")
    print(f"log:      {log}")
    print(f"watch:    basicly {watch} {args.issue}")
    return 0


def _cmd_loop_supervise(args: argparse.Namespace) -> int:

    repo_root = _repo_root()
    if getattr(args, "detach", False):
        return _detach_launch(repo_root, args, "supervise")
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
    keep = load_runner_config(repo_root).lane_log_sessions
    log = lane_log.open_pass(repo_root, session_id, keep=keep)

    def say(line: str) -> None:
        print(line)
        log.append(line)

    if log.rotated:
        say(f"rotated:  {len(log.rotated)} lane-log session(s) dropped past the {keep} kept")
    _print_supervise_header(repo_root, session_id, overrides, say)
    emit = partial(board_facts.emit_tick, repo_root, lane_label=args.label)
    hb = supervise.HeartbeatThread(lock, session_id, board=emit, report=say)
    hb.start()
    try:
        return _supervise_rounds(repo_root, args, hb=hb, say=say, session_id=session_id)
    except supervise.LaneSelectionError as exc:
        log.append(f"refused:  {exc}")
        print(f"supervise: refused - {exc}", file=sys.stderr)
        return 1
    except supervise.LockLostError as exc:
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
        carried |= supervise.committed_lanes(repo_root, state)
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

    admission = policy.spend_status(repo_root, state.root_issue)
    delegated = supervise.delegate_decisions(repo_root, state, beat=hb.check, admission=admission)
    supervise.say_delegated(delegated, say)
    for bead, coupled_to, dep_type in supervise.propose_coupling_edges(repo_root, state):
        say(f"coupling: {bead} -> {coupled_to} ({dep_type}) - from a found-info record")
    repaired = supervise.repair_stale_bindings(repo_root, state)
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

    repo_root = _repo_root()
    view = supervise.observe(repo_root, args.issue, lane_label=args.label)
    grant = policy.active_grant(repo_root, args.issue)
    if args.json:
        payload = (
            asdict(view)
            | {"supervised": view.supervised}
            | _spend_payload(view.spent_tokens, grant)
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    _print_observation(view, grant)
    return 0


SPEND_WINDOWS = {
    "spent_tokens": (
        "every measured run record on this session's track - its decomposition and the "
        "work it gates - across every grant and all time"
    ),
    "grant_spent_tokens": "spent_tokens since the active grant was issued, null when there is none",
    "token_budget": "the ceiling on grant_spent_tokens alone, never on spent_tokens",
}


def _spend_payload(spent_tokens: int, grant: policy.Grant | None) -> dict[str, object]:
    return {
        "grant_spent_tokens": (
            None if grant is None else policy.tokens_under_grant(spent_tokens, grant)
        ),
        "grant_baseline_tokens": None if grant is None else grant.spent_at_issue,
        "spend_windows": SPEND_WINDOWS,
    }


def _cmd_loop_stop(args: argparse.Namespace) -> int:

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
        print("stop: still requested - the supervisor returns after its current round")
        return 0
    print("stop: the supervisor returned; its round's lanes landed")
    return 0


def _supervisor_line(view: supervise.Observation) -> str:
    holder = view.holder
    if holder is None:
        return "(none running - basicly loop supervise <root> starts one)"
    who = f"{holder.session_id or 'unknown'} (pid {holder.pid or '?'})"
    beat = f"heartbeat {holder.age_s:.0f}s old"
    if view.holder_stale:
        beat += f" - STALE (over {supervise.STALE_AFTER_S:.0f}s; a contender may take over)"
    if not view.holder_on_this_root:
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
    return 1


_RETRY_ANSWER = re.compile(r"^\s*retry\b", re.IGNORECASE)


def _carry_out_rework_retry(repo_root: Path, item: decisions.DecisionItem) -> str | None:

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


_OFFERS_PARK = "or park?"


def _carry_out_rework_hold(repo_root: Path, item: decisions.DecisionItem) -> str | None:

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

    if binding is None:
        return None
    try:
        worktree.cleanup(binding.name, force=discard, repo_root=repo_root)
    except SystemExit as exc:
        return f"{exc}\n  Re-run with --discard to abandon them (a fresh confirm code is minted)."
    return None


def _cmd_loop_kill(args: argparse.Namespace) -> int:

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
        return 0


def cmd_runner(args: argparse.Namespace) -> int:
    handlers = {
        "list": _cmd_runner_list,
        "dry-run": _cmd_runner_dry_run,
        "run": _cmd_runner_run,
    }
    return _dispatch(args, "runner_command", handlers, group="runner")


def _resolve_runner(args: argparse.Namespace) -> runner.RunnerSpec:
    config = load_runner_config(_repo_root())
    return runner.select_runner(
        config.specs, args.runner or config.default, capable=runner.is_capable
    )


def _cmd_runner_list(_args: argparse.Namespace) -> int:
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
    spec = _resolve_runner(args)
    result = runner.run(spec, args.prompt, _repo_root(), dry_run=True)
    if result.handoff:
        print(
            f"runner '{spec.name}' [handoff]: no headless command — the work is handed off to the "
            "driving agent/human; nothing is executed."
        )
        return 0
    print(f"runner '{spec.name}':")
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
    if rejected := runner.probe_guardrails(spec):
        for problem in rejected:
            print(f"  guardrail: {problem}", file=sys.stderr)
        return 1
    return 0


def _cmd_runner_run(args: argparse.Namespace) -> int:
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
    result = merge.merge_worktree(_repo_root(), args.name, bead=args.bead, verify_mode=args.mode)
    print(f"  {result.name}: {result.status.upper()} — {result.detail}")
    return 0 if result.merged else 1


def _cmd_worktree_merge_queue(args: argparse.Namespace) -> int:
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

    repo_root = _repo_root()
    config = load_worktree_config(repo_root)
    record = tracker.read_record(repo_root, args.name)
    bound = loop_state.parse_worktree_ref(record.get("external_ref")) if record else None
    if bound is not None:
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
    worktree.cleanup(args.name, force=args.force, repo_root=_repo_root())
    return 0


def _cmd_worktree_list(_args: argparse.Namespace) -> int:
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
        "--overwrite-scaffolds",
        action="store_true",
        help=(
            "Replace scaffolded files (.vscode/tasks.json, basicly-gates.yml) with the "
            "current templates, keeping each previous copy as a .basicly-bak sibling"
        ),
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


def _add_style_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Destination output-styles root. Repeat for multiple roots.",
    )


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
        help="Deprecated no-op: every default root is used already.",
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

    parser.add_argument(
        "--label",
        metavar="LABEL",
        help="Fan out over the beads carrying LABEL instead of the root's parent-child "
        "children, so a release cut can be assembled from beads that already have an "
        "epic of origin (br permits one parent); the root then anchors the grant, the "
        "lock and the decision queue only",
    )


def _add_detach_arg(parser: argparse.ArgumentParser) -> None:

    parser.add_argument(
        "--detach",
        action="store_true",
        help="Start the command in its own session, print its pid and log path, "
        "and return at once - so no terminal that closes and no agent tool that "
        "kills a background job at its own ceiling can take a round with it",
    )


def _add_loop_input_args(parser: argparse.ArgumentParser) -> None:
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
    _add_detach_arg(l_run)
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
    _add_detach_arg(l_supervise)
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
    tracker_parser = subparsers.add_parser(
        "tracker",
        help="The owned work tracker: read the backlog, write to it, run its cutover",
    )
    tracker_sub = tracker_parser.add_subparsers(dest="tracker_command", required=True)
    tracker_query.add_parsers(tracker_sub)
    t_write = tracker_sub.add_parser("write", help="Make a tracker write through the engine seam")
    t_write.add_argument("argv", nargs=argparse.REMAINDER, help="The subcommand, after `--`")
    t_import = tracker_sub.add_parser(
        "import", help="Import a foreign tracker export (beads issues.jsonl) into the ledger"
    )
    t_import.add_argument("export", type=Path, help="Path to the export, one JSON object per line")
    t_import.add_argument("--source", default=None, help="Portable label recorded on every event")
    t_import.add_argument("--dry-run", action="store_true", help="Report and write nothing")
    t_import.add_argument(
        "--deleted",
        action="append",
        default=[],
        metavar="ID",
        help="A record you confirmed deleted out of band; absence alone never means deleted",
    )
    tracker_sub.add_parser("scrub", help="Rewrite the ledger without machine paths or usernames")


def _tolerate_narrow_consoles() -> None:

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _line_buffer_stdout() -> None:

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)


def _build_parser() -> argparse.ArgumentParser:
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

    styles_build_parser = subparsers.add_parser(
        "styles-build",
        help="Project output styles from .basicly/core/output-styles",
    )
    _add_style_root_args(styles_build_parser)

    styles_check_parser = subparsers.add_parser(
        "styles-check",
        help="Check projected output styles are up to date",
    )
    _add_style_root_args(styles_check_parser)

    retention_parser = subparsers.add_parser(
        "retention",
        help="Score a recall response against an instruction file to see if it is in context",
    )
    retention_parser.add_argument(
        "response", nargs="?", default="-", help="file holding the response, or - for stdin"
    )
    retention_parser.add_argument("--baseline", help="instruction file to score against")
    retention_parser.add_argument(
        "--verbose", action="store_true", help="list the rules that did not come back"
    )
    retention_parser.add_argument(
        "--strict", action="store_true", help="exit non-zero when the verdict is absent"
    )

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

    return {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "build": cmd_build,
        "check": cmd_check,
        "status": cmd_status,
        "health": cmd_health,
        "skills-build": cmd_skills_build,
        "skills-check": cmd_skills_check,
        "styles-build": cmd_styles_build,
        "styles-check": cmd_styles_check,
        "retention": cmd_retention,
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
    _tolerate_narrow_consoles()
    _line_buffer_stdout()
    args = _build_parser().parse_args(argv)

    try:
        return _dispatch(args, "command", _handlers())
    except ValidationError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — process boundary, reported not swallowed
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
