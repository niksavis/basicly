from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from . import __version__, ci_records
from .config import load_project_paths
from .scaffolds import CONSUMER_CI_WORKFLOW, VSCODE_TASKS_JSON, prepare_ci_runtime, repin

SCAFFOLD_BACKUP_SUFFIX = ".basicly-bak"
TASKS_LABEL = ".vscode/tasks.json"
CI_WORKFLOW_LABEL = ".github/workflows/basicly-gates.yml"
TEMPLATES = {TASKS_LABEL: VSCODE_TASKS_JSON, CI_WORKFLOW_LABEL: CONSUMER_CI_WORKFLOW}
SCAFFOLD_LABELS = tuple(TEMPLATES)
RECORD_NAME = "scaffolds.json"


@dataclass(frozen=True)
class Scaffold:
    path: Path
    content: str
    label: str
    record: Path | None = None


def record_path(repo_root: Path) -> Path:
    return load_project_paths(repo_root).state_path.with_name(RECORD_NAME)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def recorded(record: Path) -> dict[str, str]:
    try:
        found = json.loads(record.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    if not isinstance(found, dict):
        return {}
    return {str(key): str(value) for key, value in found.items()}


def _remember(record: Path | None, label: str, text: str) -> None:
    if record is None or not record.parent.is_dir():
        return
    held = recorded(record)
    if held.get(label) == _digest(text):
        return
    held[label] = _digest(text)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(held, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def edited(repo_root: Path) -> list[str]:
    held = recorded(record_path(repo_root))
    found = []
    for label, template in TEMPLATES.items():
        path = repo_root / label
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if text != template and _digest(text) != held.get(label):
            found.append(label)
    return found


def edited_refusal(labels: list[str]) -> str:
    named = "\n".join(f"  {label}" for label in labels)
    first = labels[0]
    return (
        "basicly install --overwrite-scaffolds refuses: these scaffolds differ from the "
        f"text install last wrote there, so replacing them would lose your edits:\n{named}\n"
        "Nothing was written. To replace one file and keep its old text as a backup, run:\n"
        f"  basicly install --overwrite-scaffolds {first}\n"
        "To keep your edits, run basicly install without the flag; it re-pins only the "
        "basicly pin lines."
    )


def overwrite_set(repo_root: Path, requested: list[str] | None) -> frozenset[str]:
    if requested is None:
        return frozenset()
    if requested:
        return frozenset(requested)
    if found := edited(repo_root):
        raise ValueError(edited_refusal(found))
    return frozenset(SCAFFOLD_LABELS)


def _backup(path: Path, existing: str) -> Path:
    backup = path.with_suffix(path.suffix + SCAFFOLD_BACKUP_SUFFIX)
    number = 2
    while backup.exists() and backup.read_text(encoding="utf-8") != existing:
        backup = path.with_suffix(f"{path.suffix}{SCAFFOLD_BACKUP_SUFFIX}-{number}")
        number += 1
    return backup


def ci_refusals(label: str, migrated: str) -> list[str]:
    return [
        f"{label}:{number}: {reason}; CI never reads tracker records, so install changes "
        f"nothing; fix: {ci_records.FIX}, then rerun basicly install"
        for number, reason in ci_records.violations(migrated)
    ]


def ci_workflow_refusals(repo_root: Path) -> list[str]:
    path = repo_root / CI_WORKFLOW_LABEL
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return ci_refusals(CI_WORKFLOW_LABEL, ci_records.migrate(prepare_ci_runtime(repin(text)[0]))[0])


def _keep(one: Scaffold, existing: str, *, dry_run: bool) -> bool:
    repinned, moved = repin(existing)
    migrated: list[str] = []
    if one.content == CONSUMER_CI_WORKFLOW:
        repinned, migrated = ci_records.migrate(prepare_ci_runtime(repinned))
        if refusals := ci_refusals(one.label, repinned):
            for refusal in refusals:
                print(refusal, file=sys.stderr)
            return False
    if repinned == existing:
        if existing == one.content and not dry_run:
            _remember(one.record, one.label, one.content)
        print(
            f"{one.label} already exists; left unchanged "
            f"(basicly install --overwrite-scaffolds {one.label} replaces it)"
        )
        return True
    backup = _backup(one.path, existing)
    if not dry_run:
        backup.write_text(existing, encoding="utf-8")
        one.path.write_text(repinned, encoding="utf-8")
        if repinned == one.content:
            _remember(one.record, one.label, one.content)
    for change in migrated:
        print(f"{'Would migrate' if dry_run else 'Migrated'} {one.label}: {change}")
    print(
        f"{'Would re-pin' if dry_run else 'Re-pinned'} {moved} basicly "
        f"reference(s) in {one.label} to v{__version__}; your previous copy "
        f"{'would be' if dry_run else 'is'} at {backup.name}"
    )
    return True


def write_scaffold(one: Scaffold, *, force: bool, dry_run: bool = False) -> bool:

    if one.path.exists():
        existing = one.path.read_text(encoding="utf-8")
        if not force:
            return _keep(one, existing, dry_run=dry_run)
        if existing == one.content:
            if not dry_run:
                _remember(one.record, one.label, one.content)
            print(f"{one.label} already current")
            return True
        backup = _backup(one.path, existing)
        if not dry_run:
            backup.write_text(existing, encoding="utf-8")
            one.path.write_text(one.content, encoding="utf-8")
            _remember(one.record, one.label, one.content)
        print(
            f"{'Would replace' if dry_run else 'Replaced'} {one.label}; your previous copy "
            f"{'would be' if dry_run else 'is'} at {backup.name}"
        )
        return True
    if not dry_run:
        one.path.parent.mkdir(parents=True, exist_ok=True)
        one.path.write_text(one.content, encoding="utf-8")
        _remember(one.record, one.label, one.content)
    print(f"{'Would write' if dry_run else 'Wrote'} {one.label}")
    return True


def _scaffold(repo_root: Path, label: str) -> Scaffold:
    return Scaffold(repo_root / label, TEMPLATES[label], label, record_path(repo_root))


def write_one(repo_root: Path, label: str, *, force: bool = False, dry_run: bool = False) -> bool:
    return write_scaffold(_scaffold(repo_root, label), force=force, dry_run=dry_run)


def write_all(repo_root: Path, *, force: Collection[str], dry_run: bool = False) -> bool:
    written = [
        write_one(repo_root, label, force=label in force, dry_run=dry_run) for label in TEMPLATES
    ]
    return all(written)
