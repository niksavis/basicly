"""The architecture layering section, rendered from the import contract (basicly-h7bknm).

Section 34 of the architecture document states how many tiers the engine has, how many
modules they hold, and how those tiers group into bands. **Nothing read any of it.** No
script, no test; `docs-claims` asserted CLI coverage only, and `code-citations` checks that
a citation reaches a heading, not that a number inside a document matches a config file.
Measured 2026-08-20, the document said 36 tiers where `.importlinter` had 37, and its band
labels summed to 98 modules where the contract had 102. Correcting the numbers is the repair
that is wrong again on the next tier, so they are derived here instead.

**What is derivable and what had to be declared.** The tier stack and its module sets are the
contract: `.importlinter` is the single source and this module parses it. The *bands* are not
in the contract at all - nine bands over 38 tiers is an editorial reading, and the previous
lane left two band figures alone for exactly that reason. So each band's boundary and its
example modules are declared in :data:`BANDS` and the counts are derived against them. A
declared boundary that no longer names a module, a band whose examples have moved, or a tier
that falls outside every band all raise rather than render: the numbers are bound, and the
grouping is a claim that fails loudly when the contract moves under it.

The dashed self-edges are derived too, from the contract's own ``ignore_imports``, so a cycle
removed from the contract cannot leave an exemption drawn in the diagram behind it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import takewhile
from textwrap import fill
from typing import TYPE_CHECKING

from docs_claim_sources import ClaimError, read_text

if TYPE_CHECKING:
    from pathlib import Path

IMPORT_CONTRACT = ".importlinter"

# The contract section whose `layers =` list is the engine's tier stack. The file holds more
# than one layers contract, so the section name is what picks this one out.
_ENGINE_CONTRACT = "[importlinter:contract:engine-layering]"
_SIBLING = "|"
# Both keys carry the equals sign, which is not cosmetic: the contract's own comment names
# `unmatched_ignore_imports_alerting`, so splitting on the bare key lands inside that prose
# and reads an empty exemption list - the fail-open shape, since an empty list renders a
# diagram with no exemptions drawn on it and nothing disagrees.
# The width the surrounding document is authored at. Wrapped here rather than written as
# fixed lines because a count that gains a digit would otherwise push one line long.
_PROSE_WIDTH = 95
_LAYERS_KEY = "layers ="
_IGNORE_KEY = "ignore_imports ="
# The diagram's own separators, as section 34 already writes them: a middle dot between a
# band's number, its name and its example modules, and an em dash before the count.
_EXAMPLE_SEP = " \N{MIDDLE DOT} "
_DASH = "\N{EM DASH}"
_IGNORED = re.compile(r"^\s+basicly\.(\w+)\s*->\s*basicly\.(\w+)\s*$")


@dataclass(frozen=True)
class Band:
    """One editorial grouping of consecutive tiers, and how it is held to the contract.

    *ends_at* is a module in the band's **last** tier: bands are contiguous runs of the tier
    stack, so naming one module per boundary partitions it. *examples* are the modules the
    diagram names as the band's recognisable members, and each is checked to sit in the band
    that names it - which is what catches a module moving bands rather than merely appearing.
    """

    name: str
    ends_at: str
    examples: tuple[str, ...]


# Read off section 34's diagram as it stood before this block existed, boundary by boundary.
# Each `ends_at` was recovered by fitting the diagram's own counts and example modules to the
# contract's tier order, which the fit determines uniquely: eight bands landed on a tier
# boundary exactly, and the ninth is the remainder between its neighbours.
BANDS: tuple[Band, ...] = (
    Band("entry", "cli", ("cli",)),
    Band("drivers", "release", ("supervise", "loop", "release", "usage_report")),
    Band(
        "loop mechanics",
        "capability_proof",
        ("merge", "decompose", "policy", "verify", "board_snapshot", "decisions", "plan_gate"),
    ),
    Band("configuration and isolation", "worktree", ("config", "worktree")),
    Band(
        "agent runtime",
        "context_window",
        ("runner", "lane_log", "lane_split", "context_window", "claude_settings"),
    ),
    Band(
        "projection",
        "state",
        ("loader", "planner", "renderers", "skills", "agents", "hooks", "permissions"),
    ),
    Band(
        "records and telemetry",
        "label_source",
        ("run_record", "artifact_record", "lens_review", "spend_calibration"),
    ),
    Band(
        "tracker seam",
        "board_schema",
        ("owned_store", "mirror", "dispatch_phase", "board_schema", "board_fields"),
    ),
    Band(
        "leaf data and pure helpers",
        "stemmer",
        ("integrity", "schema", "redact", "roles", "read_cost", "ui", "stemmer"),
    ),
)


def _engine_section(root: Path) -> str:
    """The engine-layering contract's own text, from the file to the next contract.

    Raises:
        ClaimError: The contract file or that section is absent - a missing source is an
            unevaluable claim, not a claim that happens to hold.
    """
    text = read_text(root / IMPORT_CONTRACT)
    if _ENGINE_CONTRACT not in text:
        raise ClaimError(f"{IMPORT_CONTRACT} declares no {_ENGINE_CONTRACT}")
    return text.split(_ENGINE_CONTRACT, 1)[1].split("\n[", 1)[0]


def _entries(section: str, key: str) -> list[str]:
    """*key*'s indented continuation lines in *section*, comments and blanks dropped.

    Two subtleties, both of which read as a working parse. The value line itself is skipped:
    `layers =` carries nothing after the equals sign, so a reader starting at the split point
    sees an unindented empty string and reports the contract as empty. And the block *ends*
    at the first unindented line rather than merely skipping it - filtering instead of
    stopping collects the section's later `ignore_imports` entries as tiers.
    """
    if key not in section:
        return []
    lines = section.split(key, 1)[1].splitlines()[1:]
    return [
        entry
        for line in takewhile(lambda line: line.startswith((" ", "\t")), lines)
        for entry in [line.strip()]
        if entry and not entry.startswith("#")
    ]


def tiers(root: Path) -> list[tuple[str, ...]]:
    """The engine contract's tier stack, highest first, each tier as its sibling set.

    Raises:
        ClaimError: The contract, the section or its ``layers =`` list is absent.
    """
    section = _engine_section(root)
    stack = [
        tuple(name.strip() for name in entry.split(_SIBLING))
        for entry in _entries(section, _LAYERS_KEY)
    ]
    if not stack:
        raise ClaimError(f"{_ENGINE_CONTRACT} declares an empty layers list")
    return stack


def exemptions(root: Path) -> list[tuple[str, str]]:
    """The contract's declared cycle exemptions, as (importer, imported) module pairs."""
    return [
        (match.group(1), match.group(2))
        for entry in _entries(_engine_section(root), _IGNORE_KEY)
        if (match := _IGNORED.match(f" {entry}"))
    ]


def grouped(stack: list[tuple[str, ...]]) -> list[tuple[Band, list[tuple[str, ...]]]]:
    """Partition *stack* into the declared bands, refusing any reading that does not fit.

    Raises:
        ClaimError: A boundary module is not in the contract below the previous boundary, the
            declared order disagrees with the contract's, a band ends up empty, or a tier
            falls outside every band. Each is the grouping going stale under a contract edit,
            and none may render a number: a band count nobody can derive is the whole defect.
    """
    out: list[tuple[Band, list[tuple[str, ...]]]] = []
    at = 0
    for band in BANDS:
        ends = next(
            (index for index in range(at, len(stack)) if band.ends_at in stack[index]), None
        )
        if ends is None:
            raise ClaimError(
                f"band '{band.name}' ends at `{band.ends_at}`, which no tier at or below "
                f"the previous band declares in {IMPORT_CONTRACT}"
            )
        out.append((band, stack[at : ends + 1]))
        at = ends + 1
    if at != len(stack):
        loose = [name for tier in stack[at:] for name in tier]
        raise ClaimError(
            f"{len(stack) - at} tier(s) below the last band belong to no band: "
            f"{', '.join(loose)} - extend BANDS or move the boundary"
        )
    for band, held in out:
        members = {name for tier in held for name in tier}
        if stray := [name for name in band.examples if name not in members]:
            raise ClaimError(
                f"band '{band.name}' names {', '.join(stray)} as its example(s), which "
                f"{IMPORT_CONTRACT} no longer places in it"
            )
    return out


def _band_of(name: str, groups: list[tuple[Band, list[tuple[str, ...]]]]) -> int | None:
    """The 1-based band number holding module *name*, or ``None``."""
    return next(
        (
            number
            for number, (_, held) in enumerate(groups, 1)
            if any(name in tier for tier in held)
        ),
        None,
    )


def _label(number: int, band: Band, held: list[tuple[str, ...]]) -> str:
    """One mermaid node: the band's number, name, module count and example members.

    The unit is spelled only where the count is one, which is where a bare numeral reads as
    an index rather than a total.
    """
    count = sum(len(tier) for tier in held)
    total = "1 module" if count == 1 else str(count)
    joined = _EXAMPLE_SEP.join(band.examples)
    return f'  b{number}["{number}{_EXAMPLE_SEP}{band.name} {_DASH} {total}<br/>{joined}"]'


def render_layering_contract(root: Path) -> list[str]:
    """Section 34's tier and band figures, derived from the import contract.

    Everything here is a count over `.importlinter` or a name :data:`BANDS` declares. The
    band *boundaries* are the declared half and the counts are the derived half, so a tier
    added to the contract moves a number in this block and the gate refuses until it is
    regenerated.
    """
    stack = tiers(root)
    groups = grouped(stack)
    modules = sum(len(tier) for tier in stack)
    chain = " --> ".join(f"b{number}" for number in range(1, len(groups) + 1))
    edges = []
    for importer, imported in exemptions(root):
        source, target = _band_of(importer, groups), _band_of(imported, groups)
        if source is None or target is None:
            raise ClaimError(
                f"the exemption `{importer} -> {imported}` names a module no band holds"
            )
        edges.append(
            f'  b{source} -.->|"declared exemption:<br/>{importer} imports {imported}"| b{target}'
        )
    named = ",".join(f"b{number}" for number in range(1, len(groups) + 1))
    return [
        "",
        *fill(
            f"The {len(stack)} tiers hold {modules} modules and group into {len(groups)} "
            "bands. Every band may import every band below it, and nothing above it. Every "
            "count here is derived from `.importlinter`. The band *boundaries* are not: "
            f"{len(groups)} bands over the tier stack is an editorial reading the contract "
            "does not carry, so they are declared in `.scripts/docs_claim_layers.py` and "
            "the counts are derived against them.",
            width=_PROSE_WIDTH,
        ).splitlines(),
        "",
        "```mermaid",
        "flowchart TB",
        *(_label(number, band, held) for number, (band, held) in enumerate(groups, 1)),
        "",
        f"  {chain}",
        *edges,
        "",
        "  classDef shipped fill:#d5efd5,stroke:#2e7d32,color:#000",
        f"  class {named} shipped",
        "```",
        "",
    ]
