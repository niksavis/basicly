from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _installer():
    cached = sys.modules.get("basicly_kit_installer")
    if cached is not None:
        return cached
    for candidate in (_HERE / "installer.py", _HERE.parents[1] / "kit_installer.py"):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("basicly_kit_installer", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules["basicly_kit_installer"] = module
        spec.loader.exec_module(module)
        return module
    raise SystemExit("the kit installer is missing from beside this package")


installer = _installer()

LEDGER_DIR = ".basicly/ledger"


def ledger_rules(directory: Path):
    log_glob = installer.read_kit_constant(directory, "events.py", "LOG_GLOB")
    derived = installer.read_kit_constant(directory, "snapshot.py", "DERIVED_PATTERNS")
    return (
        (".gitattributes", (f"{log_glob} -text merge=union",)),
        (".gitignore", tuple(f"{LEDGER_DIR}/{pattern}" for pattern in derived)),
    )


KIT = installer.Kit(
    command="basicly-tracker",
    name="tracker",
    directory=_HERE / "kit",
    module="basicly_tracker_kit_cli",
    rules=ledger_rules,
)


def main(argv=None) -> int:
    return installer.run(KIT, argv)
