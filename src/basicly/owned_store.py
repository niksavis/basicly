from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import os
import sys
import time
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

VAULT_CONFIG_KIT_MODULE = "vault_config"

VAULT_VIEW_KIT_MODULE = "vault_view"


class TrackerDivergenceError(RuntimeError):
    pass


class TrackerModeUnknownError(TrackerDivergenceError):
    pass


class LedgerMissingError(TrackerDivergenceError):
    pass


class VaultViewError(TrackerDivergenceError):
    pass


_mode_reader: list[Callable[[Path], str]] = []

_kit_modules: dict[tuple[str, str], ModuleType] = {}
_kit_sources: dict[ModuleType, bytes] = {}


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


def _vault_reach(repo_root: Path) -> tuple[Any, Any, Any] | None:

    config_kit = packaged_kit(VAULT_CONFIG_KIT_MODULE)
    try:
        config = config_kit.load(Path(repo_root))
    except config_kit.ConfigError as exc:
        raise VaultViewError(str(exc)) from None
    if config is None:
        return None
    try:
        packaged_kit("ledger_freeze").check(config.path.parent / LEDGER_DIR)
    except ValueError as exc:
        raise VaultViewError(str(exc)) from exc
    view = packaged_kit(VAULT_VIEW_KIT_MODULE)
    try:
        state = view.vault_clone.state_root(os.environ, Path.home(), windows=os.name == "nt")
        clone = view.vault_clone.locate(state, config.vault)
    except view.vault_clone.CloneError as exc:
        raise VaultViewError(str(exc)) from None
    return view, clone, config


def present_ledger(repo_root: Path, *, all_projects: bool = False) -> Path:

    reach = _vault_reach(repo_root)
    if reach is not None:
        view, clone, config = reach
        template = ledger_dir(repo_root)
        want = view.Want(config.prefix, all_projects, template if template.is_dir() else None)
        try:
            return packaged_kit("vault_pending").present(clone, want, clock=time.time)
        except view.ViewError as exc:
            raise VaultViewError(str(exc)) from None
    ledger = ledger_dir(repo_root)
    if not ledger.is_dir():
        raise LedgerMissingError(
            f"the tracker is not installed here: there is no ledger at {LEDGER_DIR.as_posix()}; "
            "run `basicly install` to set one up, or run this from the repository root"
        )
    return ledger


def readable_ledger(repo_root: Path) -> Path:

    try:
        return present_ledger(repo_root)
    except LedgerMissingError:
        return ledger_dir(repo_root)


def set_kit_version_reader(reader: Callable[[Path], str | None] | None) -> None:

    _kit_version_reader.clear()
    _agreed_kits.clear()
    if reader is not None:
        _kit_version_reader.append(reader)


def packaged_kit_dir() -> Path:

    return catalog.bundled_catalog_root() / KIT_TRACKER_DIR.relative_to(CORE_ROOT)


def packaged_constant(module_name: str, name: str) -> str:

    source = packaged_kit_dir() / f"{module_name}.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.Assign)
            and [getattr(target, "id", None) for target in node.targets] == [name]
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise TrackerDivergenceError(f"the packaged tracker kit's {source.name} declares no {name}")


def packaged_kit(module_name: str = DEFAULT_KIT_MODULE) -> Any:

    return _load_kit(packaged_kit_dir(), module_name)


def _vault_kit_error(repo_root: Path) -> TrackerDivergenceError:
    installed = _kit_version_reader[0](repo_root) if _kit_version_reader else None
    label = f" installed by basicly {installed}" if installed is not None else ""
    return TrackerDivergenceError(
        f"the project tracker kit{label} is older than the vault-write capability "
        f"required by basicly {__version__}; run `basicly install` to update the kit"
    )


def _refuse_a_kit_without_vault_admission(repo_root: Path, directory: Path) -> bool:

    resolved = str(directory.resolve())
    config = packaged_kit(VAULT_CONFIG_KIT_MODULE)
    try:
        configured = config.load(repo_root)
    except ValueError as exc:
        raise TrackerDivergenceError(str(exc)) from exc
    if configured is None:
        return False
    try:
        packaged_kit("ledger_freeze").check(configured.path.parent / LEDGER_DIR)
    except ValueError as exc:
        raise TrackerDivergenceError(str(exc)) from exc
    if resolved in _agreed_kits:
        return True
    source = directory / "vault_write.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise TrackerDivergenceError(
            "the project tracker kit cannot provide vault write admission; "
            "run `basicly install` to update it"
        ) from exc
    declared = {node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.ClassDef)}
    declared.update(
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    if not {"admission", "_vault_refused", "Write", "WriteContext", "land"} <= declared:
        raise _vault_kit_error(repo_root)
    _agreed_kits.add(resolved)
    return True


def kit(repo_root: Path, module_name: str = DEFAULT_KIT_MODULE) -> Any:

    directory = Path(repo_root) / KIT_TRACKER_DIR
    vault = directory.is_dir() and _refuse_a_kit_without_vault_admission(Path(repo_root), directory)
    module = _load_kit(directory, module_name)
    if vault and module_name == "commands":
        adapter = getattr(module, "vault_write", None)
        calls = (
            ("admission", (None, None, {}), {}),
            ("_vault_refused", (None, None, []), {}),
            ("WriteContext", (None, None), {}),
            ("Write", (None, None, None, {}), {}),
            ("land", (None, []), {"repeat": False}),
        )
        for name, args, kwargs in calls:
            function = getattr(adapter, name, None)
            if not callable(function):
                raise _vault_kit_error(Path(repo_root))
            try:
                inspect.signature(function).bind(*args, **kwargs)
            except (TypeError, ValueError) as exc:
                raise _vault_kit_error(Path(repo_root)) from exc
    return module


def _remember_kit_sources() -> None:
    for name, module in list(sys.modules.items()):
        if not name.startswith(_KIT_MODULE_PREFIX) or module is None or module in _kit_sources:
            continue
        source = getattr(module, "__file__", None)
        if source is not None:
            try:
                _kit_sources[module] = hashlib.sha256(Path(source).read_bytes()).digest()
            except OSError:
                continue


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
    loaded_as = _KIT_MODULE_PREFIX + module_name
    module = _kit_modules.get(key) or sys.modules.get(loaded_as)
    if module is not None:
        _remember_kit_sources()
        try:
            matches = _kit_sources.get(module) == hashlib.sha256(source.read_bytes()).digest()
        except OSError:
            matches = False
        if not matches:
            raise TrackerDivergenceError(
                "another checkout's tracker kit is already loaded with different code; "
                "start a fresh basicly process for this checkout "
                "before reading or writing its tracker"
            )
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
    _remember_kit_sources()
    _kit_modules[key] = module
    return module
