"""A lane's token spend split into context acquisition and implementation.

The instrument `basicly-ejdm`'s causal claim never had: `ejdm.3` is the remedy and `.4`
the measurement, and only `.4` is a claim.

**The pairing rule.** A ``tool_use`` turn's usage is the cost of *emitting* the call; the
tool's result lands in the **next turn that carries usage**. So a turn's tokens are
attributed to the last tools emitted before it, and a turn with none is unattributed rather
than guessed at. Two things this must not do: sum tokens on the turns that *called* the
tools, which measures the request and misses the answer; or pair against the immediately
preceding *line*, because a forwarded ``user`` event carrying no usage sits between the
call and its answer and would break every chain in a real transcript.

**Shares, never absolutes.** Per-turn stream usage over-reports against the run record by
1.46x to 1.79x [M 2026-08-13, four lanes], so a stream-derived token figure is in a
different denomination from the grant it would be compared against.

**Claude only.** Codex emits no per-tool event this stack parses, which matches a ledger
holding only claude and manual dispatches — stated rather than implied.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from basicly.copilot_tools import WRITE_TOOLS
from basicly.lane_log import LANE_LOGS_DIR

# `WRITE_TOOLS` is the other half, imported rather than respelled so a name added there
# extends this classification instead of leaving a hole only a reader would notice.
READ_TOOLS = frozenset({"Read", "NotebookRead", "Grep", "Glob", "WebFetch", "WebSearch"})

ACQUISITION = "acquisition"
IMPLEMENTATION = "implementation"
# Neither, and a class rather than a default: `Bash` runs `git status` and `mv` alike, so
# bucketing it would put a guess inside the number the remedy is judged by.
UNCLASSIFIED = "unclassified"
# A turn with no tool-emitting predecessor: ordinary generation, attributable to neither.
UNATTRIBUTED = "unattributed"

# Absent is unknown and `[]` is "called nothing"; collapsing them would report a lane
# written before `basicly-ejdm.1` as fully implementation.
TOOLS_KEY = "tools"


def classify(tools: Iterable[str]) -> str:
    """The class one turn's emitted tool names fall in.

    Mixed classes resolve to :data:`UNCLASSIFIED` rather than to a majority, which would
    invent the very number this module exists to measure.
    """
    classes = {
        ACQUISITION if name in READ_TOOLS else IMPLEMENTATION if name in WRITE_TOOLS else ""
        for name in tools
    }
    if not classes:
        return UNATTRIBUTED
    if classes in ({ACQUISITION}, {IMPLEMENTATION}):
        return classes.pop()
    return UNCLASSIFIED


@dataclass(frozen=True)
class LaneSplit:
    """One lane's split, or the reason it has none.

    Attributes:
        issue: The bead the lane built.
        tokens: Stream-denominated tokens by class. Empty when unclassifiable.
        unclassifiable: Why no split could be derived, or "" when one was.
    """

    issue: str
    tokens: dict[str, int]
    unclassifiable: str = ""

    @property
    def total(self) -> int:
        """Every token the transcript accounted for, in the stream's own denomination."""
        return sum(self.tokens.values())

    def share(self, name: str) -> float:
        """*name*'s share of the total, as a fraction. Zero when nothing was counted."""
        return self.tokens.get(name, 0) / self.total if self.total else 0.0


def split_events(events: Sequence[dict]) -> LaneSplit | str:
    """The split over one transcript's parsed lines, or the reason there is none.

    The reason is a plain string, so an absent split cannot read as an empty one.
    """
    if not events:
        return "the transcript is empty"
    if any(TOOLS_KEY not in event for event in events):
        return "written before the tool name field existed, so no turn can be classified"
    tokens: dict[str, int] = {}
    pending: list[str] = []
    for event in events:
        spent = int(event.get("tokens") or 0)
        if spent:
            name = classify(pending)
            tokens[name] = tokens.get(name, 0) + spent
            pending = []
        called: list[str] = [str(name) for name in event.get(TOOLS_KEY) or ()]
        if called:
            pending = called
    return LaneSplit("", tokens)


def read_transcript(path: Path) -> list[dict]:
    """One transcript's lines as objects, skipping any the writer tore on exit."""
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def lane_splits(repo_root: Path) -> list[LaneSplit]:
    """Every persisted lane transcript's split, by session then issue."""
    root = Path(repo_root) / LANE_LOGS_DIR
    found: list[LaneSplit] = []
    for path in sorted(root.glob("*/*.jsonl")) if root.is_dir() else []:
        outcome = split_events(read_transcript(path))
        if isinstance(outcome, str):
            found.append(LaneSplit(path.stem, {}, outcome))
        else:
            found.append(LaneSplit(path.stem, outcome.tokens))
    return found
