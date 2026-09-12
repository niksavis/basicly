from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Collection, Mapping
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
from release_note_standing import (  # noqa: E402 - the path above comes first
    LABEL,
    OPEN,
    UNKNOWN,
    UNSCOPED,
    Standing,
    behind_warnings,
    landing_standing,
    standings,
)

_GATE = "release_notes"
FROZEN_TABLE = f"[tool.{_GATE}.frozen]"
INVISIBLE_TABLE = f"[tool.{_GATE}.invisible]"
FROZEN_FRAGMENT = fragment(f"{_GATE}.frozen")
COUNT_KEY = "declared_count"


def load_ratchet(repo: Path) -> Ratchet[int]:

    return compose_ratchet(
        repo, _GATE, count_key=COUNT_KEY, entry_type=int, may_only=MAY_ONLY_TRACK
    )


def declarations(repo: Path) -> dict[str, str]:

    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(_GATE, {}).get("invisible", {})
    if not isinstance(table, dict) or not all(isinstance(value, str) for value in table.values()):
        raise RatchetError(f"{INVISIBLE_TABLE} must map each record id to its reason")
    return table


def _write_or_declare(subject: str) -> str:
    return (
        f"write `changelog.d/{subject}.<category>.md`, or declare it invisible to a "
        f"consumer in {INVISIBLE_TABLE} with its reason and {count_delta_remedy(_GATE, 1)}"
    )


def _owes(subject: str) -> Finding:
    return Finding(
        subject=subject,
        detail=(
            "closed with a `## Scope` naming a shipped path and no release note; the "
            "release workflow reads CHANGELOG.md from the tagged commit, so the note "
            "cannot be added once the tag exists"
        ),
        remedy=_write_or_declare(subject),
    )


def _owed_at_ship(subject: str) -> Finding:

    return Finding(
        subject=subject,
        detail=(
            "declares a shipped path in `## Scope` and holds no release note; ship closes "
            "it and removes this worktree before the closing commit is refused"
        ),
        remedy=_write_or_declare(subject),
    )


def _graduated(subject: str, standing: Standing | None, baseline: int) -> Finding:
    return Finding(
        subject=subject,
        detail=(
            f"{FROZEN_TABLE} records it as owing a release note, but it "
            f"{standing.reason if standing else UNKNOWN}"
        ),
        remedy=(
            f'record `"{subject}" = {-baseline:+d}` in {FROZEN_FRAGMENT} — an entry left '
            "behind licenses the omission coming back for free"
        ),
    )


def _grew(subject: str, count: int, baseline: int) -> Finding:
    return Finding(
        subject=subject,
        detail=f"{count} unaccounted release note(s), up from the frozen {baseline}",
        remedy=f"write the note, or record the difference in {FROZEN_FRAGMENT}",
    )


def _declared(
    subject: str, reason: str, standing: Standing | None, frozen: Mapping[str, int]
) -> list[Finding]:
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
                    f"{standing.reason if standing else UNKNOWN}"
                ),
                remedy=(
                    f"delete the entry and {count_delta_remedy(_GATE, -1)} — an exemption "
                    "nothing reproduces is a suppression nobody is policing"
                ),
            )
        )
    return findings


def _counted(declared: Collection[str], recorded: int) -> list[Finding]:
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

    owed = {subject for subject, standing in found.items() if standing.owed}
    findings: list[Finding] = []
    for subject in sorted(owed | set(ratchet.frozen) | set(declared)):
        standing = found.get(subject)
        count = standing.count if standing else 0
        if subject in declared:
            findings.extend(_declared(subject, declared[subject], standing, ratchet.frozen))
            continue
        if standing is not None and standing.behind:
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
    judged = [item for item in found.values() if item.reason not in (OPEN, UNSCOPED)]
    owed = [item for item in judged if item.owed and not item.behind]
    return (
        f"{LABEL}: {len(judged)} closed record(s) judged, {len(owed)} owing a release "
        f"note and each at its frozen entry ({len(ratchet.frozen)} frozen, "
        f"{len(declared)} declared invisible, {len(behind_warnings(found))} behind the base)"
    )


def landing(repo: Path, record_id: str) -> int:

    try:
        ratchet = load_ratchet(repo)
        declared = declarations(repo)
    except RatchetError as exc:
        print(f"{LABEL}: {exc}", file=sys.stderr)
        return 1
    standing = landing_standing(repo, record_id)
    exempt = record_id in declared or record_id in ratchet.frozen
    if standing.owed and not standing.behind and not exempt:
        report(LABEL, [_owed_at_ship(record_id)])
        return 1
    if exempt:
        settled = f"already exempt in {INVISIBLE_TABLE} or {FROZEN_TABLE}"
    elif standing.behind:
        settled = f"`{standing.behind}` is on the base branch this tree rebases onto"
    else:
        settled = standing.reason
    print(f"{LABEL}: {record_id}: nothing owed at ship — it {settled}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse a release note nobody can add later.")
    parser.add_argument(
        "--landing",
        metavar="RECORD",
        help="judge only this one still-open record, as its ship close will judge it - what "
        "a landing asks while the lane still has a worktree to write the note in",
    )
    args = parser.parse_args(argv)
    if args.landing:
        return landing(REPO_ROOT, args.landing)
    try:
        ratchet = load_ratchet(REPO_ROOT)
        declared = declarations(REPO_ROOT)
    except RatchetError as exc:
        print(f"{LABEL}: {exc}", file=sys.stderr)
        return 1

    found = standings(REPO_ROOT)
    for line in behind_warnings(found):
        print(line)
    findings = collect(found, ratchet, declared)
    if findings:
        report(LABEL, findings)
        return 1
    print(summary(found, ratchet, declared))
    return 0


if __name__ == "__main__":
    sys.exit(main())
