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
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import br, runner, verify
from .catalog import bundled_catalog_root
from .config import VerifyCheck, load_runner_config

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
RUBRIC_GATE = "rubric"
GATE_PROVIDER = "basicly-rubric"

# Answers for a check verdict.
YES = "yes"
NO = "no"
UNKNOWN = "unknown"


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


@dataclass(frozen=True)
class CheckVerdict:
    """The outcome of evaluating one check: yes/no/unknown with evidence."""

    check_id: str
    kind: str
    answer: str  # YES | NO | UNKNOWN
    evidence: str = ""


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
    """Assemble the yes/no prompt an agent answers for the judged checks."""
    lines = [
        f"You are evaluating the committed work for issue {issue_id} against the "
        f"'{rubric.id}' behavioral rubric.",
        "Inspect the repository's changes and answer each check below.",
        "Reply with one line per check, in EXACTLY this format:",
        "    <check-id>: yes|no - <one concise sentence of evidence>",
        "",
        "Checks:",
        *[f"- {check.id}: {check.question}" for check in checks],
        "",
    ]
    return "\n".join(lines)


_JUDGED_LINE = re.compile(r"\s*([A-Za-z0-9_-]+)\s*:\s*(yes|no)\b[ \t]*[-—:]?\s*(.*)", re.IGNORECASE)


def parse_judged(stdout: str, checks: list[RubricCheck]) -> list[CheckVerdict]:
    """Parse the agent's ``<id>: yes|no - evidence`` lines into verdicts.

    A check with no parseable answer is ``UNKNOWN`` (advisory — a judged verdict
    is never treated as a hard failure; see :func:`gate_status`).
    """
    answered: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        match = _JUDGED_LINE.match(line)
        if match:
            answered[match.group(1)] = (match.group(2).lower(), match.group(3).strip())
    verdicts: list[CheckVerdict] = []
    for check in checks:
        answer, evidence = answered.get(check.id, (UNKNOWN, "no parseable answer"))
        verdicts.append(CheckVerdict(check.id, JUDGED, answer, evidence))
    return verdicts


def evaluate(
    issue_id: str, rubric: Rubric, repo_root: Path, runner_name: str | None = None
) -> list[CheckVerdict]:
    """Evaluate every check in *rubric*: deterministic by command, judged by agent.

    Judged checks dispatch one prompt through the agent-agnostic runner; when no
    agent CLI is available (a handoff) they resolve to UNKNOWN so the caller can
    surface them for a human, never silently passing or failing.

    The judged dispatch obeys ``[runner] runner_timeout`` and writes a run-record
    like every other dispatch (basicly-kjc5.31): a hung judge used to hang the
    whole pass, and its tokens never reached the session's spend. It deliberately
    does *not* set ``capture_usage`` — that switches some adapters' stdout to JSON,
    which :func:`parse_judged` cannot read — so the record carries the flagged
    chars/4 estimate instead, the same honest fallback the copilot arm uses.

    A timed-out judge resolves every judged check to UNKNOWN rather than NO: no
    agent answered, and inventing a failure would enqueue a dispute nobody made.
    """
    verdicts = [
        evaluate_deterministic(check, repo_root)
        for check in rubric.checks
        if check.kind == DETERMINISTIC
    ]
    judged = [check for check in rubric.checks if check.kind == JUDGED]
    if judged:
        config = load_runner_config(repo_root)
        spec = runner.select_runner(config.specs, runner_name or config.default)
        result = runner.run(
            spec,
            build_judge_prompt(issue_id, rubric, judged),
            repo_root,
            timeout=config.runner_timeout,
        )
        runner.record_dispatch(repo_root, issue_id, spec, result)
        if result.handoff or result.timed_out:
            why = (
                f"timed out after {config.runner_timeout:.0f}s — judge manually"
                if result.timed_out
                else "handoff: no agent CLI — judge manually"
            )
            verdicts += [CheckVerdict(check.id, JUDGED, UNKNOWN, why) for check in judged]
        else:
            verdicts += parse_judged(result.stdout, judged)
    return verdicts


def gate_status(verdicts: list[CheckVerdict]) -> str:
    """Deterministic-first: fail only when an objective (deterministic) check says no.

    A judged verdict — subjective and agent-answered — is surfaced but never fails
    the gate, honoring "a subjective judged check must not silently block a merge".
    """
    return "fail" if any(v.kind == DETERMINISTIC and v.answer == NO for v in verdicts) else "pass"


def judged_failures(verdicts: list[CheckVerdict]) -> list[CheckVerdict]:
    """The judged checks that answered no.

    These never fail the gate (:func:`gate_status` stays deterministic-first), but
    D4 as amended routes them to the decision queue rather than discarding them:
    an unsatisfied acceptance criterion is a decision, not a test failure. An
    UNKNOWN verdict is not a failure — it means no agent answered (a handoff).
    """
    return [v for v in verdicts if v.kind == JUDGED and v.answer == NO]


def report_gate(repo_root: Path, issue_id: str, verdicts: list[CheckVerdict]) -> tuple[bool, str]:
    """Record the advisory ``rubric`` gate via ``br gate report`` (degrades gracefully)."""
    status = gate_status(verdicts)
    detail = ", ".join(f"{v.check_id}={v.answer}" for v in verdicts) or "no checks"
    proc = br.try_run_br(
        repo_root,
        [
            "gate",
            "report",
            "--gate",
            RUBRIC_GATE,
            "--provider",
            GATE_PROVIDER,
            "--status",
            status,
            "--note",
            f"rubric: {detail}",
            issue_id,
        ],
    )
    if proc is None:
        return False, "br not on PATH; rubric gate not recorded"
    if proc.returncode != 0:
        return False, f"br gate report failed: {(proc.stderr or proc.stdout).strip()}"
    return True, f"recorded gate {RUBRIC_GATE}={status} on {issue_id}"
