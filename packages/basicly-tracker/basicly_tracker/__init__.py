from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _sibling(module_name: str, packaged: str, source: str, what: str):
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    for candidate in (_HERE / packaged, _HERE.parents[1] / source):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location(module_name, candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    raise SystemExit(f"the {what} is missing from beside this package")


installer = _sibling("basicly_kit_installer", "installer.py", "kit_installer.py", "kit installer")

LEDGER_DIR = ".basicly/ledger"


def ledger_rules(directory: Path):
    log_glob = installer.read_kit_constant(directory, "events.py", "LOG_GLOB")
    pending_glob = installer.read_kit_constant(directory, "events.py", "PENDING_GLOB")
    derived = installer.read_kit_constant(directory, "snapshot.py", "DERIVED_PATTERNS")
    return (
        (
            ".gitattributes",
            tuple(f"{glob} -text merge=union" for glob in (log_glob, pending_glob)),
        ),
        (".gitignore", tuple(f"{LEDGER_DIR}/{pattern}" for pattern in derived)),
    )


KIT = installer.Kit(
    command="basicly-tracker",
    name="tracker",
    directory=_HERE / "kit",
    module="basicly_tracker_kit_cli",
    rules=ledger_rules,
    configure_file="install_hook.py",
    configure_args=("--ledger", LEDGER_DIR),
)


def _bundle(args) -> int:
    parser = argparse.ArgumentParser(prog=f"{KIT.command} bundle")
    parser.add_argument(
        "--out", type=Path, default=Path(f"{KIT.name}.pyz"), help="the file to write"
    )
    parsed = parser.parse_args(args)
    if not (_HERE / "kit").is_dir():
        raise SystemExit("bundle runs from the built package, which carries the kit beside it")
    bundler = _sibling("basicly_kit_bundle", "bundle.py", "kit_bundle.py", "kit bundler")
    written = bundler.bundle(_HERE, parsed.out)
    sys.stdout.write(f"{KIT.name}: wrote {written}; run it as python {written} <verb>\n")
    return 0


FOLD_FLAG = "--fold-on-merge"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["bundle"]:
        return _bundle(args[1:])
    if args[:1] in (["init"], ["update"]) and FOLD_FLAG in args:
        args.remove(FOLD_FLAG)
        folding = KIT._replace(configure_args=(*KIT.configure_args, FOLD_FLAG))
        return installer.run(folding, args)
    return installer.run(KIT, args)
