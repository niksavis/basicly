"""Generate and gate the documentation claims this repo derives from its own tree.

A document that restates a fact the tree already answers — a measured character
count, the set of shipped subcommands, a catalog inventory — goes stale the moment
the tree moves, and nothing notices. Correcting the prose fixes today's copy and
guarantees tomorrow's drift. The owner's rule applies: a deterministic fact is a
script, not an instruction. So these facts are *generated* into marked blocks and
gated on every commit rather than written by hand.

Two kinds of claim, because they fail differently:

* **Generated blocks** — the whole block between a marker pair is rendered from the
  tree, so ``--fix`` repairs any drift with no hand editing. Everything inside a
  block is derived; authored prose belongs outside it.
* **Assertions** — a claim spread through authored prose that a script can check but
  cannot write. The §8 command tables carry a hand-written behavior paragraph per
  row, so the gate asserts *coverage* (every shipped subcommand appears) and names
  what is missing; the row itself stays a human's job.

Markers are HTML comments so they render as nothing::

    <!-- docs-claims:begin <name> -->
    <!-- docs-claims:end <name> -->

The begin marker's indentation is reapplied to every generated line, so a block
nested in a numbered list keeps its list item.

Wired as a ``[[verify.checks]]`` entry rather than a new CLI subcommand pair: the
claims gated here are basicly's own documentation, not a consumer artifact, so
nothing needs projecting. ``--check`` is the check command and ``--fix`` the
``fix_command``, so the pre-commit fast set applies the regeneration and re-stages
it exactly as it does for ``ruff format``.

``tests/test_docs_drift.py`` keeps the *reverse* direction of the command claim (a
removed subcommand must leave the tables) at pre-push; this script promotes the
forward direction — an omitted subcommand — into the fast set.

Usage::

    python .scripts/docs_claims.py --check   # report drift, write nothing
    python .scripts/docs_claims.py --fix     # regenerate every stale block
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# `loop._LEAF_TYPES` is private and has no public alias; this script is repo-local
# tooling reading its own tree, and the whole point is to bind to the definition the
# engine actually uses rather than to a second copy of it.
from basicly import cli, config, loop

REPO_ROOT = Path(__file__).resolve().parent.parent
# `.scripts` is deliberately not a package, so the sibling below is importable only
# with this script's own directory on the path. Running this file as a script already
# puts it there; the insert is for `tests/test_docs_claims.py`, which loads this module
# by file path through `spec_from_file_location` and so starts with neither entry.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docs_claim_sources import ClaimError, load_yaml, read_text  # noqa: E402

ARCHITECTURE_MD = "docs/architecture/architecture.md"
IMPLEMENTATION_PLAN = "docs/plan/implementation-plan.md"
SKILLS_README = ".basicly/core/skills/README.md"
HOOKS_README = ".basicly/core/hooks/README.md"

TARGETS_DIR = ".basicly/core/targets"
SKILLS_DIR = ".basicly/core/skills"
HOOKS_DIR = ".basicly/core/hooks"
SRC_DIR = "src/basicly"

TOOL_BR_SKILL = f"{SKILLS_DIR}/tool-br/skill.yaml"

# `uv run python`, not a bare `python`: on Windows the bare form resolves to a system
# interpreter that cannot import this script's dependencies (basicly-tcmy.32), so the
# printed repair has to be the one a contributor on any platform can paste.
FIX_HINT = "uv run python .scripts/docs_claims.py --fix"


# ----------------------------------------------------------------- block splice


def _splice(text: str, name: str, body: list[str]) -> str:
    """Return *text* with the ``name`` block's content replaced by *body*."""
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


# ---------------------------------------------------------------- claim renderers


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """Render a GitHub markdown table, blank-line padded for MD058."""
    return [
        "",
        f"| {' | '.join(header)} |",
        f"| {' | '.join('---' for _ in header)} |",
        *(f"| {' | '.join(row)} |" for row in rows),
        "",
    ]


def _always_on_sizes(root: Path) -> list[str]:
    """Measured size of every always-on surface against its target's soft cap.

    A surface is a target output with a literal ``path``; the ``path_template``
    outputs are the path-scoped rules, which are not always-on. Characters, not
    bytes — ``cli.py`` compares ``len(content)`` on the decoded string.
    """
    rows: list[list[str]] = []
    for target_path in sorted((root / TARGETS_DIR).glob("*.yaml")):
        target = load_yaml(target_path)
        if not target.get("enabled", False):
            continue
        name = target.get("name") or target_path.stem
        cap = target.get("max_size_warning")
        if not isinstance(cap, int):
            raise ClaimError(f"{target_path}: 'max_size_warning' must be an integer")
        outputs = target.get("outputs") or {}
        for _, output in sorted(outputs.items()):
            surface = output.get("path")
            if not surface:
                continue
            chars = len(read_text(root / surface))
            rows.append([f"`{surface}` ({name})", str(chars), str(cap), str(cap - chars)])
    if not rows:
        raise ClaimError(f"{TARGETS_DIR}: no enabled target declares an always-on output")
    return _table(["Surface", "chars", "cap", "headroom"], rows)


def _catalog_skills(root: Path) -> list[str]:
    """One row per skill source, carrying the source's own routing fields.

    A user-invoked source legitimately carries no ``description`` — that absence
    *is* the mechanism that keeps it out of the model's always-loaded index
    (``skills.render_skill_md``) — so an empty cell is the correct rendering, not
    a defect to raise on.
    """
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


def _script_purpose(script: Path) -> str:
    """First line of *script*'s module docstring — the hook's one-line purpose."""
    docstring = ast.get_docstring(ast.parse(read_text(script)))
    if not docstring:
        raise ClaimError(f"{script}: hook scripts need a module docstring for the README table")
    return docstring.splitlines()[0].strip()


def _catalog_hooks(root: Path) -> list[str]:
    """One row per hook in ``hooks.yaml``, in the manifest's own order.

    Manifest order is authored and meaningful (pre-commit, then commit-msg, then
    pre-push), so it is preserved rather than sorted.
    """
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
            _script_purpose(hooks_dir / script),
        ])
    return _table(["Hook", "Stage", "Manager", "Script", "Purpose"], rows)


def _plan_current_state(root: Path) -> list[str]:
    """The plan's "current state" figures, measured instead of typed.

    Only facts that are **structural and slow-moving** belong here. A count of test
    *functions* was tried and removed: it moves on every test commit, so it rewrote
    this document from unrelated lanes for a figure the plan never reasons about.
    Only **structural** facts belong here — things that move when the code moves, so
    the block is stale exactly when the plan is. Tracker counts are deliberately
    absent even though they are equally derivable: ``.beads/issues.jsonl`` changes
    several times per session, so generating them would rewrite this document during
    unrelated lanes and dirty the base checkout a landing refuses on. ``br`` answers
    those on demand, which is why the plan now asks rather than asserts.

    The verify row is the one that proves the block is worth its weight: the count is
    per-mode, so any single hand-written "an N-check verify" is wrong for at least one
    mode. The plan claimed an 8-check ``full`` declaring nine; it is 15 declared.
    """
    # Through the engine's loader, not the raw array: a check a lane declared in its own
    # `basicly.d` fragment is as declared as one in basicly.toml, and counting only the array
    # would understate the row the moment the mechanism is used (basicly-ef7t).
    checks = config.load_verify_config(root).checks
    if not checks:
        raise ClaimError("basicly.toml: [[verify.checks]] must be a non-empty list")

    modes: dict[str, int] = {}
    for check in checks:
        for mode in check.modes:
            modes[mode] = modes.get(mode, 0) + 1

    test_files = sorted((root / "tests").glob("test_*.py"))

    rows = [
        ["Engine modules (`src/basicly/*.py`)", str(len(sorted((root / SRC_DIR).glob("*.py"))))],
        ["Test files", str(len(test_files))],
        ["`[[verify.checks]]` declared", str(len(checks))],
        *(
            [f"…of which run in `--mode {mode}`", str(count)]
            for mode, count in sorted(modes.items())
        ),
    ]
    return _table(["Measure", "Value"], rows)


# ------------------------------------------------------------------- assertions


def _cells(row: str) -> list[str]:
    r"""Cells of a markdown table row.

    Split on unescaped pipes only: a command cell spells its alternatives
    ``[--root ...\|--all-default-roots]``, and splitting on that ``\|`` cut the cell
    in half and lost every name after it.
    """
    return re.split(r"(?<!\\)\|", row)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    """The parser's subcommand action, or ``None`` for a leaf command."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _section_8(root: Path) -> str:
    """The architecture document's section 8, where every command is tabulated."""
    text = read_text(root / ARCHITECTURE_MD)
    start = text.index("## 8) CLI surface")
    end = text.find("\n## ", start)
    return text[start:end] if end != -1 else text[start:]


def _documented_commands(section: str) -> set[str]:
    """Subcommand names declared in the *first* cell of each section 8 table row.

    Only the command cell, never the behavior prose beside it: that column is full
    of incidental backticked words (``basicly.toml``, ``check``, ``build``) which
    would silently satisfy the coverage claim for a command nobody documented.

    Two spellings appear there, and both count: the leading ``basicly <name>``, and
    the bare backticked alternative a build/check pair is written with
    (``` `basicly agents-build` / `agents-check` ```).
    """
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
    """Every subcommand the CLI ships must appear in the section 8 command tables."""
    top = _subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")

    missing = sorted(set(top.choices) - _documented_commands(_section_8(root)))
    if missing:
        return [f"subcommands missing from the section 8 command tables: {', '.join(missing)}"]
    return []


def _cli_subcommands_covered(root: Path) -> list[str]:
    """Every subcommand of a command *group* must appear in that group's own rows.

    :func:`_cli_commands_covered` is satisfied by a single ``basicly worktree ...``
    row, which is how three of that group's six subcommands stayed undocumented
    while every gate passed (``basicly-tcmy.9``): the worktree row still described a
    lifecycle of create/list/cleanup long after ``merge`` and ``merge-queue``
    shipped, and a skill repeated the omission as "not yet part of `basicly
    worktree`".

    Coverage is scoped to the rows whose **command cell** names the parent, not to
    section 8 as a whole. Scanning the whole section would let an incidental ``list``
    in the ``catalog`` row satisfy ``worktree list`` — the same failure mode
    :func:`_documented_commands` avoids by reading only the command column, one level
    up. Within an owning row either column counts, because a group is documented as
    one row of prose rather than a row per subcommand.
    """
    top = _subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")
    rows = [row for row in _section_8(root).splitlines() if row.startswith("|")]

    problems: list[str] = []
    for parent, parser in sorted(top.choices.items()):
        nested = _subparsers(parser)
        if nested is None:
            continue
        owned = [
            row
            for row in rows
            if len(_cells(row)) >= 3 and re.search(rf"basicly {parent}\b", _cells(row)[1])
        ]
        if not owned:
            problems.append(f"no section 8 row documents the '{parent}' command group")
            continue
        documented = " ".join(owned)
        missing = [
            name
            for name in sorted(nested.choices)
            # A word boundary that also refuses a hyphen, so `merge` is not credited
            # to the `merge-queue` that happens to be documented beside it.
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", documented)
        ]
        if missing:
            problems.append(
                f"'{parent}' subcommands missing from its section 8 row(s): {', '.join(missing)}"
            )
    return problems


def _types_after(text: str, anchor: str) -> tuple[str, ...]:
    """Backticked names between *anchor* and the end of its sentence.

    A missing anchor is a :class:`ClaimError`, not an empty list: prose reworded past
    the anchor must fail loudly rather than silently assert nothing.
    """
    at = text.find(anchor)
    if at == -1:
        raise ClaimError(f"anchor {anchor.strip()!r} not found; the prose was reworded past it")
    span = text[at + len(anchor) :]
    if ";" in span:
        span = span[: span.index(";")]
    return tuple(sorted(re.findall(r"`([a-z]+)`", span)))


def _skill_work_types(root: Path) -> list[str]:
    """The ``tool-br`` skill's two type lists must be the engine's, not a copy.

    The skill advertised ``docs`` and ``question`` as valid types for months
    (``basicly-tcmy.9``). Both are rejected by :func:`basicly.classify`, so filing a
    docs bead produced one the loop could never advance — and ``br`` itself validates
    nothing, storing whatever ``--type`` is handed, so no tool caught it.
    """
    skill = load_yaml(root / TOOL_BR_SKILL)
    instructions = skill.get("instructions")
    if not isinstance(instructions, str):
        raise ClaimError(f"{TOOL_BR_SKILL}: 'instructions' must be a string")
    # The source is a wrapped YAML block scalar, so a sentence spans lines.
    text = " ".join(instructions.split())

    problems: list[str] = []
    for anchor, expected, source in (
        ("harness work types are ", config.WORK_TYPES, "config.WORK_TYPES"),
        ("leaf types ", loop._LEAF_TYPES, "loop._LEAF_TYPES"),
    ):
        stated = _types_after(text, anchor)
        if stated != tuple(sorted(expected)):
            problems.append(
                f"after {anchor.strip()!r} the skill states {list(stated)}; "
                f"{source} is {sorted(expected)}"
            )
    return problems


# ----------------------------------------------------------------------- claims


@dataclass(frozen=True)
class Block:
    """A doc region rendered wholly from the tree, and therefore auto-repairable."""

    name: str
    path: str
    render: Callable[[Path], list[str]]


@dataclass(frozen=True)
class Assertion:
    """A claim in authored prose that is checkable but not writable by a script."""

    name: str
    path: str
    check: Callable[[Path], list[str]]


BLOCKS: tuple[Block, ...] = (
    Block("always-on-sizes", ARCHITECTURE_MD, _always_on_sizes),
    Block("catalog-skills", SKILLS_README, _catalog_skills),
    Block("catalog-hooks", HOOKS_README, _catalog_hooks),
    Block("plan-current-state", IMPLEMENTATION_PLAN, _plan_current_state),
)

ASSERTIONS: tuple[Assertion, ...] = (
    Assertion("cli-commands", ARCHITECTURE_MD, _cli_commands_covered),
    Assertion("cli-subcommands", ARCHITECTURE_MD, _cli_subcommands_covered),
    Assertion("skill-work-types", TOOL_BR_SKILL, _skill_work_types),
)


# ------------------------------------------------------------------------- main


def _write(path: Path, text: str) -> None:
    """Write *text* back to *path*, preserving the file's existing line ending.

    Reading normalizes CRLF to LF, so writing without this would silently convert
    every line of a Windows checkout the first time one block drifted.
    """
    newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    path.write_text(text, encoding="utf-8", newline=newline)


def _run_blocks(root: Path, *, fix: bool) -> list[str]:
    """Compare (and optionally rewrite) every generated block; return failure lines."""
    failures: list[str] = []
    for block in BLOCKS:
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
    """``2 blocks current`` / ``1 block current`` — the clean-run summary phrase."""
    return f"{len(items)} {noun}{'' if len(items) == 1 else 's'} current"


def _run_assertions(root: Path) -> list[str]:
    """Evaluate every assertion; return failure lines."""
    failures: list[str] = []
    for assertion in ASSERTIONS:
        try:
            problems = assertion.check(root)
        except ClaimError as exc:
            problems = [str(exc)]
        failures.extend(f"{assertion.path} [{assertion.name}]: {problem}" for problem in problems)
    return failures


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``--check`` reports drift, ``--fix`` regenerates the stale blocks."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report drift; write nothing")
    mode.add_argument("--fix", action="store_true", help="Regenerate every stale block")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to evaluate (default: this script's repo)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    failures = _run_blocks(root, fix=args.fix) + _run_assertions(root)
    if failures:
        for failure in failures:
            print(f"docs-claims: {failure}", file=sys.stderr)
        return 1
    print(f"docs-claims: {_count(BLOCKS, 'generated block')}, {_count(ASSERTIONS, 'assertion')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
