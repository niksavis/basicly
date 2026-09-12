from __future__ import annotations

import argparse
import filecmp
import importlib.util
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

DEFAULT_ROOT = Path(".basicly") / "kit"

SKILL_ROOTS = (
    Path(".claude") / "skills",
    Path(".agents") / "skills",
)

RETIRED_SKILL_ROOTS = (Path(".github") / "skills",)

ALWAYS_ON_FILES = (
    Path("CLAUDE.md"),
    Path(".claude") / "CLAUDE.md",
    Path("AGENTS.md"),
    Path(".github") / "copilot-instructions.md",
)

MANAGED_ROOT = Path(".basicly") / "core" / "kit"

GUIDANCE_FILE = "GUIDANCE.md"
INSTRUCTION_FILE = "INSTRUCTION.md"

SKIPPED_NAMES = frozenset({"__pycache__", ".basicly"})


def read_kit_constant(kit_dir: Path, module_file: str, name: str):
    module = load_kit_module(kit_dir, module_file, f"basicly_kit_read_{module_file[:-3]}")
    if not hasattr(module, name):
        raise SystemExit(f"{kit_dir / module_file} declares no {name}")
    return getattr(module, name)


def skill_paths(target: Path, name: str, roots=SKILL_ROOTS) -> list:
    return [target / root / name / "SKILL.md" for root in roots]


def marker(name: str) -> tuple:
    return (f"<!-- basicly-kit:{name} begin -->", f"<!-- basicly-kit:{name} end -->")


def prune_retired_skill(target: Path, name: str, body: str, stream) -> int:
    removed = 0
    for path in skill_paths(target, name, RETIRED_SKILL_ROOTS):
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            continue
        path.unlink()
        removed += 1
        _prune_empty(path.parent, target.resolve())
        stream.write(
            f"{name}: removed {path.relative_to(target)}; Copilot reads all three skill "
            "roots with no documented dedup, so a third copy is discovered a third time\n"
        )
    return removed


def write_skill(kit_dir: Path, target: Path, name: str, stream) -> int:
    source = kit_dir / GUIDANCE_FILE
    if not source.is_file():
        return 0
    body = source.read_text(encoding="utf-8")
    prune_retired_skill(target, name, body, stream)
    written = 0
    for path in skill_paths(target, name):
        if path.exists() and path.read_text(encoding="utf-8") == body:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        stream.write(f"{name}: wrote the skill to {path.relative_to(target)}\n")
        written += 1
    return written


def drop_skill(target: Path, name: str, stream) -> int:
    removed = 0
    for path in skill_paths(target, name, (*SKILL_ROOTS, *RETIRED_SKILL_ROOTS)):
        if not path.exists():
            continue
        path.unlink()
        removed += 1
        _prune_empty(path.parent, target.resolve())
    if removed:
        stream.write(f"{name}: removed the skill from {removed} root(s)\n")
    return removed


def block_text(kit_dir: Path, name: str) -> str:
    source = kit_dir / INSTRUCTION_FILE
    if not source.is_file():
        return ""
    begin, end = marker(name)
    return f"{begin}\n\n{source.read_text(encoding='utf-8').rstrip()}\n\n{end}\n"


def write_block(kit_dir: Path, target: Path, name: str, stream) -> int:
    block = block_text(kit_dir, name)
    if not block:
        return 0
    begin, end = marker(name)
    found = [path for path in ALWAYS_ON_FILES if (target / path).is_file()]
    if not found:
        stream.write(
            f"{name}: no always-on instruction file here, so nothing was edited. "
            f"Paste this into the one your agent reads:\n\n{block}\n"
        )
        return 0
    for relative in found:
        path = target / relative
        text = path.read_text(encoding="utf-8")
        if begin in text and end in text:
            head, _, rest = text.partition(begin)
            _, _, tail = rest.partition(end)
            updated = head + block.rstrip("\n") + tail
        else:
            updated = text.rstrip("\n") + "\n\n" + block
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            stream.write(f"{name}: wrote the always-on block into {relative}\n")
    return len(found)


def drop_block(target: Path, name: str, stream) -> int:
    begin, end = marker(name)
    removed = 0
    for relative in ALWAYS_ON_FILES:
        path = target / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if begin not in text or end not in text:
            continue
        head, _, rest = text.partition(begin)
        _, _, tail = rest.partition(end)
        path.write_text(head.rstrip("\n") + "\n" + tail.lstrip("\n"), encoding="utf-8")
        removed += 1
    if removed:
        stream.write(f"{name}: removed the always-on block from {removed} file(s)\n")
    return removed


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


def managed_elsewhere(target: Path, name: str) -> Path | None:
    candidate = target / MANAGED_ROOT / name
    return candidate if candidate.is_dir() else None


def refuse_second_copy(target: Path, name: str, command: str) -> None:
    managed = managed_elsewhere(target, name)
    if managed is None:
        return
    raise SystemExit(
        f"{name}: basicly already manages this kit at {managed}, and installing a second "
        f"copy at {vendored_root(target, name)} would leave two versions that nothing "
        f"reconciles.\n"
        f"Keep the basicly-managed one and run `basicly install` to update it, or remove "
        f"basicly from this repository first and then re-run `{command} init`."
    )


def install(request) -> int:
    kit_dir, target, name, stream = (
        request.kit.directory,
        request.target,
        request.kit.name,
        request.stream,
    )
    refuse_second_copy(target, name, request.kit.command)
    destination = vendored_root(target, name)
    for rule_file, lines in host_rules(request.kit):
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
    skills = write_skill(kit_dir, target, name, stream)
    blocks = 0
    if request.instructions:
        blocks = write_block(kit_dir, target, name, stream)
    elif block_text(kit_dir, name):
        stream.write(
            f"{name}: an always-on block is available; re-run with --with-instructions to "
            f"write it into your agent instruction files, or read it in "
            f"{destination / INSTRUCTION_FILE}\n"
        )
    if written == 0 and skills == 0 and blocks == 0:
        stream.write(f"{name}: already installed at this version; nothing changed\n")
    return 0


def uninstall(request) -> int:
    kit_dir, target, name, stream = (
        request.kit.directory,
        request.target,
        request.kit.name,
        request.stream,
    )
    destination = vendored_root(target, name)
    drop_skill(target, name, stream)
    drop_block(target, name, stream)
    for rule_file, lines in host_rules(request.kit):
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


def status(request) -> int:
    kit_dir, target, name, stream = (
        request.kit.directory,
        request.target,
        request.kit.name,
        request.stream,
    )
    destination = vendored_root(target, name)
    managed = managed_elsewhere(target, name)
    if managed is not None:
        stream.write(
            f"{name}: basicly manages this kit at {managed}; that copy is the one in use\n"
        )
        return 0
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
        for rule_file, lines in host_rules(request.kit)
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


class Request(NamedTuple):
    kit: object
    target: Path
    stream: object
    instructions: bool = False


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
        parser.add_argument(
            "--with-instructions",
            action="store_true",
            help="also write the always-on block into the agent instruction files present",
        )
        parsed = parser.parse_args(args[1:])
        request = Request(kit, parsed.into, sys.stdout, parsed.with_instructions)
        return verbs[args[0]](request)
    if not args or args[0] in {"-h", "--help"}:
        sys.stdout.write(
            f"usage: {kit.command} <init|update|uninstall|status> [--into PATH]\n"
            f"       {kit.command} <the kit's own subcommands>\n\n"
            f"init      vendor the kit into ./{DEFAULT_ROOT / kit.name} and write its skill\n"
            f"          to every agent skill root, so plain python3 runs it afterwards\n"
            f"          with no uvx and no network\n"
            f"          --with-instructions also writes the always-on block\n"
            f"update    the same, reporting what changed\n"
            f"uninstall remove only the files init wrote\n"
            f"status    say whether the installed copy matches this one\n\n"
        )
        if not args:
            return 0
    return load_kit_module(kit.directory, kit.cli_file, kit.module).main(args)
