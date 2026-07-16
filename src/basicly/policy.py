"""Loop gate & checkpoint policy engine for the harness.

Deterministic-first, semantic-second: a failed (or missing) *required* gate
blocks advancement, while any other recorded gate is advisory and never blocks.
Definition-of-Ready is enforced via ``br lint`` before the decompose checkpoint.
Rework is bounded (``max_rework`` retries) and then escalates to a human. The
three human checkpoints (classify / decompose / ship) are recorded as ``br``
comment markers.

``br`` is the single source of truth — this engine keeps no side-state. Gate
results overwrite in ``br`` (no history), so rework attempts and checkpoint
approvals are recorded as inspectable comment markers rather than derived from
gate history. The block-vs-advise policy lives here; ``br`` only stores verdicts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .br import run_br as _run_br
from .config import CHECKPOINTS, PolicyConfig, load_policy_config

# Prefix for the harness's own comment markers, so they are both machine-parseable
# and obvious to a human reading the issue's comments.
MARKER = "[harness-policy]"


# --- Definition of Ready ----------------------------------------------------


@dataclass(frozen=True)
class DoRResult:
    """Whether an issue satisfies the Definition-of-Ready (via ``br lint``)."""

    ready: bool
    missing: tuple[str, ...]


def definition_of_ready(repo_root: Path, issue_id: str) -> DoRResult:
    """Return the DoR verdict for *issue_id* from ``br lint`` missing sections."""
    proc = _run_br(repo_root, ["lint", issue_id, "--json"])
    results = json.loads(proc.stdout).get("results", [])
    missing = tuple(results[0].get("missing", [])) if results else ()
    return DoRResult(ready=not missing, missing=missing)


# --- Gate status ------------------------------------------------------------


@dataclass(frozen=True)
class GateVerdict:
    """A single recorded gate result."""

    gate: str
    provider: str
    passed: bool


@dataclass(frozen=True)
class GateStatus:
    """The advance decision derived from an issue's recorded gates."""

    can_advance: bool
    required_passed: tuple[str, ...]
    required_failed: tuple[str, ...]
    required_missing: tuple[str, ...]
    advisory: tuple[GateVerdict, ...]


def gate_status(repo_root: Path, issue_id: str, config: PolicyConfig) -> GateStatus:
    """Classify recorded gates against the required set; advance only when all pass.

    A required gate that is missing or failed blocks advancement. Any recorded
    gate not in the required set is advisory and never affects ``can_advance``.
    """
    proc = _run_br(repo_root, ["gate", "list", issue_id, "--robot"])
    results = {
        r["gate"]: GateVerdict(r["gate"], r.get("provider", ""), bool(r["passed"]))
        for r in json.loads(proc.stdout).get("results", [])
    }
    required = config.required_gates
    passed = tuple(g for g in required if g in results and results[g].passed)
    failed = tuple(g for g in required if g in results and not results[g].passed)
    missing = tuple(g for g in required if g not in results)
    advisory = tuple(v for g, v in results.items() if g not in required)
    return GateStatus(
        can_advance=not failed and not missing,
        required_passed=passed,
        required_failed=failed,
        required_missing=missing,
        advisory=advisory,
    )


# --- Rework loop (bounded, then escalate) -----------------------------------


def _comment_texts(repo_root: Path, issue_id: str) -> list[str]:
    proc = _run_br(repo_root, ["comments", "list", issue_id, "--json"])
    return [str(c.get("text", "")) for c in json.loads(proc.stdout)]


def _rework_marker(gate: str) -> str:
    return f"{MARKER} rework gate={gate}"


def _marker_matches(text: str, marker: str) -> bool:
    """Token-exact marker match on the comment's first line.

    A bare prefix match would cross-count gates whose names extend each other
    (``verify`` vs ``verify-full``), so the marker must be the whole first
    line or be followed by a space-separated suffix.
    """
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return first_line == marker or first_line.startswith(marker + " ")


def rework_attempts(repo_root: Path, issue_id: str, gate: str) -> int:
    """Count the rework attempts recorded for *gate* on *issue_id*."""
    marker = _rework_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def record_rework(repo_root: Path, issue_id: str, gate: str) -> int:
    """Record one rework attempt for *gate*; return the new attempt count."""
    _run_br(repo_root, ["comments", "add", issue_id, _rework_marker(gate)])
    return rework_attempts(repo_root, issue_id, gate)


def should_escalate(repo_root: Path, issue_id: str, gate: str, config: PolicyConfig) -> bool:
    """True when rework attempts have reached the cap and the node must escalate."""
    return rework_attempts(repo_root, issue_id, gate) >= config.max_rework


# --- Human checkpoints ------------------------------------------------------


def _checkpoint_marker(name: str) -> str:
    return f"{MARKER} checkpoint={name} approved"


def checkpoint_approved(repo_root: Path, issue_id: str, name: str) -> bool:
    """True when the *name* checkpoint has been approved on *issue_id*."""
    marker = _checkpoint_marker(name)
    return any(_marker_matches(text, marker) for text in _comment_texts(repo_root, issue_id))


def approve_checkpoint(repo_root: Path, issue_id: str, name: str) -> None:
    """Record human approval of the *name* checkpoint (idempotent)."""
    if name not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {name!r}; expected one of {list(CHECKPOINTS)}")
    if not checkpoint_approved(repo_root, issue_id, name):
        _run_br(repo_root, ["comments", "add", issue_id, _checkpoint_marker(name)])


def load_policy(repo_root: Path) -> PolicyConfig:
    """Convenience re-export so callers need only import this module."""
    return load_policy_config(repo_root)
