"""The brief a failing gate leaves for the run that repairs it (D5, u2hl.4).

Two measured gaps, one mechanism (``docs/requirements/factory-loop.md`` §1):
supervised rework dispatched a *fresh* run, and the prompt it dispatched was
``loop.dispatch_prompt`` — the same fixed text the lane started from, carrying nothing
about what had just failed. So the repair run re-derived the work from the tracker and
re-discovered the defect at the same gate, if at all.

D5 makes repair a **mode of the implementer**, not a persona: same bead, same worktree,
a different brief. The brief is written where the failure is seen (``loop._rework``, the
one funnel every finding-reporting gate passes through) and read where the next run
starts — by ``loop._on_build`` on the interactive path and by ``supervise.build_bundle``
on the supervised one, so neither driver has a way to dispatch a blind rework.

It lives in the lane's own worktree under the self-ignored usage dir (the needs-input
sentinel's convention, basicly-o774): bound to the tree it describes, never in a commit,
and consumed on read so a stale brief cannot re-fire against a gate that has since
passed.

Its own module rather than a section of ``loop`` because the responsibility is nameable
without an "and": what a failed gate recorded, and what the next run is told about it.
Nothing here advances a phase.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import lens_review, needs_input, rubrics, validate_gate, verify

# The brief a failed gate leaves for the run that repairs it, relative to the
# worktree root.
REPAIR_BRIEF_FILE = Path(".basicly/usage/repair-brief.json")

# The gates whose failure a repair run can act on: the three that judge the lane's
# own work — its deterministic suite, its behavioural rubric, and the consumer
# validation of what it merged. The merge gate is not one of them — a collision is
# not a defect in the work, and ``supervise._bounce_lane`` briefs its owner from the
# conflicting paths instead. A landing that failed on its *verify* gate is admitted
# through :data:`LANDING_VERIFY_FAILED`, because that one is a red gate on this diff
# whatever the record happens to be keyed under.
REPAIR_GATES = (verify.DEFAULT_GATE, rubrics.RUBRIC_GATE, validate_gate.VALIDATE_GATE)

# The landing status that is a red verify rather than a collision or a state
# (``merge.MergeResult.status``).
LANDING_VERIFY_FAILED = "verify-failed"

# Bounds on what one brief may carry into a prompt. A gate's output is unbounded
# — a failing suite can print megabytes — and a prompt that does not fit is a
# repair that never starts, so the tail of each check's output is kept (a failure
# reports at the end) and the number of checks is capped.
MAX_REPAIR_EVIDENCE = 5
MAX_REPAIR_OUTPUT_CHARS = 2000

# The heading the recorded reviews sit under. Both halves are load-bearing: §6.4
# forbids merging lens output into one ranked list and §6.5 keeps the reviewer
# advisory, so dropping either hands the repair a move the design refuses.
REVIEW_HEADER = (
    "Review findings recorded when this unit was validated, one section per lens. They "
    "are advisory: the gate named above is what rejected the work, and no finding here "
    "is a gate of its own or a precondition on finishing this repair. Read each lens on "
    "its own terms — they are deliberately neither merged nor ranked against each other, "
    "because a change can pass one axis and fail another and a single ordering lets the "
    "strong axis hide the weak one."
)

# What a lens that recorded no review says: a lens missing from the brief would
# read as a lens that was never asked.
NO_REVIEW = "No review was recorded on this lens."


@dataclass(frozen=True)
class GateEvidence:
    """One failing check as the gate itself reported it: name, command, output."""

    check: str
    command: str = ""
    output: str = ""


@dataclass(frozen=True)
class RepairBrief:
    """What a failed gate reported, in the shape a repair dispatch is briefed with.

    *issue_id* is the bead the failure is attributed to, which is not always the
    node holding the worktree: a lane's sub-task fails on its own record and is
    repaired in the lane's tree.

    *reviews* is one entry per lens, never flattened into *findings* — that set is the
    gate's own and the convergence detector compares it round to round (basicly-w88t).
    """

    issue_id: str
    gate: str
    reason: str
    findings: tuple[str, ...] = ()
    evidence: tuple[GateEvidence, ...] = ()
    reviews: tuple[lens_review.LensFindings, ...] = ()
    # The branch head this brief was written against. What makes it possible to tell a brief
    # that still describes the tree from one whose defect somebody already fixed by another
    # route: the head is the only fact that moves when work lands, and it needs no clock
    # (basicly-59fkfu). Empty in a brief written before this field existed, which
    # :func:`stale_against` reads as *cannot tell* and dispatches.
    branch_head: str = ""

    def as_dict(self) -> dict[str, object]:
        """The JSON shape written to the worktree."""
        return {
            "issue_id": self.issue_id,
            "gate": self.gate,
            "reason": self.reason,
            "findings": list(self.findings),
            "evidence": [
                {"check": e.check, "command": e.command, "output": e.output} for e in self.evidence
            ],
            "reviews": [{"lens": r.lens, "findings": r.findings} for r in self.reviews],
            "branch_head": self.branch_head,
        }


def _parse_repair_brief(data: object) -> RepairBrief | None:
    """The brief *data* describes, or None when it is not a well-formed one.

    Tolerant in the same way and for the same reason as the needs-input sentinel:
    a garbled brief costs one un-briefed dispatch, where raising would fail the
    build phase on a file that is by construction advisory.
    """
    if not isinstance(data, dict):
        return None
    issue_id, gate = data.get("issue_id"), data.get("gate")
    if not isinstance(issue_id, str) or not issue_id.strip():
        return None
    if not isinstance(gate, str) or not gate.strip():
        return None
    reason = data.get("reason")
    raw_findings = data.get("findings")
    findings = (
        tuple(f for f in raw_findings if isinstance(f, str) and f.strip())
        if isinstance(raw_findings, list)
        else ()
    )
    raw_evidence = data.get("evidence")
    evidence = (
        tuple(
            GateEvidence(
                str(e.get("check", "")), str(e.get("command", "")), str(e.get("output", ""))
            )
            for e in raw_evidence
            if isinstance(e, dict) and str(e.get("check", "")).strip()
        )
        if isinstance(raw_evidence, list)
        else ()
    )
    return RepairBrief(
        issue_id=issue_id.strip(),
        gate=gate.strip(),
        reason=reason.strip() if isinstance(reason, str) else "",
        findings=findings,
        evidence=evidence,
        reviews=_parse_reviews(data.get("reviews")),
        branch_head=head.strip() if isinstance(head := data.get("branch_head"), str) else "",
    )


def _parse_reviews(raw: object) -> tuple[lens_review.LensFindings, ...]:
    """The per-lens reviews *raw* describes, in the order the writer recorded them.

    Never re-sorted: §6.4 says the sequence is not a ranking, so re-deriving one here
    would be a second opinion about it. An entry with no lens name is dropped — the
    name is the whole of what keeps two lenses apart.
    """
    if not isinstance(raw, list):
        return ()
    return tuple(
        lens_review.LensFindings(str(r.get("lens", "")).strip(), str(r.get("findings", "")))
        for r in raw
        if isinstance(r, dict) and str(r.get("lens", "")).strip()
    )


def write_repair_brief(cwd: Path, brief: RepairBrief) -> bool:
    """Leave *brief* in worktree *cwd* for its next dispatch; True when written.

    Never creates the worktree, only the usage dir inside an existing one: a
    binding whose tree is gone gets no brief rather than a directory nothing will
    ever read, and the caller is a gate-failure path that must not acquire a
    second way to fall over — so an unwritable tree simply yields False and the
    next dispatch is the un-briefed one it was before.
    """
    if not cwd.is_dir():
        return False
    path = cwd / REPAIR_BRIEF_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(brief.as_dict(), indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def take_repair_brief(cwd: Path) -> RepairBrief | None:
    """Read and consume the repair brief in worktree *cwd*, if a gate left one.

    Consumed on presence, valid or not, for the sentinel's reason: a brief
    describes one failed round, so leaving it would brief a second run about a
    gate the first may already have fixed.
    """
    path = cwd / REPAIR_BRIEF_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return _parse_repair_brief(data)


def stale_against(brief: RepairBrief, head: str | None) -> str:
    """Why *brief* is stale against the branch's current *head*, or "" when it is not.

    A brief names a defect in one state of a branch, so a moved head means that work landed by
    another route - observed on `basicly-gvlpxm`, whose brief asked for a change that had
    merged hours earlier. Cannot-tell dispatches: a brief predating this field, or a head that
    would not resolve, is not evidence of staleness.
    """
    if not brief.branch_head or not head or brief.branch_head == head:
        return ""
    return (
        f"the repair brief for gate {brief.gate!r} was written against {brief.branch_head} and "
        f"the branch is now at {head}, so its defect may already be fixed; re-run the gate to "
        f"raise a brief against what is there now"
    )


def no_commit_reason(brief: RepairBrief, where: str) -> str:
    """Why a repair that left the branch where it found it cannot be charged as a round.

    The other half of :func:`stale_against`. A repair that commits nothing leaves the branch
    carrying nothing base does not hold, so the next advance takes the same brief again - the
    wedge, rather than the waste.
    """
    return (
        f"the repair for gate {brief.gate!r} committed nothing in {where}; re-running it would "
        f"brief the same round again"
    )


def clip_output(text: str) -> str:
    """The tail of *text* under :data:`MAX_REPAIR_OUTPUT_CHARS`, marked when cut."""
    text = text.strip()
    if len(text) <= MAX_REPAIR_OUTPUT_CHARS:
        return text
    return "…(earlier output cut)…\n" + text[-MAX_REPAIR_OUTPUT_CHARS:]


def verify_evidence(report: verify.VerifyReport, cwd: Path, mode: str) -> tuple[GateEvidence, ...]:
    """The failing checks of *report*, each with the command it ran and its output.

    The output is collected by re-running exactly the checks that failed with
    capture on — :func:`verify.rerun_failures`, the same call the landing gate
    already makes for its unreliable-gate test, and evidence rather than a retry:
    nothing in the tree, the config or the command line differs between the two
    runs. It is paid for only where a gate has already failed and the loop is
    about to spend an agent dispatch on it, which costs orders of magnitude more
    than re-running the checks.

    A check that passes on the re-run still contributes its (empty) output rather
    than being dropped. Whether a failure reproduces is the landing gate's verdict
    to make, and a brief that quietly omitted a finding would under-report what
    the repair has to fix.
    """
    failures = sorted(set(report.failures))[:MAX_REPAIR_EVIDENCE]
    if not failures:
        return ()
    commands: dict[str, str] = {}
    with contextlib.suppress(OSError, ValueError):
        commands = {
            check.name: " ".join(check.command)
            for check in verify.load_verify_config(cwd).for_mode(mode)
        }
    outputs: dict[str, str] = {}
    with contextlib.suppress(OSError, ValueError):
        rerun = verify.rerun_failures(report, cwd, mode, capture=True)
        outputs = {r.name: (r.output or r.detail) for r in rerun.results}
    return tuple(
        GateEvidence(name, commands.get(name, ""), clip_output(outputs.get(name, "")))
        for name in failures
    )


def repair_prompt(brief: RepairBrief) -> str:
    """The dispatch prompt for a repair run: fix what the gate reported, in place.

    Deliberately not :func:`dispatch_prompt` with a note appended. A build prompt
    tells the agent to read the requirement and implement it, which on a repair is
    the wrong instruction twice over: the work exists and is committed on this
    branch, and one named gate rejected it. What the run needs is the gate, its
    command and its output — the three things the fixed text carried none of — and
    an explicit refusal of the two moves that turn a repair back into a build:
    re-planning the work, and starting somewhere else.

    Where a judged gate rejected it, the reviewers' findings follow, one section per
    lens (:data:`REVIEW_HEADER`). That is the variable §11.1 says a rework round must
    change: without them, attempt N+1 is attempt N's framing re-sent to the same tier.
    """
    lines = [
        f"You are in the existing git worktree for the tracked issue {brief.issue_id}. "
        "The work is already committed on this branch and a gate rejected it. Repair "
        "it here, in this worktree: do not re-plan the work, do not start a new "
        "branch or worktree, and do not revert the commits already on it.",
        "",
        f"Gate: {brief.gate}",
    ]
    if brief.reason:
        lines.append(f"Verdict: {brief.reason}")
    if brief.findings:
        lines += ["", "What the gate reported:", *(f"- {finding}" for finding in brief.findings)]
    for item in brief.evidence:
        if not item.command and not item.output:
            # The check's name is already in the findings, so an entry the gate
            # gave neither a command nor an output for would add a bare heading
            # and no fact — measured against this repo's own config, where a
            # check that runs in another mode resolves to exactly that.
            continue
        header = f"Check {item.check}"
        if item.command:
            header += f" — command: {item.command}"
        lines += ["", header]
        if item.output:
            lines += ["", "```", item.output, "```"]
    if brief.reviews:
        lines += ["", REVIEW_HEADER]
    for review in brief.reviews:
        lines += ["", f"Lens: {review.lens}", review.findings or NO_REVIEW]
    lines += [
        "",
        "Fix the cause the gate names and re-run its command until it passes, then "
        f"commit the fix on this branch referencing {brief.issue_id}. Do not merge, "
        "push, or close the issue — the harness loop lands and ships it.",
        "If you exhaust your ability to resolve a required fact, do NOT guess: write "
        f"{needs_input.SENTINEL_FILE.as_posix()} as "
        '{"fact": "<the missing fact>", "detail": "<what you tried>"} and stop '
        "without committing a guess — the loop will block and surface it.",
    ]
    return "\n".join(lines)
