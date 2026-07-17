"""Behavioral-rubric catalog sources: authoring + selection (basicly-0122).

basicly gates *artifacts* generically (tests/lint pass) and offers an advisory
semantic review, but has no use-case-tied yes/no **behavioral** rubrics — "did
the agent add a regression test for the bug?", "did it address every acceptance
criterion?" (foundry spike Dimension 7). This module owns the whole framework: a
rubric is a catalog source (``*.rubric.yaml``) shaped like the other lightweight
catalog manifests (``hooks.yaml``/``permissions.yaml`` — imperative validation,
no JSON schema), selected for a bead by its work type.

Each rubric lists yes/no ``checks``; a check is either ``deterministic`` (a
command whose exit code answers it — evaluated via the verify runner) or
``judged`` (an agent answers yes/no with evidence — one prompt dispatched through
the agent-agnostic runner). :func:`evaluate` runs both kinds and
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
    if kind == DETERMINISTIC and not command.strip():
        raise ValueError(f"{where} is deterministic but has no 'command' to run")
    if kind == JUDGED and command.strip():
        raise ValueError(f"{where} is judged, so it must not carry a 'command'")
    return RubricCheck(
        id=entry["id"].strip(),
        question=entry["question"].strip(),
        kind=kind,
        command=command.strip(),
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
    """Answer a deterministic check by running its command via the verify runner."""
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
        result = runner.run(spec, build_judge_prompt(issue_id, rubric, judged), repo_root)
        if result.handoff:
            verdicts += [
                CheckVerdict(check.id, JUDGED, UNKNOWN, "handoff: no agent CLI — judge manually")
                for check in judged
            ]
        else:
            verdicts += parse_judged(result.stdout, judged)
    return verdicts


def gate_status(verdicts: list[CheckVerdict]) -> str:
    """Deterministic-first: fail only when an objective (deterministic) check says no.

    A judged verdict — subjective and agent-answered — is surfaced but never fails
    the gate, honoring "a subjective judged check must not silently block a merge".
    """
    return "fail" if any(v.kind == DETERMINISTIC and v.answer == NO for v in verdicts) else "pass"


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
