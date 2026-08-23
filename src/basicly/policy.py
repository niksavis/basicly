"""Loop gate & checkpoint policy engine for the harness.

Deterministic-first, semantic-second: a failed (or missing) *required* gate
blocks advancement, while any other recorded gate is advisory and never blocks.
Definition-of-Ready is enforced in-process before the decompose checkpoint
(:func:`definition_of_ready`, basicly-wpc8.1). Rework is bounded (``max_rework``
retries) and then escalates to a human. The three human checkpoints (classify /
decompose / ship) are recorded as tracker comment markers.

The tracker is the single source of truth — this engine keeps no side-state. Gate
results overwrite in ``br`` (no history), so rework attempts and checkpoint
approvals are recorded as inspectable comment markers rather than derived from
gate history. The block-vs-advise policy lives here; ``br`` only stores verdicts.

Two things block an advance, and they answer different questions:
:func:`gate_status` asks whether the required gates passed, and
:func:`evidence_status` asks whether the phase has an artifact to point at.

Every gate the engine names declares one of four failure behaviours — pre-flight,
revision, escalation, abort — so "what happens when this fails" is a property of
the gate rather than a decision re-taken at its call site, and a gate typed
:data:`PREFLIGHT` runs with the tracker refused to it (:func:`preflight_gate`).
"""

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
    DEFAULT_TYPE_SECTIONS,
    ENGINE_GATE_PROVIDERS,
    LOOP_PHASES,
    PolicyConfig,
    SizingConfig,
    load_policy_config,
    load_type_sections,
)
from .integrity import VALIDATE_GATE
from .plan_record import ACCEPTANCE_HEADING, has_heading
from .tracker import add_comment as _add_comment
from .tracker import read_comments as _read_comments
from .tracker import write as _write

# Prefix for the harness's own comment markers, so they are both machine-parseable
# and obvious to a human reading the issue's comments.
MARKER = "[harness-policy]"


# --- Gate types (the mapping tests/test_policy.py asserts) ----------
#
# The four behaviours a gate can have when it fails. Naming them is what makes
# "what happens when this fails" a property of the gate rather than a decision
# re-taken at each call site — and the original mapping found all four already
# in use here, unnamed, so this documents existing behaviour more than it adds any.

PREFLIGHT = "pre-flight"  # blocks entry; no partial work created
REVISION = "revision"  # evaluates produced output, loops back under a cap
ESCALATION = "escalation"  # surfaces an unresolvable issue for a human decision
ABORT = "abort"  # halts to prevent damage or waste, preserving state

# The two gates the engine has that never reach ``br gate report``, so the
# taxonomy is where their names live. An abort gate records no verdict by
# definition, and a pre-flight gate that refuses records nothing either — which is
# why neither could be keyed off a name the tracker already carries.
DOR_GATE = "dor"  # blocks the classify->decompose advance
LINKED_WORKTREE_GATE = "linked-worktree"  # basicly.verify.linked_worktree_guard

# Every gate the engine names, and its type. The four recorded ones are keyed by
# the exact name they carry in ``br gate report``, so a reader of a bead's gate
# list can look one up.
#
# Three are literals: `.importlinter` makes :mod:`basicly.verify` this module's
# sibling and :mod:`basicly.rubrics` its senior, so ``verify``
# (:data:`~basicly.verify.DEFAULT_GATE`) and the rubric halves
# (:data:`~basicly.rubrics.RUBRIC_GATE` / :data:`~RUBRIC_JUDGED_GATE`) cannot be
# imported here. A test pins the keys to those constants. The fourth is imported:
# :mod:`basicly.integrity` is junior.
#
# Bounded to the gates the engine gives a *name*. The rest of §1.1's mapping — the
# commit-msg and secret-scan hooks, the projection checks, the ship preconditions,
# a landing refusing an uncommitted checkout — are hooks and driver refusals with
# nothing to key on, so inventing a name for each would put strings here that no
# code could be held to. They stay classified in the design table.
GATE_TYPE_BY_GATE: dict[str, str] = {
    DOR_GATE: PREFLIGHT,
    # Deterministic checks over produced output, bounced back to the lane that
    # produced it under ``max_rework``.
    "verify": REVISION,
    # The pre-flight half of the validate composite (§4.1, basicly-imnu.1).
    "rubric": PREFLIGHT,
    # The escalation half: it records an honest fail and enqueues a decision
    # instead of failing the lane, which is why it must stay out of
    # ``[policy] required_gates``.
    "rubric-judged": ESCALATION,
    VALIDATE_GATE: REVISION,
    # :func:`basicly.verify.linked_worktree_guard`: recording from an unredirected
    # linked worktree would lose the gate at landing, so the check halts the record
    # and reports the remedy rather than looping or enqueuing.
    LINKED_WORKTREE_GATE: ABORT,
}


def gate_type(gate: str) -> str:
    """The failure behaviour declared for *gate*.

    One of :data:`PREFLIGHT`, :data:`REVISION`, :data:`ESCALATION`, :data:`ABORT`.

    A gate this engine does not name is a consumer's own, recorded through
    ``basicly verify --issue <id> --gate <name>``: that runs after work is
    produced, which is exactly §1's test for a revision gate. Defaulting rather
    than refusing is deliberate, and the default is the only safe one — reading an
    unknown gate as :data:`PREFLIGHT` would promise a caller a read-only check the
    engine cannot vouch for, and as :data:`ESCALATION` would say its failures do
    not block when they do.
    """
    return GATE_TYPE_BY_GATE.get(gate, REVISION)


def preflight_gate(gate: str) -> contextlib.AbstractContextManager[None]:
    """Run *gate*'s check with the tracker refused to it (§2).

    A pre-flight gate reads the world, returns a verdict, and writes nothing. Any
    ``br`` write attempted inside the block raises
    :class:`basicly.tracker.TrackerWriteRefusedError` — including one from a module this
    engine calls, since the guard sits on br's own funnel rather than on a caller.

    Raises:
        ValueError: *gate* is not typed :data:`PREFLIGHT`. Asking for the guard on
            a revision gate is a category error, not a stricter setting: that gate
            is *supposed* to record its verdict and charge its rework, so silently
            honouring the request would break it at the first failure.
    """
    declared = gate_type(gate)
    if declared != PREFLIGHT:
        raise ValueError(
            f"gate {gate!r} is typed {declared}, not {PREFLIGHT}; only a pre-flight "
            "gate is read-only (architecture D-23)"
        )
    return tracker.read_only(f"pre-flight gate {gate}")


# --- Definition of Ready ----------------------------------------------------

# The section every bead needs whatever its work type, and the only one a *field* can
# satisfy as well as a body heading (basicly-58iu).
_ACCEPTANCE_CRITERIA_SECTION = ACCEPTANCE_HEADING


@dataclass(frozen=True)
class DoRResult:
    """Whether an issue satisfies the Definition-of-Ready."""

    ready: bool
    missing: tuple[str, ...]


def definition_of_ready(repo_root: Path, issue_id: str) -> DoRResult:
    """The DoR verdict for *issue_id*: the required body sections it does not carry.

    Acceptance criteria are required on **every** bead, whatever its work type.
    They are the only thing validate can judge against: D4 makes the ``rubric``
    gate required, and the shipped rubrics ask whether the change evidences the
    criteria recorded on the bead — so a bead carrying none cannot be
    meaningfully validated, and its gate reads green having proved nothing.

    The rule is owned in-process rather than read off ``br lint`` (basicly-wpc8.1), and
    that is a deletion rather than a port: lint could never express the requirement on
    its own — it derives its set from the per-type template compiled into the binary, so
    a ``chore`` was never asked for acceptance criteria and a silent lint could not be
    read as "the criteria exist", and it inspects the description *body* only, ignoring
    ``br``'s structured ``acceptance_criteria`` field. Both halves were already
    reconciled here, so what is left is :func:`required_sections` against the record,
    with the per-type half read from *repo_root*'s own ``[policy.type_sections]``.

    Either carrier satisfies the acceptance criteria — the structured field or the body
    section — but never their absence, and every other required section stays
    body-checked. A record the tracker cannot answer for reads as not-ready, which is the
    fail-closed direction for a verdict that releases work.

    Runs under :func:`preflight_gate`, so the single read below is all it *can* do: this
    gate blocks the classify->decompose advance and creates nothing, and §2's rule is
    enforced rather than merely documented.
    """
    with preflight_gate(DOR_GATE):
        record = tracker.read_record(repo_root, issue_id) or {}
        body = record.get("description")
        missing = tuple(
            section
            for section in required_sections(str(record.get("issue_type") or ""), repo_root)
            if not has_heading(body if isinstance(body, str) else "", section)
        )
        if _has_acceptance_criteria(record):
            missing = tuple(m for m in missing if m != _ACCEPTANCE_CRITERIA_SECTION)
    return DoRResult(ready=not missing, missing=missing)


def _has_acceptance_criteria(record: Mapping[str, object]) -> bool:
    """True when *record* carries acceptance criteria in ``br``'s structured field.

    The second carrier only. The body heading is checked with every other required
    section, so a blank or absent field here is not a verdict — it leaves the section
    to be found in the body or reported missing.
    """
    value = record.get("acceptance_criteria")
    return isinstance(value, str) and bool(value.strip())


# --- Body scaffolding (basicly-kjc5.44) -------------------------------------

# The placeholder a scaffolded section carries when the caller supplies no
# content. It has to read as unfinished: an empty heading passes both ``br lint``
# and :func:`definition_of_ready` (each only looks for the heading), so a body
# nobody filled in would otherwise clear the gate having stated nothing.
_TODO = "TODO"

# Scaffolded, but deliberately absent from :func:`required_sections`. An author has
# to be *shown* the section and its exact line format — being told only that a scope
# exists is what produced a tracker of headings that parse to nothing (basicly-tuy6)
# — while the Definition-of-Ready must not refuse a bead for omitting it, because
# fail-closed here blocks most of an existing tracker (the objection that settled
# basicly-vz78).
_SCOPE_SECTION = "## Scope"

# The one scope line ``decompose._SCOPE_LINE`` accepts, quoted wherever an author is
# told to write one. Stated once and imported by the parser's own warning, because two
# copies of an example are two things to drift — and an example that stopped parsing
# would teach exactly the mistake it exists to prevent. A test pins it to the pattern.
SCOPE_LINE_EXAMPLE = "- `src/basicly/cli.py`"

_SECTION_HINTS: dict[str, str] = {
    "## Steps to Reproduce": (
        f"{_TODO}: the exact commands run, the observed result, and the expected one."
    ),
    "## Success Criteria": f"{_TODO}: the high-level outcomes that close this epic.",
    _ACCEPTANCE_CRITERIA_SECTION: (
        f"- {_TODO}: Given <starting state> when <action> then <observable result>"
    ),
    # States the format by example, inline rather than as its own line — and so is
    # itself unparseable, on purpose. A scaffold nobody filled in must not read as a
    # declared scope, so it earns the same warning a hand-written prose scope does.
    _SCOPE_SECTION: (
        f"- {_TODO}: one entry per line in exactly this form: {SCOPE_LINE_EXAMPLE} "
        "— an entry that is not a backticked glob parses to nothing."
    ),
}


def required_sections(work_type: str, repo_root: Path | None = None) -> tuple[str, ...]:
    """Every body section the Definition-of-Ready requires for *work_type*.

    The set is fully derivable from the work type, so an agent never has to learn
    it by having the classify gate refuse: it is *repo_root*'s declared per-type
    sections (:func:`config.load_type_sections`) plus the acceptance criteria
    :func:`definition_of_ready` requires of every bead whatever its type. The
    per-type half is that repository's configuration, so a section set changes
    without a code change; the acceptance criteria are this engine's and do not.

    *repo_root* omitted asks for :data:`~basicly.config.DEFAULT_TYPE_SECTIONS`
    instead — the answer for a caller holding no tree, which is what the scaffold
    printer (``basicly policy scaffold``) still is.
    """
    declared = DEFAULT_TYPE_SECTIONS if repo_root is None else load_type_sections(repo_root)
    return (*declared.get(work_type, ()), _ACCEPTANCE_CRITERIA_SECTION)


def scaffold_body(work_type: str) -> str:
    """The body an author starts from: every DoR-required section, plus ``## Scope``.

    ``## Scope`` is emitted here and absent from :func:`required_sections`, and that
    split is the whole point. The scope is what sizes a lane and what the landing
    check reads, so an author needs the heading *and* its exact line format put in
    front of them; but the Definition-of-Ready cannot require it without refusing
    most of an existing tracker. Show it, warn when it parsed to nothing, never
    block on it (basicly-tuy6).
    """
    return compose_body(work_type, {_SCOPE_SECTION: ""})


def compose_body(
    work_type: str, content: Mapping[str, str] | None = None, *, preamble: str = ""
) -> str:
    """A bead body carrying every DoR-required section for *work_type*.

    *content* maps a heading to the text under it. A required section it omits
    gets a ``TODO`` placeholder for the agent to replace; a heading it carries
    that the Definition-of-Ready does *not* require (``## Scope``, which
    decompose records) is appended after the required ones rather than dropped —
    silently losing a caller's declared section would be the worse failure.
    *preamble* is prose emitted above the first heading, for a caller that has
    context to hand the reader.

    The structure is emitted, never guessed — the judgment stays the agent's.
    """
    content = content or {}
    headings = list(required_sections(work_type))
    headings += [heading for heading in content if heading not in headings]
    default = f"{_TODO}: fill this in."
    sections = (
        f"{heading}\n\n{content.get(heading) or _SECTION_HINTS.get(heading, default)}"
        for heading in headings
    )
    return (f"{preamble.strip()}\n\n" if preamble.strip() else "") + "\n\n".join(sections) + "\n"


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


def unchecked_working_set(title: str, sizing: SizingConfig) -> str:
    """What the band has to say about a package that declares no scope to size.

    Not a violation of the band — with no estimate there is nothing to be outside it —
    but it is worded here beside :func:`check_working_set` so the band's numbers are
    described one way wherever they reach an operator (basicly-jr0l.60).

    "No scope the estimator can read" rather than "no ``## Scope`` section", because
    the two are not the same and only the message can tell them apart: measured on this
    repo's own tracker, a bead can carry the heading and still size to nothing when its
    entries are prose rather than the backticked glob lines ``decompose`` writes and
    :func:`decompose.parse_scope_section` reads.
    """
    return (
        f"package {title!r} declares no scope the estimator can read, so its working set "
        f"was never checked against the {sizing.working_set_min}..{sizing.working_set_max} "
        "band: list the files it touches as backticked globs under a `## Scope` heading"
    )


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
    # Results on a *required* gate that were not counted, because their provider is
    # not the engine's own (basicly-jr0l.51). Carried so a caller can explain a gate
    # reading missing while ``br gate list`` shows a result for it; nothing here
    # ever affects ``can_advance``.
    disregarded: tuple[GateVerdict, ...] = ()


def gate_status(repo_root: Path, issue_id: str, config: PolicyConfig) -> GateStatus:
    """*issue_id*'s recorded gates, classified by :func:`classify_gates`.

    The read half only. Which store answers is `gate_source.read_gates`', not this
    module's (basicly-vkh0.27), and the rule is stated once next door so a caller
    holding rows already folded classifies them the same way.
    """
    return classify_gates(
        [
            GateVerdict(r["gate"], r.get("provider", ""), bool(r["passed"]))
            for r in gate_source.read_gates(repo_root, issue_id)
        ],
        config,
    )


def classify_gates(rows: Sequence[GateVerdict], config: PolicyConfig) -> GateStatus:
    """Classify *rows* against the required set; advance only when all pass (pure).

    A required gate that is missing or failed blocks advancement. Any recorded
    gate not in the required set is advisory and never affects ``can_advance``.

    A required gate counts only the engine's own results — those whose provider is
    in ``ENGINE_GATE_PROVIDERS``. ``br gate report`` authenticates nothing and a
    dispatched lane agent shares the real tracker through the worktree beads
    redirect, so counting any provider let a single report from inside a dispatch
    satisfy a required gate (basicly-jr0l.51). A foreign result on a required gate
    is reported as ``disregarded`` rather than dropped silently, because when it is
    the only result recorded the gate reads missing while the tracker plainly shows
    a pass — an operator needs to be told which result is being ignored and why.
    Advisory gates still accept any provider.
    """
    required = config.required_gates
    # br keeps one result per (gate, provider), not one per gate — measured, not
    # assumed: reporting verify under two providers leaves two rows, in no
    # guaranteed order. So the engine's own result is selected independently rather
    # than by collapsing every row for the gate and taking the last, which is what
    # the previous reader did — a foreign row landing last became the authoritative
    # one even alongside a genuine engine record.
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


# --- Rework loop (bounded, then escalate) -----------------------------------


def _comments(repo_root: Path, issue_id: str) -> list[dict]:
    """One bead's markers, each with its ``created_at``, from whichever store owns them.

    The wait meter needs the stamp and not only the text (basicly-kjc5.51): the
    tracker's own timestamps are what make a wait interval derivable without any
    new state. Both stores supply one — br's ``created_at`` and the owned ledger's
    event ``ts`` — which is why this can go through the seam at all (basicly-s5li).

    Hard by construction: every family read through here is a counter or a refusal,
    so a store that cannot answer must raise rather than report an empty history.
    """
    return _read_comments(repo_root, issue_id)


def _comment_texts(repo_root: Path, issue_id: str) -> list[str]:
    return [str(c.get("text", "")) for c in _comments(repo_root, issue_id)]


def _issue_is_closed(repo_root: Path, issue_id: str) -> bool:
    """True when ``br`` reports *issue_id* closed.

    One reader for one rule the engine states in two places: a marker on closed
    work is history, not live state. :func:`active_grant` retires a grant on a
    closed root issue whatever its markers say (basicly-hsrs), and
    :func:`_live_session_violations` retires a closed bead's escalations on the
    same test (basicly-i1s8) — sharing the reader is what keeps the two from
    drifting into two similar-looking special cases.
    """
    record = tracker.read_record(repo_root, issue_id)
    return record is not None and str(record.get("status", "")) == "closed"


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


def _marker_payload(text: str, marker: str) -> str | None:
    """What *marker* carries on this comment, or None when it is not that marker.

    Matched exactly as :func:`_marker_matches` does. The payload is taken from the
    whole comment rather than its first line, so a fact recorded over several lines
    comes back the way it was written.
    """
    if not _marker_matches(text, marker):
        return None
    return text.strip()[len(marker) :].strip()


def _rework_allowance_marker(gate: str) -> str:
    return f"{MARKER} rework-allowance gate={gate}"


def rework_attempts(repo_root: Path, issue_id: str, gate: str) -> int:
    """Count the rework attempts recorded for *gate* on *issue_id*.

    The raw history: every attempt ever recorded, including ones an operator has
    since forgiven. What the cap is compared against is :func:`rework_charged`.
    """
    marker = _rework_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def rework_recorded(repo_root: Path, issue_id: str) -> int:
    """Every rework attempt recorded on *issue_id*, across all gates.

    The per-node total the ship-time cost rollup reports (basicly-kjc5.50), which
    is a property of the package rather than of one gate. Token-exact matching
    keeps ``rework-allowance`` markers out of the count.
    """
    marker = f"{MARKER} rework"
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def rework_allowances(repo_root: Path, issue_id: str, gate: str) -> int:
    """Count the further attempts granted for *gate* on *issue_id*."""
    marker = _rework_allowance_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def rework_charged(repo_root: Path, issue_id: str, gate: str) -> int:
    """Attempts charged against ``max_rework``: recorded minus granted, floored at zero.

    Both counts come from one comment scan — the rework path already pays for a
    tracker read and this keeps it at one.
    """
    texts = _comment_texts(repo_root, issue_id)
    attempt_marker = _rework_marker(gate)
    allowance_marker = _rework_allowance_marker(gate)
    attempts = sum(1 for text in texts if _marker_matches(text, attempt_marker))
    granted = sum(1 for text in texts if _marker_matches(text, allowance_marker))
    return max(0, attempts - granted)


def record_rework(repo_root: Path, issue_id: str, gate: str) -> int:
    """Record one rework attempt for *gate*; return the attempts now charged.

    The return is the *charged* count, not the raw one, because every caller
    compares it against ``max_rework``. An allowance granted for an answered
    ``retry`` has to be visible at that comparison, not only in the history.
    """
    _add_comment(repo_root, issue_id, _rework_marker(gate))
    return rework_charged(repo_root, issue_id, gate)


def grant_rework_allowance(repo_root: Path, issue_id: str, gate: str) -> int:
    """Permit exactly one further attempt for *gate*; return the attempts still charged.

    This is how an answered ``retry`` is carried out (basicly-vkh0.5's sibling
    defect, basicly-4tjt: the escalation offered three choices and implemented
    none of them, because nothing anywhere cleared or offset the counter).

    Additive per grant, never a reset. Two reasons. ``br`` comments cannot be
    deleted, so a reset would have to be a marker the counter reads *around*, and
    the audit trail would stop saying how many attempts were actually spent —
    "three attempts, two of them forgiven" is the honest record. And a reset would
    hand the node a fresh full budget, where the operator answered *retry*: one
    more attempt.

    Deliberately per node and per gate. Raising ``[policy] max_rework`` loosens
    the cap for every node in the repo, which is one of the two wrong levers this
    exists to replace.
    """
    _add_comment(repo_root, issue_id, _rework_allowance_marker(gate))
    return rework_charged(repo_root, issue_id, gate)


def _unreliable_gate_marker(gate: str) -> str:
    return f"{MARKER} gate-unreliable gate={gate}"


def unreliable_gate_events(repo_root: Path, issue_id: str, gate: str) -> int:
    """Count the times *gate* failed on *issue_id* and then passed unchanged."""
    marker = _unreliable_gate_marker(gate)
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def record_unreliable_gate(repo_root: Path, issue_id: str, gate: str, detail: str = "") -> int:
    """Record that *gate* failed and then passed unchanged; return the count so far.

    The counterpart to not charging rework for it (basicly-55yh). Forgiving a
    flake silently would be worse than the bug it fixes: a chronically unreliable
    gate would stop escalating and stop being visible at the same time, and the
    lane it keeps deferring would look merely slow. The marker makes the flake
    countable, which is what turns "the suite is flaky" into a bead someone can
    act on.

    Not a rework attempt and never charged as one — a separate marker precisely so
    the two cannot be confused by a reader or by a counter.
    """
    suffix = f" {detail}" if detail else ""
    _add_comment(repo_root, issue_id, f"{_unreliable_gate_marker(gate)}{suffix}")
    return unreliable_gate_events(repo_root, issue_id, gate)


# The escalation an exhausted rework budget raises. The queue's only carrier for
# the gate is the question text, so it is written and read back in one place —
# a driver acting on the answer must not have to guess the format.
REWORK_ESCALATION_KIND = "escalation"
_REWORK_ESCALATION_RE = re.compile(r"^rework cap reached on gate (?P<gate>\S+):")


def rework_escalation_question(gate: str) -> str:
    """The canonical queue question for an exhausted rework budget on *gate*."""
    return f"rework cap reached on gate {gate}: retry, re-dispatch, or park?"


def gate_from_rework_escalation(question: str) -> str | None:
    """The gate a rework escalation asks about, or None when *question* is not one."""
    match = _REWORK_ESCALATION_RE.match(question.strip())
    return match.group("gate") if match else None


# How many times a gate may be forgiven as unreliable before a human must look
# (basicly-jr0l.41). Not charging rework for a flake is right — the flake is no
# evidence against the work — but "not charged" was implemented as "block and try
# again", with nothing counting the tries. A gate that keeps failing and then
# passing unchanged therefore deferred its lane indefinitely: no budget was ever
# spent, so no cap was ever reached, so nothing escalated, and the lane looked
# merely slow. :func:`record_unreliable_gate` already returns the count for
# exactly this purpose — this is the bound it was missing.
#
# Deliberately separate from ``max_rework`` rather than reusing it: the two count
# different things, untrustworthy result versus wrong work, and the markers were
# split in two precisely so neither a reader nor a counter could confuse them.
MAX_UNRELIABLE_GATE_EVENTS = 3
_UNRELIABLE_ESCALATION_RE = re.compile(r"^gate (?P<gate>\S+) is unreliable:")


def unreliable_gate_escalation_question(gate: str) -> str:
    """The canonical queue question for a chronically unreliable *gate*."""
    return (
        f"gate {gate} is unreliable: it failed and then passed unchanged "
        f"{MAX_UNRELIABLE_GATE_EVENTS} times; fix the flake, or land anyway?"
    )


def gate_from_unreliable_escalation(question: str) -> str | None:
    """The gate an unreliable-gate escalation asks about, or None when it is not one."""
    match = _UNRELIABLE_ESCALATION_RE.match(question.strip())
    return match.group("gate") if match else None


# The answer that chooses ``land anyway`` over ``fix the flake``. Anchored on the
# leading tokens so a rationale may follow ("land anyway - the flake is upstream"),
# while ``fix the flake`` is not mistaken for it. It lives beside the question it
# answers for the reason stated above :func:`rework_escalation_question`: the queue
# carries the wording and nothing else, so a driver acting on the answer must not
# have to guess either half of the format.
_LAND_ANYWAY_RE = re.compile(r"^\s*land\s+anyway\b", re.IGNORECASE)


def answer_lands_anyway(answer: str) -> bool:
    """True when *answer* chooses ``land anyway`` on an unreliable-gate escalation."""
    return _LAND_ANYWAY_RE.match(answer) is not None


# --- Hold and Kill: the two gate verbs nothing carried out (D3) --------------
#
# Go and Recycle the engine already has — the advance, and an answered ``retry``
# via :func:`grant_rework_allowance`. The other two were words. Every rework
# escalation offers ``park`` and no answer did anything with it, so the lane
# stayed dispatchable and the next supervised pass re-ran the work the operator
# had just stopped; ``kill`` had no surface at all.
#
# What was *not* wrong is the status vocabulary, which is where the requirements
# document's §5 first put the blame: ``deferred`` is already outside
# :data:`loop_state.DISPATCHABLE_STATUSES`, and ``supervise.ready_lanes`` already
# refuses a lane on it. The missing half is the two writes below.

# The status a held lane takes. Named rather than inlined at the ``br update``
# because the entire effect of Hold is that ``loop_state.is_dispatchable`` refuses
# this one string — a reader of either side should land on the other.
HELD_STATUS = "deferred"

_HOLD_MARKER = f"{MARKER} hold"
_KILL_MARKER = f"{MARKER} kill"

# The answer that chooses ``park`` over the other routes an escalation offers.
# Anchored on the leading token so a rationale may follow ("park - the upstream
# fix lands next week"), exactly as ``retry`` and ``land anyway`` are, and living
# beside the questions for the reason given above
# :func:`rework_escalation_question`. ``hold`` is accepted too: the queue asks
# "or park?" but D3 and anyone reading it call the verb Hold, and an operator
# should not have to know which of the two synonyms the engine matches on.
_HOLD_ANSWER_RE = re.compile(r"^\s*(?:park|hold)\b", re.IGNORECASE)


def answer_holds(answer: str) -> bool:
    """True when *answer* chooses ``park`` on an escalation that offers it."""
    return _HOLD_ANSWER_RE.match(answer) is not None


def hold_lane(repo_root: Path, issue_id: str, reason: str, gate: str | None = None) -> None:
    """Park *issue_id*: record *reason*, then take it out of the dispatch set.

    Two writes, deliberately in this order. The reason first, because of the two
    partial states a failure between them can leave, enforcement-without-a-record
    is the worse one: a bead dispatch silently refuses with nothing on it saying
    why reads as a tracker glitch, while a record whose status write did not land
    re-dispatches and is visible on the very next pass.

    *gate* names the gate whose escalation was answered, when the question carried
    one — the tracker-storage and merge-contention escalations
    (``supervise._capped_dispatch``) do not, and a lane held from one of those is
    held for the same reason and by the same authority.
    """
    named = f"gate={gate} " if gate else ""
    _add_comment(repo_root, issue_id, f"{_HOLD_MARKER} {named}{reason}".rstrip())
    _write(repo_root, ["update", issue_id, "--status", HELD_STATUS])


# Kill's own half of this section is :func:`authorize_kill` / :func:`kill_lane`,
# which live below the interactive-confirmation gate they are built on rather than
# here beside Hold — the code they mint does not exist yet at this point in the
# module.


# --- Is the rework loop converging? (basicly-m4zv.5) -------------------------

# What one rework round achieved, judged by comparing the failing gate's open
# findings against the previous round's rather than by counting attempts. The
# count is the wrong measure: a round that fixes one finding and introduces one
# leaves the same number outstanding and is not progress, and a round that
# reports the previous round's findings verbatim spends an attempt to re-derive a
# verdict already on the bead.
PROGRESSING = "progressing"
STALLED = "stalled"
DIVERGING = "diverging"

# The signature history, per bead and per gate, lives here next to the rework
# counter that already owns this accounting rather than beside any one caller: the
# merge gate had its own copy first (basicly-bdd4) and two mechanisms counting
# rounds is how they drift. A comment marker is the same durable, attributable,
# clone-travelling carrier the counter uses, and ``_marker_matches`` keeps
# ``gate=verify`` from cross-counting ``gate=verify-full``.
FINDING_SET_MARKER = f"{MARKER} finding-set"

# "The set grew" cannot be read off a digest, so the members are stored verbatim
# and the previous set is recovered rather than hashed. That makes the record's
# size the gate's business, hence a bound — kept small on purpose. The design's
# 16 KiB event cap is derived, not measured, so it is no licence to write 16 KiB.
#
# A truncated set can only ever read as *less* divergent than it is (a grown set
# whose extra members fall past the cap reads stalled instead of diverging), which
# delays an escalation by one round and never suppresses one.
MAX_FINDING_SET_MEMBERS = 20
MAX_FINDING_MEMBER_CHARS = 120

# How many consecutive rounds may report the same finding set before a human is
# asked. One is a warning: a gate reports what it checks, so an agent may have
# changed something real that this gate cannot see, and the round is only
# *probably* wasted. Two is not — nothing changed twice in a row.
#
# Deliberately looser than the merge gate's own threshold
# (:data:`supervise.MAX_REPEAT_BOUNCES`), which escalates on the first repeat
# because re-applying one branch to one anchor is provably non-converging. The
# verdict is shared; the threshold belongs to the caller.
MAX_STALLED_REWORK_ROUNDS = 2


@dataclass(frozen=True)
class Convergence:
    """Whether a gate's finding set moved between two consecutive rework rounds."""

    verdict: str
    members: tuple[str, ...]
    previous: tuple[str, ...]
    # Consecutive rounds that reported *members*, this one included: 1 the first
    # time it repeats, 0 when it did not.
    stalled_rounds: int

    @property
    def stalled(self) -> bool:
        """True when the round repeated the previous findings or its own view was truncated."""
        return self.verdict == STALLED

    @property
    def diverging(self) -> bool:
        """True when every previous finding is still open and new ones joined them."""
        return self.verdict == DIVERGING

    @property
    def detail(self) -> str:
        """What the comparison found, for a human reading the bead or the queue."""
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
    """Reduce a gate's findings to a canonical, comparable, bounded member list (pure).

    Deduped and sorted, because the order a gate happens to report its failures
    in is not a fact about them, and two orderings of one finding set must not
    read as two different rounds. Blank members are dropped and each is bounded
    per :data:`MAX_FINDING_MEMBER_CHARS`; the list itself per
    :data:`MAX_FINDING_SET_MEMBERS`.
    """
    return _finding_signature_and_truncation(findings)[0]


def _finding_signature_and_truncation(findings: Sequence[str]) -> tuple[tuple[str, ...], bool]:
    """:func:`finding_signature`'s bounded list, plus whether the cap cut a real growth to it."""
    members = {
        member.strip()[:MAX_FINDING_MEMBER_CHARS]
        for member in findings
        if member and member.strip()
    }
    ordered = sorted(members)
    return tuple(ordered[:MAX_FINDING_SET_MEMBERS]), len(ordered) > MAX_FINDING_SET_MEMBERS


def _finding_set_marker(gate: str) -> str:
    return f"{FINDING_SET_MARKER} gate={gate}"


# The members ride as JSON so a finding carrying a comma, a space, or an ``=``
# comes back exactly as the gate reported it — the set comparison is only as good
# as the round trip.
_FINDING_MEMBERS_RE = re.compile(r"findings=(?P<members>\[.*\])\s*\Z", re.DOTALL)


def _parse_finding_members(payload: str) -> tuple[str, ...] | None:
    """The member list a finding-set marker carries, or None when it carries none.

    A record this cannot read is dropped rather than raised: it is one round of
    history, and losing it costs at most a delayed escalation, where raising would
    fail the rework path that was recording a *gate failure* — the wrong moment to
    add a second way to fall over.
    """
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
    """Every finding set recorded for *gate* on *issue_id*, oldest first."""
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
    """Judge *members* against the most recent entry in *history* (pure).

    Only the most recent, because the rule is about *consecutive* rounds: a gate
    that reported A, then B, then A again has moved twice, not stalled.

    *truncated* means *members* itself hit the cap, so a view that is neither
    equal to nor a superset of *previous* can still be a reordered slice of a
    set that only grew and is read as stalled, not progress (basicly-95mp1k).
    """
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
    """Record what *gate* reported this round and compare it with the previous round.

    Written once per rework attempt, beside the attempt itself, which is what
    keeps the two histories in step — :func:`record_rework` appends per call and
    so does this, so the *n*-th signature belongs to the *n*-th attempt.

    The comparison is the whole point of storing members rather than a digest: a
    strict superset is divergence, which no hash could show. A gate reporting
    nothing has no set to compare and is not this function's business — its
    caller keeps the plain bounded cap.
    """
    members, truncated = _finding_signature_and_truncation(findings)
    verdict, previous, rounds = _compare_finding_sets(
        _finding_set_history(repo_root, issue_id, gate), members, truncated=truncated
    )
    body = f"{_finding_set_marker(gate)} verdict={verdict} findings={json.dumps(list(members))}"
    _add_comment(repo_root, issue_id, body)
    return Convergence(verdict=verdict, members=members, previous=previous, stalled_rounds=rounds)


def finding_set_escalation(convergence: Convergence) -> str | None:
    """Why a finding-set gate's rework loop must stop now, or None to keep spending.

    The threshold for the gates that report findings rather than paths, so the
    reason a human is being asked is written once and read by whoever asks.
    """
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
    """Refund the attempt a non-converging round charged — once per bead and gate.

    The attempt is genuinely not the node's to pay for: it was charged before the
    gate's findings could be compared, and it re-derived a verdict already on the
    bead. Refunding it is what leaves the cap intact for whatever the human answers.

    *Once*, for the reason basicly-jr0l.41 records against the flake counter:
    "charge nothing and try again" with nothing counting the tries is a livelock —
    no budget is spent, so no cap is ever reached, so the node defers forever while
    looking merely slow. The first non-converging round is forgiven; a second is the
    cap's business again, and the cap is what ends the loop when nobody answers the
    escalation. Written as its own marker rather than inferred from
    :func:`rework_allowances`, which an answered ``retry`` also writes: the two
    authorise an attempt for different reasons and must stay tellable apart.

    Returns True when it granted the refund.
    """
    marker = _convergence_refund_marker(gate)
    if any(_marker_matches(text, marker) for text in _comment_texts(repo_root, issue_id)):
        return False
    grant_rework_allowance(repo_root, issue_id, gate)
    _add_comment(repo_root, issue_id, marker)
    return True


# --- A shared-tracker gate failed on another lane's record (basicly-qorx) -----

# Substring conjunctions identifying a gate failure that asserts over the whole
# tracker rather than over the lane's own diff, each with the reason it does.
#
# Why the class needs a verdict of its own: every lane in a supervised pass shares
# one ledger through the redirect, so a gate that reads the tracker reads *every*
# lane's finishing record. Measured 2026-08-03 — basicly-tcmy.5 widened its own
# `## Scope` mid-flight and its finishing record then failed the working-set
# ceiling, so basicly-tcmy.6 and basicly-tcmy.22 hit the identical assertion inside
# their own landings and each was charged a rework attempt against a cap of 2, for a
# declaration in neither diff. Neither the re-run test (basicly-55yh) nor the
# dependency register (basicly-kjc5.56) can see it: the record is durable, so the
# failure reproduces, and it is this repo's own gate rather than a dependency's.
#
# Matching mirrors :data:`verify.DEPENDENCY_DEFECT_SIGNATURES` — per line and
# conjunctive — with one extra requirement that is what makes forgiving admissible
# here at all: the line must **name the bead** whose record failed the gate, because
# the attribution is the whole point. A tracker-wide failure that names no culprit
# gets no entry, since forgiving it would launder a failure nobody owns.
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
    """A tracker-wide gate failure and the lanes whose records invalidated it."""

    culprits: tuple[str, ...]
    reason: str


def shared_tracker_gate_failure(output: str, issue_id: str) -> SharedGateFailure | None:
    """The tracker-wide failure in *output* that is not *issue_id*'s to answer for.

    None means charge the lane normally, and it covers both honest reasons to: the
    output holds no tracker-wide signature at all, or it holds one that names
    *issue_id* itself — a lane whose own record invalidated the gate is exactly the
    lane the failure belongs to, so its own landing charges it. That keeps the
    forgiveness one-directional: it can only ever move a charge off a bystander and
    onto the declaration behind it, never off the declaration.

    Culprits are read off the matching lines and restricted to *issue_id*'s own id
    prefix, which is what makes reading ids out of gate output safe: the gate
    asserts over one shared tracker, so every bead it can name is a bead of that
    tracker, and a hyphenated word in the surrounding prose cannot be mistaken for
    one.
    """
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
    """Count the times *gate* failed on *issue_id*'s landing over another lane's record."""
    marker = f"{SHARED_GATE_MARKER} gate={gate}"
    return sum(1 for text in _comment_texts(repo_root, issue_id) if _marker_matches(text, marker))


def record_shared_gate_failure(
    repo_root: Path,
    issue_id: str,
    gate: str,
    culprits: Sequence[str],
    detail: str = "",
) -> int:
    """Attribute a tracker-wide *gate* failure to *culprits*; return the events on *issue_id*.

    Two records, because the failure has two halves that belong to different beads.
    On *issue_id* — the lane the gate blocked — an event marker, for the same reason
    :func:`record_unreliable_gate` writes one: forgiving silently would make a lane
    that keeps being blocked by other lanes' records look merely slow. On each
    culprit, the attribution, so the declaration that invalidated a shared gate is
    recorded where someone triaging that lane will see it.

    Never a rework attempt on either side. The bystander did not earn one, and the
    culprit's own landing charges it without help from here: the gate output names
    that lane, so :func:`shared_tracker_gate_failure` returns None for it and the
    ordinary charge applies.

    The attribution is idempotent on its whole body, like
    :func:`record_scope_violation`: a landing is retried on every advance, and one
    comment per attempt would bury the finding in its own repetitions. The event
    marker is not — counting attempts is what it is for.
    """
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
    """The canonical queue question for a *gate* another lane's record invalidated.

    Escalated on the **first** occurrence, where an unreliable gate gets
    :data:`MAX_UNRELIABLE_GATE_EVENTS` attempts, and the difference is evidence
    rather than taste: a flake may clear itself on the next landing, while a record
    in the shared tracker is durable, so every retry reaches the identical verdict
    and only delays the escalation — basicly-jr0l.16's reasoning about a
    deterministic refusal, reached by the same argument.

    Both remedies are the human's to carry out, and that is deliberate. The lane
    itself cannot fix a sibling's record or the constant it fails against, so
    offering it a ``land anyway`` here would put an unimplemented choice in the
    queue — the defect basicly-4tjt records.
    """
    return (
        f"gate {gate} failed on another lane's record: {', '.join(culprits)} "
        "invalidated it in the shared tracker, not this lane's diff; fix that lane's "
        "record, or reconcile the constant it fails against, then advance again"
    )


def gate_from_shared_gate_escalation(question: str) -> str | None:
    """The gate a shared-tracker escalation asks about, or None when it is not one."""
    match = _SHARED_GATE_ESCALATION_RE.match(question.strip())
    return match.group("gate") if match else None


def _gate_override_marker(gate: str) -> str:
    return f"{MARKER} gate-override-spent gate={gate}"


def gate_override_spent(repo_root: Path, issue_id: str, gate: str) -> bool:
    """True when the one landing an answered ``land anyway`` authorises was already taken."""
    marker = _gate_override_marker(gate)
    return any(_marker_matches(text, marker) for text in _comment_texts(repo_root, issue_id))


def spend_gate_override(repo_root: Path, issue_id: str, gate: str) -> bool:
    """Claim the single landing an answered ``land anyway`` authorises for *gate*.

    True the first time and False forever after, which is what makes the override
    one-shot. The answered decision is the authorisation; this marker is the record
    that it has been used, so a standing answer cannot silently bypass *gate* on
    every later landing of the same node.

    The record is a marker rather than in-memory state for the same reason the rework
    counters are: an advance is one process, the next advance is another, and the
    tracker is the only thing both of them read. Additive and never cleared — ``br``
    comments cannot be deleted, and "the override was spent at this point" is the
    audit trail a landing that skipped a gate has to leave behind.
    """
    if gate_override_spent(repo_root, issue_id, gate):
        return False
    _add_comment(repo_root, issue_id, _gate_override_marker(gate))
    return True


# --- Human checkpoints ------------------------------------------------------


def _checkpoint_marker(name: str) -> str:
    return f"{MARKER} checkpoint={name} approved"


def checkpoint_approved_in(texts: Iterable[str], name: str) -> bool:
    """True when *texts* records approval of the *name* checkpoint (pure).

    The rule, for a caller that already holds a record's markers: a whole-population
    read folds the log once and must not pay a read per checkpoint to ask this.
    """
    marker = _checkpoint_marker(name)
    return any(_marker_matches(text, marker) for text in texts)


def checkpoint_approved(repo_root: Path, issue_id: str, name: str) -> bool:
    """True when the *name* checkpoint has been approved on *issue_id*."""
    return checkpoint_approved_in(_comment_texts(repo_root, issue_id), name)


def approve_checkpoint(repo_root: Path, issue_id: str, name: str) -> None:
    """Record human approval of the *name* checkpoint (idempotent)."""
    if name not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {name!r}; expected one of {list(CHECKPOINTS)}")
    if not checkpoint_approved(repo_root, issue_id, name):
        _add_comment(repo_root, issue_id, _checkpoint_marker(name))


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


# One JSON parse and one atomic replace, so a lock held past this is a crashed holder.
_CONFIRM_HOLD_BUDGET_S = 5.0
_CONFIRM_WAIT_S = 60.0


def _confirm_store_lock(path: Path) -> AbstractContextManager[None]:
    """Serialize a read-modify-write on the confirm-code store across processes.

    Unguarded, two concurrent writers each read before either writes and the second
    clobbers the first (basicly-kas8q7); `_write_confirms` was already torn-write-safe
    via tmp-then-replace, which does not cover the lost update.
    """
    return base_lock.hold_file(
        path.with_suffix(".lock"),
        hold_budget_s=_CONFIRM_HOLD_BUDGET_S,
        wait_s=_CONFIRM_WAIT_S,
    )


def _issue_confirm_code(repo_root: Path, issue_id: str, name: str) -> str:
    """Generate, store, and return a one-time confirm code for the checkpoint."""
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
    """True when *code* matches the stored, unexpired code; consumes it on match."""
    path = repo_root / _CONFIRM_FILE
    with _confirm_store_lock(path):
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
    # Why this outcome. On a "challenge" it is the reason a *grant* did not
    # approve — empty when there was no grant, so a challenge with no grant
    # behind it reads exactly as it always has (basicly-5ltn).
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

    A grant that existed and declined says why on the challenge's ``detail``
    (basicly-5ltn): a bare confirmation request left the operator unable to tell
    "no grant" from "a covering grant refused because of a wrinkle in a sibling
    issue", which took several tool calls to diagnose by hand.

    Every path through here is also where the human-wait clock starts and stops
    (basicly-kjc5.51): the challenge is the moment the harness began waiting, and
    an approval is the moment it stopped.
    """
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
    """:func:`approve_checkpoint_guarded`'s decision, plus who to attribute it to.

    Split out so the queue settlement below it has exactly one site: an approval that
    reached the tracker without clearing the ask behind it is the defect
    (basicly-jr0l.24), and a per-path settle call is how three of four paths came to
    miss it. The attribution travels back with the result because only the path that
    approved knows it — a second read could not tell a human's code from a grant's.
    """
    if checkpoint_approved(repo_root, issue_id, name):
        # Reconciliation, not approval: this path did not decide anything, so it must
        # not claim a human or a grant did. It is the path a stale item arrives on.
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
    """Answer the decision-queue ask behind an approved checkpoint (basicly-jr0l.24).

    Imported here rather than at module scope because ``decisions`` imports *this*
    module for its wait recording, so a top-level import would make the pair a cycle.
    One deliberate exception at one call site reads better than a permanent cycle
    between two layers that are otherwise ordered.

    Best-effort: the approval is already recorded and durable, so a tracker that will
    not answer costs a tidy queue, never the approval. Raising here would turn a
    cosmetic follow-up into a failed checkpoint.
    """
    from . import decisions  # noqa: PLC0415 — see the cycle note above

    with contextlib.suppress(RuntimeError, OSError, ValueError):
        decisions.settle_checkpoint(repo_root, issue_id, name, by=by)


# --- Kill (D3, D15): the gate verb that removes a requirement -----------------
#
# The other half of the Hold/Kill section above :func:`answer_holds`; it sits here
# because it is built on the one-time-code gate immediately above, which does not
# exist yet where its sibling does.

# The confirm-code key a kill is minted under. Deliberately *not* a member of
# :data:`CHECKPOINTS`: it borrows the one-time-code mechanism and nothing else —
# there is no checkpoint marker to record, no phase it advances, and no grant
# level that may cover it.
KILL_CONFIRM_NAME = "kill"


def authorize_kill(repo_root: Path, issue_id: str, *, confirm: str | None = None) -> ApprovalResult:
    """Gate a kill of *issue_id* on a one-time confirm code, and write nothing.

    Kill is the only verb that removes a *requirement* rather than routing work,
    so D15 puts a human on it at every integrity level. That is stricter than
    checkpoint approval in both directions it can be: no autonomy grant is
    consulted, and an interactive TTY is no substitute either — the code is always
    required, so the deliberate second step cannot be satisfied by whatever
    terminal a lane agent happened to inherit. An agent that can kill what it
    finds hard has an exit from every difficulty, and a TTY is not evidence a
    human chose this one.

    With no *confirm* the caller gets a ``challenge`` carrying a fresh code **and
    nothing is written**: the bead is not closed, no reason is recorded, and a
    caller that ignores the challenge and re-runs bare is challenged again. A
    matching, unexpired code returns ``approved``; anything else is ``rejected``.

    Authorization only, so the caller can order the destructive half — the
    worktree teardown — *before* :func:`kill_lane` closes the bead. This module
    never touches the filesystem, and one function doing both could not leave room
    between them for the step that has to go there.
    """
    if confirm is None:
        return ApprovalResult(
            "challenge", code=_issue_confirm_code(repo_root, issue_id, KILL_CONFIRM_NAME)
        )
    if _consume_confirm_code(repo_root, issue_id, KILL_CONFIRM_NAME, confirm):
        return ApprovalResult("approved")
    return ApprovalResult("rejected", detail="invalid or expired confirm code")


def kill_lane(repo_root: Path, issue_id: str, reason: str) -> None:
    """Record *reason* on *issue_id* and close it as won't-do-this-way.

    The marker first and the close second, for the reason ``loop._on_ship`` writes
    its rollup before its close: a comment marker is the carrier that travels with
    the clone, and the only account of why a requirement was dropped must not
    depend on what the closing flush happened to pick up. The close reason repeats
    it so ``br list`` shows it without a comment read.

    Call only behind an ``approved`` :func:`authorize_kill` — this is the write,
    not the gate.
    """
    _add_comment(repo_root, issue_id, f"{_KILL_MARKER} {reason}")
    _write(repo_root, ["close", issue_id, "--reason", f"killed: {reason}"])


# --- Autonomy grants: session-scoped ledger (basicly-kjc5.3, design D3) ------
#
# A grant is a [harness-policy] comment marker on the session's root issue —
# the same durable, attributable mechanism checkpoints use. Issuance goes
# through the interactive-confirmation gate above, so an agent can never
# self-escalate; like that gate, this is a mitigation, not a boundary (a
# process sharing the human's identity could forge the comment — the D1 gap).
# Grants expire with the session (the root issue closing) and are revoked by a
# later marker; the last grant/revocation in comment order wins.

# Checkpoints each level may delegate — the *approval* over an input, never the
# input itself; :data:`PROPOSAL_COVERAGE` below is the other half. Ship
# additionally requires the lights-out preconditions (deterministic, checked at
# approval time).
GRANT_COVERAGE: dict[str, tuple[str, ...]] = {
    "L0": (),
    "L1": ("decompose",),
    "L2": ("classify", "decompose"),
    "L3": ("classify", "decompose", "ship"),
}

_GRANT_PREFIX = f"{MARKER} grant level="
_REVOKE_MARKER = f"{MARKER} grant revoked"
# The queue kind enqueued alongside the marker below at the same call site, so an
# answered question can retire the marker (basicly-jr0l.65). Duplicated from
# ``decisions.KINDS`` for the same reason ``ENGINE_BY``'s value is: naming it here
# would make the two modules a cycle.
_NEEDS_INPUT_KIND = "needs-input"
_NEEDS_INPUT_MARKER = f"{MARKER} {_NEEDS_INPUT_KIND}"


@dataclass(frozen=True)
class Grant:
    """One active autonomy grant: a level and its spend ceiling."""

    level: str
    # Required for L2+ at issuance (unbounded lights-out is unreachable);
    # None only on an L1 grant.
    token_budget: int | None
    # The session's total run-record spend when this grant was issued, so
    # :func:`spend_status` can meter what *this grant* authorized rather than what
    # the session has ever cost (basicly-jr0l.17). Zero on a grant issued before
    # this existed, which reads as the old lifetime behaviour — the strict
    # direction, so an old marker never becomes quietly unbounded.
    spent_at_issue: int = 0
    # How many unmeasurable dispatches the session had already taken at issuance
    # (basicly-jr0l.35). The same baseline trick as ``spent_at_issue`` and for the
    # same reason: the halt an unmeasurable dispatch triggers must be answerable by
    # the human re-granting, and without a baseline one estimated sample would keep
    # every future grant on this root halted forever. Zero on an older marker, which
    # is again the strict direction.
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


# The ``key=<int>`` fields a grant marker carries after its level.
_GRANT_INT_FIELDS = ("budget", "baseline", "unmetered")


def _grant_fields(tokens: Sequence[str]) -> dict[str, int] | None:
    """The marker's recognized integer fields, or None when one will not parse.

    A malformed value fails the whole marker rather than defaulting: a grant is
    authority, and a number nobody can read is not one the ceiling may assume.
    """
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
    """Parse a grant marker's first line, or None when it is not one."""
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
    # Mirror _grant_refusal: an L2+ marker without a positive budget is not a
    # grant — a hand-written sloppy marker must never be *more* powerful than
    # a well-formed issued one (unmetered lights-out).
    if tokens[0] in ("L2", "L3") and not (isinstance(budget, int) and budget > 0):
        return None
    return Grant(
        level=tokens[0],
        token_budget=budget,
        spent_at_issue=max(0, fields.get("baseline", 0)),
        unmetered_at_issue=max(0, fields.get("unmetered", 0)),
    )


def active_grant(repo_root: Path, root_issue: str) -> Grant | None:
    """The root issue's active grant: the last grant/revocation marker wins.

    Grants expire with the session, so a grant on a closed root issue is dead
    regardless of markers — without this, an old session's L3 grant would stay
    live forever unless someone remembered to revoke it.
    """
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
    if interactive:
        _write_grant(repo_root, root_issue, level, token_budget)
        return ApprovalResult("approved")
    # The code is keyed on level AND budget, so the exact grant the human saw
    # in the rerun hint is the only one the code can issue.
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
    """Record the grant marker, stamping the session's spend so far as its baseline.

    The baseline is read here rather than before the challenge, because it must be
    the total at the moment the grant *starts* — a code minted and relayed minutes
    later would otherwise bake in a stale figure and silently credit whatever the
    session spent in between (basicly-jr0l.17). It also keeps the challenge path
    free of a tracker read it has no reason to make.

    Both baselines come from the one meter read, so the count of unmeasurable
    dispatches is stamped at exactly the moment its token total is (basicly-jr0l.35)
    — two reads could disagree if a lane landed between them.
    """
    meter = session_spend(repo_root, root_issue)
    grant = Grant(
        level=level,
        token_budget=token_budget,
        spent_at_issue=meter.measured_tokens,
        unmetered_at_issue=meter.unmetered_dispatches,
    )
    _add_comment(repo_root, root_issue, _grant_marker(grant))


def revoke_grant(repo_root: Path, root_issue: str) -> None:
    """Record a revocation marker; the ledger's last-wins scan turns the grant off."""
    _add_comment(repo_root, root_issue, _REVOKE_MARKER)


def _grant_declined(grant: Grant, name: str, reasons: Sequence[str], *, scope: str = "") -> str:
    """Why a grant that covers *name* declined it anyway, quoting *reasons* verbatim.

    Verbatim so the vocabulary stays owned by the check that produced it
    (:func:`lights_out_violations`, :func:`spend_status`) instead of being
    re-worded here and drifting from it. *scope* names where the reasons were
    looked for, when that is not the checkpoint's own issue.
    """
    where = f" ({scope})" if scope else ""
    return (
        f"the active {grant.level} grant covers {name} but declined it{where}, so the "
        f"decision returns to a human: {'; '.join(reasons)}"
    )


def _grant_approval(
    repo_root: Path, issue_id: str, name: str, root_issue: str
) -> tuple[ApprovalResult | None, str, str]:
    """A delegated approval under the root's grant, or None plus why it declined.

    None (no grant, or the level does not cover *name*) drops to the normal
    challenge path. A covering grant still refuses — also via the challenge
    fallback, so the decision returns to a human — when the session's
    run-record spend has reached the grant's token budget, or when a ship
    approval finds any lights-out precondition violated (any wrinkle drops
    ship back to human — D3).

    The second element is why, for the caller to carry on the challenge it mints
    (basicly-5ltn) — every decline a *grant* made, since the operator has no
    other way to see that one was consulted at all. Empty when there was no
    grant, so that case stays exactly as bare as it was.
    """
    grant = active_grant(repo_root, root_issue)
    if grant is None:
        return None, "", ""
    if name not in GRANT_COVERAGE.get(grant.level, ()):
        return None, f"the active {grant.level} grant on {root_issue} does not delegate {name}", ""
    session_ids = session_issue_ids(repo_root, root_issue)
    if issue_id not in session_ids:
        # grant_root is caller-supplied: a grant must never authorize approvals
        # outside its own session (and the preconditions below are keyed on the
        # session, so approving a foreign issue would also check the wrong one).
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
    if spend.halted:
        return None, _grant_declined(grant, name, (spend.detail,)), ""
    if name == "ship":
        # Gates are checked on the node being shipped, not on the grant root
        # (basicly-kjc5.39): an epic's verify gate cannot exist until the epic
        # closes, so a root-scoped check refused every child ship unconditionally
        # and L3 silently degraded to L2. The session-wide preconditions below it
        # are unchanged, so any wrinkle anywhere still drops ship to a human.
        violations = lights_out_violations(
            repo_root, root_issue, config, ids=session_ids, shipping=issue_id
        )
        if violations:
            # Named, because those preconditions are session-wide: the issue a
            # violation points at is usually a sibling of the one being shipped,
            # and finding that by hand cost several tool calls (basicly-5ltn).
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
    # Nothing to close on the first ask — the grant approves before any challenge
    # is minted, which is exactly the wait it exists to remove. It does close one
    # when the grant only became able to approve later (a spend halt lifted by a
    # re-grant), and that wait is the harness's, not the human's.
    record_checkpoint_wait(repo_root, issue_id, name, by=f"grant:{grant.level}", delegated=True)
    return (
        ApprovalResult("approved", detail=f"delegated under {grant.level} grant"),
        "",
        f"grant:{grant.level}",
    )


# --- Delegated proposals: originating an input, not approving it (u6jq.2) ----
#
# Two distinct gates sit at classify and decompose, not one: *producing* the work
# type or the child plan, and *approving* the checkpoint over what was produced.
# :data:`GRANT_COVERAGE` only ever delegated the second, so a granted session
# still stopped dead at the first — the loop had no way to originate either input
# and every one of them arrived from outside (``loop advance --work-type``,
# ``--children``, ``basicly decompose --plan``). An undecomposed epic is then both
# unworkable and invisible to a supervised fan-out, which fans out over
# dependents it has none of.
#
# So the ladder is drawn again for the production half, one level stricter than
# the approval half: L1 may approve a decompose checkpoint but may not originate
# the plan it approves, because L2 is where D3 starts delegating judgment at all.

# The proposal kinds each level may originate.
PROPOSAL_COVERAGE: dict[str, tuple[str, ...]] = {
    "L0": (),
    "L1": (),
    "L2": ("work_type", "children"),
    "L3": ("work_type", "children"),
}


@dataclass(frozen=True)
class ProposalGrant:
    """Whether the grant may originate one proposal, and why not when it may not."""

    allowed: bool
    # Why the delegation was declined, for the caller to carry on its fallback
    # block — the operator's only way to see a grant was consulted at all. Empty
    # when allowed.
    reason: str = ""
    # The level that allowed it, for attribution on the recorded step.
    level: str = ""


def proposal_delegated(repo_root: Path, issue_id: str, kind: str, root_issue: str) -> ProposalGrant:
    """Whether *root_issue*'s grant lets the loop originate *kind* for *issue_id*.

    The same four guards a delegated checkpoint approval passes
    (:func:`_grant_approval`), against :data:`PROPOSAL_COVERAGE` instead of
    :data:`GRANT_COVERAGE`: there must be a live grant, its level must cover this
    proposal kind, the issue must be inside that grant's own session, and the
    session must not have reached D3's spend halt.

    A pure read — unlike :func:`_grant_approval` it records nothing, because a
    proposal is not yet a decision. What the proposer comes back with still has to
    pass the plan schema and the working-set governor before anything is written,
    so this decides only whether an agent may be *asked*.
    """
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
    spend = spend_status(repo_root, root_issue, grant=grant, ids=session_ids)
    if spend.halted:
        return ProposalGrant(False, _grant_declined(grant, f"the {kind} proposal", (spend.detail,)))
    return ProposalGrant(True, level=grant.level)


# --- Session accounting for grants: spend, needs-input, preconditions --------


def session_issue_ids(repo_root: Path, root_issue: str) -> tuple[str, ...]:
    """The session's bead ids: the root plus the track it is organised around.

    A track is assembled from two kinds of edge, so the walk follows both.

    **Parent-child dependents** give the decomposition. It nests fractally (a
    feature child decomposes into its own children), and both the spend meter
    and the lights-out preconditions claim session-wide coverage — so
    grandchildren must count too, or their spend and needs-input events would
    silently bypass the grant (D3).

    **Gating dependencies** give the cross-cutting track. A release, or any root
    that gates work it did not parent, records that work as its own ``blocks``
    dependencies — a bead's parent is its epic of origin and nothing is
    re-parented, so a descent-only walk found a session of exactly one bead and
    the grant covered nothing at all (basicly-jr0l.40). Such a track spans
    several parents and usually some parentless beads besides, so no set of
    per-parent grants can substitute for following the edge that defines it.

    The two directions are deliberately not symmetric. A gating *dependency* is
    work the root waits on, which is precisely the track a grant on that root
    means to authorize; a gating *dependent* is something waiting on the root,
    outside the track, and following it would widen a grant past what was
    granted over.

    Public, and the only session walk in the package: a second copy in
    ``loop_state`` followed parent-child alone and disagreed with this one by 14
    beads on a real root, which left the decision queue reading a narrower session
    than the grant it is accounted against (basicly-tcmy.30). ``policy`` owns it
    because the dependency runs that way — ``loop_state`` imports ``policy``.
    """
    # One ledger read for the whole population: `tracker.read_record` folds the *whole*
    # log per call and this walk hops once per bead, so 87 ids cost 8.77 s over 87 reads
    # against 0.20 s here (basicly-mdv1qu). An id the population does not hold — a
    # dangling edge, a tombstone — still goes to the seam, so coverage is unmoved.
    population = {row["id"]: row for row in tracker.all_records(repo_root)}
    # (record key, dependency type) pairs: the edges that lead into the session.
    edges = (("dependents", "parent-child"), ("dependencies", "blocks"))
    seen: dict[str, None] = {root_issue: None}  # insertion-ordered BFS
    queue = [root_issue]
    while queue:
        held = queue.pop(0)
        record = population.get(held) or tracker.read_record(repo_root, held)
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
    """How many beads a grant on *root_issue* would cover, the root included.

    Issuance reports this because coverage is not visible from the grant itself:
    an L3 marker with a 25000000-token ceiling reads as authority over a whole
    release whether its session is twenty beads or the one it sits on
    (basicly-jr0l.40). The count is the only thing that tells those apart, and a
    session of one has to be able to say so before the operator relies on it.
    """
    return len(session_issue_ids(repo_root, root_issue))


@dataclass(frozen=True)
class SpendMeter:
    """What the session's run records say it spent, split by how each sample was known.

    The split is the safety property (basicly-jr0l.35). ``runner.extract_usage``
    falls back to a chars/4 count over the *captured output* when an adapter reports
    no usage it can parse, and that number cannot see the prompt, the system prompt,
    the tool definitions or cache writes — which is where nearly all of an agentic
    dispatch's tokens are. Measured on a live copilot probe (2026-07-29): 5514 bytes
    of stdout estimated 1378 tokens against 24210 real input tokens read from the
    session store, 17.6x under, and with plain-text output the captured answer was
    two characters — about 1 token against the same 24210.
    :func:`session_spend` therefore keeps the two apart rather than adding a floor
    into a total that is then read as a measurement, and :func:`spend_status` halts
    on the presence of one instead of metering it.
    """

    measured_tokens: int
    estimated_tokens: int
    # How many dispatches ran an agent and contributed an estimate rather than a
    # measurement. A count, not a flag, so a baseline can subtract the ones an
    # earlier grant already answered for (:attr:`Grant.unmetered_at_issue`). A
    # dispatch that never started an agent is excluded — see :func:`session_spend`.
    unmetered_dispatches: int


def tokens_under_grant(spent_tokens: int, grant: Grant) -> int:
    """The part of *spent_tokens* this grant authorized — the only figure its budget bounds.

    Lifetime spend and a budget cover different windows, so dividing one by the other
    reports a ceiling the session never reached: basicly-kjc5 read 177970761/4000000
    with nothing spent under that grant (basicly-e2mz.13). Clamped, because pruned or
    lost run records can drop the total below the baseline and negative spend must
    never buy extra budget.
    """
    return max(0, spent_tokens - grant.spent_at_issue)


@dataclass(frozen=True)
class SpendStatus:
    """The session's standing against its grant's D3 token ceiling."""

    grant: Grant | None
    # Measured spend only. An unmeasurable dispatch's chars/4 floor is deliberately
    # absent from this number — it is not spend that was counted, and adding it would
    # be the face-value counting basicly-jr0l.35 removed. `unmetered_dispatches`
    # below is how such a dispatch reaches the ceiling.
    spent_tokens: int
    # True when a grant with a budget can no longer authorize new spend: either that
    # budget has been reached, or a dispatch under this grant could not be metered so
    # the remaining budget is unknowable. No grant (or an L1 grant with no budget)
    # means there is no ceiling to enforce, not that everything is halted — the
    # session is simply human-driven already.
    halted: bool
    detail: str = ""
    # Dispatches under *this grant* whose usage could not be measured, so nothing
    # says what they cost.
    unmetered_dispatches: int = 0

    @property
    def remaining_tokens(self) -> int | None:
        """Budget left under this grant, or None when no ceiling applies.

        The same subtraction :func:`spend_status` halts on, exposed as the quantity
        a *forward*-looking gate needs (basicly-jr0l.22). Clamped at both ends for
        the same reason :func:`tokens_under_grant` is.

        Zero once a dispatch under this grant went unmetered: with an unknown amount
        already spent there is no remainder that can honestly be offered to a forward
        gate. That agrees with ``halted``, which every dispatch path checks first —
        but a forward gate reading only this must not be told a budget is free when
        what is left is simply unknown.
        """
        if self.grant is None or self.grant.token_budget is None:
            return None
        if self.unmetered_dispatches:
            return 0
        return max(0, self.grant.token_budget - tokens_under_grant(self.spent_tokens, self.grant))


def check_pass_spend(forecast_tokens: int, status: SpendStatus) -> str | None:
    """D3 looking forward: why a pass will not fit the remainder, or None when it does.

    :func:`spend_status` compares spend *already recorded* against the budget, so a
    pass is admitted whenever the previous ones happened to fit and the overspend is
    only noticed on the pass after the money is gone — measured on the
    basicly-u6jq.1 proof run, where a 5000000-token ceiling admitted a pass that
    spent 46026602. With concurrent lanes a single pass can spend an unbounded
    multiple of the budget, because nothing sums what it is about to start.

    This is the missing half, and it is deliberately the *only* new thing: the
    remedy for an over-budget pass is to start nothing, never to interrupt a lane
    that is already running. Cost is bounded by sizing the work, never by killing a
    working agent — so this runs before dispatch, and in-flight lanes still land
    through the routing layer untouched.

    Returns None when there is no ceiling to enforce, which is the ungranted and L1
    case that :attr:`SpendStatus.remaining_tokens` already collapses to None.
    """
    remaining = status.remaining_tokens
    if remaining is None or forecast_tokens <= remaining:
        return None
    level = status.grant.level if status.grant is not None else "active"
    if status.unmetered_dispatches:
        # The remainder is zero because it is unknown, not because it was spent, and a
        # message telling an operator to re-scope would send them at the wrong problem.
        return (
            f"this pass forecasts {forecast_tokens} tokens against an unknown remainder "
            f"under the {level} grant: {status.unmetered_dispatches} dispatch(es) reported "
            "no measurable usage, so nothing says what is left"
        )
    return (
        f"this pass forecasts {forecast_tokens} tokens against {remaining} remaining "
        f"under the {level} grant: re-scope the lanes into smaller packages, or "
        "re-grant with a budget that covers them"
    )


def spend_status(
    repo_root: Path,
    root_issue: str,
    *,
    grant: Grant | None = None,
    ids: tuple[str, ...] | None = None,
) -> SpendStatus:
    """Where the session stands against D3's spend ceiling — the one halt predicate.

    D3: once run-record spend for the session reaches the grant's
    ``token_budget``, *no new dispatches or delegated decisions occur* and the
    session drops to human-only until re-granted. Three call sites enforce that
    one rule — delegated checkpoint approval (:func:`_grant_approval`), lane
    dispatch admission, and decider delegation — so the comparison itself lives
    here rather than being re-derived at each of them.

    A dispatch the adapter could not meter halts the session too (basicly-jr0l.35).
    Its chars/4 sample is a floor over captured output, structurally far below what
    the dispatch really cost (:class:`SpendMeter`), so counting it as spend would let
    the ceiling pass on a number that is not the session's spend — a declared safety
    property the code does not enforce. There is no honest multiplier to inflate it
    by either, so the ceiling errs the only way a ceiling may: it stops. Re-granting
    clears it, because the new grant's baseline answers for the dispatches already
    taken and the human has then seen the reason.

    That is a rule about an *agent run* nobody could meter. A dispatch that died
    before its agent process started is the other case and does not halt: no
    process ran, so no spend is hiding under its floor (basicly-jr0l.64,
    :func:`session_spend`).

    *grant* and *ids* let a caller that already read them skip the re-walk.
    """
    if grant is None:
        grant = active_grant(repo_root, root_issue)
    meter = session_spend(repo_root, root_issue, ids=ids)
    spent = meter.measured_tokens
    if grant is None or grant.token_budget is None:
        return SpendStatus(grant=grant, spent_tokens=spent, halted=False)
    budget = grant.token_budget
    # Clock-free by construction: two readings of one monotonically-growing counter,
    # never a timestamp comparison — a spend gate that branched on wall-clock would be
    # the same defect class the tracker's own clock bug keeps demonstrating.
    under_grant = tokens_under_grant(spent, grant)
    # Both counters are metered against the grant the same way, for the same reason:
    # a session that took an unmeasurable dispatch before this grant was issued has
    # already been answered for by the human who issued it.
    unmetered = max(0, meter.unmetered_dispatches - grant.unmetered_at_issue)
    if unmetered:
        return SpendStatus(
            grant=grant,
            spent_tokens=spent,
            halted=True,
            unmetered_dispatches=unmetered,
            detail=(
                f"{grant.level} grant cannot be metered: {unmetered} dispatch(es) under it "
                f"reported no measurable usage, so only a chars/4 floor over their captured "
                f"output exists ({meter.estimated_tokens} estimated, far below real spend) "
                f"and {under_grant}/{budget} tokens is not what this grant has cost; the "
                "session is human-only until re-granted or the runner is configured with a "
                "usage format"
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
    repo_root: Path, root_issue: str, *, ids: tuple[str, ...] | None = None
) -> SpendMeter:
    """Run-record spend across the session's beads, split by how it was known.

    The grant's meter. An entry the adapter measured adds to
    :attr:`SpendMeter.measured_tokens`; a chars/4 fallback
    (``estimated=True``) adds to :attr:`SpendMeter.estimated_tokens` and is
    counted as one unmeasurable dispatch instead. *ids* skips re-walking the
    session tree when the caller already has it.

    An entry carrying tokens but no ``estimated`` flag at all can only come from a
    version that predates the field, and every writer since sets it explicitly
    whenever tokens are present (``runner.extract_usage`` returns a bool or no usage
    at all), so it is read as measured — the behaviour it had when it was written.

    An ``unstarted`` entry is the one estimate that is *not* an unmeasurable
    dispatch (basicly-jr0l.64). Its floor still lands in
    :attr:`SpendMeter.estimated_tokens` — nothing measured it — but it counts no
    unmeasurable dispatch, because what makes a floor dangerous is the agent run
    hiding behind it, and there was no agent: the captured error text *is* the
    whole transcript. Counting it halted a 60000000-token grant with 43438526
    unspent over a tracker read that spawned no process (the 2026-08-02
    basicly-tcmy pass).
    """
    records = run_record.load_run_records(repo_root) or {}
    measured = 0
    estimated = 0
    unmetered = 0
    for issue_id in ids if ids is not None else session_issue_ids(repo_root, root_issue):
        history = records.get(issue_id)
        if not isinstance(history, list):
            continue
        for entry in history:
            tokens = entry.get("tokens") if isinstance(entry, dict) else None
            if not isinstance(tokens, int) or isinstance(tokens, bool):
                continue
            if entry.get("estimated") is True:
                estimated += tokens
                if entry.get("outcome") != run_record.UNSTARTED:
                    unmetered += 1
            else:
                measured += tokens
    return SpendMeter(
        measured_tokens=measured,
        estimated_tokens=estimated,
        unmetered_dispatches=unmetered,
    )


def record_needs_input(repo_root: Path, issue_id: str, fact: str) -> None:
    """Durably record a needs-input event as a marker comment on *issue_id*.

    The sentinel file is consumed when the loop surfaces it, so this marker is
    the trace the L3 lights-out precondition counts (zero needs-input events in
    the session — D3).
    """
    _add_comment(repo_root, issue_id, f"{_NEEDS_INPUT_MARKER} {fact}")


def _answered_asks(repo_root: Path, issue_id: str) -> frozenset[tuple[str, str]]:
    """The ``(kind, question)`` asks on *issue_id* whose latest queue item is answered.

    Every marker :func:`_live_session_violations` counts is enqueued as a decision
    item at the same call site, so the queue already records whether the fact was
    supplied or the escalation triaged — reading resolution needs no new storage.

    Keyed by the question because neither marker family carries the decision id: a
    needs-input marker carries the fact, which *is* the item's question, and a rework
    escalation's question is reconstructible from its gate
    (:func:`rework_escalation_question`). Only the *latest* item per question counts,
    so a fact that blocked again after a wrong answer — re-opened under the next
    generation by ``decisions.enqueue`` — reads as live again.

    Imported locally for the cycle reason :func:`_settle_checkpoint_queue` documents.
    """
    from . import decisions  # noqa: PLC0415 — see the cycle note above

    latest: dict[tuple[str, str], bool] = {}
    for item in decisions.items_on(repo_root, issue_id):
        latest[item.kind, item.question] = item.pending
    return frozenset(ask for ask, pending in latest.items() if not pending)


def _live_session_violations(repo_root: Path, issue_id: str, config: PolicyConfig) -> list[str]:
    """The session-wide precondition violations *issue_id* still contributes.

    Every marker read here is append-only, so resolved history must be discounted
    or one bead poisons every later ship in its session (basicly-i1s8): a closed
    bead's markers are history (basicly-hsrs), an answered ask or a granted retry
    allowance retires what it answered (basicly-jr0l.65, basicly-54t8w5), and an
    unanswered ask on open work still refuses a delegated ship. Each rule is
    pinned in ``test_policy.py``.
    """
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
        # Charged, not raw, matching `rework_charged` (basicly-54t8w5).
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
    """The deterministic reasons an L3 ship delegation must refuse (D3).

    Two preconditions are session-wide — zero *unresolved* rework escalations and
    zero unresolved needs-input events across the session — so any live wrinkle
    anywhere drops ship back to a human, while a marker whose bead has closed or
    whose question has been answered does not
    (:func:`_live_session_violations`). The gate check is scoped to *shipping*,
    the node actually being shipped (default: the root, for a single-node session).

    Scoping that one check is deliberate (basicly-kjc5.39, owner decision
    2026-07-25). Checking the root's gates could never hold mid-session: an
    epic's own verify gate is missing until the epic closes, so an L3 grant
    refused every child ship and degraded to L2 for exactly the long multi-lane
    sessions lights-out exists for. It was an accident of scoping, not a safety
    property — the safety comes from the node's own gates being green plus a
    session with no escalations and no missing facts anywhere.
    """
    violations: list[str] = []
    gated = shipping or root_issue
    status = gate_status(repo_root, gated, config)
    if not status.can_advance:
        pending = ", ".join((*status.required_failed, *status.required_missing))
        detail = f"required gates not green on {gated}: {pending}"
        if status.disregarded:
            # Otherwise this reads as a plain missing gate while br shows a pass for
            # it, and the operator has nothing to act on (basicly-jr0l.51).
            foreign = ", ".join(sorted({v.provider or "(none)" for v in status.disregarded}))
            detail += f" (disregarded a result from provider {foreign}: not the engine's own)"
        violations.append(detail)
    for issue_id in ids if ids is not None else session_issue_ids(repo_root, root_issue):
        violations.extend(_live_session_violations(repo_root, issue_id, config))
    return tuple(violations)


# --- Human wait time (D11, basicly-kjc5.51) ----------------------------------
#
# A factory's wall clock is dominated by waiting on a human — a checkpoint
# approval, or an item in the decision queue — while the run record measures
# only dispatch (``duration_s``). A delivery forecast built from that data
# predicts the compute and misses the bottleneck.
#
# Nothing new has to be stored to fix it. A wait's *start* is already on the
# bead (the ``[harness-decision]`` enqueue marker, or the request marker below
# for a checkpoint the harness had to ask about) and ``br`` stamps every comment
# with ``created_at``, so the interval is derivable. It is recorded as another
# ``[harness-*]`` marker family rather than a tracker field, per the plan's
# §3.4: markers are a format we own, so they migrate with us in Phase 6.

WAIT_MARKER = "[harness-wait]"

# What the harness was waiting on: a human checkpoint, or a decision-queue item.
WAIT_KINDS = ("checkpoint", "decision")

# Attribution recorded when a human answered. Matches ``loop answer``'s default
# so one token means one thing across both surfaces.
HUMAN_BY = "human"

# Attribution for a queue item the engine tidied rather than decided — an ask left
# open behind a checkpoint that was already approved (basicly-jr0l.24). Deliberately
# ``decisions.ENGINE_BY``'s value: the queue's attribution vocabulary is one set, and
# a settlement nobody made a judgment for must not read as a human's or a grant's.
_RECONCILED_BY = "engine"


@dataclass(frozen=True)
class WaitEvent:
    """One interval the harness spent waiting for an answer, from its marker."""

    wait_id: str
    issue_id: str
    kind: str  # one of WAIT_KINDS
    subject: str  # the checkpoint name, or the queued item's kind
    waited_s: int
    answered_by: str
    # True when the harness disposed of the question itself — a covering autonomy
    # grant, or the decider agent — instead of a human. Recorded rather than
    # re-derived from *answered_by* at read time: the recorder knows, and a
    # prefix heuristic would silently reclassify any later attribution token.
    delegated: bool
    requested_at: str = ""
    answered_at: str = ""


def wait_id_for_checkpoint(issue_id: str, name: str) -> str:
    """The wait id a checkpoint ask on *issue_id* is recorded under.

    Derived, not minted, so a re-issued challenge (an expired code, a re-run
    advance) reopens nothing: it is the same wait, still running.
    """
    return f"{issue_id}#wait-{name}"


def _parse_ts(text: str) -> datetime | None:
    """Parse a tracker timestamp; None when it is absent or malformed.

    A naive stamp is read as UTC — ``br`` reports Zulu, and guessing the local
    zone would turn a missing suffix into an hours-wrong interval.
    """
    try:
        stamp = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _parse_wait_header(text: str) -> tuple[str, bool] | None:
    """The (wait id, answered) a ``[harness-wait]`` first line declares, or None."""
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if not first_line.startswith(WAIT_MARKER):
        return None
    tokens = first_line[len(WAIT_MARKER) :].split()
    fields = dict(token.split("=", 1) for token in tokens if "=" in token)
    wait_id = fields.get("id", "")
    return (wait_id, "answered" in tokens) if wait_id else None


def _parse_wait_event(text: str, issue_id: str) -> WaitEvent | None:
    """Parse one answered wait marker; None for anything else, or a garbled one.

    Best-effort like the sibling marker parsers — a malformed marker is skipped,
    never raised. Only the id is read from the header; every other field comes
    from the JSON payload, so a mangled header line can lose evidence but never
    misreport who answered or for how long.
    """
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
    """Every closed wait recorded on *issue_id* — the per-bead evidence (D11)."""
    events = (
        _parse_wait_event(str(comment.get("text", "")), issue_id)
        for comment in _comments(repo_root, issue_id)
    )
    return tuple(event for event in events if event is not None)


def record_wait_request(repo_root: Path, issue_id: str, name: str) -> str | None:
    """Record that the harness has started waiting on a human for *name*.

    The one thing the tracker does not already know: a checkpoint the harness
    asked about carries no trace of *when* it asked, so the challenge is where
    the clock has to start. Returns the wait id, or None when a wait for it is
    already open — the wait began at the first ask, so a re-issued challenge must
    neither duplicate the marker nor restart the interval.
    """
    wait_id = wait_id_for_checkpoint(issue_id, name)
    if _open_wait_stamp(repo_root, issue_id, wait_id) is not None:
        return None
    _add_comment(repo_root, issue_id, f"{WAIT_MARKER} id={wait_id} kind=checkpoint requested")
    return wait_id


def _open_wait_stamp(repo_root: Path, issue_id: str, wait_id: str) -> str | None:
    """The tracker stamp on a still-unanswered request for *wait_id*, else None.

    None covers every "nothing measurable to close": no request was recorded, one
    was and has already been answered, or the tracker reported no ``created_at``
    for it. Order-independent — any answer marker anywhere in the comment list
    closes the wait, and comment order is not guaranteed chronological.
    """
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
    """Record the wait *wait_id* as closed now; return the event, or None.

    None when *requested_at* does not parse: an unmeasurable start is recorded as
    nothing rather than as a fabricated interval. The start is always the
    tracker's own stamp, so a wait outlives the process that opened it; the end
    is the local clock, because the closing marker has no stamp until ``br`` has
    written it. Both are the same wall clock — ``br`` runs here.

    *by* must be a single token (it lands on the header line); both callers
    supply one, and the reader takes attribution from the payload regardless.
    """
    if kind not in WAIT_KINDS:
        raise ValueError(f"unknown wait kind {kind!r}; expected one of {WAIT_KINDS}")
    start = _parse_ts(requested_at)
    if start is None:
        return None
    now = _now()
    # Clamped: tracker stamps are whole seconds and a machine's clock can step
    # backwards, and a negative wait is not evidence of anything.
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
    """Close the wait an approval of *name* just ended; None when nothing waited.

    Recorded only when the harness actually asked (an open request marker), which
    is what makes the grant's value measurable: a covering grant approves before
    any challenge is minted, so it records no wait — and the wait it removed is
    the wait that is now absent from the rollup.
    """
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
    """A session's wait accounting, reported apart from its dispatch time."""

    events: tuple[WaitEvent, ...]
    # Wall-clock seconds the session spent blocked on a human, and on the harness
    # answering for one. Overlapping intervals count once (see
    # :func:`_wall_clock_seconds`).
    human_wait_s: int
    delegated_wait_s: int
    # Agent seconds from the run record — summed, not merged, because that is
    # what ``duration_s`` has always meant. Reported beside the waits rather than
    # added to them: conflating the two is the mistake this rollup exists to fix.
    dispatch_s: float


def _wall_clock_seconds(events: tuple[WaitEvent, ...]) -> int:
    """The wall clock *events* cover, counting overlapping intervals once.

    A sum would over-report twice over. Concurrent lanes wait on the same human
    at the same time, and one ask can be recorded twice — the supervisor queues a
    ``checkpoint`` decision *and* mints the challenge behind it — so the union is
    what forecasts delivery. An event whose stamps do not parse is added as its
    own interval instead of vanishing, so a malformed marker under-reports the
    overlap rather than the wait.
    """
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
    """Total run-record dispatch seconds across the session's beads.

    The compute half of the rollup, and the reason the wait half exists: this is
    the only duration the harness has ever measured. *ids* skips re-walking the
    session tree when the caller already has it.
    """
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
    """The session's wait rollup: human time, delegated time, and dispatch time.

    Session-scoped like the spend meter, and for the same reason — a lane's wait
    is the session's wall clock, not that lane's private cost. Splitting human
    from delegated is what puts a number on an autonomy grant: the grant's value
    is the wait it moves out of the human column.
    """
    ids = ids if ids is not None else session_issue_ids(repo_root, root_issue)
    events = tuple(event for issue_id in ids for event in wait_events(repo_root, issue_id))
    return WaitSummary(
        events=events,
        human_wait_s=_wall_clock_seconds(tuple(e for e in events if not e.delegated)),
        delegated_wait_s=_wall_clock_seconds(tuple(e for e in events if e.delegated)),
        dispatch_s=session_dispatch_seconds(repo_root, root_issue, ids=ids),
    )


# --- Declared evidence artifacts (basicly-m4zv.13) ---------------------------
#
# Archon's ``evidence_policy.required`` reduced to the single property that needs
# neither a schema nor a judgment: a phase may *declare* a file, and the engine
# asserts it is there before that phase may report success. A lane could otherwise
# reach ship having recorded a passing gate with nothing to point at — the gate
# records a status, not an artifact — and when a landing is later questioned the
# evidence is whatever happened to be committed.
#
# Opt-in, blocking where declared (owner decision 2026-07-31). Nothing is declared
# by default, so the mechanism is inert until a consumer writes
# ``[policy.evidence]``, and removing the declaration removes the requirement.
# Blocking every phase was rejected as too strict, record-only as toothless.
#
# **Presence only.** The engine stats the artifact and never opens it. Anything
# more would put a parser — and with it a schema, and a verdict about content — on
# the deterministic side of the gate contract. The corollary, stated rather than
# hidden: an ``echo x >`` satisfies this, exactly as a forged provider string
# satisfies a required gate. What it buys is that "verified" can no longer be
# claimed with an empty disk behind it.
#
# Archon's own completion gate is ``signalDetected || bashComplete``, which lets a
# model's self-emitted DONE short-circuit the deterministic half. That disjunction
# is rejected; only the evidence requirement is adopted.

EVIDENCE_MARKER = f"{MARKER} evidence"


@dataclass(frozen=True)
class EvidenceStatus:
    """Whether one phase's declared evidence artifact is present. Presence only."""

    phase: str
    # The path as declared, or None when the phase declares nothing.
    declared: str | None = None
    satisfied: bool = True
    # Why it is unsatisfied, with the remedy; empty when satisfied.
    reason: str = ""
    # The joined (unresolved) path, set only when satisfied.
    path: Path | None = None


def unknown_evidence_phases(config: PolicyConfig) -> tuple[str, ...]:
    """Keys in ``[policy.evidence]`` that name no loop phase, sorted."""
    return tuple(sorted(p for p in config.evidence if p not in LOOP_PHASES))


def evidence_status(root: Path, config: PolicyConfig, phase: str) -> EvidenceStatus:
    """Evaluate *phase*'s ``[policy.evidence]`` declaration against *root*.

    Satisfied when the phase declares nothing: the mechanism is opt-in, so an
    absent declaration is not a failure. Every other answer fails closed — a
    declaration this function cannot honour refuses the advance rather than
    degrading to "no requirement", because a gate the operator believes is on and
    is not is the exact failure this mechanism exists to remove.

    That is also why a **misspelled phase name refuses every phase**, not just its
    own: ``verfiy = "..."`` would otherwise be a requirement that never fires. The
    engine cannot tell which phase was meant, so it declines to let any of them
    report success. Holding costs one line of TOML, named in the message; passing
    costs an unverifiable landing.

    An escaping path is refused too. Not as a security boundary — the declaration
    and the artifact belong to the same operator — but because an artifact outside
    the repo neither travels with a clone nor can be found by whoever later
    questions the landing, and an absolute path is the spelling that looks least
    like an escape (``Path('/repo') / '/etc/hostname'`` is ``/etc/hostname``;
    ``planner.contained_output_path`` documents the same pathlib trap on the
    projection side, where the path is written rather than read).

    *root* is the checkout the phase's own work happened in, which is not always
    the base: a ``build`` artifact is produced in the lane's worktree and is
    checked there, before the merge that would bring it into base.
    """
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
    """Whether *declared* names a usable, present, non-empty artifact under *root*.

    The presence half of :func:`evidence_status`, split out from the declaration
    half so neither reads as a wall of branches. Every branch is a refusal; the
    engine never opens the file.
    """
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
    # ``resolved != base`` first, so a declaration that resolves to the checkout
    # root itself falls through to the is-a-file check below and gets the accurate
    # diagnostic rather than being reported as outside the repo.
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
    """Record that *phase*'s declared artifact was present, on the bead (idempotent).

    The declaration lives in ``basicly.toml`` and says nothing about *this* bead, so
    without this a closed issue carries no trace of which file its phase's success
    rested on. ``br`` exports comments, so the marker travels with a clone while a
    local artifact may not (D11) — it records the path, never the content.

    Written *before* the phase's own transition runs, for the reason the ship-time
    cost rollup is: ``_on_ship`` commits the tracker state, and a marker added after
    that commit would sit in the local db only. Returns True when it wrote one.
    """
    body = f"{EVIDENCE_MARKER} phase={phase} path={declared}"
    if any(_marker_matches(text, body) for text in _comment_texts(repo_root, issue_id)):
        return False
    _add_comment(repo_root, issue_id, body)
    return True


# --- Declared file scope, checked at the landing (basicly-jr0l.44) -----------

SCOPE_VIOLATION_MARKER = f"{MARKER} scope-violation"


def record_scope_violation(
    repo_root: Path,
    issue_id: str,
    paths: Sequence[str],
    colliding: Sequence[str] = (),
) -> bool:
    """Record the paths a lane changed outside its declared scope (idempotent).

    Evidence about the **plan**, not about the code: the lane declared a file
    scope at decompose time, the landing is the first moment the actual diff can
    be held against it, and without a durable record the mismatch would surface
    only later as a merge conflict with no trace of who declared what
    (basicly-jr0l.44). ``br`` exports comments, so this travels with a clone (D11).

    Written whatever ``[policy] scope_collision`` then decides, because the
    evidence is the half that must not depend on the policy — a repo that sets
    ``warn`` still gets an auditable record of every landing that reached outside
    its plan. *colliding* names the live lanes whose own declared scope covers one
    of the paths, so the record says whether this was a lonely overreach or the
    collision that produces a conflict.

    Idempotent on the whole body, like :func:`record_evidence`: the landing is
    retried on every advance, and one comment per attempt would bury the finding
    in its own repetitions. A *different* set of paths is a different finding and
    is recorded again. Returns True when it wrote one.
    """
    body = f"{SCOPE_VIOLATION_MARKER} paths={','.join(paths)}"
    if colliding:
        body += f" collides={','.join(colliding)}"
    if any(_marker_matches(text, body) for text in _comment_texts(repo_root, issue_id)):
        return False
    _add_comment(repo_root, issue_id, body)
    return True


def load_policy(repo_root: Path) -> PolicyConfig:
    """Convenience re-export so callers need only import this module."""
    return load_policy_config(repo_root)
