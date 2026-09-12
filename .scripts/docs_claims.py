from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from basicly import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docs_claim_layers as layers  # noqa: E402 - sibling; needs the insert above
import docs_claim_status as status_view  # noqa: E402 - sibling; needs the insert above
import docs_claim_surfaces as surfaces  # noqa: E402 - sibling; needs the insert above
import docs_claim_work_types as work_types  # noqa: E402 - sibling; needs the insert above
from docs_claim_sources import (  # noqa: E402
    ARCHITECTURE_MD,
    SKILLS_DIR,
    ClaimError,
    load_yaml,
    read_text,
    subparsers,
)

SKILLS_README = ".basicly/core/skills/README.md"
HOOKS_README = ".basicly/core/hooks/README.md"

TARGETS_DIR = ".basicly/core/targets"
HOOKS_DIR = ".basicly/core/hooks"
SRC_DIR = "src/basicly"

TUTORIAL_DIR = "docs/tutorial"
CLI_MD = "docs/reference/cli.md"
CHANGELOG_MD = "CHANGELOG.md"

_RELEASE_HEADING = re.compile(
    r"^## v(?P<version>\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}[ \t]*$", re.MULTILINE
)
_TUTORIAL_VERSIONS = (
    (
        re.compile(r"@v(?P<version>\d+\.\d+\.\d+)"),
        "install pin @v{found} is not the released v{released}",
    ),
    (
        re.compile(r"basicly (?P<version>\d+\.\d+\.\d+)"),
        "transcript quotes basicly {found}, not the released {released}",
    ),
)

FIX_HINT = "uv run python .scripts/docs_claims.py --fix"


def _splice(text: str, name: str, body: list[str]) -> str:
    begin = re.search(
        rf"^([ \t]*)<!-- docs-claims:begin {re.escape(name)} -->$", text, re.MULTILINE
    )
    end = re.search(rf"^[ \t]*<!-- docs-claims:end {re.escape(name)} -->$", text, re.MULTILINE)
    if begin is None or end is None:
        raise ClaimError(f"marker pair for block {name!r} not found")
    if end.start() < begin.end():
        raise ClaimError(f"block {name!r}: end marker precedes its begin marker")

    indent = begin.group(1)
    rendered = "".join(f"{indent}{line}\n" if line else "\n" for line in body)
    return f"{text[: begin.end()]}\n{rendered}{text[end.start() :]}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "",
        f"| {' | '.join(header)} |",
        f"| {' | '.join('---' for _ in header)} |",
        *(f"| {' | '.join(row)} |" for row in rows),
        "",
    ]


def _always_on_sizes(root: Path) -> list[str]:

    rows: list[list[str]] = []
    for target_path in sorted((root / TARGETS_DIR).glob("*.yaml")):
        target = load_yaml(target_path)
        if not target.get("enabled", False):
            continue
        name = target.get("name") or target_path.stem
        cap = target.get("max_size_warning")
        if not isinstance(cap, int):
            raise ClaimError(f"{target_path}: 'max_size_warning' must be an integer")
        unit = str(target.get("max_size_unit", "characters"))
        outputs = target.get("outputs") or {}
        for _, output in sorted(outputs.items()):
            surface = output.get("path")
            if not surface:
                continue
            text = read_text(root / surface)
            size = len(text.encode("utf-8")) if unit == "bytes" else len(text)
            rows.append([
                f"`{surface}` ({name})",
                f"{size} {unit}",
                str(cap),
                str(cap - size),
                str(len(text.splitlines())),
                str(target.get("max_lines_warning", "")),
            ])
    if not rows:
        raise ClaimError(f"{TARGETS_DIR}: no enabled target declares an always-on output")
    return _table(["Surface", "size", "cap", "headroom", "lines", "line cap"], rows)


def _catalog_skills(root: Path) -> list[str]:

    rows: list[list[str]] = []
    for source in sorted((root / SKILLS_DIR).glob("*/skill.yaml")):
        skill = load_yaml(source)
        name = skill.get("name")
        if not isinstance(name, str):
            raise ClaimError(f"{source}: 'name' must be a string")
        description = skill.get("description")
        technologies = skill.get("technologies") or []
        rows.append([
            f"`{name}`",
            f"`{skill.get('invocation', 'model')}`",
            ", ".join(f"`{tech}`" for tech in technologies) or "any",
            " ".join(description.split()) if isinstance(description, str) else "",
        ])
    if not rows:
        raise ClaimError(f"{SKILLS_DIR}: no skill sources found")
    return _table(["Skill", "Invocation", "Technologies", "Description"], rows)


def _script_purpose(entry: dict) -> str:

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ClaimError(
            f"{HOOKS_DIR}/hooks.yaml: {entry.get('id')!r} needs a `description` for the table"
        )
    return " ".join(description.split())


def _catalog_hooks(root: Path) -> list[str]:

    hooks_dir = root / HOOKS_DIR
    manifest = load_yaml(hooks_dir / "hooks.yaml")
    entries = manifest.get("hooks")
    if not isinstance(entries, list) or not entries:
        raise ClaimError(f"{HOOKS_DIR}/hooks.yaml: 'hooks' must be a non-empty list")

    rows: list[list[str]] = []
    for entry in entries:
        hook_id = entry.get("id")
        script = entry.get("script")
        stage = entry.get("stage")
        if not (hook_id and script and stage):
            raise ClaimError(f"{HOOKS_DIR}/hooks.yaml: entry {entry!r} needs id, script and stage")
        rows.append([
            f"`{hook_id}`",
            f"`{stage}`",
            f"`{entry.get('manager', 'git')}`",
            f"[`{script}`]({script})",
            _script_purpose(entry),
        ])
    return _table(["Hook", "Stage", "Manager", "Script", "Purpose"], rows)


def _cells(row: str) -> list[str]:

    return re.split(r"(?<!\\)\|", row)


def _cli_reference(root: Path) -> str:

    return read_text(root / CLI_MD)


def _documented_commands(section: str) -> set[str]:

    documented: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 3:
            continue
        command_cell = cells[1]
        lead = re.search(r"basicly ([a-z][a-z-]*)", command_cell)
        if lead:
            documented.add(lead.group(1))
        documented.update(re.findall(r"`([a-z][a-z-]+)`", command_cell))
    return documented


def _cli_commands_covered(root: Path) -> list[str]:
    top = subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")

    missing = sorted(set(top.choices) - _documented_commands(_cli_reference(root)))
    if missing:
        return [f"subcommands missing from the CLI tables: {', '.join(missing)}"]
    return []


def _cli_subcommands_covered(root: Path) -> list[str]:

    top = subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")
    rows = [row for row in _cli_reference(root).splitlines() if row.startswith("|")]

    problems: list[str] = []
    for parent, parser in sorted(top.choices.items()):
        nested = subparsers(parser)
        if nested is None:
            continue
        owned = [
            row
            for row in rows
            if len(_cells(row)) >= 3 and re.search(rf"basicly {parent}\b", _cells(row)[1])
        ]
        if not owned:
            problems.append(f"no CLI row documents the '{parent}' command group")
            continue
        documented = " ".join(owned)
        missing = [
            name
            for name in sorted(nested.choices)
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", documented)
        ]
        if missing:
            problems.append(
                f"'{parent}' subcommands missing from its CLI row(s): {', '.join(missing)}"
            )
    return problems


def _released_version(root: Path) -> str:
    found = _RELEASE_HEADING.search(read_text(root / CHANGELOG_MD))
    if found is None:
        raise ClaimError(f"{CHANGELOG_MD}: no `## vX.Y.Z - YYYY-MM-DD` release heading found")
    return found.group("version")


def _tutorial_versions_current(root: Path) -> list[str]:

    version = _released_version(root)
    problems: list[str] = []
    for path in sorted((root / TUTORIAL_DIR).glob("*.md")):
        name = path.relative_to(root).as_posix()
        for number, line in enumerate(read_text(path).splitlines(), start=1):
            for pattern, shape in _TUTORIAL_VERSIONS:
                problems.extend(
                    f"{name}:{number}: "
                    f"{shape.format(found=found.group('version'), released=version)}"
                    " — re-record the page against it"
                    for found in pattern.finditer(line)
                    if found.group("version") != version
                )
    return problems


@dataclass(frozen=True)
class Block:
    name: str
    path: str
    render: Callable[[Path], list[str]]


@dataclass(frozen=True)
class Assertion:
    name: str
    path: str
    check: Callable[[Path], list[str]]


BLOCKS: tuple[Block, ...] = (
    Block("always-on-sizes", ARCHITECTURE_MD, _always_on_sizes),
    Block("catalog-skills", SKILLS_README, _catalog_skills),
    Block("catalog-hooks", HOOKS_README, _catalog_hooks),
    Block("status-view", status_view.STATUS_MD, status_view.render_status_view),
    Block("layering-contract", ARCHITECTURE_MD, layers.render_layering_contract),
)

ASSERTIONS: tuple[Assertion, ...] = (
    Assertion("cli-commands", CLI_MD, _cli_commands_covered),
    Assertion("cli-subcommands", CLI_MD, _cli_subcommands_covered),
    Assertion("tutorial-versions", TUTORIAL_DIR, _tutorial_versions_current),
    Assertion("skill-work-types", SKILLS_DIR, work_types.skill_work_types),
    Assertion(
        "architecture-grading", ARCHITECTURE_MD, status_view.architecture_grades_no_capability
    ),
    *(
        Assertion(
            "consumer-commands",
            surface,
            partial(surfaces.consumer_commands_exist, surface=surface),
        )
        for surface in surfaces.CONSUMER_SURFACES
    ),
)


def _write(path: Path, text: str) -> None:

    newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    path.write_text(text, encoding="utf-8", newline=newline)


def _run_blocks(root: Path, blocks: tuple[Block, ...], *, fix: bool) -> list[str]:
    failures: list[str] = []
    for block in blocks:
        path = root / block.path
        try:
            current = read_text(path)
            updated = _splice(current, block.name, block.render(root))
        except ClaimError as exc:
            failures.append(f"{block.path} [{block.name}]: {exc}")
            continue
        if updated == current:
            continue
        if fix:
            _write(path, updated)
            print(f"regenerated: {block.path} [{block.name}]")
        else:
            failures.append(
                f"{block.path} [{block.name}]: generated block is stale — run `{FIX_HINT}`"
            )
    return failures


def _count(items: tuple[object, ...], noun: str) -> str:
    return f"{len(items)} {noun}{'' if len(items) == 1 else 's'} current"


def _run_assertions(root: Path) -> list[str]:
    failures: list[str] = []
    for assertion in ASSERTIONS:
        try:
            problems = assertion.check(root)
        except ClaimError as exc:
            problems = [str(exc)]
        failures.extend(f"{assertion.path} [{assertion.name}]: {problem}" for problem in problems)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate and gate the documentation claims derived from this repo itself."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report drift; write nothing")
    mode.add_argument("--fix", action="store_true", help="Regenerate every stale block")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to evaluate (default: this script's repo)",
    )
    parser.add_argument("--block", help="Only this block")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    blocks = tuple(b for b in BLOCKS if args.block in (None, b.name))
    if not blocks:
        parser.error(f"unknown block {args.block!r}")
    failures = _run_blocks(root, blocks, fix=args.fix)
    summary = _count(blocks, "generated block")
    if not args.block:
        failures += _run_assertions(root)
        summary += f", {_count(ASSERTIONS, 'assertion')}"
    if failures:
        for failure in failures:
            print(f"docs-claims: {failure}", file=sys.stderr)
        return 1
    print(f"docs-claims: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
