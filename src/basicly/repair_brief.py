from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import lens_review, needs_input, rubrics, validate_gate, verify

REPAIR_BRIEF_FILE = Path(".basicly/usage/repair-brief.json")

REPAIR_GATES = (verify.DEFAULT_GATE, rubrics.RUBRIC_GATE, validate_gate.VALIDATE_GATE)

LANDING_VERIFY_FAILED = "verify-failed"

MAX_REPAIR_EVIDENCE = 5
MAX_REPAIR_OUTPUT_CHARS = 2000

REVIEW_HEADER = (
    "Review findings recorded when this unit was validated, one section per lens. They "
    "are advisory: the gate named above is what rejected the work, and no finding here "
    "is a gate of its own or a precondition on finishing this repair. Read each lens on "
    "its own terms — they are deliberately neither merged nor ranked against each other, "
    "because a change can pass one axis and fail another and a single ordering lets the "
    "strong axis hide the weak one."
)

NO_REVIEW = "No review was recorded on this lens."


@dataclass(frozen=True)
class GateEvidence:
    check: str
    command: str = ""
    output: str = ""


@dataclass(frozen=True)
class RepairBrief:
    issue_id: str
    gate: str
    reason: str
    findings: tuple[str, ...] = ()
    evidence: tuple[GateEvidence, ...] = ()
    reviews: tuple[lens_review.LensFindings, ...] = ()
    branch_head: str = ""

    def as_dict(self) -> dict[str, object]:
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

    if not isinstance(raw, list):
        return ()
    return tuple(
        lens_review.LensFindings(str(r.get("lens", "")).strip(), str(r.get("findings", "")))
        for r in raw
        if isinstance(r, dict) and str(r.get("lens", "")).strip()
    )


def write_repair_brief(cwd: Path, brief: RepairBrief) -> bool:

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

    if not brief.branch_head or not head or brief.branch_head == head:
        return ""
    return (
        f"the repair brief for gate {brief.gate!r} was written against {brief.branch_head} and "
        f"the branch is now at {head}, so its defect may already be fixed; discarding it and "
        f"landing what the branch holds now"
    )


def no_commit_reason(brief: RepairBrief, where: str) -> str:

    return (
        f"the repair for gate {brief.gate!r} committed nothing in {where}; re-running it would "
        f"brief the same round again"
    )


def clip_output(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_REPAIR_OUTPUT_CHARS:
        return text
    return "…(earlier output cut)…\n" + text[-MAX_REPAIR_OUTPUT_CHARS:]


def verify_evidence(report: verify.VerifyReport, cwd: Path, mode: str) -> tuple[GateEvidence, ...]:

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
