"""Whether the next work is parallel or a queue, off the edges the document already carries.

Owner, 2026-09-05, in one breath: *"even a graph on the page is a nice touch"* and *"is the
work parallel or sequential"*. The board could not answer the second, and the data for it had
been riding on every snapshot: `graph.edges`, drawn as **one number in the footer** -
`DEP EDGES 846` - and nowhere else (basicly-pck9fx).

`board_footer`'s own docstring already stated the intent that number falls short of: edges are
*"the answer to why an item is not ready - the one question a count of blocked items raises
and cannot settle"*. The page printed `BLOCKED 56` beside `DEP EDGES 846` and settled nothing.

**Not the whole graph.** 846 edges over 293 active records is not a picture at six metres.
Three questions are worth the space, and each is one line:

* **parallel or a queue** - how many records have nothing behind them against how many wait;
* **what unblocks the most** - the second term the scheduler already ranks on and the page
  never showed;
* **the longest chain** - because a depth of four is a fortnight of sequence and a depth of
  one is an afternoon.

On this repository the answer is `261 need nothing, 24 wait on one, 8 wait on a chain`: the
work is overwhelmingly parallel and the queue is a short tail. That is a different plan from
the one a reader would make off `BLOCKED 56`.

**Direction is measured, not assumed.** For a `blocks` edge ``from`` is the blocked record and
``to`` is the blocker - checked against `basicly tracker blocked`, which reports
`basicly-4t9z blocked by basicly-7bur` for the edge `{from: basicly-4t9z, to: basicly-7bur}`.
It is the same convention `parent-child` uses, where the child declares the edge onto its
parent. Inverting it would report every blocker as blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .board_wall import ABSENT, BY_KEY, RENDERABLE, Bar, bar, clip, more, number

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .board_wall import Reading, State

# The edge kind that holds work back. `parent-child` is the feature tree and is already drawn
# as the ready list's group headings; `related` and `discovered-from` bind nothing.
BLOCKS = "blocks"

# The bands, in order, with the depth each admits. Three and not five: a reader asking
# "parallel or sequential" is separating none from one from more, and a histogram of exact
# depths spends five rows to say what these three say.
BANDS: tuple[tuple[str, str], ...] = (
    ("needs nothing", "nothing behind it"),
    ("waits on one", "one thing behind it"),
    ("waits on a chain", "two or more, in sequence"),
)

# How many blockers are named, and how long a chain is drawn. Both bounded and both reported:
# an unbounded row on a wall is the appended panel again.
BLOCKER_SLOTS = 3
CHAIN_SLOTS = 6

# Where "waits on a chain" begins. Named rather than inline, because it is the band's own
# boundary and a reader of the table above has to be able to find it.
CHAIN_DEPTH = 2

ID_MAX = 24


@dataclass(frozen=True)
class Depth:
    """One band of the frontier: what it is called, how many sit in it, and its share."""

    label: str
    detail: str
    count: int
    share: Bar | None


@dataclass(frozen=True)
class Blocker:
    """A record other work waits on, and how much of it."""

    ident: str
    blocking: int


@dataclass(frozen=True)
class Queue:
    """The shape of what is waiting: the frontier, the top blockers, the longest chain."""

    state: State
    bands: tuple[Depth, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    dropped: str = ""
    chain: tuple[str, ...] = ()
    note: str = ""


def _blockers(edges: Sequence[Mapping[str, Any]], live: frozenset[str]) -> dict[str, set[str]]:
    """What each record waits on, keeping only blockers the document still lists.

    A closed blocker holds nothing, and `units[]` is the active population - so an edge onto
    a record that is not there is a debt already paid. Counting it would report a frontier
    narrower than the one a person can actually start work on.
    """
    held: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("kind") != BLOCKS:
            continue
        blocked, blocker = str(edge.get("from") or ""), str(edge.get("to") or "")
        if blocked and blocker in live:
            held.setdefault(blocked, set()).add(blocker)
    return held


def _depth(node: str, blockers: Mapping[str, set[str]], path: frozenset[str]) -> int:
    """How deep the chain behind *node* runs, cutting any cycle at *path*.

    **The guard has to be on the recursion, not on a walk that calls it.** The first version
    of this module iterated the chain with a `seen` set and chose each branch by recursing
    with a *fresh* one, so `x` blocked by `y` blocked by `x` recursed until the interpreter
    stopped it - and a `RecursionError` in a consumer means the page does not render at all.
    The edge set is a producer's and this consumer may not assume it is acyclic.
    """
    if node in path or node not in blockers:
        return 0
    ahead = path | {node}
    return 1 + max(_depth(name, blockers, ahead) for name in blockers[node])


def _chain(start: str, blockers: Mapping[str, set[str]]) -> list[str]:
    """The longest chain of blockers from *start*, cycle-safe."""
    walk = [start]
    seen = {start}
    node = start
    while node in blockers:
        ahead = sorted(blockers[node] - seen)
        if not ahead:
            break
        # The branch that leads furthest, so the line drawn is the worst case and not the
        # first one found - a shorter branch would understate the sequence. The accumulated
        # `seen` is handed down as the path, which is what cuts a cycle.
        node = max(ahead, key=lambda name: _depth(name, blockers, frozenset(seen)))
        walk.append(node)
        seen.add(node)
    return walk


def queue(reads: Mapping[str, Reading]) -> Queue:
    """The frontier, the blockers and the longest chain, or the reason there are none."""
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
