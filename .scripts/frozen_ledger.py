from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEDGER = Path(".basicly") / "ledger"
ARCHIVE = Path(".basicly") / "ledger-archive"
CONFIG = ".basicly-tracker.toml"
KIT = Path(".basicly") / "core" / "kit" / "tracker"
_MODULE = "basicly_tracker_kit_vault_clone_for_checks"


class FrozenLedgerError(Exception):
    pass


@dataclass(frozen=True)
class VaultRead:
    events: tuple[dict[str, Any], ...]
    note: str


def frozen(repo: Path) -> bool:
    return (repo / LEDGER).is_file()


def archive(repo: Path) -> Path:
    found = repo / ARCHIVE
    if not found.is_dir():
        raise FrozenLedgerError(
            f"{LEDGER.as_posix()} is frozen but {ARCHIVE.as_posix()} is missing; "
            "restore the archive the freeze made"
        )
    return found


def _settings(repo: Path) -> tuple[str, str]:
    path = repo / CONFIG
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FrozenLedgerError(f"{CONFIG} is unreadable: {exc}") from exc
    vault, prefix = data.get("vault"), data.get("prefix")
    if not isinstance(vault, str) or not isinstance(prefix, str) or not vault or not prefix:
        raise FrozenLedgerError(f"{CONFIG} must name both vault and prefix")
    return vault, prefix


def _vault_clone(repo: Path) -> Any:
    cached = sys.modules.get(_MODULE)
    if cached is not None:
        return cached
    kit = repo / KIT
    source = kit / "vault_clone.py"
    if not source.is_file():
        raise FrozenLedgerError(f"no tracker kit at {kit.as_posix()}")
    if str(kit) not in sys.path:
        sys.path.insert(0, str(kit))
    spec = importlib.util.spec_from_file_location(_MODULE, source)
    if spec is None or spec.loader is None:
        raise FrozenLedgerError(f"{source.as_posix()} is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = module
    spec.loader.exec_module(module)
    return module


def vault_events(repo: Path, environ: Mapping[str, str] = os.environ) -> VaultRead:
    vault, prefix = _settings(repo)
    module = _vault_clone(repo)
    state = module.state_root(environ, Path.home(), windows=os.name == "nt")
    clone = module.locate(state, vault)
    if not clone.bare.exists():
        return VaultRead((), "vault: no local clone, so only the archive was read")
    own = tuple(
        event["ledger"]
        for event in module.valid_events(clone)
        if event.get("kind") == "ledger"
        and isinstance(event.get("ledger"), dict)
        and str(event["ledger"].get("record", "")).partition("-")[0] == prefix
    )
    return VaultRead(own, f"vault: {len(own)} event(s) of prefix {prefix} from the local clone")
