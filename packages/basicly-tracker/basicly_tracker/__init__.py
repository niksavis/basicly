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
modes = _sibling("basicly_kit_modes", "modes.py", "kit_modes.py", "kit modes")

LEDGER_DIR = ".basicly/ledger"


def ledger_rules(directory: Path):
    log_glob = installer.read_kit_constant(directory, "events.py", "LOG_GLOB")
    pending_glob = installer.read_kit_constant(directory, "events.py", "PENDING_GLOB")
    derived = installer.read_kit_constant(directory, "snapshot.py", "DERIVED_PATTERNS")
    lock_name = installer.read_kit_constant(directory, "events.py", "LOCK_NAME")
    return (
        (
            ".gitattributes",
            tuple(f"{glob} -text merge=union" for glob in (log_glob, pending_glob)),
        ),
        (".gitignore", tuple(f"{LEDGER_DIR}/{pattern}" for pattern in (*derived, lock_name))),
    )


PLACES = (
    modes.sandbox_file("tracker").as_posix(),
    (installer.DEFAULT_ROOT / "tracker" / "cli.py").as_posix(),
)
MANAGED = (
    f"{installer.DEFAULT_ROOT.as_posix()}/",
    modes.sandbox_file("tracker").as_posix(),
    modes.sandbox_file("board").as_posix(),
)
LAYOUT_ARGS = (
    *(arg for place in PLACES for arg in ("--tracker-at", place)),
    *(arg for path in MANAGED for arg in ("--managed", path)),
)

USER_SKILL = modes.UserSkill(
    command="basicly-tracker",
    engine_use="its `work-tracker` skill",
    places=PLACES,
    replacements=(
        ("name: work-tracker", "name: basicly-tracker"),
        (
            "description: Use the append-only work tracker as this repository's issue tracker.",
            "description: In a repository that holds .basicly/ledger/, use the append-only "
            "work tracker as its issue tracker.",
        ),
        (
            "`.basicly/kit/tracker/REFERENCE.md` lists every command with one example.",
            "`basicly-tracker --help` lists every command.",
        ),
        ("python3 .basicly/kit/tracker/cli.py", "basicly-tracker"),
        ("the `tracker-board` skill", "the `basicly-board` skill"),
    ),
)

KIT = installer.Kit(
    command="basicly-tracker",
    name="tracker",
    directory=_HERE / "kit",
    module="basicly_tracker_kit_cli",
    rules=ledger_rules,
    configure_file="install_hook.py",
    configure_args=(
        "--ledger",
        LEDGER_DIR,
        "--pin",
        "--import-command",
        "basicly-tracker init --import {source}",
        *LAYOUT_ARGS,
    ),
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


def _plugin(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"{KIT.command} plugin")
    parser.add_argument("--out", type=Path, required=True, help="a new plugin directory")
    parsed = parser.parse_args(args)
    if not KIT.directory.is_dir():
        raise SystemExit(
            "plugin runs from the built tracker package, which carries the kit beside it"
        )
    exporter = _sibling("basicly_kit_plugin", "plugin.py", "kit_plugin.py", "plugin exporter")
    bundler = _sibling("basicly_kit_bundle", "bundle.py", "kit_bundle.py", "kit bundler")
    version = installer.read_kit_constant(KIT.directory, "pin.py", "KIT_VERSION")
    try:
        written = exporter.export(_HERE, parsed.out, version, bundler.bundle)
    except ValueError as error:
        sys.stderr.write(f"tracker: {error}\n")
        return 1
    sys.stdout.write(f"tracker: wrote plugin {written}\n")
    return 0


def _serve(args: list[str]) -> int:
    if not (_HERE / "board" / "server.py").is_file():
        raise SystemExit("serve runs from the built tracker package, which carries its human UI")
    server = _sibling("basicly_tracker_ui", "board/server.py", "", "tracker UI")
    server.routes.TRACKER_DIR = KIT.directory
    return int(server.main(args))


FOLD_FLAG = "--fold-on-merge"
END_MIRROR_FLAG = "--end-mirror"
IMPORT_FLAG = "--import"
MIRROR_FLAG = "--mirror"


def _configure_flags(args: list) -> tuple:
    passed = []
    for flag in (FOLD_FLAG, END_MIRROR_FLAG):
        if flag in args:
            args.remove(flag)
            passed.append(flag)
    for flag, sources in ((IMPORT_FLAG, ("beads", "beans")), (MIRROR_FLAG, ("beads",))):
        if flag in args:
            at = args.index(flag)
            if at + 1 == len(args):
                named = " or ".join(f"{flag} {source}" for source in sources)
                raise SystemExit(f"{flag} needs a source: {named}")
            passed += args[at : at + 2]
            del args[at : at + 2]
    return tuple(passed)


USER_FLAG = "--user"


def _user(args: list) -> int:
    home = modes.user_home()
    if args[0] == "uninstall":
        return modes.uninstall_user(USER_SKILL, home, sys.stdout)
    return modes.install_user(KIT.directory, USER_SKILL, home, sys.stdout)


def _replacements(typed: str) -> tuple:
    return (
        ("python3 .basicly/kit/tracker/cli.py", typed),
        (
            "`.basicly/kit/tracker/REFERENCE.md` lists every command with one example.",
            f"`{typed} --help` lists every command.",
        ),
        *((("`work-tracker` skill", "`basicly-tracker` skill"),) if typed == KIT.command else ()),
    )


def _bundle_into(out: Path) -> Path:
    bundler = _sibling("basicly_kit_bundle", "bundle.py", "kit_bundle.py", "kit bundler")
    return bundler.bundle(_HERE, out)


PACKAGE = modes.Package(
    installer=installer,
    kit=KIT,
    user=USER_SKILL,
    source=installer.read_kit_constant(KIT.directory, "pin.py", "INSTALL_SOURCE").format(
        version=installer.read_kit_constant(KIT.directory, "pin.py", "KIT_VERSION")
    )
    if (KIT.directory / "pin.py").is_file()
    else "",
    replacements=_replacements,
    bundle=_bundle_into,
    check=("stats", LEDGER_DIR),
)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[:1] in (["--help"], ["-h"]):
        sys.stdout.write(
            "Package commands:\n"
            "  serve LEDGER [--port PORT]  run the human UI and HTTP API\n"
            "  plugin --out DIRECTORY     export a portable agent plugin\n"
            "  bundle [--out FILE]         export the standalone Python archive\n\n"
        )
    if args[:1] == ["bundle"]:
        return _bundle(args[1:])
    if args[:1] == ["plugin"]:
        return _plugin(args[1:])
    if args[:1] == ["serve"]:
        return _serve(args)
    if args == ["update"]:
        sys.stderr.write(
            "basicly-tracker: update needs a ledger and a record, so nothing ran. Update a "
            "record with `basicly-tracker update <ledger> <record> --field name=value`; "
            "install or upgrade the kit in this repository with `basicly-tracker init`\n"
        )
        return 2
    if args[:1] in (["init"], ["update"], ["uninstall"]) and USER_FLAG in args:
        return _user(args)
    passed = _configure_flags(args) if args[:1] in (["init"], ["update"]) else ()
    sandbox = modes.SANDBOX_FLAG in args
    if sandbox:
        args.remove(modes.SANDBOX_FLAG)
    kit = KIT._replace(configure_args=(*KIT.configure_args, *passed))
    package = PACKAGE._replace(kit=kit)
    verbs = {
        "init": lambda request: modes.install_mode(package, request, sandbox),
        "update": lambda request: modes.install_mode(package, request, sandbox),
        "uninstall": lambda request: modes.uninstall_mode(package, request),
    }
    return installer.run(kit, args, verbs)
