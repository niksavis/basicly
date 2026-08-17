"""Fail when an epic states a problem a machine reads as fact and no child accounts for.

An epic's problem statement is the decider's intake corpus, so a bullet the epic's own
closed children already fixed is read as current by an agent that has no other authority
(basicly-b9ef). :mod:`basicly.corpus_drift` holds the rule and the measurement behind it —
attribution is by *named child*, never by resemblance; this is the human-runnable half.

Scope is the **committed tracker**, the owned event log, so the gate runs in a fresh
clone with no tracker binary and reports what a reviewer can see in the same diff as the
correction. It reads open parents only: a closed bead's statement is history and nothing
dispatches a decider on it.

**A ratchet, not a hard gate.** One bead was already unaccounted for when this landed
(``basicly-u2hl``, four bullets), and its correction is a tracker write, which does not
belong in a lane's worktree. So the go-live debt is recorded per bead in
``[tool.corpus_drift.frozen]`` and may only fall — a bead not in that closed list may not
have a single unaccounted bullet, and a bead whose count drops has to bank it in the same
diff, because leaving the higher number licenses regrowth back to it.

The corpus annotation is *not* ratcheted: a frozen bullet still reaches a decider marked
unverified. The baseline says which debt blocks a commit, never which claim is a fact.

Run over every open parent, or over named ones::

    uv run python .scripts/check_corpus_drift.py
    uv run python .scripts/check_corpus_drift.py basicly-u2hl
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly import config, corpus_drift, tracker  # noqa: E402 - reachable after the path insert

_LABEL = "corpus-drift"
FROZEN_TABLE = "[tool.corpus_drift.frozen]"
# Enough of a bullet to recognise it in the bead; the fix is made in the bead, not here.
_BULLET_WIDTH = 96
_NAMED_CHILDREN = 6


class RatchetError(RuntimeError):
    """The recorded baseline is missing or malformed."""


@dataclass(frozen=True)
class Verdict:
    """One bead's standing against its baseline, and what has to happen about it."""

    issue_id: str
    detail: str
    remedy: str


def load_frozen(repo: Path) -> dict[str, int]:
    """The recorded per-bead debt from ``pyproject.toml``.

    Raises:
        RatchetError: The table is absent or malformed — defaulting to an empty
            baseline would fail every recorded bead at once, and defaulting to a
            permissive one would pass everything, which is worse.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get("corpus_drift")
    if not isinstance(table, dict) or not isinstance(table.get("frozen"), dict):
        raise RatchetError(f"no {FROZEN_TABLE} in pyproject.toml")
    frozen = table["frozen"]
    if not all(isinstance(value, int) for value in frozen.values()):
        raise RatchetError(f"{FROZEN_TABLE} must map each bead id to its go-live count")
    return frozen


def findings(repo_root: Path, wanted: tuple[str, ...] = ()) -> tuple[corpus_drift.Finding, ...]:
    """Every unaccounted problem bullet in *repo_root*'s open parents.

    *wanted* narrows to named ids so an author can check one bead.
    """
    # Importing `config` is what installs the tracker mode reader `owned_store` refuses
    # to answer without, and naming it here is what keeps that import from reading as
    # unused. A script reaching the engine outside the CLI is exactly the caller that
    # used to file its work against the wrong store (`owned_store.set_mode_reader`).
    config.load_tracker_mode(repo_root)
    records = tracker.all_records(repo_root)
    children = corpus_drift.children_by_parent(records)
    found: list[corpus_drift.Finding] = []
    for record in records:
        issue_id = str(record.get("id") or "")
        closed = record.get("status") == corpus_drift.CLOSED_STATUS
        if closed or (wanted and issue_id not in wanted):
            continue
        found.extend(
            corpus_drift.epic_findings(
                issue_id, str(record.get("description") or ""), children.get(issue_id, {})
            )
        )
    return tuple(found)


def verdicts(found: tuple[corpus_drift.Finding, ...], frozen: dict[str, int]) -> list[Verdict]:
    """The ratchet's reading of *found*: what grew, what appeared, and what graduated."""
    counts = dict.fromkeys(frozen, 0)
    for finding in found:
        counts[finding.issue_id] = counts.get(finding.issue_id, 0) + 1
    verdict: list[Verdict] = []
    for issue_id, count in sorted(counts.items()):
        baseline = frozen.get(issue_id)
        if baseline is None:
            verdict.append(
                Verdict(
                    issue_id,
                    f"{count} problem bullet(s) name no child of this bead",
                    "correct each superseded bullet in place naming the child that superseded "
                    f"it, or mark it {corpus_drift.UNVERIFIED} — the decider's authority is "
                    "this text",
                )
            )
        elif count > baseline:
            verdict.append(
                Verdict(
                    issue_id,
                    f"{count} unaccounted bullet(s), up from the frozen {baseline}",
                    f"account for the new bullet(s); {FROZEN_TABLE} may only fall",
                )
            )
        elif count < baseline:
            verdict.append(
                Verdict(
                    issue_id,
                    f"{count} unaccounted bullet(s), down from the frozen {baseline}",
                    f'bank it: set `"{issue_id}" = {count}` in {FROZEN_TABLE}, or delete the '
                    "entry at zero",
                )
            )
    return verdict


def report(found: tuple[corpus_drift.Finding, ...], verdict: list[Verdict]) -> str:
    """The failing beads, each with its bullets and what its own statement does account for."""
    lines: list[str] = []
    for entry in verdict:
        group = [finding for finding in found if finding.issue_id == entry.issue_id]
        lines.append(f"{entry.issue_id}: {entry.detail}")
        if group:
            named = ", ".join(group[0].accounted_children[:_NAMED_CHILDREN]) or "no child"
            lines.append(
                f"  {len(group[0].closed_children)} of its children have closed; "
                f"the statement accounts for: {named}"
            )
            lines += [f"  - {finding.bullet[:_BULLET_WIDTH]}" for finding in group]
        lines.append(f"  fix: {entry.remedy}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point: report every problem bullet a decider would read as current fact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", nargs="*", help="Only check these parent ids")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the recorded baseline: fail on every unaccounted bullet, including "
        "the go-live debt. What an author runs to see what a decider is still being told.",
    )
    args = parser.parse_args(argv)
    wanted = tuple(args.issue)
    try:
        frozen = {} if args.strict else load_frozen(REPO_ROOT)
    except RatchetError as exc:
        print(f"[{_LABEL}] {exc}", file=sys.stderr)
        return 2
    if wanted:
        frozen = {issue_id: count for issue_id, count in frozen.items() if issue_id in wanted}
    found = findings(REPO_ROOT, wanted)
    verdict = verdicts(found, frozen)
    if not verdict:
        print(
            f"[{_LABEL}] {len(found)} recorded unaccounted bullet(s); no bead is above "
            f"its {FROZEN_TABLE} baseline"
        )
        return 0
    print(f"[{_LABEL}] {len(verdict)} bead(s) off their recorded problem-statement debt")
    print(report(found, verdict))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
