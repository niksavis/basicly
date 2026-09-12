from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import decisions, loop_state
from .config import load_policy_config

DOWNSTREAM_PHASES = ("verify", "validate", "ship")

WIP_QUESTION = (
    "review and land the finished lanes, or raise [policy] max_downstream_wip: this "
    "session's unlanded work is at its limit and no further lane can start"
)


class Unit(Protocol):
    @property
    def issue_id(self) -> str: ...


@dataclass(frozen=True)
class WipAdmission[T: Unit]:
    limit: int
    downstream: tuple[str, ...]
    admitted: tuple[T, ...]
    refused: tuple[T, ...]

    @property
    def stalled(self) -> bool:
        return bool(self.refused) and not self.admitted

    @property
    def reason(self) -> str:

        return (
            f"downstream work in progress is at the [policy] max_downstream_wip limit "
            f"of {self.limit} ({len(self.downstream)} unit(s) past build)"
        )

    @property
    def coverage(self) -> str:

        parts = [f"{len(self.downstream)}/{self.limit} unlanded downstream of build"]
        if self.downstream:
            parts.append(f"waiting: {', '.join(self.downstream)}")
        parts.append(f"{len(self.admitted)} lane(s) admitted")
        if self.refused:
            parts.append(f"REFUSED: {self._held_ids()}")
        return "; ".join(parts)

    @property
    def detail(self) -> str:
        if not self.refused:
            return ""
        return (
            f"{len(self.refused)} ready lane(s) not dispatched ({self._held_ids()}): {self.reason}"
        )

    def _held_ids(self) -> str:
        return ", ".join(unit.issue_id for unit in self.refused)


def downstream_units(repo_root: Path, issue_ids: Iterable[str]) -> tuple[str, ...]:

    return tuple(
        issue_id
        for issue_id in issue_ids
        if loop_state.read_node_state(repo_root, issue_id).phase in DOWNSTREAM_PHASES
    )


def admit[T: Unit](
    repo_root: Path, ready: Sequence[T], parked: Iterable[T], *, exclude: str = ""
) -> WipAdmission[T]:

    limit = load_policy_config(repo_root).max_downstream_wip
    downstream = downstream_units(
        repo_root, (unit.issue_id for unit in parked if unit.issue_id != exclude)
    )
    at_limit = len(downstream) >= limit
    return WipAdmission(
        limit=limit,
        downstream=downstream,
        admitted=() if at_limit else tuple(ready),
        refused=tuple(ready) if at_limit else (),
    )


def record_refusal[T: Unit](
    repo_root: Path, root_issue: str, admission: WipAdmission[T]
) -> decisions.DecisionItem | None:

    if not admission.stalled:
        return None
    return decisions.enqueue(repo_root, root_issue, "escalation", WIP_QUESTION, admission.detail)
