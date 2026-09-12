from __future__ import annotations

import argparse
import filecmp
import importlib.util
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

DEFAULT_ROOT = Path(".basicly") / "kit"

SKIPPED_NAMES = frozenset({"__pycache__", ".basicly"})


def read_kit_constant(kit_dir: Path, module_file: str, name: str):
    module = load_kit_module(kit_dir, module_file, f"basicly_kit_read_{module_file[:-3]}")
    if not hasattr(module, name):
        raise SystemExit(f"{kit_dir / module_file} declares no {name}")
    return getattr(module, name)


def host_rules(kit):
    return () if kit is None or kit.rules is None else kit.rules(kit.directory)


def ensure_lines(path: Path, lines) -> list[str]:
    existing = path.read_text(encoding="utf-8").split("\n") if path.exists() else []
    present = {line.strip() for line in existing}
    missing = [line for line in lines if line not in present]
    if not missing:
        return []
    body = "\n".join(existing).rstrip("\n")
    prefix = body + "\n" if body else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prefix + "\n".join(missing) + "\n", encoding="utf-8")
    return missing


def drop_lines(path: Path, lines) -> int:
    if not path.exists():
        return 0
    kept = [line for line in path.read_text(encoding="utf-8").split("\n") if line not in lines]
    while kept and kept[-1] == "":
        kept.pop()
    removed = len(path.read_text(encoding="utf-8").split("\n")) - len(kept)
    if not [line for line in kept if line.strip()]:
        path.unlink()
        return removed
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def kit_files(kit_dir: Path) -> list[Path]:
    found = [
        path
        for path in sorted(kit_dir.rglob("*"))
        if path.is_file()
        and not any(part in SKIPPED_NAMES for part in path.relative_to(kit_dir).parts)
    ]
    if not found:
        raise SystemExit(f"the kit is missing from {kit_dir}")
    return found


def vendored_root(target: Path, name: str) -> Path:
    return target / DEFAULT_ROOT / name


def install(kit_dir: Path, target: Path, name: str, stream, kit=None) -> int:
    destination = vendored_root(target, name)
    for rule_file, lines in host_rules(kit):
        path = target / rule_file
        try:
            added = ensure_lines(path, lines)
        except OSError as err:
            raise SystemExit(
                f"{name}: cannot write {path}, which the kit's correctness depends on, "
                f"so nothing was installed: {err}"
            ) from err
        for line in added:
            stream.write(f"{name}: added to {rule_file}: {line}\n")
    written = unchanged = 0
    for source in kit_files(kit_dir):
        relative = source.relative_to(kit_dir)
        path = destination / relative
        if path.exists() and filecmp.cmp(source, path, shallow=False):
            unchanged += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        written += 1
    stream.write(f"{name}: {written} file(s) written, {unchanged} unchanged, in {destination}\n")
    if written == 0:
        stream.write(f"{name}: already installed at this version; nothing changed\n")
    return 0


def uninstall(kit_dir: Path, target: Path, name: str, stream, kit=None) -> int:
    destination = vendored_root(target, name)
    for rule_file, lines in host_rules(kit):
        dropped = drop_lines(target / rule_file, set(lines))
        if dropped:
            stream.write(f"{name}: removed {dropped} line(s) from {rule_file}\n")
    if not destination.exists():
        stream.write(f"{name}: not installed at {destination}; nothing removed\n")
        return 0
    shipped = {source.relative_to(kit_dir) for source in kit_files(kit_dir)}
    removed = kept = 0
    for path in sorted(destination.rglob("*"), reverse=True):
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
            continue
        relative = path.relative_to(destination)
        derived = path.suffix == ".pyc" and "__pycache__" in relative.parts
        if relative in shipped or derived:
            path.unlink()
            removed += 1
        else:
            kept += 1
    if not kept:
        _prune_empty(destination, target.resolve())
    stream.write(
        f"{name}: {removed} file(s) removed, {kept} left in place because we did not write them\n"
    )
    return 0


def _prune_empty(directory: Path, stop_at: Path) -> None:
    path = directory.resolve()
    while path != stop_at and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        path = path.parent


def status(kit_dir: Path, target: Path, name: str, stream, kit=None) -> int:
    destination = vendored_root(target, name)
    if not destination.exists():
        stream.write(f"{name}: not installed at {destination}\n")
        return 1
    stale = [
        str(source.relative_to(kit_dir))
        for source in kit_files(kit_dir)
        if not (destination / source.relative_to(kit_dir)).exists()
        or not filecmp.cmp(source, destination / source.relative_to(kit_dir), shallow=False)
    ]
    if stale:
        stream.write(
            f"{name}: installed at {destination} but {len(stale)} file(s) differ: {stale[0]}\n"
        )
        stream.write(f"{name}: run `init` to update\n")
        return 1
    missing = [
        f"{rule_file}: {line}"
        for rule_file, lines in host_rules(kit)
        for line in lines
        if line not in _lines_of(target / rule_file)
    ]
    if missing:
        stream.write(f"{name}: installed at {destination}, but {len(missing)} rule(s) are absent\n")
        for entry in missing:
            stream.write(f"{name}:   {entry}\n")
        stream.write(f"{name}: run `init` to write them\n")
        return 1
    stream.write(f"{name}: installed and current at {destination}\n")
    return 0


def _lines_of(path: Path) -> set:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").split("\n")}


def load_kit_module(kit_dir: Path, file_name: str, module_name: str):
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, kit_dir / file_name)
    if spec is None or spec.loader is None:
        raise SystemExit(f"the kit's {file_name} is missing from {kit_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class Kit(NamedTuple):
    command: str
    name: str
    directory: Path
    module: str
    cli_file: str = "cli.py"
    rules: object = None


def run(kit: Kit, argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verbs = {"init": install, "update": install, "uninstall": uninstall, "status": status}
    if args and args[0] in verbs:
        parser = argparse.ArgumentParser(prog=f"{kit.command} {args[0]}")
        parser.add_argument(
            "--into",
            type=Path,
            default=Path(),
            help="the repository to install into (default: the working directory)",
        )
        parsed = parser.parse_args(args[1:])
        return verbs[args[0]](kit.directory, parsed.into, kit.name, sys.stdout, kit)
    if not args or args[0] in {"-h", "--help"}:
        sys.stdout.write(
            f"usage: {kit.command} <init|update|uninstall|status> [--into PATH]\n"
            f"       {kit.command} <the kit's own subcommands>\n\n"
            f"init      vendor the kit into ./{DEFAULT_ROOT / kit.name}, so plain python3\n"
            f"          runs it afterwards with no uvx and no network\n"
            f"update    the same, reporting what changed\n"
            f"uninstall remove only the files init wrote\n"
            f"status    say whether the installed copy matches this one\n\n"
        )
        if not args:
            return 0
    return load_kit_module(kit.directory, kit.cli_file, kit.module).main(args)
