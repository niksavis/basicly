"""Fail when a closed record produced no release note and nothing else notices.

`changelog.d` refuses a fragment that is empty or misnamed, so every check it has binds on
**a fragment that exists**. Nothing bound on a closed record that produced none, and
absence is the one shape a presence check cannot see. Measured at the v0.9.0 cut: 54
records closed since v0.8.0, 35 with a fragment, 19 without — `basicly-4kdm` among them,
the seven specialist agents and five loop skills that `basicly install` vendors to every
consumer, and the largest thing in that release. Eight fragments were written by hand at
cut time; that was mopping.

It is unrecoverable rather than untidy. `.github/workflows/release.yml` extracts
`CHANGELOG.md` from the **tagged** commit, so a note written afterwards can never reach the
published release (basicly-m3od.1 paid for the same one-shot property from the other side).

**The discriminator is the record's own `## Scope`, and absence alone would not do.** A
closed record with no fragment is ambiguous three ways: a forgotten note, a change no
consumer can see, or a record that closed before any of this existed. So the gate judges
only a record that declares a machine-readable scope — the `## Scope` backticks
:func:`~basicly.plan_record.backticked_entries` reads, which the decomposer writes and
`plan_gate` refuses a dispatch without. A closed record carrying none is not reported at
all: 435 of 740 closed records here are in that state, and reporting them would be
reporting the convention's own arrival as a defect. `basicly-r343` and the wired-or-deleted
baseline share the trap — bind on a marker a producer writes, not on the absence of one.

**Owed is measured against the shipped surface, positively.** `src/basicly/` and
`.basicly/core/` are what the wheel carries (`[tool.hatch.build.targets.wheel]`);
`README.md` and `site/` are what a consumer reads and what the release rewrites pins in. An
exclusion list would silently admit every directory added after it was written. 17 closed
records declare machinery alone — `docs/`, `tests/`, the ledger, `.scripts/`, the ratchet
tables — and owe nothing.

**Accounted for is a citation, not a resemblance.** The note may be an unassembled fragment
named for the record, a citation inside another fragment's body, or a citation in
`CHANGELOG.md`, where both go once assembly deletes the files. Parenthetical, and
restricted to the id prefixes the tracker holds: a loose `word-word` match reads "(see the
pre-commit hook)" as a citation, which is how `basicly-jms0` read English as an id.

**A ratchet, not a hard gate**, and `ratchet.py` holds the mechanism the other three use.
145 records were already unaccounted for when this landed, every one closed through a green
gate, and failing them would have meant turning the gate off. Each is recorded in
`[tool.release_notes.frozen]` at 1, with ``may_only = "track"`` so the record must *equal*
the tree. Four ways it disagrees:

* A record **not in the table** owes a note. Refused on sight — the omission this exists
  for.
* A frozen record **gained a note**: it has graduated and the entry goes with it. Leaving it
  would license the omission coming back for free.
* A frozen record **is no longer closed**. Reported for the same reason, and it is what
  closes the reopen hole: the exemption cannot survive rework, because the repairs are
  deleting the entry or writing the note, and after the deletion the re-close is refused.
* A **declaration** that exempts nothing.

**The declaration half.** A change genuinely invisible to a consumer has to be declarable
or the gate trains people to write empty fragments. It is an entry in
`[tool.release_notes.invisible]` carrying its reason, counted against `declared_count` as
`[tool.module_size]`'s `waiver_count` is, and validated against the population it exempts
from: an entry naming a record the tracker does not hold, or one that is not closed, or one
that owes no note anyway, or one that already has a note, fails as stale. An empty reason
fails as an empty fragment does.

Run::

    uv run python .scripts/check_release_notes.py
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    MAY_ONLY_TRACK,
    Finding,
    Ratchet,
    RatchetError,
    compose_ratchet,
    count_delta_remedy,
    fragment,
    report,
)

from basicly import checkout, config, plan_record, release, tracker  # noqa: E402 - the path above

# The gate, as `[tool.release_notes]` and `[ratchet.release_notes]` spell it.
_GATE = "release_notes"
FROZEN_TABLE = f"[tool.{_GATE}.frozen]"
INVISIBLE_TABLE = f"[tool.{_GATE}.invisible]"
FROZEN_FRAGMENT = fragment(f"{_GATE}.frozen")
COUNT_KEY = "declared_count"

# What the wheel carries plus what a consumer reads.
SHIPPED = ("src/basicly/", ".basicly/core/", "README.md", "site/")

# One record owes at most one note, so an entry is a flag counted as a number: the ratchet
# machinery is arithmetic over a per-subject count, and this is that count's only non-zero
# value.
OWED = 1

CLOSED = "closed"

_LABEL = "release-notes"

# Why a record owes nothing, in the words a stale-entry finding quotes back.
_UNKNOWN = "names no record the tracker holds"
_OPEN = "is not closed"
_UNSCOPED = "declares no backticked `## Scope`, so nothing says what it touched"
_MACHINERY = "declares no shipped path"
_NOTED = "already has a release note"


@dataclass(frozen=True)
class Standing:
    """One record's answer to "does this owe a release note", and why not when it does not."""

    record: str
    owed: bool
    reason: str

    @property
    def count(self) -> int:
        """What the ratchet measures for this record."""
        return OWED if self.owed else 0


def standings(repo: Path) -> dict[str, Standing]:
    """Every record the tracker holds, judged."""
    # The `config` call is for its side effect: it installs the tracker mode reader the
    # owned store refuses to answer without. `check_corpus_drift` reaches the committed
    # ledger the same way, so this runs in a fresh clone with no tracker binary.
    config.load_tracker_mode(repo)
    records = tracker.all_records(repo)
    ids = [str(record.get("id")) for record in records]
    accounted = release.accounted_records(repo, ids)
    return {
        issue_id: _standing(issue_id, record, accounted)
        for issue_id, record in zip(ids, records, strict=True)
    }


def _standing(issue_id: str, record: Mapping[str, object], accounted: Collection[str]) -> Standing:
    """Whether *record* owes a release note, and the reason when it does not."""
    if record.get("status") != CLOSED:
        return Standing(issue_id, False, _OPEN)
    scope = plan_record.backticked_entries(
        str(record.get("description") or ""), plan_record.SCOPE_HEADING
    )
    if not scope:
        return Standing(issue_id, False, _UNSCOPED)
    if not any(path.startswith(SHIPPED) for path in scope):
        return Standing(issue_id, False, _MACHINERY)
    if issue_id in accounted:
        return Standing(issue_id, False, _NOTED)
    return Standing(issue_id, True, "")


def load_ratchet(repo: Path) -> Ratchet[int]:
    """This gate's baseline: the frozen unaccounted records, and the declaration count.

    ``may_only="track"`` for the reason `noqa-debt` states: the record must *equal* the
    tree. A frozen record that gained a note has to be banked in the same diff, because
    leaving the entry licenses the omission returning for free.
    """
    return compose_ratchet(
        repo, _GATE, count_key=COUNT_KEY, entry_type=int, may_only=MAY_ONLY_TRACK
    )


def declarations(repo: Path) -> dict[str, str]:
    """The declared-invisible records and the reason each carries.

    Raises:
        RatchetError: pyproject.toml is unreadable, or the table holds something other than
            a reason — never defaulted to empty, which would turn a typo into a silent
            withdrawal of every declaration.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(_GATE, {}).get("invisible", {})
    if not isinstance(table, dict) or not all(isinstance(value, str) for value in table.values()):
        raise RatchetError(f"{INVISIBLE_TABLE} must map each record id to its reason")
    return table


def _on_base_branch(subject: str) -> str | None:
    """The fragment path for *subject* that the base branch already holds, or None.

    **The population and the evidence come from different trees.** The record comes from
    the shared ledger a worktree reaches through the redirect; the fragment is in the
    lane's own checkout. A record closed on base after the lane branched therefore
    refuses every commit on that branch over a note that exists one tree away - three
    times in one session, and one lane answered by declaring it invisible with a control
    true at its branch point and false on arrival.
    """
    for ref in ("origin/main", "main"):
        for name in checkout.names_in(ref, "changelog.d", cwd=REPO_ROOT):
            if name.startswith(f"{subject}."):
                return f"changelog.d/{name}"
    return None


def _owes(subject: str) -> Finding:
    """A closed record that changed a shipped surface and produced no release note."""
    remedy = (
        f"write `changelog.d/{subject}.<category>.md`, or declare it invisible to a "
        f"consumer in {INVISIBLE_TABLE} with its reason and "
        f"{count_delta_remedy(_GATE, 1)}"
    )
    if existing := _on_base_branch(subject):
        remedy = (
            f"`{existing}` is on the base branch and absent here: this tree is behind, "
            f"not in debt. Rebase. Never declare it invisible - such an entry is true at "
            f"a branch point and false on arrival"
        )
    return Finding(
        subject=subject,
        detail=(
            "closed with a `## Scope` naming a shipped path and no release note; the "
            "release workflow reads CHANGELOG.md from the tagged commit, so the note "
            "cannot be added once the tag exists"
        ),
        remedy=remedy,
    )


def _graduated(subject: str, standing: Standing | None, baseline: int) -> Finding:
    """A frozen record that stopped owing a note, so its entry describes nothing."""
    return Finding(
        subject=subject,
        detail=(
            f"{FROZEN_TABLE} records it as owing a release note, but it "
            f"{standing.reason if standing else _UNKNOWN}"
        ),
        remedy=(
            f'record `"{subject}" = {-baseline:+d}` in {FROZEN_FRAGMENT} — an entry left '
            "behind licenses the omission coming back for free"
        ),
    )


def _grew(subject: str, count: int, baseline: int) -> Finding:
    """A frozen entry the tree disagrees with upward, which only a hand-edit produces."""
    return Finding(
        subject=subject,
        detail=f"{count} unaccounted release note(s), up from the frozen {baseline}",
        remedy=f"write the note, or record the difference in {FROZEN_FRAGMENT}",
    )


def _declared(
    subject: str, reason: str, standing: Standing | None, frozen: Mapping[str, int]
) -> list[Finding]:
    """Whether a declaration still exempts something, and whether it argued its case."""
    findings: list[Finding] = []
    if subject in frozen:
        findings.append(
            Finding(
                subject=subject,
                detail=f"declared in {INVISIBLE_TABLE} and frozen in {FROZEN_TABLE}",
                remedy="delete one — a record is exempt once or not at all",
            )
        )
    if not reason.strip():
        findings.append(
            Finding(
                subject=subject,
                detail=f"declared invisible to a consumer in {INVISIBLE_TABLE} with no reason",
                remedy="write the reason, or write the release note instead",
            )
        )
    if standing is None or not standing.owed:
        findings.append(
            Finding(
                subject=subject,
                detail=(
                    f"declared invisible in {INVISIBLE_TABLE} but exempts nothing: it "
                    f"{standing.reason if standing else _UNKNOWN}"
                ),
                remedy=(
                    f"delete the entry and {count_delta_remedy(_GATE, -1)} — an exemption "
                    "nothing reproduces is a suppression nobody is policing"
                ),
            )
        )
    return findings


def _counted(declared: Collection[str], recorded: int) -> list[Finding]:
    """The declaration ratchet, which moves only in a diff that says it moved."""
    if len(declared) == recorded:
        return []
    grew = len(declared) > recorded
    return [
        Finding(
            subject="pyproject.toml",
            detail=(
                f"{len(declared)} record(s) declared invisible but {COUNT_KEY} is "
                f"{recorded} — a declaration was {'added' if grew else 'withdrawn'} "
                f"without saying so (declared: {', '.join(sorted(declared)) or 'none'})"
            ),
            remedy=count_delta_remedy(_GATE, len(declared) - recorded),
        )
    ]


def collect(
    found: Mapping[str, Standing], ratchet: Ratchet[int], declared: Mapping[str, str]
) -> list[Finding]:
    """Every disagreement between the tree and the recorded ratchet.

    The subjects are the **union** of what the tree owes, what the table freezes and what
    the declarations name, so the recorded set is visited whether or not the tree produced
    it. Iterating the measured set alone is how a stale entry — a record that gained a note,
    or one that was reopened — comes to be satisfied by never being looked at.
    """
    owed = {subject for subject, standing in found.items() if standing.owed}
    findings: list[Finding] = []
    for subject in sorted(owed | set(ratchet.frozen) | set(declared)):
        standing = found.get(subject)
        count = standing.count if standing else 0
        if subject in declared:
            findings.extend(_declared(subject, declared[subject], standing, ratchet.frozen))
            continue
        baseline = ratchet.frozen.get(subject)
        if baseline is None:
            findings.append(_owes(subject))
        elif count < baseline:
            findings.append(_graduated(subject, standing, baseline))
        elif count > baseline:
            findings.append(_grew(subject, count, baseline))
    findings.extend(_counted(list(declared), ratchet.count))
    return sorted(findings, key=lambda finding: (finding.subject, finding.detail))


def summary(found: Mapping[str, Standing], ratchet: Ratchet[int], declared: Collection[str]) -> str:
    """The pass line: what was judged, and how much of it is exempt rather than accounted."""
    judged = [item for item in found.values() if item.reason not in (_OPEN, _UNSCOPED)]
    owed = [item for item in judged if item.owed]
    return (
        f"{_LABEL}: {len(judged)} closed record(s) judged, {len(owed)} owing a release "
        f"note and each at its frozen entry ({len(ratchet.frozen)} frozen, "
        f"{len(declared)} declared invisible)"
    )


def main() -> int:
    """Entry point: report every closed record whose change reached a consumer unannounced."""
    try:
        ratchet = load_ratchet(REPO_ROOT)
        declared = declarations(REPO_ROOT)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    found = standings(REPO_ROOT)
    findings = collect(found, ratchet, declared)
    if findings:
        report(_LABEL, findings)
        return 1
    print(summary(found, ratchet, declared))
    return 0


if __name__ == "__main__":
    sys.exit(main())
