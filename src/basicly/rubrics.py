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

DETERMINISTIC = "deterministic"
JUDGED = "judged"
CHECK_KINDS = (DETERMINISTIC, JUDGED)

VERIFY_MODES = ("fast", "full", "staged")

RUBRIC_GATE = "rubric"
RUBRIC_JUDGED_GATE = "rubric-judged"
GATE_PROVIDER = RUBRIC_GATE_PROVIDER

YES = "yes"
NO = "no"
UNKNOWN = "unknown"

BLOCKER = "BLOCKER"
IMPORTANT = "IMPORTANT"
MINOR = "MINOR"
SEVERITIES = (BLOCKER, IMPORTANT, MINOR)

JUDGE_ATTEMPTS = 2


@dataclass(frozen=True)
class RubricCheck:
    id: str
    question: str
    kind: str
    command: str = ""
    verify_mode: str = ""


@dataclass(frozen=True)
class Rubric:
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

    rubrics_dir = rubrics_dir or _catalog_rubrics_dir()
    if not rubrics_dir.is_dir():
        return []
    rubrics: list[Rubric] = []
    for path in sorted(rubrics_dir.glob(RUBRIC_GLOB)):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rubrics.append(_parse_rubric(data, path))
    return rubrics


def select_rubrics(rubrics: list[Rubric], work_type: str) -> list[Rubric]:
    return [rubric for rubric in rubrics if work_type in rubric.applies_to]


class JudgedSchemaError(ValueError):
    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = tuple(violations)
        super().__init__("judged output rejected: " + "; ".join(self.violations))


@dataclass(frozen=True)
class CheckVerdict:
    check_id: str
    kind: str
    answer: str
    evidence: str = ""
    severity: str = ""

    def __post_init__(self) -> None:
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
    return (
        f"{prompt}\nYour previous reply was rejected as malformed and none of it was "
        f"read: {'; '.join(rejected.violations)}. Answer again in the exact format above."
    )


_JUDGED_LINE = re.compile(
    r"\s*([A-Za-z0-9_-]+)\s*:\s*(yes|no)\b[ \t]*[-—:]?[ \t]*"
    r"(?:(BLOCKER|IMPORTANT|MINOR)[ \t]*(?:[-—:][ \t]*|$))?"
    r"(.*)",
    re.IGNORECASE,
)


def parse_judged(stdout: str, checks: list[RubricCheck]) -> list[CheckVerdict]:

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

    return "fail" if any(v.kind == DETERMINISTIC and v.answer == NO for v in verdicts) else "pass"


def escalation_status(verdicts: list[CheckVerdict]) -> str:

    return "fail" if judged_failures(verdicts) else "pass"


def judged_failures(verdicts: list[CheckVerdict]) -> list[CheckVerdict]:

    return [v for v in verdicts if v.kind == JUDGED and v.answer == NO]


def _report_one(
    repo_root: Path, issue_id: str, gate: str, status: str, note: str
) -> tuple[bool, str]:

    args = ["gate", "report", "--gate", gate, "--provider", GATE_PROVIDER]
    args += ["--status", status, "--note", note, issue_id]
    try:
        appended = tracker.write(repo_root, args)
    except RuntimeError as exc:
        return False, f"{gate} gate not recorded: {exc}"
    return True, f"recorded {gate}={status}" if appended else f"{gate}={status} already held"


def report_gate(repo_root: Path, issue_id: str, verdicts: list[CheckVerdict]) -> tuple[bool, str]:

    deterministic = [v for v in verdicts if v.kind == DETERMINISTIC]
    judged = [v for v in verdicts if v.kind == JUDGED]

    def detail(subset: list[CheckVerdict]) -> str:
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
        f"rubric escalation (judged, never fails the lane): {detail(judged)}"
        + ("; enqueued as a decision" if escalation == "fail" else ""),
    )
    if not (preflight_ok and escalation_ok):
        return False, f"{preflight_msg}; {escalation_msg}"
    return True, f"{preflight_msg}, {escalation_msg} on {issue_id}"
