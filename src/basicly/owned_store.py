from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from basicly import tracker_paths

MODE_OWNED = "owned"

TRACKER_MODES = (MODE_OWNED,)
DEFAULT_TRACKER_MODE = MODE_OWNED

KIT_TRACKER_DIR = Path(".basicly") / "core" / "kit" / "tracker"

LEDGER_DIR = tracker_paths.LEDGER_DIR_NAME

_KIT_MODULE_PREFIX = "basicly_tracker_kit_"

DEFAULT_KIT_MODULE = "differential"

SCHEDULER_KIT_MODULE = "scheduler"

GATES_KIT_MODULE = "gates"


class TrackerDivergenceError(RuntimeError):
    pass


class TrackerModeUnknownError(TrackerDivergenceError):
    pass


_mode_reader: list[Callable[[Path], str]] = []

_kit_modules: dict[tuple[str, str], ModuleType] = {}


_prefix_reader: list[Callable[[Path], str | None]] = []


def set_prefix_reader(reader: Callable[[Path], str | None] | None) -> None:

    _prefix_reader.clear()
    if reader is not None:
        _prefix_reader.append(reader)


def tracker_prefix(repo_root: Path) -> str | None:

    if not _prefix_reader:
        return None
    return _prefix_reader[0](Path(repo_root))


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


def kit(repo_root: Path, module_name: str = DEFAULT_KIT_MODULE) -> Any:

    directory = Path(repo_root) / KIT_TRACKER_DIR
    source = directory / f"{module_name}.py"
    if not source.is_file():
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
