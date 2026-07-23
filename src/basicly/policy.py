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
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from . import run_record
from .br import run_br as _run_br
from .config import AUTONOMY_LEVELS, CHECKPOINTS, PolicyConfig, SizingConfig, load_policy_config

# Prefix for the harness's own comment markers, so they are both machine-parseable
# and obvious to a human reading the issue's comments.
MARKER = "[harness-policy]"


# --- Definition of Ready ----------------------------------------------------

# The ``br lint`` template section satisfied by ``br``'s structured
# ``acceptance_criteria`` field (basicly-58iu). ``br lint`` only inspects the
# description body for this heading and ignores the field, so the harness credits
# the field itself — other template sections (e.g. a bug's Steps to Reproduce)
# stay body-checked. ``br lint`` has no config to teach it the field, so the fix
# lives here rather than upstream in beads_rust.
_ACCEPTANCE_CRITERIA_SECTION = "## Acceptance Criteria"


@dataclass(frozen=True)
class DoRResult:
    """Whether an issue satisfies the Definition-of-Ready (via ``br lint``)."""

    ready: bool
    missing: tuple[str, ...]


def definition_of_ready(repo_root: Path, issue_id: str) -> DoRResult:
    """Return the DoR verdict for *issue_id* from ``br lint`` missing sections.

    A non-empty structured ``acceptance_criteria`` field satisfies the
    ``## Acceptance Criteria`` template section without duplicating it into the
    description body (basicly-58iu); every other missing section still blocks.
    """
    proc = _run_br(repo_root, ["lint", issue_id, "--json"])
    results = json.loads(proc.stdout).get("results", [])
    missing = tuple(results[0].get("missing", [])) if results else ()
    if _ACCEPTANCE_CRITERIA_SECTION in missing and _has_acceptance_criteria(repo_root, issue_id):
        missing = tuple(m for m in missing if m != _ACCEPTANCE_CRITERIA_SECTION)
    return DoRResult(ready=not missing, missing=missing)


def _has_acceptance_criteria(repo_root: Path, issue_id: str) -> bool:
    """True when the issue's structured ``acceptance_criteria`` field is non-empty.

    Best-effort: a br failure or an unexpected payload shape returns False, so the
    body-heading requirement stands rather than a lookup error relaxing the gate.
    """
    try:
        proc = _run_br(repo_root, ["show", issue_id, "--json"])
        data = json.loads(proc.stdout)
    except RuntimeError, ValueError:
        return False
    issue = data[0] if isinstance(data, list) and data else data
    if not isinstance(issue, dict):
        return False
    value = issue.get("acceptance_criteria")
    return isinstance(value, str) and bool(value.strip())


# --- Working-set sizing governor (basicly-kjc5.2, factory design D8) ---------


def check_working_set(
    title: str, total_tokens: int, scope_tokens: int, sizing: SizingConfig
) -> str | None:
    """The DoR sizing rule: a violation message for *title*, or None when it fits.

    Above the ceiling the engine refuses and the agent must split — flatten the
    tree into more top-level packages, never deepen it (D7/D8). Below the floor
    the package wastes per-lane overhead (economics, never model quality), so the
    guidance is to merge it with a sibling in its scope group. The floor applies
    only when the declared scope matches existing material (*scope_tokens* > 0):
    a pure-greenfield child has nothing to read yet, so a floor refusal would
    wedge legitimate new-file decompositions.
    """
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


# --- Interactive-confirmation gate on checkpoint approval -------------------
#
# A tool-invoked Bash (Claude Code, and codex/copilot via the same piped
# subprocess) has no controlling TTY, so a subagent cannot self-approve a
# checkpoint by default. A non-interactive caller must echo back a one-time
# ephemeral code, forcing a deliberate second step an autopilot "drive to ship"
# directive will not contain. This is a mitigation, not a boundary: it does not
# stop a determined process that shares the human's OS/git identity (the D1 gap).

CONFIRM_TTL_SECONDS = 900
_CONFIRM_FILE = Path(".basicly/usage/checkpoint-confirms.json")


def _now() -> float:
    """Wall-clock seconds; indirection so tests can pin the clock."""
    return time.time()


def _new_code() -> str:
    """A short one-time confirm code; indirection so tests can pin it."""
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
    """Atomically persist the confirm-code map to the self-ignored usage dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    gitignore = path.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    tmp = path.with_suffix(f".{os.getpid()}.json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _issue_confirm_code(repo_root: Path, issue_id: str, name: str) -> str:
    """Generate, store, and return a one-time confirm code for the checkpoint."""
    path = repo_root / _CONFIRM_FILE
    data = _read_confirms(path)
    code = _new_code()
    data[_confirm_key(issue_id, name)] = {"code": code, "expires": _now() + CONFIRM_TTL_SECONDS}
    _write_confirms(path, data)
    return code


def _consume_confirm_code(repo_root: Path, issue_id: str, name: str, code: str) -> bool:
    """True when *code* matches the stored, unexpired code; consumes it on match."""
    path = repo_root / _CONFIRM_FILE
    data = _read_confirms(path)
    entry = data.get(_confirm_key(issue_id, name))
    if not isinstance(entry, dict):
        return False
    expired = _now() > float(entry.get("expires", 0))
    ok = not expired and secrets.compare_digest(str(entry.get("code", "")), code)
    if expired or ok:  # single-use on match, housekeeping on expiry
        data.pop(_confirm_key(issue_id, name), None)
        _write_confirms(path, data)
    return ok


@dataclass(frozen=True)
class ApprovalResult:
    """Outcome of a guarded checkpoint approval."""

    status: str  # "approved" | "challenge" | "rejected"
    code: str | None = None  # the confirm code to relay, when status == "challenge"
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
    """Approve a checkpoint via a TTY, a valid confirm code, or a covering grant.

    Interactive callers approve directly. A non-interactive caller with no
    ``confirm`` is first checked against the session's autonomy grant ledger
    (factory design D3, basicly-kjc5.3): a grant on *grant_root* (default: the
    issue itself) whose level covers *name* — with the lights-out preconditions
    holding for ship, and spend under the grant's token budget — approves with
    an attributed marker. Otherwise the caller gets a one-time ``challenge``
    code it must relay to a human; a matching, unexpired code approves, anything
    else is ``rejected`` with no marker recorded. Already-approved checkpoints
    short-circuit to ``approved`` (idempotent).
    """
    if name not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {name!r}; expected one of {list(CHECKPOINTS)}")
    if checkpoint_approved(repo_root, issue_id, name):
        return ApprovalResult("approved", detail="already approved")
    if interactive:
        approve_checkpoint(repo_root, issue_id, name)
        return ApprovalResult("approved")
    if confirm is None:
        delegated = _grant_approval(repo_root, issue_id, name, grant_root or issue_id)
        if delegated is not None:
            return delegated
        return ApprovalResult("challenge", code=_issue_confirm_code(repo_root, issue_id, name))
    if _consume_confirm_code(repo_root, issue_id, name, confirm):
        approve_checkpoint(repo_root, issue_id, name)
        return ApprovalResult("approved")
    return ApprovalResult("rejected", detail="invalid or expired confirm code")


# --- Autonomy grants: session-scoped ledger (basicly-kjc5.3, design D3) ------
#
# A grant is a [harness-policy] comment marker on the session's root issue —
# the same durable, attributable mechanism checkpoints use. Issuance goes
# through the interactive-confirmation gate above, so an agent can never
# self-escalate; like that gate, this is a mitigation, not a boundary (a
# process sharing the human's identity could forge the comment — the D1 gap).
# Grants expire with the session (the root issue closing) and are revoked by a
# later marker; the last grant/revocation in comment order wins.

# Checkpoints each level may delegate. Ship additionally requires the
# lights-out preconditions (deterministic, checked at approval time).
GRANT_COVERAGE: dict[str, tuple[str, ...]] = {
    "L0": (),
    "L1": ("decompose",),
    "L2": ("classify", "decompose"),
    "L3": ("classify", "decompose", "ship"),
}

_GRANT_PREFIX = f"{MARKER} grant level="
_REVOKE_MARKER = f"{MARKER} grant revoked"
_NEEDS_INPUT_MARKER = f"{MARKER} needs-input"


@dataclass(frozen=True)
class Grant:
    """One active autonomy grant: a level and its spend ceiling."""

    level: str
    # Required for L2+ at issuance (unbounded lights-out is unreachable);
    # None only on an L1 grant.
    token_budget: int | None


def _grant_marker(grant: Grant) -> str:
    text = f"{_GRANT_PREFIX}{grant.level}"
    if grant.token_budget is not None:
        text += f" budget={grant.token_budget}"
    return text


def _parse_grant(text: str) -> Grant | None:
    """Parse a grant marker's first line, or None when it is not one."""
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if not first_line.startswith(_GRANT_PREFIX):
        return None
    tokens = first_line[len(_GRANT_PREFIX) :].split()
    if not tokens or tokens[0] not in AUTONOMY_LEVELS:
        return None
    budget: int | None = None
    for token in tokens[1:]:
        if token.startswith("budget="):
            try:
                budget = int(token[len("budget=") :])
            except ValueError:
                return None
    # Mirror _grant_refusal: an L2+ marker without a positive budget is not a
    # grant — a hand-written sloppy marker must never be *more* powerful than
    # a well-formed issued one (unmetered lights-out).
    if tokens[0] in ("L2", "L3") and not (isinstance(budget, int) and budget > 0):
        return None
    return Grant(level=tokens[0], token_budget=budget)


def active_grant(repo_root: Path, root_issue: str) -> Grant | None:
    """The root issue's active grant: the last grant/revocation marker wins.

    Grants expire with the session, so a grant on a closed root issue is dead
    regardless of markers — without this, an old session's L3 grant would stay
    live forever unless someone remembered to revoke it.
    """
    proc = _run_br(repo_root, ["show", root_issue, "--json"])
    data = json.loads(proc.stdout)
    record = data[0] if isinstance(data, list) else data
    if isinstance(record, dict) and str(record.get("status", "")) == "closed":
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
    """The deterministic reason issuing this grant must be refused, or None.

    An unknown or L0 level, a level above the repo's ``[policy] autonomy``
    ceiling, or an L2+ grant without a positive token budget are rejected
    outright, before any interactivity gate.
    """
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
    """Issue a grant only via an interactive TTY or a valid confirm code (D3).

    The same anti-autopilot gate as checkpoint approval: an agent without a TTY
    gets a challenge code only a human should relay back — so a grant can never
    be self-issued. Deterministic refusals (:func:`_grant_refusal`) come first.
    """
    refusal = _grant_refusal(level, token_budget, config)
    if refusal is not None:
        return ApprovalResult("rejected", detail=refusal)
    grant = Grant(level=level, token_budget=token_budget)
    if interactive:
        _run_br(repo_root, ["comments", "add", root_issue, _grant_marker(grant)])
        return ApprovalResult("approved")
    # The code is keyed on level AND budget, so the exact grant the human saw
    # in the rerun hint is the only one the code can issue.
    checkpoint_name = f"grant-{level}-{token_budget}"
    if confirm is None:
        return ApprovalResult(
            "challenge", code=_issue_confirm_code(repo_root, root_issue, checkpoint_name)
        )
    if _consume_confirm_code(repo_root, root_issue, checkpoint_name, confirm):
        _run_br(repo_root, ["comments", "add", root_issue, _grant_marker(grant)])
        return ApprovalResult("approved")
    return ApprovalResult("rejected", detail="invalid or expired confirm code")


def revoke_grant(repo_root: Path, root_issue: str) -> None:
    """Record a revocation marker; the ledger's last-wins scan turns the grant off."""
    _run_br(repo_root, ["comments", "add", root_issue, _REVOKE_MARKER])


def _grant_approval(
    repo_root: Path, issue_id: str, name: str, root_issue: str
) -> ApprovalResult | None:
    """A delegated approval under the root's grant, or None to fall back to a challenge.

    None (no grant, or the level does not cover *name*) drops to the normal
    challenge path. A covering grant still refuses — also via the challenge
    fallback, so the decision returns to a human — when the session's
    run-record spend has reached the grant's token budget, or when a ship
    approval finds any lights-out precondition violated (any wrinkle drops
    ship back to human — D3).
    """
    grant = active_grant(repo_root, root_issue)
    if grant is None or name not in GRANT_COVERAGE.get(grant.level, ()):
        return None
    session_ids = _session_issue_ids(repo_root, root_issue)
    if issue_id not in session_ids:
        # grant_root is caller-supplied: a grant must never authorize approvals
        # outside its own session (and the preconditions below are keyed on the
        # session, so approving a foreign issue would also check the wrong one).
        return None
    config = load_policy_config(repo_root)
    if grant.token_budget is not None:
        spent = session_spend_tokens(repo_root, root_issue, ids=session_ids)
        if spent >= grant.token_budget:
            return None  # spend halt: human-only until re-granted
    if name == "ship":
        violations = lights_out_violations(repo_root, root_issue, config, ids=session_ids)
        if violations:
            return None
    marker = f"{_checkpoint_marker(name)} under grant {grant.level}"
    _run_br(repo_root, ["comments", "add", issue_id, marker])
    return ApprovalResult("approved", detail=f"delegated under {grant.level} grant")


# --- Session accounting for grants: spend, needs-input, preconditions --------


def _session_issue_ids(repo_root: Path, root_issue: str) -> tuple[str, ...]:
    """The session's bead ids: the root plus its parent-child tree, transitively.

    The tree nests fractally (a feature child decomposes into its own
    children), and both the spend meter and the lights-out preconditions claim
    session-wide coverage — so grandchildren must count too, or their spend and
    needs-input events would silently bypass the grant (D3).
    """
    seen: dict[str, None] = {root_issue: None}  # insertion-ordered BFS
    queue = [root_issue]
    while queue:
        proc = _run_br(repo_root, ["show", queue.pop(0), "--json"])
        data = json.loads(proc.stdout)
        record = data[0] if isinstance(data, list) else data
        if not isinstance(record, dict):
            continue
        for dep in record.get("dependents") or []:
            is_child = isinstance(dep, dict) and dep.get("dependency_type") == "parent-child"
            if is_child and "id" in dep and str(dep["id"]) not in seen:
                seen[str(dep["id"])] = None
                queue.append(str(dep["id"]))
    return tuple(seen)


def session_spend_tokens(
    repo_root: Path, root_issue: str, *, ids: tuple[str, ...] | None = None
) -> int:
    """Total run-record tokens across the session's beads (the grant's meter).

    Estimated (chars/4) samples count too — a spend ceiling must err toward
    halting, never toward uncounted spend. *ids* skips re-walking the session
    tree when the caller already has it.
    """
    records = run_record.load_run_records(repo_root) or {}
    total = 0
    for issue_id in ids if ids is not None else _session_issue_ids(repo_root, root_issue):
        history = records.get(issue_id)
        if not isinstance(history, list):
            continue
        for entry in history:
            tokens = entry.get("tokens") if isinstance(entry, dict) else None
            if isinstance(tokens, int) and not isinstance(tokens, bool):
                total += tokens
    return total


def record_needs_input(repo_root: Path, issue_id: str, fact: str) -> None:
    """Durably record a needs-input event as a marker comment on *issue_id*.

    The sentinel file is consumed when the loop surfaces it, so this marker is
    the trace the L3 lights-out precondition counts (zero needs-input events in
    the session — D3).
    """
    _run_br(repo_root, ["comments", "add", issue_id, f"{_NEEDS_INPUT_MARKER} {fact}"])


def lights_out_violations(
    repo_root: Path,
    root_issue: str,
    config: PolicyConfig,
    *,
    ids: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """The deterministic reasons an L3 ship delegation must refuse (D3).

    All three preconditions are session-wide: every required gate green on the
    root, zero rework escalations on any session bead, and zero needs-input
    events recorded anywhere in the session.
    """
    violations: list[str] = []
    status = gate_status(repo_root, root_issue, config)
    if not status.can_advance:
        pending = ", ".join((*status.required_failed, *status.required_missing))
        violations.append(f"required gates not green on {root_issue}: {pending}")
    for issue_id in ids if ids is not None else _session_issue_ids(repo_root, root_issue):
        texts = _comment_texts(repo_root, issue_id)
        needs = sum(1 for text in texts if _marker_matches(text, _NEEDS_INPUT_MARKER))
        if needs:
            violations.append(f"{needs} needs-input event(s) recorded on {issue_id}")
        for gate in config.required_gates:
            marker = _rework_marker(gate)
            attempts = sum(1 for text in texts if _marker_matches(text, marker))
            if attempts >= config.max_rework:
                violations.append(
                    f"rework escalation on {issue_id} (gate {gate}: {attempts}/{config.max_rework})"
                )
    return tuple(violations)


def load_policy(repo_root: Path) -> PolicyConfig:
    """Convenience re-export so callers need only import this module."""
    return load_policy_config(repo_root)
