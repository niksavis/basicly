"""The ratchet three `[[verify.checks]]` gates hold this tree to.

A ratchet is a recorded state the tree may leave in one direction only, and it is one thing
with three faces: the population it is measured over, the baseline recorded for that
population, and the departure it reports. `module-size`, `comment-density` and `noqa-debt`
each spelled all three for themselves until basicly-2j5a, so :data:`SCOPE_ROOTS` agreed
across them by luck — a fourth root added to one gate silently unscoped the other two, and
the composer divergence basicly-05g0 repaired is the defect that triplication already
produced.

**The boundary is the ratchet against the measurement.** Nothing here reads what a subject
*means*. Tokens, prose share and suppression counts are each their own gate's, and so is the
judgement of which departure is which. This module answers only: which files are in scope,
what the record says, and how a finding prints.

**A ratchet, not a hard cap.** 78 of 179 tracked modules were already over the module-size
cap when the first of these landed, and failing all of them would have meant turning the
gate off. Instead each subject's go-live number is recorded in ``[tool.<gate>.frozen]``, and
a frozen subject may only move the safe way. Three consequences, each its own finding:

* A subject the list does not name may never cross the cap. The list is closed — an entry is
  only ever removed.
* A frozen subject that went the wrong way fails, naming both numbers.
* A frozen subject that has reached the cap has graduated, and its entry must go with it.
  Leaving it would license regrowth back to the go-live number, which is the fail-open shape
  this repo keeps paying for. An entry that reaches zero is deleted, not zeroed.

**Waivers, and why they are counted.** A subject may exceed the cap deliberately by carrying
a one-line reason as a column-0 comment — ``<gate-marker>:`` followed by the reason, which
:func:`waiver_reason` reads. The count is itself ratcheted against the recorded
``waiver_count``, exactly as ``[tool.vulture]``'s suppression list is policed by
`wired_or_deleted.py`, so a waiver may be added only in a diff that moves the count. The
frozen list needs no equivalent, and the asymmetry is the point: an entry added there is a
line in ``pyproject.toml`` that a reviewer sees, while a waiver is one comment somewhere
inside a 5,000-line module that nobody would find. The reason must be non-empty and the
marker must start the line, which is what keeps a mention of it inside a string or a
docstring from waiving the file that mentions it.

A lane moves any of these numbers with a **delta** in its own ``basicly.d`` fragment rather
than by editing the shared table (basicly-ef7t); :func:`count_delta_remedy` is how a finding
says so. Stdlib plus :mod:`basicly.dropin`, because these gates run on every commit.
"""

# comment-density-waiver: this module's payload is the rationale three gates enforce, held
# once instead of three times - the 78-of-179 measurement that made these ratchets rather
# than caps, why the frozen list is reviewable and the waiver count therefore has to be
# ratcheted, and the 51.3 - 0.1 float case behind the rounding. Measured at 58.4% against
# 1264 tokens of code: reaching 50% means cutting 500 of 1772 prose tokens, and what is here
# is evidence, not narration.

from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
import tomllib
import types
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly.dropin import (  # noqa: E402 - the path above comes first
    COUNT_DELTA,
    FRAGMENT_DIR,
    MAY_ONLY_FALL,
    MAY_ONLY_TRACK,
    RATCHET_SECTION,
    FragmentError,
    compose,
)

# Every directory whose Python this repo authors. `.basicly/core` is here because the kit
# and the hooks ship to consumers and run in the dispatch path; omitting it would exempt the
# code with the widest blast radius. Tracked, from `git ls-files`, because an untracked
# scratch file is not something a gate should have an opinion about. Tests are in scope and
# are the larger half of the debt — they will not fall out of a `src/` refactor as a side
# effect.
SCOPE_ROOTS = ("src", "tests", ".scripts", ".basicly/core")

# The precision a fractional baseline is recorded and compared in. A composed share is a sum
# of one-decimal floats and binary addition does not stay on that grid: a lane cutting 0.1
# off the 51.3 frozen for `fsck.py` composes 51.199999999999996, and the module it just cut,
# measured at 51.2, would be reported as having grown. The rounding is applied whatever the
# entries are counted in and is a no-op on a whole one; `entry_type` re-applies at the call
# site because `round` widens a constrained type parameter to `float`.
_PLACES = 1


# Re-exported for the three gates, which import this module rather than reaching past it.
# Explicit because the unused-import fixer removed `MAY_ONLY_TRACK` once and broke the one
# gate that needs it.
__all__ = ["MAY_ONLY_FALL", "MAY_ONLY_TRACK"]


class RatchetError(Exception):
    """The gate could not reach an answer: no ratchet to read, or git refused the question."""


@dataclass(frozen=True)
class Ratchet[Number: (int, float)]:
    """The recorded state a change is measured against, per subject and in total.

    ``Number`` is what one entry is counted in: whole for a count of things, fractional for a
    share. :class:`~basicly.dropin.Baseline` is the arithmetic that produced it; this is that
    arithmetic after the gate has validated and rounded it.
    """

    frozen: Mapping[str, Number]
    count: int
    # Subject -> the fragment that deliberately raised its baseline, so the gate can say
    # how many there are. Empty for a gate whose entries track a measurement.
    rebaselined: Mapping[str, str] = types.MappingProxyType({})


@dataclass(frozen=True)
class Finding:
    """One way the tree disagrees with the ratchet, with the repair named.

    *subject* is whatever the ratchet records one number for — a path for the two per-module
    gates, a rule code for `noqa-debt`.
    """

    subject: str
    detail: str
    remedy: str


def frozen_table(gate: str) -> str:
    """Where *gate*'s per-subject baseline is written down."""
    return f"[tool.{gate}.frozen]"


def fragment(gate: str) -> str:
    """Where a lane declares its change to *gate*'s numbers instead of editing the table."""
    return f"[{RATCHET_SECTION}.{gate}] in {FRAGMENT_DIR}/<bead-id>.toml"


def rebaseline_clause(ratchet: Ratchet) -> str:
    """`, N rebaselined` when a fragment raised a baseline, empty when none did.

    On the pass line and not only on a failure, because the whole reason a rebaseline is a
    separate table is that it is countable: an unreported one is indistinguishable from the
    silent `frozen` raise this route replaced (basicly-e2mz.20).
    """
    return f", {len(ratchet.rebaselined)} rebaselined" if ratchet.rebaselined else ""


def count_delta_remedy(gate: str, moved: int) -> str:
    """How a finding tells a lane to record that *gate*'s tree-wide count moved by *moved*.

    Not spelled ``delta``: `[tool.vulture] ignore_names` suppresses that bare name for
    `health.py`, and vulture matches whole-tree, so a parameter of that name here would read
    as a use and quietly retire a live suppression.
    """
    return f"record `{COUNT_DELTA} = {moved:+d}` under {fragment(gate)}"


def compose_ratchet[Number: (int, float)](
    repo: Path,
    gate: str,
    *,
    count_key: str,
    entry_type: type[Number],
    may_only: str = MAY_ONLY_FALL,
) -> Ratchet[Number]:
    """*gate*'s recorded state in ``pyproject.toml``, with the ``basicly.d`` fragments applied.

    Args:
        repo: The repository root.
        gate: The gate, as ``[tool.<gate>]`` spells it.
        count_key: The key that table records the tree-wide count under.
        may_only: Which direction this gate's entries are allowed to move — see
            :data:`~basicly.dropin.MAY_ONLY_FALL`. The gate declares it because only the
            gate knows what its subject means.
        entry_type: What one frozen entry is counted in. ``float`` also widens what a
            fragment may declare per entry; ``count_delta`` counts subjects and stays whole
            either way, so a waiver can never be half taken.

    Raises:
        RatchetError: The table is absent or malformed, or a fragment declares a delta of the
            wrong kind. The gate must not pass by defaulting to an empty baseline, which
            would fail every frozen subject at once.
    """
    fractional = entry_type is float
    table = _table(repo, gate)
    frozen = table.get("frozen", {})
    count = table.get(count_key)
    permitted = int | float if fractional else int
    if not isinstance(frozen, dict) or not all(
        isinstance(value, permitted) for value in frozen.values()
    ):
        raise RatchetError(f"{frozen_table(gate)} must map each subject to its go-live number")
    if not isinstance(count, int):
        raise RatchetError(f"[tool.{gate}] must declare {count_key} as an integer")
    try:
        composed = compose(
            repo,
            gate,
            frozen={subject: entry_type(value) for subject, value in frozen.items()},
            count=count,
            fractional=fractional,
            may_only=may_only,
        )
    except FragmentError as exc:
        # Re-typed, not re-worded: a gate has one way of failing to reach an answer, and the
        # fragment's own message already names the file and the key.
        raise RatchetError(str(exc)) from exc
    return Ratchet(
        frozen={
            subject: entry_type(round(value, _PLACES)) for subject, value in composed.frozen.items()
        },
        count=composed.count,
        rebaselined=composed.rebaselined,
    )


def _table(repo: Path, gate: str) -> dict:
    """*gate*'s ``[tool.<gate>]`` table, refusing an absent one rather than defaulting."""
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(gate)
    if not isinstance(table, dict):
        raise RatchetError(f"no [tool.{gate}] in pyproject.toml")
    return table


def tracked_sources(repo: Path) -> Iterator[tuple[str, str]]:
    """Every tracked ``.py`` under :data:`SCOPE_ROOTS`, as its repo-relative path and text.

    Yields:
        Each source in ``git ls-files`` order. A tracked path with no readable file — deleted
        in the working tree, or unreadable — is skipped; a frozen entry for it is then
        reported as stale rather than silently satisfied.

    Raises:
        RatchetError: git refused to list the tree.
    """
    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "ls-files", "-z", "--", *SCOPE_ROOTS],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git exited {completed.returncode}"
        raise RatchetError(f"could not list tracked files: {detail}")
    for name in completed.stdout.split("\0"):
        if not name.endswith(".py"):
            continue
        try:
            text = (repo / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield name, text


def waiver_reason(text: str, marker: str) -> str | None:
    """The reason *text* waives *marker*'s cap with, or ``None`` if it does not waive it.

    *marker* is spelled without its colon. The pattern is built rather than held as a
    constant so that this module names no gate's marker and therefore cannot waive itself.
    """
    match = re.search(
        rf"^#[ \t]*{re.escape(marker)}:[ \t]*(\S.*?)[ \t]*$", text, flags=re.MULTILINE
    )
    return match.group(1) if match else None


def stale(gate: str, subject: str, detail: str) -> Finding:
    """A frozen entry that no longer describes anything."""
    return Finding(
        subject=subject,
        detail=detail,
        remedy=f'delete `"{subject}"` from {frozen_table(gate)}',
    )


def waiver_findings(gate: str, waived: Collection[str], recorded: int) -> list[Finding]:
    """The waiver-count ratchet, which moves only in a diff that says it moved."""
    listed_paths = sorted(waived)
    if len(listed_paths) == recorded:
        return []
    direction = "added" if len(listed_paths) > recorded else "removed"
    listed = ", ".join(listed_paths) or "none"
    return [
        Finding(
            subject="pyproject.toml",
            detail=(
                f"{len(listed_paths)} module(s) carry a waiver but waiver_count is "
                f"{recorded} — a waiver was {direction} without saying so (waived: {listed})"
            ),
            remedy=count_delta_remedy(gate, len(listed_paths) - recorded),
        )
    ]


def report(label: str, findings: Iterable[Finding]) -> None:
    """Print each finding as the disagreement, then how to repair it."""
    for finding in findings:
        print(f"{label}: {finding.subject}: {finding.detail}", file=sys.stderr)
        print(f"{label}:   {finding.remedy}", file=sys.stderr)
