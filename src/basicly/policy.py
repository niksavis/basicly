from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import base_lock, gate_source, run_record, tracker
from .config import (
    AUTONOMY_LEVELS,
    CHECKPOINTS,
    ENGINE_GATE_PROVIDERS,
    LOOP_PHASES,
    PolicyConfig,
    SizingConfig,
    load_policy_config,
)
from .integrity import VALIDATE_GATE
from .invest import TRIGGER_HEADING, missing_sections, required_conditions, trigger_remedy
from .plan_record import ACCEPTANCE_HEADING
from .tracker import add_comment as _add_comment
from .tracker import read_comments as _read_comments
from .tracker import write as _write

MARKER = "[harness-policy]"


PREFLIGHT = "pre-flight"
REVISION = "revision"
ESCALATION = "escalation"
ABORT = "abort"

DOR_GATE = "dor"
LINKED_WORKTREE_GATE = "linked-worktree"

GATE_TYPE_BY_GATE: dict[str, str] = {
    DOR_GATE: PREFLIGHT,
    "verify": REVISION,
    "rubric": PREFLIGHT,
    "rubric-judged": ESCALATION,
    VALIDATE_GATE: REVISION,
    LINKED_WORKTREE_GATE: ABORT,
}


def gate_type(gate: str) -> str:

    return GATE_TYPE_BY_GATE.get(gate, REVISION)


def preflight_gate(gate: str) -> contextlib.AbstractContextManager[None]:

    declared = gate_type(gate)
    if declared != PREFLIGHT:
        raise ValueError(
            f"gate {gate!r} is typed {declared}, not {PREFLIGHT}; only a pre-flight "
            "gate is read-only (architecture D-23)"
        )
    return tracker.read_only(f"pre-flight gate {gate}")


_ACCEPTANCE_CRITERIA_SECTION = ACCEPTANCE_HEADING


@dataclass(frozen=True)
class DoRResult:
    ready: bool
    missing: tuple[str, ...]


def definition_of_ready(repo_root: Path, issue_id: str) -> DoRResult:

    with preflight_gate(DOR_GATE):
        record = tracker.read_record(repo_root, issue_id) or {}
        required = required_sections(str(record.get("issue_type") or ""), repo_root)
        missing = missing_sections(record, required)
    return DoRResult(ready=not missing, missing=missing)


_TODO = "TODO"

_SCOPE_SECTION = "## Scope"

SCOPE_LINE_EXAMPLE = "- `src/basicly/cli.py`"

_SECTION_HINTS: dict[str, str] = {
    TRIGGER_HEADING: f"{_TODO}: {trigger_remedy()}",
    "## Steps to Reproduce": (
        f"{_TODO}: the exact commands run, the observed result, and the expected one."
    ),
    "## Success Criteria": f"{_TODO}: the high-level outcomes that close this epic.",
    _ACCEPTANCE_CRITERIA_SECTION: (
        f"- {_TODO}: Given <starting state> when <action> then <observable result>"
    ),
    _SCOPE_SECTION: (
        f"- {_TODO}: one entry per line in exactly this form: {SCOPE_LINE_EXAMPLE} "
        "— an entry that is not a backticked glob parses to nothing."
    ),
}


def required_sections(work_type: str, repo_root: Path | None = None) -> tuple[str, ...]:

    return required_conditions(work_type, repo_root)


def scaffold_body(work_type: str) -> str:

    return compose_body(work_type, {_SCOPE_SECTION: ""})


def compose_body(
    work_type: str, content: Mapping[str, str] | None = None, *, preamble: str = ""
) -> str:

    content = content or {}
    headings = list(required_sections(work_type))
    headings += [heading for heading in content if heading not in headings]
    default = f"{_TODO}: fill this in."
    sections = (
        f"{heading}\n\n{content.get(heading) or _SECTION_HINTS.get(heading, default)}"
        for heading in headings
    )
    return (f"{preamble.strip()}\n\n" if preamble.strip() else "") + "\n\n".join(sections) + "\n"


def check_working_set(
    title: str, total_tokens: int, scope_tokens: int, sizing: SizingConfig
) -> str | None:

    if total_tokens > sizing.working_set_max:
        return (
            f"child {title!r} estimates {total_tokens} working-set tokens, above "
            f"working_set_max {sizing.working_set_max}: split it into smaller "
            "top-level packages (flatten, do not deepen)"
        )
    if scope_tokens > 0 and total_tokens < sizing.working_set_min:
        return (
            f"child {title!r} estimates {total_tokens} working-set tokens, below "
            f"working_set_min {sizing.working_set_min}: merge it with a sibling "
            "in its scope group (under-cutting wastes per-lane overhead)"
        )
    return None


def unchecked_working_set(title: str, sizing: SizingConfig) -> str:

    return (
        f"package {title!r} declares no scope the estimator can read, so its working set "
        f"was never checked against the {sizing.working_set_min}..{sizing.working_set_max} "
        "band: list the files it touches as backticked globs under a `## Scope` heading"
    )


@dataclass(frozen=True)
class GateVerdict:
    gate: str
    provider: str
    passed: bool


@dataclass(frozen=True)
class GateStatus:
    can_advance: bool
    required_passed: tuple[str, ...]
    required_failed: tuple[str, ...]
    required_missing: tuple[str, ...]
    advisory: tuple[GateVerdict, ...]
    disregarded: tuple[GateVerdict, ...] = ()


def gate_status(repo_root: Path, issue_id: str, config: PolicyConfig) -> GateStatus:

    return classify_gates(
        [
            GateVerdict(r["gate"], r.get("provider", ""), bool(r["passed"]))
            for r in gate_source.read_gates(repo_root, issue_id)
        ],
        config,
    )


def classify_gates(rows: Sequence[GateVerdict], config: PolicyConfig) -> GateStatus:

    required = config.required_gates
    engine = {v.gate: v for v in rows if v.provider in ENGINE_GATE_PROVIDERS}
    latest = {v.gate: v for v in rows}
    passed = tuple(g for g in required if g in engine and engine[g].passed)
    failed = tuple(g for g in required if g in engine and not engine[g].passed)
    missing = tuple(g for g in required if g not in engine)
    advisory = tuple(v for g, v in latest.items() if g not in required)
    disregarded = tuple(
        v for v in rows if v.gate in required and v.provider not in ENGINE_GATE_PROVIDERS
    )
    return GateStatus(
        can_advance=not failed and not missing,
        required_passed=passed,
        required_failed=failed,
        required_missing=missing,
        advisory=advisory,
        disregarded=disregarded,
    )


def _comments(repo_root: Path, issue_id: str) -> list[dict]:

    return _read_comments(repo_root, issue_id)


def _comment_texts(repo_root: Path, issue_id: str) -> list[str]:
    return [str(c.get("text", "")) for c in _comments(repo_root, issue_id)]


def _issue_is_closed(repo_root: Path, issue_id: str) -> bool:

    record = tracker.read_record(repo_root, issue_id)
    return record is not None and str(record.get("status", "")) == "closed"


def _rework_marker(gate: str) -> str:
    return f"{MARKER} rework gate={gate}"


def _marker_matches(text: str, marker: str) -> bool:

    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return first_line == marker or first_line.startswith(marker + " ")


def _marker_payload(text: str, marker: str) -> str | None:

    if not _marker_matches(text, marker):
        return None
    return text.strip()[len(marker) :].strip()


def _rework_allowance_marker(gate: str) -> str:
    return f"{MARKER} rework-allowance gate={gate}"


def rework_attempts(repo_root: Path, issue_id: str, gate: str) -> int:

    marker = _rework_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def rework_recorded(repo_root: Path, issue_id: str) -> int:

    marker = f"{MARKER} rework"
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def rework_allowances(repo_root: Path, issue_id: str, gate: str) -> int:
    marker = _rework_allowance_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def rework_charged(repo_root: Path, issue_id: str, gate: str) -> int:

    texts = _comment_texts(repo_root, issue_id)
    attempt_marker = _rework_marker(gate)
    allowance_marker = _rework_allowance_marker(gate)
    attempts = sum(1 for text in texts if _marker_matches(text, attempt_marker))
    granted = sum(1 for text in texts if _marker_matches(text, allowance_marker))
    return max(0, attempts - granted)


def record_rework(repo_root: Path, issue_id: str, gate: str) -> int:

    _add_comment(repo_root, issue_id, _rework_marker(gate))
    return rework_charged(repo_root, issue_id, gate)


def grant_rework_allowance(repo_root: Path, issue_id: str, gate: str) -> int:

    _add_comment(repo_root, issue_id, _rework_allowance_marker(gate))
    return rework_charged(repo_root, issue_id, gate)


def _unreliable_gate_marker(gate: str) -> str:
    return f"{MARKER} gate-unreliable gate={gate}"


def unreliable_gate_events(repo_root: Path, issue_id: str, gate: str) -> int:
    marker = _unreliable_gate_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def record_unreliable_gate(repo_root: Path, issue_id: str, gate: str, detail: str = "") -> int:

    suffix = f" {detail}" if detail else ""
    _add_comment(repo_root, issue_id, f"{_unreliable_gate_marker(gate)}{suffix}")
    return unreliable_gate_events(repo_root, issue_id, gate)


REWORK_ESCALATION_KIND = "escalation"
_REWORK_ESCALATION_RE = re.compile(r"^rework cap reached on gate (?P<gate>\S+):")


def rework_escalation_question(gate: str) -> str:
    return f"rework cap reached on gate {gate}: retry, re-dispatch, or park?"


def gate_from_rework_escalation(question: str) -> str | None:
    match = _REWORK_ESCALATION_RE.match(question.strip())
    return match.group("gate") if match else None


MAX_UNRELIABLE_GATE_EVENTS = 3
_UNRELIABLE_ESCALATION_RE = re.compile(r"^gate (?P<gate>\S+) is unreliable:")


def unreliable_gate_escalation_question(gate: str) -> str:
    return (
        f"gate {gate} is unreliable: it failed and then passed unchanged "
        f"{MAX_UNRELIABLE_GATE_EVENTS} times; fix the flake, or land anyway?"
    )


def gate_from_unreliable_escalation(question: str) -> str | None:
    match = _UNRELIABLE_ESCALATION_RE.match(question.strip())
    return match.group("gate") if match else None


_LAND_ANYWAY_RE = re.compile(r"^\s*land\s+anyway\b", re.IGNORECASE)


def answer_lands_anyway(answer: str) -> bool:
    return _LAND_ANYWAY_RE.match(answer) is not None


HELD_STATUS = "deferred"

_HOLD_MARKER = f"{MARKER} hold"
_KILL_MARKER = f"{MARKER} kill"

_HOLD_ANSWER_RE = re.compile(r"^\s*(?:park|hold)\b", re.IGNORECASE)


def answer_holds(answer: str) -> bool:
    return _HOLD_ANSWER_RE.match(answer) is not None


def hold_lane(repo_root: Path, issue_id: str, reason: str, gate: str | None = None) -> None:

    named = f"gate={gate} " if gate else ""
    _add_comment(repo_root, issue_id, f"{_HOLD_MARKER} {named}{reason}".rstrip())
    _write(repo_root, ["update", issue_id, "--status", HELD_STATUS])


PROGRESSING = "progressing"
STALLED = "stalled"
DIVERGING = "diverging"

FINDING_SET_MARKER = f"{MARKER} finding-set"

MAX_FINDING_SET_MEMBERS = 20
MAX_FINDING_MEMBER_CHARS = 120

MAX_STALLED_REWORK_ROUNDS = 2


@dataclass(frozen=True)
class Convergence:
    verdict: str
    members: tuple[str, ...]
    previous: tuple[str, ...]
    stalled_rounds: int

    @property
    def stalled(self) -> bool:
        return self.verdict == STALLED

    @property
    def diverging(self) -> bool:
        return self.verdict == DIVERGING

    @property
    def detail(self) -> str:
        if self.stalled and self.members == self.previous:
            return (
                f"the gate reported the same {len(self.members)} finding(s) as the previous "
                f"attempt ({', '.join(self.members)}); this round changed nothing it reports"
            )
        if self.stalled:
            return (
                f"the gate's finding set hit the recorded cap of {len(self.members)}; a "
                "truncated view cannot prove this round improved, so it reads as no progress"
            )
        if self.diverging:
            added = ", ".join(m for m in self.members if m not in set(self.previous))
            return (
                f"the gate's finding set grew to {len(self.members)}: the previous "
                f"{len(self.previous)} are all still open and {added} joined them"
            )
        return ""


def finding_signature(findings: Sequence[str]) -> tuple[str, ...]:

    return _finding_signature_and_truncation(findings)[0]


def _finding_signature_and_truncation(findings: Sequence[str]) -> tuple[tuple[str, ...], bool]:
    members = {
        member.strip()[:MAX_FINDING_MEMBER_CHARS]
        for member in findings
        if member and member.strip()
    }
    ordered = sorted(members)
    return tuple(ordered[:MAX_FINDING_SET_MEMBERS]), len(ordered) > MAX_FINDING_SET_MEMBERS


def _finding_set_marker(gate: str) -> str:
    return f"{FINDING_SET_MARKER} gate={gate}"


_FINDING_MEMBERS_RE = re.compile(r"findings=(?P<members>\[.*\])\s*\Z", re.DOTALL)


def _parse_finding_members(payload: str) -> tuple[str, ...] | None:

    match = _FINDING_MEMBERS_RE.search(payload)
    if match is None:
        return None
    try:
        data = json.loads(match.group("members"))
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return tuple(str(item) for item in data)


def _finding_set_history(repo_root: Path, issue_id: str, gate: str) -> list[tuple[str, ...]]:
    marker = _finding_set_marker(gate)
    history: list[tuple[str, ...]] = []
    for text in _comment_texts(repo_root, issue_id):
        payload = _marker_payload(text, marker)
        if payload is None:
            continue
        members = _parse_finding_members(payload)
        if members is not None:
            history.append(members)
    return history


def _compare_finding_sets(
    history: Sequence[tuple[str, ...]], members: tuple[str, ...], *, truncated: bool = False
) -> tuple[str, tuple[str, ...], int]:

    if not history:
        return PROGRESSING, (), 0
    previous = history[-1]
    if members == previous:
        rounds = 1
        for earlier in reversed(history[:-1]):
            if earlier != members:
                break
            rounds += 1
        return STALLED, previous, rounds
    if set(members) > set(previous):
        return DIVERGING, previous, 0
    if truncated:
        return STALLED, previous, 0
    return PROGRESSING, previous, 0


def record_finding_set(
    repo_root: Path, issue_id: str, gate: str, findings: Sequence[str]
) -> Convergence:

    members, truncated = _finding_signature_and_truncation(findings)
    verdict, previous, rounds = _compare_finding_sets(
        _finding_set_history(repo_root, issue_id, gate), members, truncated=truncated
    )
    body = f"{_finding_set_marker(gate)} verdict={verdict} findings={json.dumps(list(members))}"
    _add_comment(repo_root, issue_id, body)
    return Convergence(verdict=verdict, members=members, previous=previous, stalled_rounds=rounds)


def finding_set_escalation(convergence: Convergence) -> str | None:

    if convergence.diverging:
        return (
            f"{convergence.detail} — rework is making the work worse, not better; "
            "re-scope it, brief the agent with the gate's output, or fix it by hand"
        )
    if convergence.stalled_rounds >= MAX_STALLED_REWORK_ROUNDS:
        return (
            f"{convergence.detail}, and neither did the {convergence.stalled_rounds - 1} "
            "round(s) before it — the rework loop is not converging; re-scope it, brief the "
            "agent with the gate's output, or fix it by hand"
        )
    return None


def _convergence_refund_marker(gate: str) -> str:
    return f"{MARKER} convergence-refund gate={gate}"


def spend_convergence_refund(repo_root: Path, issue_id: str, gate: str) -> bool:

    marker = _convergence_refund_marker(gate)
    if any(_marker_matches(text, marker) for text in _comment_texts(repo_root, issue_id)):
        return False
    grant_rework_allowance(repo_root, issue_id, gate)
    _add_comment(repo_root, issue_id, marker)
    return True


SHARED_TRACKER_GATE_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("completed at an estimate of", "above working_set_max"),
        "the working-set ceiling asserts over every completed lane in the shared "
        "tracker, so one lane's scope declaration fails it inside every sibling "
        "landing of the same pass",
    ),
    (
        ("failed at an estimate of", "admits it"),
        "the working-set ceiling's upper half asserts over every failed lane in the "
        "shared tracker, so one lane's record fails it inside every sibling landing "
        "of the same pass",
    ),
)


@dataclass(frozen=True)
class SharedGateFailure:
    culprits: tuple[str, ...]
    reason: str


def shared_tracker_gate_failure(output: str, issue_id: str) -> SharedGateFailure | None:

    prefix, _, _ = issue_id.partition("-")
    if not prefix or prefix == issue_id:
        return None
    bead_id = re.compile(rf"\b{re.escape(prefix)}-[A-Za-z0-9]+(?:\.\d+)*\b")
    culprits: list[str] = []
    reasons: list[str] = []
    for line in output.splitlines():
        for substrings, reason in SHARED_TRACKER_GATE_SIGNATURES:
            if not all(s in line for s in substrings):
                continue
            named = bead_id.findall(line)
            if issue_id in named:
                return None
            culprits += [bead for bead in named if bead not in culprits]
            if reason not in reasons:
                reasons.append(reason)
    if not culprits:
        return None
    return SharedGateFailure(tuple(culprits), "; ".join(reasons))


SHARED_GATE_MARKER = f"{MARKER} gate-shared-tracker"
GATE_INVALIDATED_MARKER = f"{MARKER} gate-invalidated"


def shared_gate_events(repo_root: Path, issue_id: str, gate: str) -> int:
    marker = f"{SHARED_GATE_MARKER} gate={gate}"
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def record_shared_gate_failure(
    repo_root: Path,
    issue_id: str,
    gate: str,
    culprits: Sequence[str],
    detail: str = "",
) -> int:

    suffix = f" {detail}" if detail else ""
    _add_comment(
        repo_root,
        issue_id,
        f"{SHARED_GATE_MARKER} gate={gate} culprits={','.join(culprits)}{suffix}",
    )
    body = f"{GATE_INVALIDATED_MARKER} gate={gate} lanes={issue_id}"
    for culprit in culprits:
        if not any(_marker_matches(text, body) for text in _comment_texts(repo_root, culprit)):
            _add_comment(repo_root, culprit, body)
    return shared_gate_events(repo_root, issue_id, gate)


_SHARED_GATE_ESCALATION_RE = re.compile(r"^gate (?P<gate>\S+) failed on another lane's record:")


def shared_gate_escalation_question(gate: str, culprits: Sequence[str]) -> str:

    return (
        f"gate {gate} failed on another lane's record: {', '.join(culprits)} "
        "invalidated it in the shared tracker, not this lane's diff; fix that lane's "
        "record, or reconcile the constant it fails against, then advance again"
    )


def gate_from_shared_gate_escalation(question: str) -> str | None:
    match = _SHARED_GATE_ESCALATION_RE.match(question.strip())
    return match.group("gate") if match else None


def _gate_override_marker(gate: str) -> str:
    return f"{MARKER} gate-override-spent gate={gate}"


def gate_override_spent(repo_root: Path, issue_id: str, gate: str) -> bool:
    marker = _gate_override_marker(gate)
    return any(_marker_matches(text, marker) for text in _comment_texts(repo_root, issue_id))


def spend_gate_override(repo_root: Path, issue_id: str, gate: str) -> bool:

    if gate_override_spent(repo_root, issue_id, gate):
        return False
    _add_comment(repo_root, issue_id, _gate_override_marker(gate))
    return True


def _checkpoint_marker(name: str) -> str:
    return f"{MARKER} checkpoint={name} approved"


def checkpoint_approved_in(texts: Iterable[str], name: str) -> bool:

    marker = _checkpoint_marker(name)
    return any(_marker_matches(text, marker) for text in texts)


def checkpoint_approved(repo_root: Path, issue_id: str, name: str) -> bool:
    return checkpoint_approved_in(_comment_texts(repo_root, issue_id), name)


def approve_checkpoint(repo_root: Path, issue_id: str, name: str) -> None:
    if name not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {name!r}; expected one of {list(CHECKPOINTS)}")
    if not checkpoint_approved(repo_root, issue_id, name):
        _add_comment(repo_root, issue_id, _checkpoint_marker(name))


CONFIRM_TTL_SECONDS = 900
_CONFIRM_FILE = Path(".basicly/usage/checkpoint-confirms.json")


def _now() -> float:
    return time.time()


def _new_code() -> str:
    return secrets.token_hex(4)


def _confirm_key(issue_id: str, name: str) -> str:
    return f"{issue_id}:{name}"


def _read_confirms(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_confirms(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gitignore = path.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    tmp = path.with_suffix(f".{os.getpid()}.json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


_CONFIRM_HOLD_BUDGET_S = 5.0
_CONFIRM_WAIT_S = 60.0


def _confirm_store_lock(path: Path) -> AbstractContextManager[None]:

    return base_lock.hold_file(
        path.with_suffix(".lock"),
        hold_budget_s=_CONFIRM_HOLD_BUDGET_S,
        wait_s=_CONFIRM_WAIT_S,
    )


def _issue_confirm_code(repo_root: Path, issue_id: str, name: str) -> str:
    path = repo_root / _CONFIRM_FILE
    code = _new_code()
    with _confirm_store_lock(path):
        data = _read_confirms(path)
        data[_confirm_key(issue_id, name)] = {
            "code": code,
            "expires": _now() + CONFIRM_TTL_SECONDS,
        }
        _write_confirms(path, data)
    return code


def _consume_confirm_code(repo_root: Path, issue_id: str, name: str, code: str) -> bool:
    path = repo_root / _CONFIRM_FILE
    with _confirm_store_lock(path):
        data = _read_confirms(path)
        entry = data.get(_confirm_key(issue_id, name))
        if not isinstance(entry, dict):
            return False
        expired = _now() > float(entry.get("expires", 0))
        ok = not expired and secrets.compare_digest(str(entry.get("code", "")), code)
        if expired or ok:
            data.pop(_confirm_key(issue_id, name), None)
            _write_confirms(path, data)
    return ok


@dataclass(frozen=True)
class ApprovalResult:
    status: str
    code: str | None = None
    detail: str = ""


def approve_checkpoint_guarded(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    issue_id: str,
    name: str,
    *,
    interactive: bool,
    confirm: str | None = None,
    grant_root: str | None = None,
) -> ApprovalResult:

    if name not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {name!r}; expected one of {list(CHECKPOINTS)}")
    result, by = _checkpoint_approval(
        repo_root,
        issue_id,
        name,
        interactive=interactive,
        confirm=confirm,
        grant_root=grant_root or issue_id,
    )
    if result.status == "approved":
        _settle_checkpoint_queue(repo_root, issue_id, name, by=by)
    return result


def _checkpoint_approval(  # noqa: PLR0913 — the guarded surface it was split out of
    repo_root: Path,
    issue_id: str,
    name: str,
    *,
    interactive: bool,
    confirm: str | None,
    grant_root: str,
) -> tuple[ApprovalResult, str]:

    if checkpoint_approved(repo_root, issue_id, name):
        return ApprovalResult("approved", detail="already approved"), _RECONCILED_BY
    if interactive:
        approve_checkpoint(repo_root, issue_id, name)
        record_checkpoint_wait(repo_root, issue_id, name, by=HUMAN_BY, delegated=False)
        return ApprovalResult("approved"), HUMAN_BY
    if confirm is None:
        delegated, declined, granted_by = _grant_approval(repo_root, issue_id, name, grant_root)
        if delegated is not None:
            return delegated, granted_by
        record_wait_request(repo_root, issue_id, name)
        return (
            ApprovalResult(
                "challenge",
                code=_issue_confirm_code(repo_root, issue_id, name),
                detail=declined,
            ),
            "",
        )
    if _consume_confirm_code(repo_root, issue_id, name, confirm):
        approve_checkpoint(repo_root, issue_id, name)
        record_checkpoint_wait(repo_root, issue_id, name, by=HUMAN_BY, delegated=False)
        return ApprovalResult("approved"), HUMAN_BY
    return ApprovalResult("rejected", detail="invalid or expired confirm code"), ""


def _settle_checkpoint_queue(repo_root: Path, issue_id: str, name: str, *, by: str) -> None:

    from . import decisions  # noqa: PLC0415 — see the cycle note above

    with contextlib.suppress(RuntimeError, OSError, ValueError):
        decisions.settle_checkpoint(repo_root, issue_id, name, by=by)


KILL_CONFIRM_NAME = "kill"


def authorize_kill(repo_root: Path, issue_id: str, *, confirm: str | None = None) -> ApprovalResult:

    if confirm is None:
        return ApprovalResult(
            "challenge", code=_issue_confirm_code(repo_root, issue_id, KILL_CONFIRM_NAME)
        )
    if _consume_confirm_code(repo_root, issue_id, KILL_CONFIRM_NAME, confirm):
        return ApprovalResult("approved")
    return ApprovalResult("rejected", detail="invalid or expired confirm code")


def kill_lane(repo_root: Path, issue_id: str, reason: str) -> None:

    _add_comment(repo_root, issue_id, f"{_KILL_MARKER} {reason}")
    _write(repo_root, ["close", issue_id, "--reason", f"killed: {reason}"])


GRANT_COVERAGE: dict[str, tuple[str, ...]] = {
    "L0": (),
    "L1": ("decompose",),
    "L2": ("classify", "decompose"),
    "L3": ("classify", "decompose", "ship"),
}

_GRANT_PREFIX = f"{MARKER} grant level="
_REVOKE_MARKER = f"{MARKER} grant revoked"
_NEEDS_INPUT_KIND = "needs-input"
_NEEDS_INPUT_MARKER = f"{MARKER} {_NEEDS_INPUT_KIND}"


@dataclass(frozen=True)
class Grant:
    level: str
    token_budget: int | None
    spent_at_issue: int = 0
    unmetered_at_issue: int = 0


def _grant_marker(grant: Grant) -> str:
    text = f"{_GRANT_PREFIX}{grant.level}"
    if grant.token_budget is not None:
        text += f" budget={grant.token_budget}"
    if grant.spent_at_issue:
        text += f" baseline={grant.spent_at_issue}"
    if grant.unmetered_at_issue:
        text += f" unmetered={grant.unmetered_at_issue}"
    return text


_GRANT_INT_FIELDS = ("budget", "baseline", "unmetered")


def _grant_fields(tokens: Sequence[str]) -> dict[str, int] | None:

    values: dict[str, int] = {}
    for token in tokens:
        key, _, raw = token.partition("=")
        if key not in _GRANT_INT_FIELDS:
            continue
        try:
            values[key] = int(raw)
        except ValueError:
            return None
    return values


def _parse_grant(text: str) -> Grant | None:
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if not first_line.startswith(_GRANT_PREFIX):
        return None
    tokens = first_line[len(_GRANT_PREFIX) :].split()
    if not tokens or tokens[0] not in AUTONOMY_LEVELS:
        return None
    fields = _grant_fields(tokens[1:])
    if fields is None:
        return None
    budget = fields.get("budget")
    if tokens[0] in ("L2", "L3") and not (isinstance(budget, int) and budget > 0):
        return None
    return Grant(
        level=tokens[0],
        token_budget=budget,
        spent_at_issue=max(0, fields.get("baseline", 0)),
        unmetered_at_issue=max(0, fields.get("unmetered", 0)),
    )


def active_grant(repo_root: Path, root_issue: str) -> Grant | None:

    if _issue_is_closed(repo_root, root_issue):
        return None
    grant: Grant | None = None
    for text in _comment_texts(repo_root, root_issue):
        if _marker_matches(text, _REVOKE_MARKER):
            grant = None
            continue
        parsed = _parse_grant(text)
        if parsed is not None:
            grant = parsed
    return grant


def _grant_refusal(level: str, token_budget: int | None, config: PolicyConfig) -> str | None:

    if level not in AUTONOMY_LEVELS or level == "L0":
        grantable = [lvl for lvl in AUTONOMY_LEVELS if lvl != "L0"]
        return f"grant level must be one of {grantable}"
    if AUTONOMY_LEVELS.index(level) > AUTONOMY_LEVELS.index(config.autonomy):
        return (
            f"level {level} exceeds the [policy] autonomy ceiling "
            f"({config.autonomy}); raise it in basicly.toml to opt in"
        )
    if level in ("L2", "L3") and not (isinstance(token_budget, int) and token_budget > 0):
        return (
            f"an {level} grant requires a positive token_budget "
            "(unbounded lights-out is unreachable by design)"
        )
    return None


def issue_grant_guarded(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    root_issue: str,
    level: str,
    token_budget: int | None,
    config: PolicyConfig,
    *,
    interactive: bool,
    confirm: str | None = None,
) -> ApprovalResult:

    refusal = _grant_refusal(level, token_budget, config)
    if refusal is not None:
        return ApprovalResult("rejected", detail=refusal)
    if interactive:
        _write_grant(repo_root, root_issue, level, token_budget)
        return ApprovalResult("approved")
    checkpoint_name = f"grant-{level}-{token_budget}"
    if confirm is None:
        return ApprovalResult(
            "challenge", code=_issue_confirm_code(repo_root, root_issue, checkpoint_name)
        )
    if _consume_confirm_code(repo_root, root_issue, checkpoint_name, confirm):
        _write_grant(repo_root, root_issue, level, token_budget)
        return ApprovalResult("approved")
    return ApprovalResult("rejected", detail="invalid or expired confirm code")


def _write_grant(repo_root: Path, root_issue: str, level: str, token_budget: int | None) -> None:

    meter = session_spend(repo_root, root_issue)
    grant = Grant(
        level=level,
        token_budget=token_budget,
        spent_at_issue=meter.measured_tokens,
        unmetered_at_issue=meter.unmetered_dispatches,
    )
    _add_comment(repo_root, root_issue, _grant_marker(grant))


def revoke_grant(repo_root: Path, root_issue: str) -> None:
    _add_comment(repo_root, root_issue, _REVOKE_MARKER)


def _grant_declined(grant: Grant, name: str, reasons: Sequence[str], *, scope: str = "") -> str:

    where = f" ({scope})" if scope else ""
    return (
        f"the active {grant.level} grant covers {name} but declined it{where}, so the "
        f"decision returns to a human: {'; '.join(reasons)}"
    )


def _grant_approval(
    repo_root: Path, issue_id: str, name: str, root_issue: str
) -> tuple[ApprovalResult | None, str, str]:

    grant = active_grant(repo_root, root_issue)
    if grant is None:
        return None, "", ""
    if name not in GRANT_COVERAGE.get(grant.level, ()):
        return None, f"the active {grant.level} grant on {root_issue} does not delegate {name}", ""
    session_ids = session_issue_ids(repo_root, root_issue)
    if issue_id not in session_ids:
        return (
            None,
            (
                f"the active {grant.level} grant on {root_issue} does not cover {issue_id}: "
                "it is not in that session's issue tree"
            ),
            "",
        )
    config = load_policy_config(repo_root)
    spend = spend_status(repo_root, root_issue, grant=grant, ids=session_ids)
    if name == "ship":
        violations = lights_out_violations(
            repo_root, root_issue, config, ids=session_ids, shipping=issue_id
        )
        if violations:
            return (
                None,
                _grant_declined(
                    grant,
                    name,
                    violations,
                    scope=f"lights-out preconditions across session {root_issue}",
                ),
                "",
            )
    marker = f"{_checkpoint_marker(name)} under grant {grant.level}"
    _add_comment(repo_root, issue_id, marker)
    record_checkpoint_wait(repo_root, issue_id, name, by=f"grant:{grant.level}", delegated=True)
    over = f"; {spend.detail}" if spend.halted else ""
    return (
        ApprovalResult("approved", detail=f"delegated under {grant.level} grant{over}"),
        "",
        f"grant:{grant.level}",
    )


PROPOSAL_COVERAGE: dict[str, tuple[str, ...]] = {
    "L0": (),
    "L1": (),
    "L2": ("work_type", "children"),
    "L3": ("work_type", "children"),
}


@dataclass(frozen=True)
class ProposalGrant:
    allowed: bool
    reason: str = ""
    level: str = ""


def proposal_delegated(repo_root: Path, issue_id: str, kind: str, root_issue: str) -> ProposalGrant:

    if kind not in PROPOSAL_COVERAGE["L3"]:
        raise ValueError(
            f"unknown proposal kind {kind!r}; expected one of {list(PROPOSAL_COVERAGE['L3'])}"
        )
    grant = active_grant(repo_root, root_issue)
    if grant is None:
        return ProposalGrant(False, f"no active grant on {root_issue} to delegate it under")
    if kind not in PROPOSAL_COVERAGE.get(grant.level, ()):
        return ProposalGrant(
            False,
            f"the active {grant.level} grant on {root_issue} approves the checkpoint but does "
            f"not originate the {kind} proposal",
        )
    session_ids = session_issue_ids(repo_root, root_issue)
    if issue_id not in session_ids:
        return ProposalGrant(
            False,
            f"the active {grant.level} grant on {root_issue} does not cover {issue_id}: "
            "it is not in that session's issue tree",
        )
    return ProposalGrant(True, level=grant.level)


def records_by_id(repo_root: Path) -> dict[str, dict]:

    return {row["id"]: row for row in tracker.all_records(repo_root)}


def session_issue_ids(
    repo_root: Path, root_issue: str, *, population: Mapping[str, dict] | None = None
) -> tuple[str, ...]:

    held = population if population is not None else records_by_id(repo_root)
    edges = (("dependents", "parent-child"), ("dependencies", "blocks"))
    seen: dict[str, None] = {root_issue: None}
    queue = [root_issue]
    while queue:
        walked = queue.pop(0)
        record = held.get(walked) or tracker.read_record(repo_root, walked)
        if record is None:
            continue
        for key, wanted in edges:
            for dep in record.get(key) or []:
                if not isinstance(dep, dict) or dep.get("dependency_type") != wanted:
                    continue
                if "id" in dep and str(dep["id"]) not in seen:
                    seen[str(dep["id"])] = None
                    queue.append(str(dep["id"]))
    return tuple(seen)


def session_coverage(repo_root: Path, root_issue: str) -> int:

    return len(session_issue_ids(repo_root, root_issue))


@dataclass(frozen=True)
class SpendMeter:
    measured_tokens: int
    estimated_tokens: int
    unmetered_dispatches: int
    unmetered_labels: tuple[str, ...] = ()
    dispatches_seen: int = 0


def tokens_under_grant(spent_tokens: int, grant: Grant) -> int:

    return max(0, spent_tokens - grant.spent_at_issue)


@dataclass(frozen=True)
class SpendStatus:
    grant: Grant | None
    spent_tokens: int
    halted: bool
    detail: str = ""
    unmetered_dispatches: int = 0
    unmetered_labels: tuple[str, ...] = ()

    @property
    def remaining_tokens(self) -> int | None:

        if self.grant is None or self.grant.token_budget is None:
            return None
        if self.unmetered_dispatches:
            return 0
        return max(0, self.grant.token_budget - tokens_under_grant(self.spent_tokens, self.grant))


def check_pass_spend(forecast_tokens: int, status: SpendStatus) -> str | None:

    remaining = status.remaining_tokens
    if remaining is None or forecast_tokens <= remaining:
        return None
    level = status.grant.level if status.grant is not None else "active"
    if status.unmetered_dispatches:
        return (
            f"this pass forecasts {forecast_tokens} tokens against an unknown remainder "
            f"under the {level} grant: {status.unmetered_dispatches} dispatch(es) reported "
            "no measurable usage, so nothing says what is left"
        )
    return (
        f"this pass forecasts {forecast_tokens} tokens against {remaining} remaining "
        f"under the {level} grant; it starts anyway. Re-scope the lanes or re-grant "
        "if that figure is not the one you meant to spend"
    )


def spend_status(
    repo_root: Path,
    root_issue: str,
    *,
    grant: Grant | None = None,
    ids: tuple[str, ...] | None = None,
) -> SpendStatus:

    if grant is None:
        grant = active_grant(repo_root, root_issue)
    meter = session_spend(repo_root, root_issue, ids=ids)
    spent = meter.measured_tokens
    if grant is None or grant.token_budget is None:
        return SpendStatus(grant=grant, spent_tokens=spent, halted=False)
    budget = grant.token_budget
    under_grant = tokens_under_grant(spent, grant)
    unmetered = max(0, meter.unmetered_dispatches - grant.unmetered_at_issue)
    if unmetered:
        return SpendStatus(
            grant=grant,
            spent_tokens=spent,
            halted=True,
            unmetered_dispatches=unmetered,
            unmetered_labels=meter.unmetered_labels,
            detail=(
                f"{grant.level} grant cannot be metered: {unmetered} dispatch(es) under it "
                f"reported no measurable usage, so only a chars/4 floor over their captured "
                f"output exists ({meter.estimated_tokens} estimated, far below real spend) "
                f"and {under_grant}/{budget} tokens is not what this grant has cost; the "
                "session is human-only until re-granted or the runner is configured with a "
                f"usage format. Unmeasured here: {', '.join(meter.unmetered_labels)}"
            ),
        )
    if under_grant < budget:
        return SpendStatus(grant=grant, spent_tokens=spent, halted=False)
    return SpendStatus(
        grant=grant,
        spent_tokens=spent,
        halted=True,
        detail=(
            f"{grant.level} grant token_budget spent ({under_grant}/{budget} tokens "
            f"under this grant; {spent} lifetime); the session is human-only until "
            "re-granted"
        ),
    )


def session_spend(
    repo_root: Path,
    root_issue: str,
    *,
    ids: tuple[str, ...] | None = None,
    history: Mapping[str, list] | None = None,
) -> SpendMeter:

    records = history if history is not None else run_record.load_run_records(repo_root) or {}
    measured = 0
    estimated = 0
    seen = 0
    unmetered: list[str] = []
    for issue_id in ids if ids is not None else session_issue_ids(repo_root, root_issue):
        entries = records.get(issue_id)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            seen += 1
            sample = run_record.spend_sample(entry)
            if sample is None:
                continue
            tokens, kind = sample
            if kind == run_record.MEASURED:
                measured += tokens
                continue
            estimated += tokens
            if kind == run_record.UNMETERED:
                unmetered.append(run_record.dispatch_label(issue_id, entry))
    return SpendMeter(
        measured_tokens=measured,
        estimated_tokens=estimated,
        unmetered_dispatches=len(unmetered),
        unmetered_labels=tuple(unmetered),
        dispatches_seen=seen,
    )


def record_needs_input(repo_root: Path, issue_id: str, fact: str) -> None:

    _add_comment(repo_root, issue_id, f"{_NEEDS_INPUT_MARKER} {fact}")


def _answered_asks(repo_root: Path, issue_id: str) -> frozenset[tuple[str, str]]:

    from . import decisions  # noqa: PLC0415 — see the cycle note above

    latest: dict[tuple[str, str], bool] = {}
    for item in decisions.items_on(repo_root, issue_id):
        latest[item.kind, item.question] = item.pending
    return frozenset(ask for ask, pending in latest.items() if not pending)


def _live_session_violations(repo_root: Path, issue_id: str, config: PolicyConfig) -> list[str]:

    texts = _comment_texts(repo_root, issue_id)
    facts = [
        fact for text in texts if (fact := _marker_payload(text, _NEEDS_INPUT_MARKER)) is not None
    ]
    capped: list[tuple[str, int]] = []
    for gate in config.required_gates:
        marker = _rework_marker(gate)
        allowance = _rework_allowance_marker(gate)
        attempts = sum(1 for text in texts if _marker_matches(text, marker))
        granted = sum(1 for text in texts if _marker_matches(text, allowance))
        charged = max(0, attempts - granted)
        if charged >= config.max_rework:
            capped.append((gate, charged))
    if not facts and not capped:
        return []
    if _issue_is_closed(repo_root, issue_id):
        return []
    answered = _answered_asks(repo_root, issue_id)
    violations: list[str] = []
    needs = sum(1 for fact in facts if (_NEEDS_INPUT_KIND, fact) not in answered)
    if needs:
        violations.append(f"{needs} needs-input event(s) recorded on {issue_id}")
    for gate, attempts in capped:
        if (REWORK_ESCALATION_KIND, rework_escalation_question(gate)) in answered:
            continue
        violations.append(
            f"rework escalation on {issue_id} (gate {gate}: {attempts}/{config.max_rework})"
        )
    return violations


def lights_out_violations(
    repo_root: Path,
    root_issue: str,
    config: PolicyConfig,
    *,
    ids: tuple[str, ...] | None = None,
    shipping: str | None = None,
) -> tuple[str, ...]:

    violations: list[str] = []
    gated = shipping or root_issue
    status = gate_status(repo_root, gated, config)
    if not status.can_advance:
        pending = ", ".join((*status.required_failed, *status.required_missing))
        detail = f"required gates not green on {gated}: {pending}"
        if status.disregarded:
            foreign = ", ".join(sorted({v.provider or "(none)" for v in status.disregarded}))
            detail += f" (disregarded a result from provider {foreign}: not the engine's own)"
        violations.append(detail)
    for issue_id in ids if ids is not None else session_issue_ids(repo_root, root_issue):
        violations.extend(_live_session_violations(repo_root, issue_id, config))
    return tuple(violations)


WAIT_MARKER = "[harness-wait]"

WAIT_KINDS = ("checkpoint", "decision")

HUMAN_BY = "human"

_RECONCILED_BY = "engine"


@dataclass(frozen=True)
class WaitEvent:
    wait_id: str
    issue_id: str
    kind: str
    subject: str
    waited_s: int
    answered_by: str
    delegated: bool
    requested_at: str = ""
    answered_at: str = ""


def wait_id_for_checkpoint(issue_id: str, name: str) -> str:

    return f"{issue_id}#wait-{name}"


def _parse_ts(text: str) -> datetime | None:

    try:
        stamp = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _parse_wait_header(text: str) -> tuple[str, bool] | None:
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if not first_line.startswith(WAIT_MARKER):
        return None
    tokens = first_line[len(WAIT_MARKER) :].split()
    fields = dict(token.split("=", 1) for token in tokens if "=" in token)
    wait_id = fields.get("id", "")
    return (wait_id, "answered" in tokens) if wait_id else None


def _parse_wait_event(text: str, issue_id: str) -> WaitEvent | None:

    header = _parse_wait_header(text)
    if header is None or not header[1]:
        return None
    lines = text.strip().splitlines()
    try:
        payload = json.loads("\n".join(lines[1:]) or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    waited = payload.get("waited_s")
    kind = str(payload.get("kind", ""))
    if not isinstance(waited, int) or isinstance(waited, bool) or kind not in WAIT_KINDS:
        return None
    return WaitEvent(
        wait_id=header[0],
        issue_id=issue_id,
        kind=kind,
        subject=str(payload.get("subject", "")),
        waited_s=max(0, waited),
        answered_by=str(payload.get("by", "")),
        delegated=bool(payload.get("delegated", False)),
        requested_at=str(payload.get("requested_at", "")),
        answered_at=str(payload.get("answered_at", "")),
    )


def wait_events(repo_root: Path, issue_id: str) -> tuple[WaitEvent, ...]:
    events = (
        _parse_wait_event(str(comment.get("text", "")), issue_id)
        for comment in _comments(repo_root, issue_id)
    )
    return tuple(event for event in events if event is not None)


def record_wait_request(repo_root: Path, issue_id: str, name: str) -> str | None:

    wait_id = wait_id_for_checkpoint(issue_id, name)
    if _open_wait_stamp(repo_root, issue_id, wait_id) is not None:
        return None
    _add_comment(repo_root, issue_id, f"{WAIT_MARKER} id={wait_id} kind=checkpoint requested")
    return wait_id


def _open_wait_stamp(repo_root: Path, issue_id: str, wait_id: str) -> str | None:

    requested_at: str | None = None
    for comment in _comments(repo_root, issue_id):
        header = _parse_wait_header(str(comment.get("text", "")))
        if header is None or header[0] != wait_id:
            continue
        if header[1]:
            return None
        requested_at = str(comment.get("created_at", "")) or requested_at
    return requested_at


def record_wait(  # noqa: PLR0913 — one parameter per recorded fact
    repo_root: Path,
    issue_id: str,
    *,
    wait_id: str,
    kind: str,
    subject: str,
    requested_at: str,
    by: str,
    delegated: bool,
) -> WaitEvent | None:

    if kind not in WAIT_KINDS:
        raise ValueError(f"unknown wait kind {kind!r}; expected one of {WAIT_KINDS}")
    start = _parse_ts(requested_at)
    if start is None:
        return None
    now = _now()
    waited_s = max(0, int(now - start.timestamp()))
    event = WaitEvent(
        wait_id=wait_id,
        issue_id=issue_id,
        kind=kind,
        subject=subject,
        waited_s=waited_s,
        answered_by=by,
        delegated=delegated,
        requested_at=start.isoformat(),
        answered_at=datetime.fromtimestamp(now, UTC).isoformat(),
    )
    payload = json.dumps(
        {
            "answered_at": event.answered_at,
            "by": by,
            "delegated": delegated,
            "kind": kind,
            "requested_at": event.requested_at,
            "subject": subject,
            "waited_s": waited_s,
        },
        sort_keys=True,
    )
    header = f"{WAIT_MARKER} id={wait_id} kind={kind} answered waited_s={waited_s} by={by}"
    _add_comment(repo_root, issue_id, f"{header}\n{payload}")
    return event


def record_checkpoint_wait(
    repo_root: Path, issue_id: str, name: str, *, by: str, delegated: bool
) -> WaitEvent | None:

    wait_id = wait_id_for_checkpoint(issue_id, name)
    requested_at = _open_wait_stamp(repo_root, issue_id, wait_id)
    if requested_at is None:
        return None
    return record_wait(
        repo_root,
        issue_id,
        wait_id=wait_id,
        kind="checkpoint",
        subject=name,
        requested_at=requested_at,
        by=by,
        delegated=delegated,
    )


@dataclass(frozen=True)
class WaitSummary:
    events: tuple[WaitEvent, ...]
    human_wait_s: int
    delegated_wait_s: int
    dispatch_s: float


def _wall_clock_seconds(events: tuple[WaitEvent, ...]) -> int:

    spans: list[tuple[float, float]] = []
    loose = 0
    for event in events:
        start, end = _parse_ts(event.requested_at), _parse_ts(event.answered_at)
        if start is None or end is None:
            loose += event.waited_s
            continue
        spans.append((start.timestamp(), max(end.timestamp(), start.timestamp())))
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return int(sum(end - start for start, end in merged)) + loose


def session_dispatch_seconds(
    repo_root: Path, root_issue: str, *, ids: tuple[str, ...] | None = None
) -> float:

    records = run_record.load_run_records(repo_root) or {}
    total = 0.0
    for issue_id in ids if ids is not None else session_issue_ids(repo_root, root_issue):
        history = records.get(issue_id)
        if not isinstance(history, list):
            continue
        for entry in history:
            duration = entry.get("duration_s") if isinstance(entry, dict) else None
            if isinstance(duration, int | float) and not isinstance(duration, bool):
                total += float(duration)
    return total


def session_wait_summary(
    repo_root: Path, root_issue: str, *, ids: tuple[str, ...] | None = None
) -> WaitSummary:

    ids = ids if ids is not None else session_issue_ids(repo_root, root_issue)
    events = tuple(event for issue_id in ids for event in wait_events(repo_root, issue_id))
    return WaitSummary(
        events=events,
        human_wait_s=_wall_clock_seconds(tuple(e for e in events if not e.delegated)),
        delegated_wait_s=_wall_clock_seconds(tuple(e for e in events if e.delegated)),
        dispatch_s=session_dispatch_seconds(repo_root, root_issue, ids=ids),
    )


EVIDENCE_MARKER = f"{MARKER} evidence"


@dataclass(frozen=True)
class EvidenceStatus:
    phase: str
    declared: str | None = None
    satisfied: bool = True
    reason: str = ""
    path: Path | None = None


def unknown_evidence_phases(config: PolicyConfig) -> tuple[str, ...]:
    return tuple(sorted(p for p in config.evidence if p not in LOOP_PHASES))


def evidence_status(root: Path, config: PolicyConfig, phase: str) -> EvidenceStatus:

    unknown = unknown_evidence_phases(config)
    if unknown:
        return EvidenceStatus(
            phase,
            config.evidence.get(phase),
            False,
            f"[policy.evidence] names unknown phase(s) {', '.join(unknown)}; every advance "
            f"is refused until they are corrected or removed — expected one of "
            f"{', '.join(LOOP_PHASES)}",
        )
    declared = config.evidence.get(phase)
    if declared is None:
        return EvidenceStatus(phase)
    return _artifact_status(root, phase, declared)


def _artifact_status(root: Path, phase: str, declared: str) -> EvidenceStatus:

    remedy = (
        f"produce it before advancing past {phase!r}, or drop the "
        f"[policy.evidence] {phase} declaration"
    )
    if not declared:
        return EvidenceStatus(
            phase,
            declared,
            False,
            f"[policy.evidence] {phase} declares an empty path; {remedy}",
        )
    candidate = Path(declared)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        return EvidenceStatus(
            phase,
            declared,
            False,
            f"[policy.evidence] {phase} is {declared!r}, an absolute path; an evidence "
            f"artifact must be relative to the checkout so it travels with the repo",
        )
    joined = root / candidate
    base = root.resolve()
    resolved = joined.resolve()
    if resolved != base and base not in resolved.parents:
        return EvidenceStatus(
            phase,
            declared,
            False,
            f"[policy.evidence] {phase} is {declared!r}, which resolves to {resolved}, "
            f"outside the checkout {base}; an evidence artifact must stay inside it",
        )
    if not joined.is_file():
        return EvidenceStatus(
            phase,
            declared,
            False,
            f"declared evidence artifact {declared!r} for phase {phase!r} is not a readable "
            f"file under {root}; {remedy}",
        )
    if joined.stat().st_size == 0:
        return EvidenceStatus(
            phase,
            declared,
            False,
            f"declared evidence artifact {declared!r} for phase {phase!r} is empty; {remedy}",
        )
    return EvidenceStatus(phase, declared, True, "", joined)


def record_evidence(repo_root: Path, issue_id: str, phase: str, declared: str) -> bool:

    body = f"{EVIDENCE_MARKER} phase={phase} path={declared}"
    if any(_marker_matches(text, body) for text in _comment_texts(repo_root, issue_id)):
        return False
    _add_comment(repo_root, issue_id, body)
    return True


SCOPE_VIOLATION_MARKER = f"{MARKER} scope-violation"


def record_scope_violation(
    repo_root: Path,
    issue_id: str,
    paths: Sequence[str],
    colliding: Sequence[str] = (),
) -> bool:

    body = f"{SCOPE_VIOLATION_MARKER} paths={','.join(paths)}"
    if colliding:
        body += f" collides={','.join(colliding)}"
    if any(_marker_matches(text, body) for text in _comment_texts(repo_root, issue_id)):
        return False
    _add_comment(repo_root, issue_id, body)
    return True


def load_policy(repo_root: Path) -> PolicyConfig:
    return load_policy_config(repo_root)
