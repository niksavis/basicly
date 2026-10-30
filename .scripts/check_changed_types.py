from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

from basicly import checkout

HIDDEN_CHECK = Path(__file__).with_name("check_hidden_types.py")


def _included(root: Path) -> list[str]:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(entry).rstrip("/") for entry in config["tool"]["pyright"]["include"]]


def _hidden(entry: str) -> bool:
    return any(part.startswith(".") and part != "." for part in PurePosixPath(entry).parts)


def _owner(path: str, included: list[str]) -> str | None:
    return next((e for e in included if path == e or path.startswith(e + "/")), None)


def _pyright(command: list[str], root: Path) -> bool:
    result = checkout.run(
        ["pyright", *command, "--pythonpath", sys.executable], cwd=root, check=False
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode == 0


def _scoped(root: Path, entry: str, files: list[str]) -> bool:
    folder = root / entry
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="basicly-pyright-",
        dir=folder,
        delete=False,
        encoding="utf-8",
    ) as stream:
        scoped = Path(stream.name)
        relative = [PurePosixPath(path).relative_to(entry).as_posix() for path in files]
        json.dump({"extends": str(root / "pyproject.toml"), "include": relative}, stream)
    try:
        return _pyright(["--project", str(scoped)], root)
    finally:
        scoped.unlink()


def _everything(root: Path) -> int:
    spec = importlib.util.spec_from_file_location("check_hidden_types", HIDDEN_CHECK)
    if spec is None or spec.loader is None:
        print(f"pyright: {HIDDEN_CHECK.name} is missing", file=sys.stderr)
        return 1
    hidden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hidden)
    visible = _pyright([], root)
    return int(not visible or hidden.main(["--root", str(root)]) != 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not args.files:
        return _everything(root)
    included = _included(root)
    groups: dict[str, list[str]] = {}
    for name in args.files:
        path = PurePosixPath(name.removeprefix("./")).as_posix()
        entry = _owner(path, included)
        if entry is not None and path.endswith(".py") and (root / path).is_file():
            groups.setdefault(entry if _hidden(entry) else "", []).append(path)
    visible = groups.pop("", [])
    passed = _pyright(visible, root) if visible else True
    for entry, files in groups.items():
        passed = _scoped(root, entry, files) and passed
    print(f"pyright: checked {sum(map(len, groups.values())) + len(visible)} changed files")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
