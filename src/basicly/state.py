"""Install provenance state (``.basicly/state/install.json``).

``basicly install`` snapshots the managed core right after materializing the
bundled catalog: basicly version, timestamp, and a per-file sha256 map. A later
mismatch between the snapshot and the on-disk core means the managed content
was hand-edited; the upgrade sync (``basicly-zrj.12.2``) uses that to protect
user changes from silent overwrites, and ``basicly check`` reports it as
advisory drift. The authoring repo (core is its own bundled source) never
writes a state file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .catalog import iter_catalog_files
from .schema import ValidationError

STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class InstallState:
    """Provenance recorded by the most recent ``basicly install``."""

    basicly_version: str
    installed_at: str
    core_hashes: dict[str, str]


def _sha256_of_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_core(core_root: Path) -> dict[str, str]:
    """Hash every managed core file, keyed by core-relative posix path."""
    return {
        path.relative_to(core_root).as_posix(): _sha256_of_file(path)
        for path in iter_catalog_files(core_root)
    }


def write_install_state(state_path: Path, version: str, core_root: Path) -> InstallState:
    """Snapshot the on-disk core and write the provenance file."""
    state = InstallState(
        basicly_version=version,
        installed_at=datetime.now(UTC).isoformat(),
        core_hashes=snapshot_core(core_root),
    )
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "basicly_version": state.basicly_version,
        "installed_at": state.installed_at,
        "core": state.core_hashes,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def read_install_state(state_path: Path) -> InstallState | None:
    """Read the provenance file; None when absent, ValidationError when corrupt."""
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid install state: {exc}", state_path) from exc

    core = payload.get("core")
    version = payload.get("basicly_version")
    installed_at = payload.get("installed_at")
    if (
        not isinstance(core, dict)
        or not isinstance(version, str)
        or not isinstance(installed_at, str)
    ):
        raise ValidationError(
            "invalid install state: expected basicly_version, installed_at, and core keys",
            state_path,
        )
    return InstallState(
        basicly_version=version,
        installed_at=installed_at,
        core_hashes={str(key): str(value) for key, value in core.items()},
    )


def core_drift(state: InstallState, core_root: Path) -> list[tuple[str, str]]:
    """Compare the recorded snapshot against the on-disk core.

    Returns ``(core-relative path, reason)`` pairs, reason being ``"modified"``
    or ``"removed"``. Files added to the core after install are not drift — the
    snapshot only guards what install materialized.
    """
    drift: list[tuple[str, str]] = []
    for rel_path, recorded in sorted(state.core_hashes.items()):
        on_disk = core_root / rel_path
        if not on_disk.exists():
            drift.append((rel_path, "removed"))
        elif _sha256_of_file(on_disk) != recorded:
            drift.append((rel_path, "modified"))
    return drift
