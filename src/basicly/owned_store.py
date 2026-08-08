"""Where the owned tracker store is, for one repo.

One responsibility, and it is *resolution*: which rung of the cutover a repo
declares, which directory its event log lives in, and which kit module can read
it. Nothing here reads or writes an event — :mod:`basicly.mirror` says what a
write becomes and :mod:`basicly.br` appends it — so asking where the store is
never loads it.

Steps 3 and 4 of the cutover in `docs/requirements/work-tracker.md` §5. The kit under
:data:`KIT_TRACKER_DIR` is the owned store; the engine side of the seam that
writes to it and, once flipped, reads from it, sits above this module.

**Why the flip is a change to a seam rather than to callers.**
`basicly-tcmy.14` collapsed eleven hand-written unwraps of ``br show --json``
into ``br.read_record``, and every br invocation already goes through
``br.run_br``/``br.try_run_br``. Those two facts are the whole reason the flip is
an edit to one funnel rather than to eight modules: the engine's *write* surface
is one function and its *record read* surface is another.

Split out of ``br`` when the module-size ratchet caught that module growing. The
boundary is *the owned store* against *the external one*: :mod:`basicly.br` is
the single seam that spawns the ``br`` CLI, and nothing here spawns anything —
which is why the split leaves no import back into the module it came from.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from basicly import tracker_usage

MODE_EXTERNAL = "external"
MODE_DUAL = "dual"
MODE_OWNED = "owned"

# The cutover ladder, in the order §5 walks it. `external` is today's behaviour;
# `dual` writes both stores with br still authoritative for reads; `owned` is the
# flip — reads come from the ledger and br is *still written*, because the other ten
# subcommands the engine spawns still answer out of it (see `br.read_record`).
TRACKER_MODES = (MODE_EXTERNAL, MODE_DUAL, MODE_OWNED)
DEFAULT_TRACKER_MODE = MODE_EXTERNAL

# The kit's work-tracker store, relative to the repo that installed it.
KIT_TRACKER_DIR = Path(".basicly") / "core" / "kit" / "tracker"

# The ledger directory, taken off the usage ledger's own path rather than spelled a
# second time: both artifacts live in `.basicly/ledger/`, and
# `.scripts/kit_deployment.py` gates that directory's ignore rules against the same
# location. A literal here could drift from either without a gate noticing.
LEDGER_DIR = tracker_usage.LEDGER_FILE.parent

# The prefix a kit module is loaded under. Fixed, and checked against `sys.modules`
# before loading, for the reason `differential._load_migrate` gives: two loads of one
# file give two `Event` classes and an `isinstance` against the wrong one is false for
# the right reason. The kit's own sibling loaders follow the same convention
# (`basicly_tracker_kit_migrate`, `..._ids`, `..._differential`), so a module the kit
# loads for itself and one the engine loads here are the same object.
_KIT_MODULE_PREFIX = "basicly_tracker_kit_"

# The kit module :func:`kit` answers with when a caller names none — the differential,
# which carries `events` and `migrate` under it.
DEFAULT_KIT_MODULE = "differential"

# The kit module that owns the ranking (basicly-vkh0.20).
SCHEDULER_KIT_MODULE = "scheduler"


class TrackerDivergenceError(RuntimeError):
    """The owned ledger did not record a write the external tracker accepted.

    A hard failure on the write path, never a warning (`basicly-vkh0.19`'s first
    acceptance criterion). The two stores are only worth running side by side while
    they hold the same facts: a mirrored write that failed and said so in a log line
    leaves the ledger quietly short of one event, and the *next* thing to notice is
    the shadow differential — after however many more writes landed on top.

    A subclass of ``RuntimeError`` so a caller that already handles a br failure
    handles this one, and so the message is what `br.run_br` callers already print.
    """


# One-slot holder for the mode reader. A list rather than a rebound module global:
# `global` is the shape a reader has to chase, and this dependency is inverted
# already (see :func:`set_mode_reader`), so it should be obvious rather than terse.
_mode_reader: list[Callable[[Path], str]] = []

# Kit modules by (resolved tracker-directory path, module name).
_kit_modules: dict[tuple[str, str], ModuleType] = {}


def set_mode_reader(reader: Callable[[Path], str] | None) -> None:
    """Install the function that answers which tracker mode a repo declares.

    **The dependency is inverted, and an import cycle is why.** The declaration lives
    in ``[tracker] mode`` and only :mod:`basicly.config` may read it — it owns the
    three-layer merge over ``basicly.toml``, the gitignored overlay and the session
    overrides, and the strict schema that refuses a key this engine cannot honour.
    This module cannot import it: ``config`` imports ``runner``, ``runner`` imports
    ``run_record``, and ``run_record`` imports ``br``, which imports this module, so
    a reach back up to ``config`` would close a genuine cycle rather than merely
    inverting a lint tier. So ``config`` reaches down and installs its reader here,
    which is the same direction every other engine module takes to this one.

    With no reader installed the mode is :data:`DEFAULT_TRACKER_MODE`, which is the
    behaviour this seam had before the cutover existed — nothing is mirrored and
    nothing is flipped. Every process that reaches the tracker imports ``config``
    (``basicly.cli`` does, and it is the only entry point), and
    ``tests/test_owned_store.py`` asserts the installation rather than assuming it.

    Passing ``None`` uninstalls, which is what a test that wants the pre-cutover
    behaviour back should do.
    """
    _mode_reader.clear()
    if reader is not None:
        _mode_reader.append(reader)


def tracker_mode(repo_root: Path) -> str:
    """The cutover mode *repo_root* declares, or :data:`DEFAULT_TRACKER_MODE`."""
    if not _mode_reader:
        return DEFAULT_TRACKER_MODE
    return _mode_reader[0](Path(repo_root))


def ledger_dir(repo_root: Path) -> Path:
    """The owned ledger's directory for *repo_root*.

    **One ledger per repo, never one per worktree**, which is why this goes through
    :func:`tracker_usage.ledger_root` rather than joining onto *repo_root*. A loop
    worktree shares the base checkout's tracker through br's ``redirect`` file; a
    ledger that did not follow the same rule would take a lane's writes into the
    worktree's own copy and lose every one of them at teardown, which is exactly what
    happened to the usage spool (basicly-vkh0.8).
    """
    return tracker_usage.ledger_root(Path(repo_root)) / LEDGER_DIR


def kit(repo_root: Path, module_name: str = DEFAULT_KIT_MODULE) -> Any:
    """The installed kit's *module_name*; by default ``differential``.

    The differential rather than the event log directly, for the reason it loads
    ``migrate`` rather than ``events``: it is the module that owns every vocabulary
    the engine has to write in the store's own terms — the ``edge`` kind, the ``gate``
    kind and its payload keys — so reaching it through this one attribute chain
    (``kit(root).events``, ``kit(root).migrate``) keeps a second spelling of any of
    them impossible.

    A kit module that is not reachable that way is named instead — the scheduler
    (basicly-vkh0.20) is the first, because it sits *beside* the differential rather
    than under it. It loads its own sibling under the same fixed ``sys.modules`` name
    this function uses, which is what keeps one `RecordView` class in the process
    however the two are reached.

    Raises:
        TrackerDivergenceError: the module is not installed, or will not load. A hard
            failure rather than a degrade: a mode above ``external`` has already promised
            that both stores hold the same facts.
    """
    directory = Path(repo_root) / KIT_TRACKER_DIR
    source = directory / f"{module_name}.py"
    # Asked of the filesystem before either cache, and that ordering is the finding:
    # reusing an already-loaded kit is right (one `Event` class per process), but if the
    # reuse came first then a repo with no kit installed would be answered out of some
    # other repo's, and the mode would look enabled while writing nowhere.
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
