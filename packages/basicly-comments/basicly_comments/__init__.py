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


KIT = installer.Kit(
    command="basicly-comments",
    name="comments",
    directory=_HERE / "kit",
    module="basicly_comments_kit_cli",
)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "ratchet":
        for source, kit_root in (
            (_HERE / "git_adapter.py", KIT.directory),
            (
                _HERE.parents[2] / ".basicly/core/hooks/comment_ratchet.py",
                _HERE.parents[2] / ".basicly/core/kit/comments",
            ),
        ):
            if source.is_file():
                adapter = installer.load_kit_module(
                    source.parent, source.name, "basicly_comments_git_adapter"
                )
                return adapter.main(args[1:], kit_root)
        raise SystemExit("the comments ratchet adapter is missing from beside this package")
    return installer.run(KIT, args)
