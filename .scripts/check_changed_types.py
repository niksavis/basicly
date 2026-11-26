from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

from basicly import checkout

HIDDEN_CHECK = Path(__file__).with_name("check_hidden_types.py")
SKIPPED = frozenset({".venv", "node_modules", "__pycache__", ".git"})
IMPORT = re.compile(
    r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import[ \t]+(\([^)]*\)|[^\n]+)|import[ \t]+([^\n]+))",
    re.MULTILINE,
)


def _included(root: Path) -> list[str]:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(entry).rstrip("/") for entry in config["tool"]["pyright"]["include"]]


def _hidden(entry: str) -> bool:
    return any(part.startswith(".") and part != "." for part in PurePosixPath(entry).parts)


def _owner(path: str, included: list[str]) -> str | None:
    return next((e for e in included if path == e or path.startswith(e + "/")), None)


def _dotted(path: str) -> str | None:
    parts = list(PurePosixPath(path.removeprefix("src/")).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def _names(clause: str) -> list[str]:
    lines = (line.split("#", 1)[0] for line in clause.strip().strip("()").splitlines())
    items = (item.split(" as ")[0].strip() for line in lines for item in line.split(","))
    return [item for item in items if item.isidentifier() or "." in item]


def _imports(path: str, text: str) -> set[str]:
    module = _dotted(path) or ""
    package = module.rsplit(".", 1)[0] if not path.endswith("__init__.py") else module
    found: set[str] = set()
    for source, names, plain in IMPORT.findall(text):
        if plain:
            found.update(_names(plain))
            continue
        level = len(source) - len(source.lstrip("."))
        base = source.lstrip(".")
        if level:
            anchor = package.split(".")[: max(len(package.split(".")) - level + 1, 0)]
            base = ".".join([*anchor, base] if base else anchor)
        found.add(base)
        found.update(f"{base}.{name}" for name in _names(names))
    return found


def _sources(root: Path, included: list[str]) -> list[str]:
    found = []
    for entry in included:
        top = root / entry
        candidates = [top] if top.is_file() else top.rglob("*.py") if top.is_dir() else []
        for path in candidates:
            relative = path.relative_to(root)
            if path.suffix == ".py" and not SKIPPED.intersection(relative.parts):
                found.append(relative.as_posix())
    return found


def _importers(root: Path, included: list[str], changed: list[str]) -> list[str]:
    modules = {name for path in changed if (name := _dotted(path))}
    if not modules:
        return []
    callers = []
    for path in _sources(root, included):
        if path in changed:
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        if _imports(path, text) & modules:
            callers.append(path)
    return callers


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
    changed = [PurePosixPath(name.removeprefix("./")).as_posix() for name in args.files]
    callers = _importers(root, included, [p for p in changed if p.endswith(".py")])
    groups: dict[str, list[str]] = {}
    for name in [*changed, *callers]:
        path = PurePosixPath(name.removeprefix("./")).as_posix()
        entry = _owner(path, included)
        if entry is not None and path.endswith(".py") and (root / path).is_file():
            groups.setdefault(entry if _hidden(entry) else "", []).append(path)
    visible = groups.pop("", [])
    passed = _pyright(visible, root) if visible else True
    for entry, files in groups.items():
        passed = _scoped(root, entry, files) and passed
    total = sum(map(len, groups.values())) + len(visible)
    print(f"pyright: checked {total} files, {len(callers)} of them importers of a changed module")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
