from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .board_wall import ABSENT, BY_KEY, RENDERABLE, Bar, bar, clip, more, number

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .board_wall import Reading, State

BLOCKS = "blocks"

BANDS: tuple[tuple[str, str], ...] = (
    ("needs nothing", "nothing behind it"),
    ("waits on one", "one thing behind it"),
    ("waits on a chain", "two or more, in sequence"),
)

BLOCKER_SLOTS = 3
CHAIN_SLOTS = 6

CHAIN_DEPTH = 2

ID_MAX = 24

SHAPE_ELEMENTS: tuple[str, ...] = ("chain",)


@dataclass(frozen=True)
class Depth:
    label: str
    detail: str
    count: int
    share: Bar | None


@dataclass(frozen=True)
class Blocker:
    ident: str
    blocking: int


@dataclass(frozen=True)
class Queue:
    state: State
    bands: tuple[Depth, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    dropped: str = ""
    chain: tuple[str, ...] = ()
    note: str = ""


def _blockers(edges: Sequence[Mapping[str, Any]], live: frozenset[str]) -> dict[str, set[str]]:

    held: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("kind") != BLOCKS:
            continue
        blocked, blocker = str(edge.get("from") or ""), str(edge.get("to") or "")
        if blocked and blocker in live:
            held.setdefault(blocked, set()).add(blocker)
    return held


def _depth(node: str, blockers: Mapping[str, set[str]], path: frozenset[str]) -> int:

    if node in path or node not in blockers:
        return 0
    ahead = path | {node}
    return 1 + max(_depth(name, blockers, ahead) for name in blockers[node])


def _chain(start: str, blockers: Mapping[str, set[str]]) -> list[str]:
    walk = [start]
    seen = {start}
    node = start
    while node in blockers:
        ahead = sorted(blockers[node] - seen)
        if not ahead:
            break
        node = max(ahead, key=lambda name: _depth(name, blockers, frozenset(seen)))
        walk.append(node)
        seen.add(node)
    return walk


def queue(reads: Mapping[str, Reading]) -> Queue:
    graph, units = reads["graph"], reads["units"]
    if not graph.drawn or not units.drawn:
        absent = graph if not graph.drawn else units
        return Queue(BY_KEY[ABSENT], note=f"{absent.name} {absent.note}")
    live = frozenset(str(row.get("id")) for row in units.dicts if row.get("id"))
    edges = graph.held.get("edges") if isinstance(graph.held, dict) else None
    blockers = _blockers(edges if isinstance(edges, list) else [], live)
    if not blockers:
        settled = f"nothing waits on anything, over {number(len(live))} records"
        return Queue(BY_KEY[RENDERABLE], note=settled)

    depths = {name: _depth(name, blockers, frozenset()) for name in live}
    counts = (
        sum(1 for depth in depths.values() if depth == 0),
        sum(1 for depth in depths.values() if depth == 1),
        sum(1 for depth in depths.values() if depth >= CHAIN_DEPTH),
    )
    whole = sum(counts)
    bands = tuple(
        Depth(label, detail, count, bar(count, whole))
        for (label, detail), count in zip(BANDS, counts, strict=True)
    )

    holding: dict[str, int] = {}
    for waited_on in blockers.values():
        for blocker in waited_on:
            holding[blocker] = holding.get(blocker, 0) + 1
    ranked = sorted(holding.items(), key=lambda pair: (-pair[1], pair[0]))
    worst = max(live, key=lambda name: depths[name])
    deepest = _chain(worst, blockers)
    return Queue(
        BY_KEY[RENDERABLE],
        bands=bands,
        blockers=tuple(
            Blocker(clip(name, ID_MAX), count) for name, count in ranked[:BLOCKER_SLOTS]
        ),
        dropped=more(len(ranked) - BLOCKER_SLOTS, "blockers"),
        chain=tuple(clip(name, ID_MAX) for name in deepest[:CHAIN_SLOTS]),
        note=(
            f"{number(len(blockers))} of {number(len(live))} wait on something"
            f"{f'; the chain runs {depths[worst]} deep' if depths[worst] else ''}"
        ),
    )
