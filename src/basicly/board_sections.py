"""The board producer's section reducers: one function per section of the snapshot.

The boundary is which rows a section is, against what may cross the wire
(:mod:`basicly.board_fields`). Every string still leaves through :func:`board_fields.text`, so
that rule is enforced in one place and consumed in seven (basicly-y754k2).
"""

# comment-density-waiver: cohesion: 51.5% after the split that basicly-y754k2 asked for,
# and the split is the cause: seven reducers each carry the contract a schema consumer needs
# - which rows a section is, which fields are omitted rather than guessed - against a body
# that is one comprehension. Measured at every step down from 55.1%: the stale `_session`
# cross-reference
# and the restatement of what `test_board_sections` asserts are gone. What remains is the
# 140/203/1 pairing criterion, the second-fold cost this reader exists to avoid, and why a
# lane phase is an argument. Deleting any of those is what the cap exists to prevent.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_fields

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

# module-size-waiver: cost(basicly-k6tpep.1): 4416 of 4000 tokens. `lanes[].state` and the
# two keys that date and explain it added 432: the closed set this layer bounds the wire
# with, three fields, and `_standing`. The nameable cut is the one `board_facts` and
# `board_regions` already name in their own waivers - `LaneFacts`, `lanes` and `_standing`
# into `board_lane.py` - and it needs a line in `.importlinter`, whose entries leave no
# module unlisted. No prose was cut to pay for this; the density share is waived already.

# The separator a wait id uses to carry its subject, read only by :func:`asks`.
_SUBJECT_SEP = "#wait-"

# The closed set `board-snapshot.schema.json` permits on `lanes[].state`, spelled here
# because this layer bounds what reaches the wire and a value outside a closed set costs the
# whole `lanes` section rather than one key. `supervise.LANE_*` are the writers and the
# schema is the contract; `tests/test_board_facts.py` asserts the three agree, because three
# spellings of one closed set is exactly the drift a shipped consumer refuses a document for.
LANE_STATES = frozenset({"queued", "running", "waits-to-land", "landing", "refused", "parked"})


@dataclass(frozen=True)
class LaneFacts:
    """One in-flight lane, supplied by a caller that drives the loop rather than derived.

    **`phase` is why this is an argument.** The schema requires it, and its authority is
    `loop_state.read_node_state`, which calls `validate_gate.required_config` to learn what
    the unit owes before it reads a gate. That required-gate set is a fourth source, outside
    the three files this producer opens, and a phase folded out of ledger evidence alone
    diverges from the engine's for any unit owing validation. The schema has no field marking
    a value as derived, so the two would render identically. Caller-supplied or omitted, and
    nothing between - the same rule the supervisor lock takes in `board_snapshot.SessionFacts`.

    The first eight fields are what a caller above the loop already holds:
    `supervise.LaneView` carries `issue_id`, `status`, `last_agent`, `last_tokens`,
    `branch` and `last_run_at`, and the phase comes from the loop read beside it. The schema's
    other lane properties - `model`, `cost_usd`, `elapsed_s`, the `context_used` pair, the
    rework counters, `note` - are carried too, on the same rule: emitted only where the caller
    held one, so an omitted property renders as absent and a guessed one would render as
    fact. It bites hardest on a *live* lane, whose last run holds a cost and an occupancy
    for a different dispatch; carrying those forward states this run's spend as last run's.

    `live` and `provisioned` used to share one key (basicly-ze0po3): `live` is an agent inside
    the lane now, `provisioned` is only its worktree existing. `board_facts._lane_fact` says why.

    `state` is where the lane stands in the *pass*, which `live` true-or-false had nothing
    between: a finished lane waiting for the merge queue, a lane being landed and one the WIP
    bound refused all read as an idle build (basicly-ncday7). Bounded to :data:`LANE_STATES`.
    """

    id: str
    phase: str
    status: str = ""
    state: str = ""
    state_detail: str = ""
    state_since: str = ""
    agent: str = ""
    live: bool | None = None
    provisioned: bool | None = None
    started_at: str = ""
    tokens: int | None = None
    branch: str = ""
    model: str = ""
    cost_usd: float | None = None
    elapsed_s: float | None = None
    context_used: int | None = None
    context_window: int | None = None
    rework_attempt: int | None = None
    rework_allowance: int | None = None
    note: str = ""


@dataclass(frozen=True)
class RepoFacts:
    """Which checkout the document is about, past the name a path component answers.

    **A subprocess is the whole reason this is an argument.** ``dirty`` is the index against
    the working tree - ``git status`` and nothing cheaper - while the producer's contract is
    that it opens files and spawns nothing, pinned by a spy in
    ``tests/test_board_snapshot.py``. Reading git state below this line would break that for
    every caller to serve one, so the caller already running git supplies all three at once.
    """

    branch: str = ""
    head: str = ""
    dirty: bool | None = None


def repo(name: str, facts: RepoFacts | None) -> dict[str, object]:
    """Which checkout this is: *name*, plus whatever git state the caller held.

    A field the caller left empty omits its key rather than filling one, because a branch with
    no ``dirty`` beside it is a weaker claim than a clean tree that is not clean.
    """
    section: dict[str, object] = {"name": board_fields.text(name, board_fields.ID_MAX)}
    if facts is None:
        return section
    if facts.branch:
        section["branch"] = board_fields.text(facts.branch, board_fields.TEXT_MAX)
    if facts.head:
        section["head"] = board_fields.text(facts.head, board_fields.HEAD_MAX)
    if facts.dirty is not None:
        section["dirty"] = facts.dirty
    return section


@dataclass(frozen=True)
class Readiness:
    """Which records the tracker calls ready and which it calls blocked, from the caller.

    **Two sets rather than a predicate, because the third answer is the point.** A record in
    neither set is one the tracker's own ready walk did not rule on, and :meth:`flag` returns
    None for it so the row omits ``ready`` instead of reading False. `ready` is a derivation
    over a status vocabulary and the whole edge population - the kit's `queries` owns it - and
    a second spelling here is how two derivations come to disagree.
    """

    ready: frozenset[str] = frozenset()
    blocked: frozenset[str] = frozenset()

    def flag(self, record: str) -> bool | None:
        """Whether *record* is ready; None when neither set names it."""
        if record in self.ready:
            return True
        return False if record in self.blocked else None


def asks(
    markers: Sequence[board_fields.Marker],
    *,
    now: datetime,
    questions: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """The pending asks: a wait whose id no marker anywhere reports as answered.

    **Pairing, not counting, and that is the whole criterion.** Reading every request as
    pending reports **140** against **1** genuinely open, with **203** distinct answered ids
    behind it - so a parser matching nothing still looks plausible on the pending count, and
    the test pins all three. Order-independent, ``policy._open_wait_stamp``'s rule: an answer
    anywhere closes the wait, and comment order is not chronological.

    ``waiting_s`` is *now* minus the request stamp, and *now* is the caller's injected instant
    rather than a reading taken here - the same stamp the document is dated with, so the two
    can never disagree. ``question`` is caller-supplied and keyed by wait id: a request marker
    carries no prose at all (``policy.record_wait_request`` writes id, kind and ``requested``),
    so the wording exists only on the decision queue and a wait with none keeps the key absent.
    """
    waits = [row for row in markers if row.family == board_fields.WAIT_FAMILY]
    answered = {
        row.fields["id"]
        for row in waits
        if "id" in row.fields and board_fields.ANSWERED in row.flags
    }
    pending = []
    for row in waits:
        wait_id = row.fields.get("id", "")
        kind = row.fields.get("kind", "")
        requested = board_fields.instant(row.at)
        if not wait_id or not kind or wait_id in answered or requested is None:
            continue
        ask: dict[str, object] = {
            "wait_id": board_fields.text(wait_id, board_fields.WAIT_ID_MAX),
            "kind": board_fields.text(kind, board_fields.KIND_MAX),
            "requested_at": board_fields.stamp(requested),
            "issue": board_fields.text(row.record, board_fields.ID_MAX),
        }
        ask["waiting_s"] = max(0.0, (now - requested).total_seconds())
        if subject := wait_id.partition(_SUBJECT_SEP)[2]:
            ask["subject"] = board_fields.text(subject, board_fields.TEXT_MAX)
        if question := (questions or {}).get(wait_id, ""):
            ask["question"] = board_fields.text(question, board_fields.QUESTION_MAX)
        pending.append(ask)
    return pending


def _standing(lane: LaneFacts) -> dict[str, object]:
    """*lane*'s pass-state keys, or nothing where its state is outside the closed set.

    The three travel together under one guard: a state with no stamp cannot be aged, and a
    detail with no state has nothing to explain. A value the schema does not permit is
    dropped rather than clipped, because a closed set refused costs the whole section.
    """
    if lane.state not in LANE_STATES:
        return {}
    held: dict[str, object] = {"state": lane.state}
    if lane.state_detail:
        held["state_detail"] = board_fields.text(lane.state_detail, board_fields.TEXT_MAX)
    if (entered := board_fields.instant(lane.state_since)) is not None:
        held["state_since"] = board_fields.stamp(entered)
    return held


def lanes(facts: Iterable[LaneFacts]) -> list[dict[str, object]]:
    """*facts* as bounded lane rows, one per lane, in the order the caller supplied.

    A lane missing either required value is skipped rather than completed: `id` and `phase`
    are the two the schema refuses a row without, and a row invented for a lane whose phase
    the caller could not read is the estimate :class:`LaneFacts` exists to refuse. Every
    other value is emitted only when the caller held one, so no lane panel draws a zero it
    was not given. An empty result is still a section: `[]` is a pass with nothing running,
    and *absent* is a producer that cannot see lanes - the schema separates the two.
    """
    rows = []
    for lane in facts:
        if not lane.id or not lane.phase:
            continue
        row: dict[str, object] = {
            "id": board_fields.text(lane.id, board_fields.ID_MAX),
            "phase": board_fields.text(lane.phase, board_fields.KIND_MAX),
        }
        if lane.status:
            row["status"] = board_fields.text(lane.status, board_fields.KIND_MAX)
        row.update(_standing(lane))
        if lane.agent:
            row["agent"] = board_fields.text(lane.agent, board_fields.AGENT_MAX)
        row.update(
            (name, flag)
            for name, flag in (("live", lane.live), ("provisioned", lane.provisioned))
            if flag is not None
        )
        if (started := board_fields.instant(lane.started_at)) is not None:
            row["started_at"] = board_fields.stamp(started)
        if lane.tokens is not None:
            row["tokens"] = max(0, lane.tokens)
        if lane.branch:
            row["branch"] = board_fields.text(lane.branch, board_fields.TEXT_MAX)
        if lane.model:
            row["model"] = board_fields.text(lane.model, board_fields.AGENT_MAX)
        if lane.note:
            row["note"] = board_fields.text(lane.note, board_fields.TEXT_MAX)
        for name, held in (
            ("cost_usd", lane.cost_usd),
            ("elapsed_s", lane.elapsed_s),
            ("context_used", lane.context_used),
            ("context_window", lane.context_window),
            ("rework_attempt", lane.rework_attempt),
            ("rework_allowance", lane.rework_allowance),
        ):
            if held is not None:
                row[name] = max(0, held)
        rows.append(row)
    return rows


def units(
    states: Iterable[Any],
    *,
    phases: Mapping[str, str] | None = None,
    ready: Readiness | None = None,
) -> list[dict[str, object]]:
    """One bounded row per folded record in *states*, at the fields a board draws.

    **This is the rule at its sharpest: fields, never records.** A folded record carries its
    description, its acceptance criteria and every comment body, and a row shaped like one
    would put 1,472,207 tokens on the wire against 11,113 for the selection - the 132.5x this
    module exists for. `title` is the only prose admitted and it is bounded, so a description
    cannot arrive by being called a title.

    `phase` and `ready` reach a row from *phases* and *ready* and from nowhere else, which is
    the same rule :class:`LaneFacts` states one section over: `phase`'s authority is
    `loop_state.derive_phase` reading a required-gate set outside the three files this producer
    opens, and `ready` is the tracker's own walk. Neither map has to be complete - a record
    absent from *phases* keeps `phase` absent, and :meth:`Readiness.flag` returning None keeps
    `ready` absent - because the schema has no field marking a value as derived here, so a
    guess would render identically to a read.
    """
    rows = []
    for state in states:
        row: dict[str, object] = {"id": board_fields.text(state.record, board_fields.ID_MAX)}
        if title := state.fields.get("title"):
            row["title"] = board_fields.text(title, board_fields.TEXT_MAX)
        if state.status:
            row["status"] = board_fields.text(state.status, board_fields.KIND_MAX)
        priority = state.fields.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool):
            row["priority"] = board_fields.text(f"P{priority}", board_fields.PRIORITY_MAX)
        if kind := state.fields.get("issue_type"):
            row["type"] = board_fields.text(kind, board_fields.KIND_MAX)
        if phase := (phases or {}).get(state.record, ""):
            row["phase"] = board_fields.text(phase, board_fields.KIND_MAX)
        if ready is not None and (flag := ready.flag(state.record)) is not None:
            row["ready"] = flag
        rows.append(row)
    return rows


def edge_triples(kit: Any, collected: Iterable[Any]) -> list[tuple[str, str, str]]:
    """Every edge the log still asserts, as ``(source, kind, target)``, last statement wins.

    **Read off the events rather than through the kit's own `views_from_events`, and the
    reason is the one guarantee this producer sells.** That function folds the log a second
    time to answer this, and a second fold is exactly what makes `observe()` cost 6.1 s over
    93 of them. So the caller's already-read event list is walked once more here, which is a
    pass and not a fold.

    Nothing about the dialect is respelled: the kinds, the payload keys and the ordering are
    all *kit* values reached through the sanctioned attribute chain, and
    `tests/test_board_sections.py` binds the result to `views_from_events`'s own edge set on
    a corpus holding a retraction. A retracted edge is absent here while both of its events
    stay in the log, which is what makes a retraction not a deletion.
    """
    held: dict[tuple[str, str, str], bool] = {}
    for event in kit.events.canonical_order(collected):
        if event.kind not in (kit.events.KIND_EDGE, kit.events.KIND_EDGE_RETRACTED):
            continue
        target = event.payload.get(kit.migrate.EDGE_TO)
        kind = event.payload.get(kit.migrate.EDGE_TYPE)
        if isinstance(target, str) and isinstance(kind, str):
            held[(event.record, kind, target)] = event.kind == kit.events.KIND_EDGE
    return [edge for edge, asserted in held.items() if asserted]


def graph(triples: Iterable[tuple[str, str, str]]) -> dict[str, object]:
    """*triples* as the `graph` section: `from`, `to` and the edge kind, each bounded.

    Separate from :func:`units` because edges answer the one question a count of blocked
    items raises and cannot settle. The kind is passed through rather than mapped: the schema
    leaves it an open string, so a foreign harness's own vocabulary crosses unaltered.
    """
    return {
        "edges": [
            {
                "from": board_fields.text(source, board_fields.ID_MAX),
                "to": board_fields.text(target, board_fields.ID_MAX),
                "kind": board_fields.text(kind, board_fields.KIND_MAX),
            }
            for source, kind, target in triples
        ]
    }


def events(markers: Sequence[board_fields.Marker], limit: int) -> list[dict[str, object]]:
    """The last *limit* marker rows, each as its family and its declared fields.

    The family is the event kind, which is where the whole roster is used and why all 12 are
    parsed. ``text`` is the header's fields re-rendered, never the body.
    """
    rows = []
    for row in markers[-limit:]:
        at = board_fields.instant(row.at)
        if at is None:
            continue
        declared = " ".join(f"{name}={value}" for name, value in sorted(row.fields.items()))
        rows.append({
            "at": board_fields.stamp(at),
            "issue": board_fields.text(row.record, board_fields.ID_MAX),
            "kind": board_fields.text(row.family.strip("[]"), board_fields.KIND_MAX),
            "text": board_fields.text(
                " ".join([*sorted(row.flags), declared]).strip(), board_fields.TEXT_MAX
            ),
        })
    return rows
