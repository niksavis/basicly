from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from basicly.copilot_tools import WRITE_TOOLS
from basicly.lane_log import LANE_LOGS_DIR

READ_TOOLS = frozenset({"Read", "NotebookRead", "Grep", "Glob", "WebFetch", "WebSearch"})

ACQUISITION = "acquisition"
IMPLEMENTATION = "implementation"
UNCLASSIFIED = "unclassified"
UNATTRIBUTED = "unattributed"

TOOLS_KEY = "tools"


def classify(tools: Iterable[str]) -> str:

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
    issue: str
    tokens: dict[str, int]
    unclassifiable: str = ""

    @property
    def total(self) -> int:
        return sum(self.tokens.values())

    def share(self, name: str) -> float:
        return self.tokens.get(name, 0) / self.total if self.total else 0.0


def split_events(events: Sequence[dict]) -> LaneSplit | str:

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
    root = Path(repo_root) / LANE_LOGS_DIR
    found: list[LaneSplit] = []
    for path in sorted(root.glob("*/*.jsonl")) if root.is_dir() else []:
        outcome = split_events(read_transcript(path))
        if isinstance(outcome, str):
            found.append(LaneSplit(path.stem, {}, outcome))
        else:
            found.append(LaneSplit(path.stem, outcome.tokens))
    return found
