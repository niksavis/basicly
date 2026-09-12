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

_ENGINE_CONTRACT = "[importlinter:contract:engine-layering]"
_SIBLING = "|"
_PROSE_WIDTH = 95
_LAYERS_KEY = "layers ="
_IGNORE_KEY = "ignore_imports ="
_EXAMPLE_SEP = " \N{MIDDLE DOT} "
_DASH = "\N{EM DASH}"
_IGNORED = re.compile(r"^\s+basicly\.(\w+)\s*->\s*basicly\.(\w+)\s*$")


@dataclass(frozen=True)
class Band:
    name: str
    ends_at: str
    examples: tuple[str, ...]


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

    text = read_text(root / IMPORT_CONTRACT)
    if _ENGINE_CONTRACT not in text:
        raise ClaimError(f"{IMPORT_CONTRACT} declares no {_ENGINE_CONTRACT}")
    return text.split(_ENGINE_CONTRACT, 1)[1].split("\n[", 1)[0]


def _entries(section: str, key: str) -> list[str]:

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

    section = _engine_section(root)
    stack = [
        tuple(name.strip() for name in entry.split(_SIBLING))
        for entry in _entries(section, _LAYERS_KEY)
    ]
    if not stack:
        raise ClaimError(f"{_ENGINE_CONTRACT} declares an empty layers list")
    return stack


def exemptions(root: Path) -> list[tuple[str, str]]:
    return [
        (match.group(1), match.group(2))
        for entry in _entries(_engine_section(root), _IGNORE_KEY)
        if (match := _IGNORED.match(f" {entry}"))
    ]


def grouped(stack: list[tuple[str, ...]]) -> list[tuple[Band, list[tuple[str, ...]]]]:

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
    return next(
        (
            number
            for number, (_, held) in enumerate(groups, 1)
            if any(name in tier for tier in held)
        ),
        None,
    )


def _label(number: int, band: Band, held: list[tuple[str, ...]]) -> str:

    count = sum(len(tier) for tier in held)
    total = "1 module" if count == 1 else str(count)
    joined = _EXAMPLE_SEP.join(band.examples)
    return f'  b{number}["{number}{_EXAMPLE_SEP}{band.name} {_DASH} {total}<br/>{joined}"]'


def render_layering_contract(root: Path) -> list[str]:

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
