from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import replace
from typing import TYPE_CHECKING

from . import decisions, integrity, policy
from .config import VERIFY_GATE_PROVIDER
from .dispatch_brief import VERDICT_PREFIX
from .integrity import VALIDATE_GATE
from .tracker import read_comments as _read_comments
from .tracker import write as _write

if TYPE_CHECKING:
    from pathlib import Path

    from .config import PolicyConfig

_LEVEL_FIELD = "level="

VALIDATE_DECISION_KIND = "validate"

_MARKUP = re.compile(r"[*_`]+")
_MARKER = re.compile(r"^[#>\-+\s]+")


def _texts(repo_root: Path, issue_id: str) -> Iterator[str]:
    return (str(comment.get("text", "")) for comment in _read_comments(repo_root, issue_id))


def level_in(texts: Iterable[str]) -> str | None:

    level: str | None = None
    for body in texts:
        text = body.strip()
        if not text.startswith(integrity.CLASSIFICATION_MARKER):
            continue
        for field in text.split():
            if field.startswith(_LEVEL_FIELD):
                level = field[len(_LEVEL_FIELD) :]
    return level if level in integrity.LEVELS else None


def requires_validation(level: str | None) -> bool:
    if level is None:
        return False
    return VALIDATE_GATE in integrity.selection_for(level).gates


def required_in(texts: Iterable[str], config: PolicyConfig) -> PolicyConfig:

    if not requires_validation(level_in(texts)):
        return config
    if VALIDATE_GATE in config.required_gates:
        return config
    return replace(config, required_gates=(*config.required_gates, VALIDATE_GATE))


def required_config(repo_root: Path, issue_id: str, config: PolicyConfig) -> PolicyConfig:
    return required_in(_texts(repo_root, issue_id), config)


def outstanding(gates: policy.GateStatus) -> bool:

    return VALIDATE_GATE in gates.required_failed or VALIDATE_GATE in gates.required_missing


def has_foreign_result(gates: policy.GateStatus) -> bool:
    return any(v.gate == VALIDATE_GATE for v in gates.disregarded)


def refusal_reason(gates: policy.GateStatus) -> str:

    foreign = sorted({v.provider or "(none)" for v in gates.disregarded if v.gate == VALIDATE_GATE})
    if foreign:
        return (
            f"{VALIDATE_GATE} has no engine result: a result from provider "
            f"{', '.join(foreign)} was disregarded because a required gate counts only "
            "the engine's own — re-run the validation through the harness"
        )
    return (
        f"{VALIDATE_GATE} is required at the recorded integrity level and has no engine "
        "result: exercise the change as a consumer would (the validate-as-consumer "
        "skill), then record the gate"
    )


def queue_unreadable_verdict(repo_root: Path, issue_id: str, reply: str) -> str:

    decisions.enqueue(
        repo_root,
        issue_id,
        VALIDATE_DECISION_KIND,
        f"the validator for {issue_id} ran and its reply carries no "
        f"`{VERDICT_PREFIX} PASS`/`{VERDICT_PREFIX} FAIL` line, so {VALIDATE_GATE} has "
        "no result: re-run the validation, record the gate by hand, or rework?",
        reply or "the validator's reply was empty",
    )
    return (
        f"the validator recorded no {VALIDATE_GATE} result; the unit stays in validate "
        "— queued as a decision (dispose of it with `basicly loop answer`)"
    )


def verdict_from_reply(text: str) -> bool | None:

    for line in reversed(text.splitlines()):
        stripped = _MARKER.sub("", _MARKUP.sub("", line)).strip()
        if not stripped.upper().startswith(VERDICT_PREFIX):
            continue
        answer = stripped[len(VERDICT_PREFIX) :].strip().upper()
        if answer.startswith("PASS"):
            return True
        if answer.startswith("FAIL"):
            return False
    return None


def record_verdict(repo_root: Path, issue_id: str, *, passed: bool) -> None:

    _write(
        repo_root,
        [
            "gate",
            "report",
            issue_id,
            "--gate",
            VALIDATE_GATE,
            "--provider",
            VERIFY_GATE_PROVIDER,
            "--status",
            "pass" if passed else "fail",
        ],
    )
