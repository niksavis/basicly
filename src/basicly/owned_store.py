from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from basicly import __version__, catalog, tracker_paths

MODE_OWNED = "owned"

TRACKER_MODES = (MODE_OWNED,)
DEFAULT_TRACKER_MODE = MODE_OWNED

CORE_ROOT = Path(".basicly") / "core"

KIT_TRACKER_DIR = CORE_ROOT / "kit" / "tracker"

LEDGER_DIR = tracker_paths.LEDGER_DIR_NAME

TEMPLATE_FILE = "template.json"

PREFIX_HOME = (LEDGER_DIR / TEMPLATE_FILE).as_posix()

_KIT_MODULE_PREFIX = "basicly_tracker_kit_"

DEFAULT_KIT_MODULE = "differential"

SCHEDULER_KIT_MODULE = "scheduler"

GATES_KIT_MODULE = "gates"


class TrackerDivergenceError(RuntimeError):
    pass


class TrackerModeUnknownError(TrackerDivergenceError):
    pass


class LedgerMissingError(TrackerDivergenceError):
    pass


_mode_reader: list[Callable[[Path], str]] = []

_kit_modules: dict[tuple[str, str], ModuleType] = {}


_prefix_reader: list[Callable[[Path], str | None]] = []

_kit_version_reader: list[Callable[[Path], str | None]] = []

_agreed_kits: set[str] = set()


def set_prefix_reader(reader: Callable[[Path], str | None] | None) -> None:

    _prefix_reader.clear()
    if reader is not None:
        _prefix_reader.append(reader)


def tracker_prefix(repo_root: Path) -> str | None:

    if not _prefix_reader:
        return None
    return _prefix_reader[0](Path(repo_root))


def set_prefix_command(prefix: str) -> str:

    kit_cli = (KIT_TRACKER_DIR / "cli.py").as_posix()
    return f"python3 {kit_cli} config {LEDGER_DIR.as_posix()} set prefix {prefix}"


def set_mode_reader(reader: Callable[[Path], str] | None) -> None:

    _mode_reader.clear()
    if reader is not None:
        _mode_reader.append(reader)


def tracker_mode(repo_root: Path) -> str:

    if not _mode_reader:
        raise TrackerModeUnknownError(
            "the tracker mode reader is not installed, so this process cannot tell "
            "which store a write reaches; import basicly.config, which installs it"
        )
    return _mode_reader[0](Path(repo_root))


def ledger_dir(repo_root: Path) -> Path:

    return tracker_paths.ledger_dir(Path(repo_root))


def present_ledger(repo_root: Path) -> Path:

    ledger = ledger_dir(repo_root)
    if not ledger.is_dir():
        raise LedgerMissingError(
            f"the tracker is not installed here: there is no ledger at {LEDGER_DIR.as_posix()}; "
            "run `basicly install` to set one up, or run this from the repository root"
        )
    return ledger


def set_kit_version_reader(reader: Callable[[Path], str | None] | None) -> None:

    _kit_version_reader.clear()
    _agreed_kits.clear()
    if reader is not None:
        _kit_version_reader.append(reader)


def packaged_kit_dir() -> Path:

    return catalog.bundled_catalog_root() / KIT_TRACKER_DIR.relative_to(CORE_ROOT)


def packaged_kit(module_name: str = DEFAULT_KIT_MODULE) -> Any:

    return _load_kit(packaged_kit_dir(), module_name)


def _refuse_a_kit_of_another_version(repo_root: Path, directory: Path) -> None:

    resolved = str(directory.resolve())
    if resolved in _agreed_kits or not _kit_version_reader:
        return
    installed = _kit_version_reader[0](repo_root)
    if installed is not None and installed != __version__:
        raise TrackerDivergenceError(
            f"the tracker kit at {KIT_TRACKER_DIR.as_posix()} was installed by basicly "
            f"{installed}, and this engine is basicly {__version__}; run `basicly install` "
            "to update the kit"
        )
    _agreed_kits.add(resolved)


def kit(repo_root: Path, module_name: str = DEFAULT_KIT_MODULE) -> Any:

    directory = Path(repo_root) / KIT_TRACKER_DIR
    if directory.is_dir():
        _refuse_a_kit_of_another_version(Path(repo_root), directory)
    return _load_kit(directory, module_name)


def _load_kit(directory: Path, module_name: str) -> Any:

    source = directory / f"{module_name}.py"
    if not source.is_file():
        if directory.is_dir():
            raise TrackerDivergenceError(
                f"the tracker kit at {directory} has no {source.name}, so it is older than "
                f"this engine; run `basicly install` to update it"
            )
        raise TrackerDivergenceError(f"the tracker kit is not installed at {directory}")
    key = (str(directory.resolve()), module_name)
    if (cached := _kit_modules.get(key)) is not None:
        return cached
    loaded_as = _KIT_MODULE_PREFIX + module_name
    module = sys.modules.get(loaded_as)
    if module is None:
        spec = importlib.util.spec_from_file_location(loaded_as, source)
        if spec is None or spec.loader is None:
            raise TrackerDivergenceError(f"the tracker kit is not installed at {directory}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[loaded_as] = module
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError) as exc:
            del sys.modules[loaded_as]
            raise TrackerDivergenceError(
                f"the tracker kit at {directory} did not load: {exc}"
            ) from exc
    _kit_modules[key] = module
    return module
