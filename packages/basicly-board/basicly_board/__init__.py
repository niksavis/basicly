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


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["init"], ["update"], ["uninstall"]) and USER_FLAG in args:
        home = modes.user_home()
        if args[0] == "uninstall":
            return modes.uninstall_user(USER_SKILL, home, sys.stdout)
        return modes.install_user(KIT.directory, USER_SKILL, home, sys.stdout)
    return installer.run(KIT, args)
