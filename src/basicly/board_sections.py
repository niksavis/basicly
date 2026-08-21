"""The board producer's section reducers: one function per section of the snapshot.

The boundary is which rows a section is, against what may cross the wire
(:mod:`basicly.board_fields`). Every string still leaves through :func:`board_fields.text`, so
that rule is enforced in one place and consumed in six (basicly-y754k2).
"""

# comment-density-waiver: cohesion: 51.5% after the split that basicly-y754k2 asked for,
# and the split is the cause: six reducers each carry the contract a schema consumer needs
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
    from collections.abc import Iterable, Sequence

# The separator a wait id uses to carry its subject, read only by :func:`asks`.
_SUBJECT_SEP = "#wait-"


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

    The eight fields are exactly what a caller above the loop already holds:
    `supervise.LaneView` carries `issue_id`, `status`, `last_agent`, `live`, `last_tokens`,
    `branch` and `last_run_at`, and the phase comes from the loop read beside it. The schema's
    other lane properties - `model`, `cost_usd`, `elapsed_s`, the `context_used` pair, the
    rework counters, `note` - are unemitted until a caller holds them, because an omitted
    property renders as absent while a guessed one renders as fact.
    """

    id: str
    phase: str
    status: str = ""
    agent: str = ""
    live: bool | None = None
    started_at: str = ""
    tokens: int | None = None
    branch: str = ""


def asks(markers: Sequence[board_fields.Marker]) -> list[dict[str, object]]:
    """The pending asks: a wait whose id no marker anywhere reports as answered.

    **Pairing, not counting, and that is the whole criterion.** Reading every request as
    pending reports **140** against **1** genuinely open, with **203** distinct answered ids
    behind it - so a parser matching nothing still looks plausible on the pending count, and
    the test pins all three. Order-independent, ``policy._open_wait_stamp``'s rule: an answer
    anywhere closes the wait, and comment order is not chronological.

    No ``waiting_s``: the schema leaves it optional, a consumer holding ``requested_at`` and
    the document's ``generated_at`` has it exactly, and subtracting two wall-clock readings in
    the engine is what ``test_no_engine_interval_is_measured_on_a_wall_clock`` refuses.
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
        if subject := wait_id.partition(_SUBJECT_SEP)[2]:
            ask["subject"] = board_fields.text(subject, board_fields.TEXT_MAX)
        pending.append(ask)
    return pending


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
        if lane.agent:
            row["agent"] = board_fields.text(lane.agent, board_fields.AGENT_MAX)
        if lane.live is not None:
            row["live"] = lane.live
        if (started := board_fields.instant(lane.started_at)) is not None:
            row["started_at"] = board_fields.stamp(started)
        if lane.tokens is not None:
            row["tokens"] = max(0, lane.tokens)
        if lane.branch:
            row["branch"] = board_fields.text(lane.branch, board_fields.TEXT_MAX)
        rows.append(row)
    return rows


def units(states: Iterable[Any]) -> list[dict[str, object]]:
    """One bounded row per folded record in *states*, at the five fields a board draws.

    **This is the rule at its sharpest: fields, never records.** A folded record carries its
    description, its acceptance criteria and every comment body, and a row shaped like one
    would put 1,472,207 tokens on the wire against 11,113 for the selection - the 132.5x this
    module exists for. `title` is the only prose admitted and it is bounded, so a description
    cannot arrive by being called a title.

    Two properties the schema offers are deliberately not filled. `phase` has the same
    authority problem :class:`LaneFacts` documents, and `ready` is the tracker's own
    derivation over a status vocabulary and the whole edge population - the same reason
    `backlog` carries no `ready` or `blocked`. A second spelling of either here is how two
    derivations come to disagree, so both stay absent until a caller supplies them.
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
