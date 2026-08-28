"""Behavioral-rubric catalog sources: authoring + selection (basicly-0122).

basicly gates *artifacts* generically (tests/lint pass) and offers an advisory
semantic review, but has no use-case-tied yes/no **behavioral** rubrics — "did
the agent add a regression test for the bug?", "did it address every acceptance
criterion?" (foundry spike Dimension 7). This module owns the whole framework: a
rubric is a catalog source (``*.rubric.yaml``) shaped like the other lightweight
catalog manifests (``hooks.yaml``/``permissions.yaml`` — imperative validation,
no JSON schema), selected for a bead by its work type.

Each rubric lists yes/no ``checks``; a check is either ``deterministic`` —
answered mechanically, either by a ``verify_mode`` (the consumer repo's own
configured verify checks, the portable form a shipped rubric uses) or by an
explicit ``command``'s exit code — or ``judged`` (an agent answers yes/no with
evidence — one prompt dispatched through the agent-agnostic runner). Every
rubric must carry at least one deterministic check, or its gate could never fail
(basicly-kjc5.19). :func:`evaluate` runs both kinds and
:func:`report_gate` records the outcome as an advisory ``rubric`` gate:
deterministic-first, so a subjective judged verdict is surfaced but never fails
the gate, and the gate is non-required (advisory) until a consumer promotes it.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import review, run_record, runner, tracker, verify
from .catalog import bundled_catalog_root
from .config import RUBRIC_GATE_PROVIDER, RunnerConfig, VerifyCheck, load_runner_config

RUBRICS_DIRNAME = "rubrics"
RUBRIC_GLOB = "*.rubric.yaml"

# Check kinds.
DETERMINISTIC = "deterministic"
JUDGED = "judged"
CHECK_KINDS = (DETERMINISTIC, JUDGED)

# Verify modes a deterministic check may delegate to (mirrors [verify] config).
VERIFY_MODES = ("fast", "full", "staged")

# The advisory gate this framework reports; non-required by default (the gate
# ledger treats any gate outside [policy] required_gates as advisory), so a
# consumer promotes it by adding "rubric" to required_gates.
# Validate is a **composite of two gates with different types**, recorded
# separately (amending factory-design D4).
#
# The pre-flight half keeps the original gate name, because it is the half that
# behaves the way a gate is assumed to: deterministic, objective, and able to fail
# the lane. It is what the lane-level promotion to required means.
RUBRIC_GATE = "rubric"
# The escalation half. It records a real ``fail`` when a judged check answers no —
# which the single combined gate could not, since a judged no left the gate
# reading ``pass`` and survived only as text in the note. What stops that fail
# from killing the lane is the gate's *type*, not a special case at the call
# site: an escalation gate enqueues a decision and the lane holds. It is
# deliberately absent from ``[policy] required_gates``, which is what keeps R4's
# "no persona passes or fails a required gate" intact while the required
# pre-flight half still has teeth.
#
# Before the split, D4 promoted one gate to required whose judged checks could
# not fail it — so it could pass having checked nothing.
RUBRIC_JUDGED_GATE = "rubric-judged"
# Single-sourced in config so policy.gate_status can recognise it as engine-owned
# without importing this module — which is what keeps the promotion of
# RUBRIC_GATE to required satisfiable (basicly-jr0l.51).
GATE_PROVIDER = RUBRIC_GATE_PROVIDER

# Answers for a check verdict.
YES = "yes"
NO = "no"
UNKNOWN = "unknown"

# Severity vocabulary for a judged finding.
# Deliberately small: two named classes is gsd-core's choice and three is ours
# only because §4.3 needs a class explicitly excluded from the rework loop.
BLOCKER = "BLOCKER"  # the goal is not achieved unless this is fixed
IMPORTANT = "IMPORTANT"  # fix before landing
MINOR = "MINOR"  # record, do not loop
SEVERITIES = (BLOCKER, IMPORTANT, MINOR)

# How many times the judge is asked. A malformed reply is rejected and
# re-requested exactly once, the way unparseable JSON would be; a second
# violation is an agent that cannot meet the contract, and asking a third time
# spends tokens to learn the same thing.
JUDGE_ATTEMPTS = 2


@dataclass(frozen=True)
class RubricCheck:
    """One yes/no behavioral check within a rubric."""

    id: str
    question: str
    kind: str
    # For a deterministic check: the command whose exit code answers the question
    # (0 = yes/pass). Empty for a judged check.
    command: str = ""
    # The portable alternative to ``command`` for a deterministic check: run the
    # *consumer's* configured verify checks for this mode. A rubric ships in the
    # core catalog to every consumer repo, so a hardcoded command would bind the
    # check to one toolchain; a mode binds it to whatever that repo configured.
    verify_mode: str = ""


@dataclass(frozen=True)
class Rubric:
    """A work-type-tied set of behavioral checks."""

    id: str
    description: str
    applies_to: tuple[str, ...]
    checks: tuple[RubricCheck, ...]


def _catalog_rubrics_dir() -> Path:
    return bundled_catalog_root() / RUBRICS_DIRNAME


def _parse_check(entry: object, where: str) -> RubricCheck:
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a mapping")
    for key in ("id", "question", "kind"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ValueError(f"{where} is missing a non-empty {key!r}")
    kind = entry["kind"].strip()
    if kind not in CHECK_KINDS:
        raise ValueError(f"{where} has unknown kind {kind!r}; allowed: {list(CHECK_KINDS)}")
    command = entry.get("command", "")
    if not isinstance(command, str):
        raise ValueError(f"{where} 'command' must be a string")
    verify_mode = entry.get("verify_mode", "")
    if not isinstance(verify_mode, str):
        raise ValueError(f"{where} 'verify_mode' must be a string")
    command, verify_mode = command.strip(), verify_mode.strip()
    if kind == DETERMINISTIC:
        if bool(command) == bool(verify_mode):
            raise ValueError(
                f"{where} is deterministic, so it needs exactly one of 'command' or "
                f"'verify_mode' (got {'both' if command else 'neither'})"
            )
        if verify_mode and verify_mode not in VERIFY_MODES:
            raise ValueError(
                f"{where} has unknown verify_mode {verify_mode!r}; allowed: {list(VERIFY_MODES)}"
            )
    if kind == JUDGED and (command or verify_mode):
        raise ValueError(f"{where} is judged, so it must not carry a 'command' or a 'verify_mode'")
    return RubricCheck(
        id=entry["id"].strip(),
        question=entry["question"].strip(),
        kind=kind,
        command=command,
        verify_mode=verify_mode,
    )


def _parse_rubric(data: object, path: Path) -> Rubric:
    if not isinstance(data, dict):
        raise ValueError(f"{path}: rubric must be a mapping")
    for key in ("id", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{path}: rubric is missing a non-empty {key!r}")
    applies_to = data.get("applies_to")
    if not (isinstance(applies_to, list) and applies_to) or not all(
        isinstance(item, str) and item.strip() for item in applies_to
    ):
        raise ValueError(f"{path}: 'applies_to' must be a non-empty list of work-type strings")
    raw_checks = data.get("checks")
    if not (isinstance(raw_checks, list) and raw_checks):
        raise ValueError(f"{path}: 'checks' must be a non-empty list")
    checks = tuple(
        _parse_check(entry, f"{path}: check[{index}]") for index, entry in enumerate(raw_checks)
    )
    # A judged-only rubric cannot fail its gate (gate_status is deterministic-first),
    # so promoting it to required buys nothing and reads as green having proved
    # nothing — worse than staying advisory. Refuse it at authoring time.
    if not any(check.kind == DETERMINISTIC for check in checks):
        raise ValueError(
            f"{path}: rubric has no deterministic check, so its gate could never fail; "
            "add one (a 'verify_mode' check is the portable form)"
        )
    return Rubric(
        id=data["id"].strip(),
        description=data["description"].strip(),
        applies_to=tuple(item.strip() for item in applies_to),
        checks=checks,
    )


def load_rubrics(rubrics_dir: Path | None = None) -> list[Rubric]:
    """Load and validate every ``*.rubric.yaml`` in the given (or bundled) dir.

    Validated imperatively (the lightweight ``hooks.yaml`` pattern, no JSON
    schema). A missing directory yields no rubrics; a malformed file raises.
    """
    rubrics_dir = rubrics_dir or _catalog_rubrics_dir()
    if not rubrics_dir.is_dir():
        return []
    rubrics: list[Rubric] = []
    for path in sorted(rubrics_dir.glob(RUBRIC_GLOB)):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rubrics.append(_parse_rubric(data, path))
    return rubrics


def select_rubrics(rubrics: list[Rubric], work_type: str) -> list[Rubric]:
    """The rubrics whose ``applies_to`` includes *work_type*, in load order."""
    return [rubric for rubric in rubrics if work_type in rubric.applies_to]


# --- Evaluation (deterministic first, judged second) ------------------------


class JudgedSchemaError(ValueError):
    """Judged output that violates the verdict contract (§5.4).

    A schema violation, not a quality complaint: the engine rejects and
    re-requests the reply exactly as it would reject unparseable JSON, rather
    than accepting a finding it cannot classify and noting the gap in a string
    nothing reads.
    """

    def __init__(self, violations: Sequence[str]) -> None:
        """Reject the reply, naming every violation so one re-request can fix all of them."""
        self.violations = tuple(violations)
        super().__init__("judged output rejected: " + "; ".join(self.violations))


@dataclass(frozen=True)
class CheckVerdict:
    """The outcome of evaluating one check: yes/no/unknown with evidence.

    A judged ``NO`` is a *finding*, and §5.4 makes its severity a required field
    — so the invariant lives on the record itself rather than at whichever call
    site happens to build one. There is no path to a severity-less judged
    finding: it cannot be parsed, constructed, or recorded.

    The other three shapes carry no severity. A ``YES`` and an ``UNKNOWN`` report
    no finding, so there is nothing to classify, and a deterministic ``NO`` is a
    test failure whose consequence comes from its gate's type, not from a model's
    opinion of how bad it is.
    """

    check_id: str
    kind: str
    answer: str  # YES | NO | UNKNOWN
    evidence: str = ""
    severity: str = ""  # one of SEVERITIES; required on (and only on) a judged NO

    def __post_init__(self) -> None:
        """Enforce §5.4 structurally: a judged finding carries a severity, nothing else does."""
        if self.kind == JUDGED and self.answer == NO:
            if self.severity not in SEVERITIES:
                raise JudgedSchemaError([
                    f"{self.check_id}: a judged 'no' is a finding and needs a severity "
                    f"({'/'.join(SEVERITIES)}); got {self.severity or 'none'}"
                ])
        elif self.severity:
            raise JudgedSchemaError([
                f"{self.check_id}: only a judged 'no' carries a severity; "
                f"got {self.severity!r} on a {self.kind} {self.answer}"
            ])


def evaluate_deterministic(check: RubricCheck, repo_root: Path) -> CheckVerdict:
    """Answer a deterministic check: the repo's verify checks, or an explicit command."""
    if check.verify_mode:
        report = verify.run_verify(repo_root, check.verify_mode)
        answer = YES if report.passed else NO
        detail = "all checks passed" if report.passed else f"failed: {', '.join(report.failures)}"
        return CheckVerdict(
            check.id, DETERMINISTIC, answer, f"verify {check.verify_mode}: {detail}"
        )
    vcheck = VerifyCheck(
        name=check.id, command=tuple(shlex.split(check.command)), modes=frozenset({"full"})
    )
    result = verify.run_check(vcheck, repo_root, "full")
    answer = YES if result.status == "pass" else NO
    return CheckVerdict(check.id, DETERMINISTIC, answer, f"command exited {result.returncode}")


def build_judge_prompt(issue_id: str, rubric: Rubric, checks: list[RubricCheck]) -> str:
    """Assemble the yes/no prompt an agent answers for the judged checks.

    The severity vocabulary is spelled out with what each class *means*, because
    the contract is enforced (§5.4): a ``no`` without one is rejected and
    re-requested, so a prompt that left the vocabulary implicit would buy a
    guaranteed second dispatch.

    Linted before it is returned, like every other reviewer bundle
    (:func:`basicly.review.reject_pre_judging`) — a rubric's ``question`` is
    catalog content, and a check that told the judge what not to find would
    otherwise reach the agent as an instruction.
    """
    lines = [
        f"You are evaluating the committed work for issue {issue_id} against the "
        f"'{rubric.id}' behavioral rubric.",
        "Inspect the repository's changes and answer each check below.",
        "Reply with one line per check, in EXACTLY one of these two formats:",
        "    <check-id>: yes - <one concise sentence of evidence>",
        "    <check-id>: no - <SEVERITY> - <one concise sentence of evidence>",
        "",
        "A 'no' is a finding and MUST carry a severity. Use exactly one of:",
        f"    {BLOCKER} - the goal is not achieved unless this is fixed",
        f"    {IMPORTANT} - fix before landing",
        f"    {MINOR} - record it, but it is not worth another round",
        "A 'no' with no severity is not a valid answer; it is rejected unread.",
        "",
        "Checks:",
        *[f"- {check.id}: {check.question}" for check in checks],
        "",
    ]
    prompt = "\n".join(lines)
    review.reject_pre_judging(prompt)
    return prompt


def rerequest_judge_prompt(prompt: str, rejected: JudgedSchemaError) -> str:
    """The re-request: the original prompt plus what was wrong with the last reply."""
    return (
        f"{prompt}\nYour previous reply was rejected as malformed and none of it was "
        f"read: {'; '.join(rejected.violations)}. Answer again in the exact format above."
    )


# The severity token is recognised only when a separator follows it, so evidence
# that merely opens with the word ("no - MINOR issue in the helper") keeps its
# first word instead of having it silently reclassified as the severity field.
_JUDGED_LINE = re.compile(
    r"\s*([A-Za-z0-9_-]+)\s*:\s*(yes|no)\b[ \t]*[-—:]?[ \t]*"
    r"(?:(BLOCKER|IMPORTANT|MINOR)[ \t]*(?:[-—:][ \t]*|$))?"
    r"(.*)",
    re.IGNORECASE,
)


def parse_judged(stdout: str, checks: list[RubricCheck]) -> list[CheckVerdict]:
    """Parse the agent's answer lines into verdicts, or reject the whole reply.

    A check with no parseable answer is ``UNKNOWN`` (advisory — a judged verdict
    is never treated as a hard failure; see :func:`gate_status`). A check the
    agent *did* answer ``no`` for without a severity is different in kind: the
    contract was violated rather than unmet, so this raises
    :class:`JudgedSchemaError` listing every violation at once, and the caller
    re-requests (§5.4). Silence is an absence of judgment; a severity-less
    finding is malformed output.

    Takes the agent's *own* text: a metered dispatch wraps that in a usage
    envelope whose lines all start with ``{``, and none of them match the answer
    pattern, so the caller unwraps it first with
    :func:`basicly.runner.result_text` (basicly-gczc).
    """
    answered: dict[str, tuple[str, str, str]] = {}
    for line in stdout.splitlines():
        match = _JUDGED_LINE.match(line)
        if match:
            severity = (match.group(3) or "").upper()
            answered[match.group(1)] = (match.group(2).lower(), severity, match.group(4).strip())
    violations = [
        f"{check.id}: answered 'no' with no severity ({'/'.join(SEVERITIES)})"
        for check in checks
        if answered.get(check.id, ("", "", ""))[:2] == (NO, "")
    ]
    if violations:
        raise JudgedSchemaError(violations)
    verdicts: list[CheckVerdict] = []
    for check in checks:
        answer, severity, evidence = answered.get(check.id, (UNKNOWN, "", "no parseable answer"))
        verdicts.append(CheckVerdict(check.id, JUDGED, answer, evidence, severity))
    return verdicts


def evaluate(
    issue_id: str, rubric: Rubric, repo_root: Path, runner_name: str | None = None
) -> list[CheckVerdict]:
    """Evaluate every check in *rubric*: deterministic by command, judged by agent.

    Judged checks go through the agent-agnostic runner (:func:`_judge`); when no
    agent CLI is available (a handoff) they resolve to UNKNOWN so the caller can
    surface them for a human, never silently passing or failing.

    A timed-out judge resolves every judged check to UNKNOWN rather than NO: no
    agent answered, and inventing a failure would enqueue a dispute nobody made.
    A reply the severity contract rejects (§5.4) resolves the same way once the
    re-request is also refused.
    """
    verdicts = [
        evaluate_deterministic(check, repo_root)
        for check in rubric.checks
        if check.kind == DETERMINISTIC
    ]
    if any(check.kind == JUDGED for check in rubric.checks):
        config = load_runner_config(repo_root)
        spec = runner.select_runner(config.specs, runner_name or config.default)
        verdicts += _judge(issue_id, rubric, repo_root, spec, config)
    return verdicts


def _dispatch_judge(
    issue_id: str, repo_root: Path, spec: runner.RunnerSpec, prompt: str, timeout: float
) -> runner.RunResult:
    """Run one judge dispatch and record it — every attempt, not just the first.

    The judged dispatch obeys ``[runner] runner_timeout`` and writes a run-record
    like every other dispatch (basicly-kjc5.31): a hung judge used to hang the
    whole pass, and its tokens never reached the session's spend. It sets
    ``capture_usage`` so the record carries the adapter's own numbers rather than
    the flagged chars/4 estimate — which
    :func:`basicly.policy.session_spend` counts as an *unmeterable* dispatch, and
    one of those zeroes the grant's remaining budget (basicly-gczc). That is also
    why a re-requested judge comes back through here rather than calling the
    runner directly: a retry that skipped the record would halt the very session
    it was added to rescue.

    That flag switches some adapters' stdout to a usage envelope
    :func:`parse_judged` cannot read, which is why it stayed unset until the
    envelope could be undone: the answer is now read back through
    :func:`basicly.runner.result_text` instead of off raw stdout, so the claude and
    codex arms keep their reply *and* their measurement. copilot needed neither —
    it measures out of band from its own session store (basicly-2rn9) and its
    stdout was plain text all along.
    """
    # The judge is a read-only helper: it queues on the best-effort remainder
    # rather than taking a slot a lane or the decider is reserved.
    with runner.process_budget().slot(runner.HELPER):
        result = runner.run(spec, prompt, repo_root, capture_usage=True, timeout=timeout)
    runner.record_dispatch(
        repo_root,
        issue_id,
        spec,
        result,
        prompt=prompt,
        phase=run_record.VALIDATE_PHASE,
    )
    return result


def _judge(
    issue_id: str,
    rubric: Rubric,
    repo_root: Path,
    spec: runner.RunnerSpec,
    config: RunnerConfig,
) -> list[CheckVerdict]:
    """Ask the judge, rejecting and re-requesting a reply that breaks the contract.

    Three outcomes, all of them UNKNOWN-safe: a parseable reply becomes verdicts;
    a handoff or timeout resolves to UNKNOWN because no agent answered; and a
    reply :func:`parse_judged` rejects is re-requested once with the violation
    named, then resolves to UNKNOWN if the second reply is malformed too.

    UNKNOWN is the right floor for a rejection as well as for silence. Inventing
    a NO would enqueue a dispute the agent never made, and accepting the
    unclassifiable finding is exactly the "complain about it" behaviour §5.4
    replaces — so the rejection survives on the verdict's evidence, where the
    gate record and the operator can both see it, rather than as a verdict.
    """
    judged = [check for check in rubric.checks if check.kind == JUDGED]
    timeout = config.runner_timeout
    prompt = build_judge_prompt(issue_id, rubric, judged)
    attempt_prompt, rejection = prompt, ""
    for _attempt in range(JUDGE_ATTEMPTS):
        result = _dispatch_judge(issue_id, repo_root, spec, attempt_prompt, timeout)
        if result.handoff or result.timed_out:
            why = (
                f"timed out after {timeout:.0f}s — judge manually"
                if result.timed_out
                else "handoff: no agent CLI — judge manually"
            )
            return [CheckVerdict(check.id, JUDGED, UNKNOWN, why) for check in judged]
        try:
            return parse_judged(runner.result_text(spec, result.stdout), judged)
        except JudgedSchemaError as exc:
            rejection = "; ".join(exc.violations)
            attempt_prompt = rerequest_judge_prompt(prompt, exc)
    return [
        CheckVerdict(check.id, JUDGED, UNKNOWN, f"reply rejected as malformed: {rejection}")
        for check in judged
    ]


def gate_status(verdicts: list[CheckVerdict]) -> str:
    """The **pre-flight** half's status: fail only when a deterministic check says no.

    A judged verdict never fails this gate — it is reported on
    :data:`RUBRIC_JUDGED_GATE` instead, which is what "a subjective judged check
    must not silently block a merge" means once the two halves are separated.
    """
    return "fail" if any(v.kind == DETERMINISTIC and v.answer == NO for v in verdicts) else "pass"


def escalation_status(verdicts: list[CheckVerdict]) -> str:
    """The **escalation** half's status: fail when a judged check answers no.

    This is allowed to say ``fail`` precisely because the gate is not required:
    the fail is the *signal* that a decision was enqueued, not a verdict that the
    lane is broken. Recording it honestly is the point of the split — a judged no
    used to leave the combined gate reading ``pass``, so a reader of the gate
    record could not tell a satisfied acceptance criterion from a disputed one.

    ``UNKNOWN`` is not a failure: it means no agent answered (a handoff, or an
    unparseable reply), which is an absence of judgment rather than a negative
    one.
    """
    return "fail" if judged_failures(verdicts) else "pass"


def judged_failures(verdicts: list[CheckVerdict]) -> list[CheckVerdict]:
    """The judged checks that answered no.

    These never fail the gate (:func:`gate_status` stays deterministic-first), but
    D4 as amended routes them to the decision queue rather than discarding them:
    an unsatisfied acceptance criterion is a decision, not a test failure. An
    UNKNOWN verdict is not a failure — it means no agent answered (a handoff).
    """
    return [v for v in verdicts if v.kind == JUDGED and v.answer == NO]


def _report_one(
    repo_root: Path, issue_id: str, gate: str, status: str, note: str
) -> tuple[bool, str]:
    """Record one gate through the write seam; a refusal carries its own reason out.

    Not swallowed into a bare False: a cause-less "gate not recorded" strands whoever has
    to explain the gap in the gate list. The message carries the verb: a ledger already
    holding the row appends nothing (basicly-wu4w8v).
    """
    args = ["gate", "report", "--gate", gate, "--provider", GATE_PROVIDER]
    args += ["--status", status, "--note", note, issue_id]
    try:
        appended = tracker.write(repo_root, args)
    except RuntimeError as exc:
        return False, f"{gate} gate not recorded: {exc}"
    return True, f"recorded {gate}={status}" if appended else f"{gate}={status} already held"


def report_gate(repo_root: Path, issue_id: str, verdicts: list[CheckVerdict]) -> tuple[bool, str]:
    """Record validate as its two separately-typed gates (§4.1, amending D4).

    Both halves are always recorded, including when a half has no checks of its
    kind. A gate that appears only when it has something to say is unreadable
    after the fact: a missing ``rubric-judged`` would be ambiguous between "no
    judged checks existed" and "the judged half never ran", and only one of those
    is fine.

    The pre-flight half is reported first, so if br fails midway the required half
    is the one that survives. Returns ok=False when *either* report failed, with
    every outcome in the message — a partial record is worse than a clear failure,
    because the gate list would then look authoritative while describing half the
    step.
    """
    deterministic = [v for v in verdicts if v.kind == DETERMINISTIC]
    judged = [v for v in verdicts if v.kind == JUDGED]

    def detail(subset: list[CheckVerdict]) -> str:
        # The severity rides onto the record with the answer. A gate note reading
        # only "j=no" makes a MINOR and a BLOCKER indistinguishable to whoever
        # disposes of the decision, which is the whole point of requiring it.
        return (
            ", ".join(
                f"{v.check_id}={v.answer}" + (f" ({v.severity})" if v.severity else "")
                for v in subset
            )
            or "no checks"
        )

    preflight_ok, preflight_msg = _report_one(
        repo_root,
        issue_id,
        RUBRIC_GATE,
        gate_status(verdicts),
        f"rubric pre-flight (deterministic): {detail(deterministic)}",
    )
    escalation = escalation_status(verdicts)
    escalation_ok, escalation_msg = _report_one(
        repo_root,
        issue_id,
        RUBRIC_JUDGED_GATE,
        escalation,
        # Spelled out on the record because a bare `fail` on this gate invites
        # exactly the misreading the split exists to prevent.
        f"rubric escalation (judged, never fails the lane): {detail(judged)}"
        + ("; enqueued as a decision" if escalation == "fail" else ""),
    )
    if not (preflight_ok and escalation_ok):
        return False, f"{preflight_msg}; {escalation_msg}"
    return True, f"{preflight_msg}, {escalation_msg} on {issue_id}"
