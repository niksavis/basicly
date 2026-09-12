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
    basicly_version: str
    installed_at: str
    core_hashes: dict[str, str]


def sha256_of_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_core(core_root: Path) -> dict[str, str]:
    return {
        path.relative_to(core_root).as_posix(): sha256_of_file(path)
        for path in iter_catalog_files(core_root)
    }


def write_install_state(
    state_path: Path, version: str, core_hashes: dict[str, str]
) -> InstallState:

    state = InstallState(
        basicly_version=version,
        installed_at=datetime.now(UTC).isoformat(),
        core_hashes=dict(core_hashes),
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
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid install state: {exc}", state_path) from exc

    recorded_schema = payload.get("schema_version")
    if isinstance(recorded_schema, int) and recorded_schema > STATE_SCHEMA_VERSION:
        raise ValidationError(
            f"install state declares schema_version {recorded_schema}, newer than "
            f"this basicly understands ({STATE_SCHEMA_VERSION}); upgrade basicly",
            state_path,
        )

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

    drift: list[tuple[str, str]] = []
    for rel_path, recorded in sorted(state.core_hashes.items()):
        on_disk = core_root / rel_path
        if not on_disk.exists():
            drift.append((rel_path, "removed"))
        elif sha256_of_file(on_disk) != recorded:
            drift.append((rel_path, "modified"))
    return drift
