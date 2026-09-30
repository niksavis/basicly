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


def _modes():
    cached = sys.modules.get("basicly_kit_modes")
    if cached is not None:
        return cached
    for candidate in (_HERE / "modes.py", _HERE.parents[1] / "kit_modes.py"):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("basicly_kit_modes", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules["basicly_kit_modes"] = module
        spec.loader.exec_module(module)
        return module
    raise SystemExit("the kit modes module is missing from beside this package")


modes = _modes()

PLACES = (
    modes.sandbox_file("board").as_posix(),
    (installer.DEFAULT_ROOT / "board" / "server.py").as_posix(),
)
USER_SKILL = modes.UserSkill(
    command="basicly-board",
    engine_use="`basicly board serve`",
    places=PLACES,
    replacements=(
        ("name: tracker-board", "name: basicly-board"),
        (
            "description: Serve the tracker",
            "description: In a repository that holds .basicly/ledger/, serve the tracker",
        ),
        ("python3 .basicly/kit/board/server.py", "basicly-board"),
        ("the `work-tracker` skill", "the `basicly-tracker` skill"),
        ("`.basicly/kit/board/README.md` has the table", "The board README has the table"),
    ),
)
USER_FLAG = "--user"


KIT = installer.Kit(
    command="basicly-board",
    name="board",
    directory=_HERE / "kit",
    module="basicly_board_kit_server",
    cli_file="server.py",
)


SOURCE = "git+https://github.com/niksavis/basicly@v{version}#subdirectory=packages/basicly-board"


def _replacements(typed: str) -> tuple:
    default = typed == USER_SKILL.command
    return (
        ("python3 .basicly/kit/board/server.py", typed),
        ("`.basicly/kit/board/README.md` has the table", "The board README has the table"),
        *((("`work-tracker` skill", "`basicly-tracker` skill"),) if default else ()),
        *((("`tracker-board` skill", "`basicly-board` skill"),) if default else ()),
    )


def _bundle_into(out: Path) -> Path:
    spec = importlib.util.spec_from_file_location("basicly_kit_bundle", _HERE / "bundle.py")
    if spec is None or spec.loader is None:
        raise SystemExit("the kit bundler is missing from beside this package")
    bundler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundler)
    return bundler.bundle(_HERE, out)


def _version() -> str:
    pin = _HERE / "tracker" / "pin.py"
    return installer.read_kit_constant(pin.parent, "pin.py", "KIT_VERSION") if pin.is_file() else ""


PACKAGE = modes.Package(
    installer=installer,
    kit=KIT,
    user=USER_SKILL,
    source=SOURCE.format(version=_version()),
    replacements=_replacements,
    bundle=_bundle_into,
)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["init"], ["update"], ["uninstall"]) and USER_FLAG in args:
        home = modes.user_home()
        if args[0] == "uninstall":
            return modes.uninstall_user(USER_SKILL, home, sys.stdout)
        return modes.install_user(KIT.directory, USER_SKILL, home, sys.stdout)
    sandbox = modes.SANDBOX_FLAG in args
    if sandbox:
        args.remove(modes.SANDBOX_FLAG)
    verbs = {
        "init": lambda request: modes.install_mode(PACKAGE, request, sandbox),
        "update": lambda request: modes.install_mode(PACKAGE, request, sandbox),
        "uninstall": lambda request: modes.uninstall_mode(PACKAGE, request),
    }
    return installer.run(KIT, args, verbs)
