"""The handoff artifacts a loop state hands the next one, and what refuses a bad one.

Three of the eight §8 names an artifact for, and deliberately three: `implementation-plan`
(DECOMPOSE → BUILD), `change-summary` (BUILD → VERIFY) and `release-record` (SHIP, which
has already merged, so nothing downstream is left to refuse it). D4 was taken against a
recommendation to prove one schema first, and §2.1's accepted mitigation is to sequence
these first and let the rest be built to a shape that has survived contact.
`implementation-plan` was the cheapest to start from because the plan gate already refuses
a child that declares none of its plan fields, so the schema formalises a live contract
rather than inventing one. Which of the eight run is :data:`PRODUCERS`, and a kind nothing
records is inert here even where its schema is installed — seven schemas on disk read as
seven live contracts (`basicly-u2hl.59`).

One responsibility, and it is the ruling: compose a state's own facts into a payload, and
say whether the artifact a unit carries may be accepted by the state after it. Nothing
here decides *whether a plan is adequate* — that is :mod:`basicly.plan_gate`'s, whose
``PlannedUnit`` this reads a child through so the artifact and the gate cannot describe
different field sets — and nothing here writes or parses a marker, which is
:mod:`basicly.artifact_record`'s. The boundary is *the ruling* against *the recorded
form*, the same cut ``plan_gate``/``plan_record`` is drawn on.

## The ratchet, and the population it discriminates

:func:`entry_verdict` admits a unit that carries **no** artifact. Absence is ambiguous — a
feature decomposed before this existed has none, and refusing those would stop the harness
rather than gate the work that follows it — so the gate binds on what its own producer
records. An artifact that is present and does not validate is a defect and is refused naming
the failing field, which is the whole point: a corrupted plan is caught at BUILD entry,
before the tokens.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from . import artifact_record, catalog_source, plan_gate

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

# The wired artifact kinds, spelled as their schema files are named so a kind cannot name
# a schema that does not exist. A constant exists here only for a kind a producer records,
# so :data:`PRODUCERS` is where all eight are enumerated and the four unwired schemas —
# plus `solution-design`, which has none — are strings no caller can reach by name.
IMPLEMENTATION_PLAN = "implementation-plan"
CHANGE_SUMMARY = "change-summary"
RELEASE_RECORD = "release-record"

# Every kind §8 names, against the symbol that records it: ``module:function`` inside this
# package, or None for a kind nothing records. **Declared, never derived from absence.** A
# probe for a kind's own name reads the English word as a producer — measured 2026-08-20,
# six files for `classification` and five of them prose — so a missing *reference* is
# ambiguous between unwired and probed wrongly, while a missing *declaration* is not.
# Wired or not, and no third state: *why* an unwired kind has no producer, scheduled or
# planned by nobody, is a backlog fact nothing here branches on, and a second copy of it
# would go stale unnoticed the day a record lands. ``test_handoff`` holds the other half —
# that each declared symbol is defined, names this kind's constant, and is called — so a
# renamed producer is a defect rather than a quiet demotion to unwired.
PRODUCERS: dict[str, str | None] = {
    IMPLEMENTATION_PLAN: "decompose:decompose",
    CHANGE_SUMMARY: "loop:_record_change_summary",
    RELEASE_RECORD: "curate:record",
    "classification": None,
    "change-shape": None,
    "validation-transcript": None,
    "verification-evidence": None,
    "solution-design": None,
}

# The artifact-format version every payload declares. Bumped only when a consumer's
# contract changes; the schemas pin it with `const`, so a payload from a newer producer
# is refused by the field rather than misread.
SCHEMA_VERSION = 1


class ArtifactError(ValueError):
    """An artifact that did not validate. A ``ValueError``, like ``PlanGateError``."""

    def __init__(self, verdict: ArtifactVerdict) -> None:
        """Carry the *verdict* alongside its rendered reason, so a caller can read it."""
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass(frozen=True)
class ArtifactVerdict:
    """Whether one unit's artifact of one kind may be accepted, and why not (pure data)."""

    issue_id: str
    kind: str
    violations: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        """True when the next state may accept this handoff."""
        return not self.violations

    @property
    def reason(self) -> str:
        """Every reason the artifact was refused, naming each field; empty when admitted."""
        if not self.violations:
            return ""
        return (
            f"{self.issue_id}'s {self.kind} artifact does not validate: "
            f"{'; '.join(self.violations)}"
        )


def wired(kind: str) -> bool:
    """True when a producer records *kind*, so its schema is a contract and not a file."""
    return PRODUCERS.get(kind) is not None


def _validator(repo_root: Path, kind: str) -> Draft202012Validator | None:
    """The *kind* schema as installed in *repo_root*, or None when it is not wired there.

    None is not a failure and is not silence, and two conditions reach it. A kind **no
    producer records** is inert wherever it is asked about, however many schemas sit on
    disk: four such schemas ship, and letting them resolve here is what let a file read as
    a live contract. Past that an artifact schema is a **catalog source**, so a repo that
    has not installed one has not adopted the contract, and these handoffs are inert there
    for the same reason a repo with no ``[[verify.checks]]`` runs no checks. Both sides
    read this before anything else, which is what keeps the two ends of one contract from
    disagreeing — a producer that skipped the write can never leave a consumer refusing
    what it did not get.
    """
    if not wired(kind):
        return None
    try:
        return catalog_source.schema_validator(repo_root, f"{kind}.schema.json")
    except OSError:
        return None


def adopted(repo_root: Path, kind: str) -> bool:
    """True when *repo_root* carries the *kind* contract, so both ends of it run.

    Public because a producer has facts to *gather* before it has a payload to record,
    and gathering them in a repo that will record nothing is pure cost — the loop reads
    a branch's head and changed paths only once this answers yes.
    """
    return _validator(repo_root, kind) is not None


def _violations(validator: Draft202012Validator, payload: object) -> tuple[str, ...]:
    """*payload*'s violations, each naming the field it is about (pure).

    Each line is ``<json path>: <message>``, because "is not of type 'integer'" alone
    leaves a reader to work out which of thirty fields it was about.
    """
    # `object` rather than jsonschema's JSON union, on purpose: the whole job of this
    # call is to decide what a decoded payload turned out to be, so a caller that had to
    # narrow it first would be asserting the answer before asking the question.
    instance = cast("Any", payload)
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    return tuple(f"{err.json_path}: {err.message}" for err in errors)


def record(repo_root: Path, issue_id: str, kind: str, payload: dict) -> None:
    """Validate *payload* and record it on *issue_id* as the *kind* artifact.

    Validated **before** the write, so a producing state can never hand on an artifact
    the consuming state will refuse — the failure surfaces where the facts were composed
    rather than one state later, with a dispatch already spent. A repo carrying no schema
    for *kind* records nothing at all (:func:`_validator`); it does not record an
    unvalidated artifact, which is the one outcome that would leave the consumer holding
    something nobody ruled on.

    Raises:
        ArtifactError: *payload* does not validate against the *kind* schema.
        RuntimeError: the artifact did not reach the authoritative store.
    """
    validator = _validator(repo_root, kind)
    if validator is None:
        return
    violations = _violations(validator, payload)
    if violations:
        raise ArtifactError(ArtifactVerdict(issue_id, kind, violations))
    artifact_record.write(repo_root, issue_id, kind, payload)


def entry_verdict(repo_root: Path, issue_id: str, kind: str) -> ArtifactVerdict:
    """Whether the next state may accept *issue_id*'s *kind* artifact (a read).

    Admits a unit carrying no artifact of that kind — the ratchet the module docstring
    states — and otherwise reports every schema violation at once, so an operator fixing
    one field per round trip does not pay an advance for each. A body the retired marker
    transport cut reports the cut instead: every field after it is missing, so the schema's
    answer would be a list of consequences of one cause.

    The schema is resolved before the tracker is read, so a repo that has not installed
    the contract costs this predicate no tracker round trip at all, and the two ends
    turn on together: nothing wrote an artifact there either.
    """
    validator = _validator(repo_root, kind)
    if validator is None:
        return ArtifactVerdict(issue_id, kind)
    payload = artifact_record.read(repo_root, issue_id, kind)
    if payload is None:
        return ArtifactVerdict(issue_id, kind)
    violations = _violations(validator, payload)
    if not violations:
        return ArtifactVerdict(issue_id, kind)
    cut = artifact_record.cut_violation(repo_root, issue_id, kind, payload)
    return ArtifactVerdict(issue_id, kind, (cut,) if cut else violations)


# --- Composing the two payloads ----------------------------------------------


@runtime_checkable
class PlannedTask(Protocol):
    """One recorded child as the plan artifact reads it.

    Structural rather than an import of ``decompose.CreatedChild``: this module sits
    below the decomposer, and a seam that needs an import back into its origin was cut
    across the responsibility instead of along it. Every member is a read-only property
    for the reason ``plan_gate.PlannedFields`` states — a plain attribute declares a
    writable slot no frozen dataclass can satisfy.

    The spec is :class:`plan_gate.PlannedUnit` itself rather than a second protocol over
    the same fields, so there is one declaration of what a planned child carries and this
    artifact cannot come to describe a field the gate does not define. The converse does
    **not** hold and is worth saying: a member added to the protocol and never read here
    is no type error, so nothing but a test catches a field the gate requires and the
    handoff silently drops — which is what ``test_handoff`` asserts field by field.

    One field set read two ways, then, not two field sets: the schema requires
    ``demonstration`` alongside ``PLAN_FIELDS`` because a proposed plan owes it and this
    artifact has no producer but a gated plan, while ``plan_entry``'s predicate over an
    already-recorded bead cannot (see :data:`plan_gate.DEMONSTRATION_FIELD`).
    """

    @property
    def issue_id(self) -> str:
        """The recorded child this planned task became."""
        ...

    @property
    def spec(self) -> plan_gate.PlannedUnit:
        """The child spec that was planned: its title and the plan fields."""
        ...

    @property
    def depends_on(self) -> tuple[str, ...]:
        """Sibling ids this child follows, declared and computed edges unioned."""
        ...


@runtime_checkable
class RecordedDecomposition(Protocol):
    """A finished decomposition as the plan artifact reads it (``decompose.DecomposeResult``)."""

    @property
    def feature_id(self) -> str:
        """The issue that was decomposed."""
        ...

    @property
    def children(self) -> tuple[PlannedTask, ...]:
        """The recorded children, in the plan's declared order."""
        ...

    @property
    def groups(self) -> tuple[tuple[str, ...], ...]:
        """Ids per parallel group — the graph the plan artifact carries."""
        ...


def plan_payload(result: RecordedDecomposition) -> dict:
    """The ``implementation-plan`` payload for a finished decomposition (pure).

    Reads the plan fields off each child's own spec rather than re-deriving them: the
    artifact has to say what the children were *created* under, or it is a second opinion
    about the plan instead of a record of it. That includes ``demonstration`` (D18), which
    the gate requires of a proposed plan but ``PLAN_FIELDS`` leaves out — dropping it here
    would leave the one field BUILD is meant to read from the artifact recoverable only
    from each child's body, which is the re-derivation this artifact exists to replace.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "feature": result.feature_id,
        "tasks": [
            {
                "issue_id": child.issue_id,
                "title": child.spec.title,
                "acceptance": list(child.spec.acceptance),
                "scope": list(child.spec.scope),
                "depends_on": list(child.depends_on),
                "budget_tokens": child.spec.budget_tokens,
                "integrity": child.spec.integrity,
                "demonstration": child.spec.demonstration,
            }
            for child in result.children
        ],
        "groups": [list(group) for group in result.groups],
    }


@dataclass(frozen=True)
class SelfCheck:
    """A landing's own verdict — the self-check BUILD's exit gate names.

    A type rather than three loose arguments, because ``passed`` is the landing's answer
    and must never be re-derived here from ``status``: ``merge.MergeResult`` owns what
    counts as merged, and a second copy of that rule in a module that cannot import it is
    how two definitions of one fact come to disagree.
    """

    status: str
    detail: str
    passed: bool


def _changed_facts(changed: tuple[str, ...]) -> tuple[int, str]:
    """How many distinct paths *changed* holds, and the sha256 of them sorted (pure).

    Sorted: a reader deriving the paths from the commit reads them in git's order.
    """
    paths = sorted(set(changed))
    return len(paths), hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


def summary_payload(
    issue_id: str, why: str, built: tuple[str, tuple[str, ...]], self_check: SelfCheck
) -> dict:
    """The ``change-summary`` payload for one landing (pure).

    Every argument is a fact the engine already holds at the build->verify transition —
    the bead's title, the branch head and changed paths read before the merge, and the
    landing's own verdict. None of it is composed by the agent, which is why this
    artifact needs no output contract for a model to satisfy.

    The paths go in as a count and a digest, not as the list (`basicly-gvlpxm`, D-36): the
    list is the only part that grows with the diff, and the only part a reader recovers
    from the ``commit`` beside it — the digest tells it the recovery was complete.
    """
    commit, changed = built
    count, digest = _changed_facts(changed)
    return {
        "schema_version": SCHEMA_VERSION,
        "issue": issue_id,
        "why": why,
        "commit": commit,
        "changed_count": count,
        "changed_digest": digest,
        "self_check": {
            "status": self_check.status,
            "passed": self_check.passed,
            "detail": self_check.detail,
        },
    }
