"""Tests for the owned ranking (basicly-vkh0.20).

The bead's first criterion is a *negative* one — no age term contributes — and a negative
is the shape that passes vacuously. So it is asserted as a pair, and the second half is
what makes the first mean anything:

- **The kit ranks an unchanged graph identically under two clocks.** Two real ledgers are
  written from the same drafts with `events.append`'s clock injected, so they differ in
  every ``ts`` *and* in the ``created_at`` field an import carries onto the ``created``
  event — and in nothing else.
- **The ordering it replaces would have differed under those same two clocks.**
  :func:`_age_ordered` is br's documented fallback policy, ``priority ASC, created_at ASC,
  id ASC``, run over the same two folds. It reverses. Without it, a scheduler that ranked
  everything by id would pass the first assertion while proving nothing.

Neither half waits: the clocks are arguments, so the difference between "old" and "new" is
test data rather than elapsed time, and the whole file runs in whatever order pytest picks.

The second criterion — the score stays explainable — is asserted by decoding: a score
carries both its terms and :func:`scheduler.explain` recovers them, which is what a reader
of a months-old dispatch marker has instead of the graph.

Every ordering test authors :class:`scheduler.Candidate` values directly. The ledger is
exercised end to end too, but a sort is clearer to read as its inputs than as a log.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    """Load a standalone kit module by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scheduler = _load(KIT_DIR / "scheduler.py", "tracker_scheduler")
# The modules the scheduler itself loaded, not second copies: two loads of one file give
# two `RecordView` classes, and a candidate built against one is not the type the other's
# `is_ready` reads.
differential = scheduler.differential
events = scheduler.events
migrate = differential.migrate

VOCAB = differential.DEFAULT_VOCABULARY

# The same three-record graph, written twice under these. Under A the ids are also the age
# order; under B the oldest record is the last id, so the two disagree maximally.
CLOCK_A = (1_000_000_000.0, 1_000_000_060.0, 1_000_000_120.0)
CLOCK_B = tuple(reversed(CLOCK_A))

GRAPH = (("sched-aa01", 2), ("sched-bb02", 2), ("sched-cc03", 2))


# --- authored candidates ------------------------------------------------------


def _view(
    record: str,
    *,
    status: str = "open",
    dependencies: Sequence[tuple[str, str]] = (),
    tombstoned: bool = False,
) -> Any:
    """One record as the graph holds it, edges given as ``(target, type)`` pairs."""
    return differential.RecordView(
        record=record,
        status=status,
        dependencies=tuple(
            differential.Edge(target=target, type=kind) for target, kind in dependencies
        ),
        tombstoned=tombstoned,
    )


def _candidate(record: str, *, priority: int = 2, title: str = "", **view: Any) -> Any:
    return scheduler.Candidate(view=_view(record, **view), priority=priority, title=title)


def _population(*candidates: Any) -> dict[str, Any]:
    return {candidate.record: candidate for candidate in candidates}


def _order(ranking: Any) -> list[str]:
    return [entry.record for entry in ranking.records]


# --- ledgers, stamped by an injected clock ------------------------------------


def _fixed(stamp: float) -> Callable[[], float]:
    """A clock that always reads *stamp*. Bound here so no closure captures a loop name."""
    return lambda: stamp


def _iso(stamp: float) -> str:
    """*stamp* as the ISO-8601 UTC text an export's ``created_at`` carries."""
    return datetime.datetime.fromtimestamp(stamp, tz=datetime.UTC).isoformat()


def _write(directory: Path, stamps: Sequence[float]) -> None:
    """Write :data:`GRAPH` into *directory*, each record stamped by its own clock.

    The clock reaches the ledger both ways an age term could ever arrive: as the event's
    own ``ts``, and as the ``created_at`` field `migrate.py` imports verbatim onto the
    ``created`` event. Everything else — ids, priorities, statuses — is identical between
    the two runs, which is what makes "the same graph" a fact rather than a claim.
    """
    for (record, priority), stamp in zip(GRAPH, stamps, strict=True):
        events.append(
            directory,
            [
                events.Draft(
                    record,
                    events.KIND_CREATED,
                    {"title": record, "priority": priority, "created_at": _iso(stamp)},
                ),
                events.Draft(record, events.KIND_STATUS, {"status": "open"}),
            ],
            clock=_fixed(stamp),
        )


def _age_ordered(ledger_events: Iterable[Any]) -> list[str]:
    """The records under the ordering the kit replaces: ``priority ASC, created_at ASC, id ASC``.

    br's own documented ``fallback_policy``, quoted in `work-tracker.md` §9.2 and
    reproduced here as the control. It reads ``created_at`` off the fold, which is exactly
    where the ledger keeps it.
    """
    folded = events.fold(ledger_events)

    def key(record: str) -> tuple[Any, Any, str]:
        fields: Mapping[str, Any] = folded.records[record].fields
        return (fields.get("priority"), fields.get("created_at"), record)

    return sorted(folded.records, key=key)


# --- the age term, dropped ----------------------------------------------------


def test_two_clocks_move_the_age_ordering_and_leave_the_owned_ranking_identical(
    tmp_path: Path,
) -> None:
    """The bead's first criterion, with its own discrimination control attached.

    The two ledgers are the same work written by two clocks. The replaced ordering reverses
    between them; the owned one is equal down to the scores. A ranking that ignored the
    graph entirely would pass the second assertion, which is why the first is here.
    """
    early, late = tmp_path / "early", tmp_path / "late"
    _write(early, CLOCK_A)
    _write(late, CLOCK_B)

    events_early = differential.read_ledger(early)
    events_late = differential.read_ledger(late)
    # The positive control for the fixture itself: the clock really did reach the ledger,
    # so a "no difference" result below cannot be two identical files agreeing.
    assert [event.ts for event in events_early] != [event.ts for event in events_late]

    assert _age_ordered(events_early) == ["sched-aa01", "sched-bb02", "sched-cc03"]
    assert _age_ordered(events_late) == ["sched-cc03", "sched-bb02", "sched-aa01"]

    assert scheduler.ranking(early) == scheduler.ranking(late)
    assert _order(scheduler.ranking(early)) == ["sched-aa01", "sched-bb02", "sched-cc03"]


def test_the_candidate_cannot_reach_the_age_the_ledger_holds(tmp_path: Path) -> None:
    """Purity by structure: the fold carries ``created_at`` and the ranking's input does not.

    The behavioural test above shows the age is not *used*; this shows it is not *reachable*,
    which is the property that survives somebody editing the scorer later.
    """
    _write(tmp_path, CLOCK_A)
    ledger_events = differential.read_ledger(tmp_path)
    folded = events.fold(ledger_events)
    assert "created_at" in folded.records["sched-aa01"].fields

    candidate = scheduler.candidates_from_events(ledger_events)["sched-aa01"]
    reachable = {field.name for field in dataclasses.fields(candidate)} | {
        field.name for field in dataclasses.fields(candidate.view)
    }
    assert "created_at" not in reachable
    assert not any("time" in name or "_at" in name for name in reachable)


# --- the ordering -------------------------------------------------------------


def test_priority_outranks_the_critical_path() -> None:
    """A P0 blocking nothing still goes before a P1 blocking work. §9.2's order, in order."""
    ranking = scheduler.rank(
        _population(
            _candidate("sched-critical", priority=0),
            _candidate("sched-busy", priority=1),
            _candidate("sched-blocked-a", dependencies=[("sched-busy", "blocks")]),
            _candidate("sched-blocked-b", dependencies=[("sched-busy", "blocks")]),
        )
    )
    assert _order(ranking)[:2] == ["sched-critical", "sched-busy"]


def test_dependents_break_a_priority_tie_most_blocked_first() -> None:
    """Two P2s tied on priority: the one releasing more work goes first."""
    ranking = scheduler.rank(
        _population(
            _candidate("sched-zzzz"),
            _candidate("sched-aaaa"),
            _candidate("sched-w1", dependencies=[("sched-zzzz", "blocks")]),
            _candidate("sched-w2", dependencies=[("sched-zzzz", "blocks")]),
        )
    )
    # `sched-aaaa` sorts first by id and loses anyway, so this cannot pass by tie-break.
    assert _order(ranking)[:2] == ["sched-zzzz", "sched-aaaa"]


def test_id_breaks_a_full_tie_so_the_order_is_total() -> None:
    """Same priority, same dependents: ascending id, deterministically."""
    ranking = scheduler.rank(
        _population(_candidate("sched-cc"), _candidate("sched-aa"), _candidate("sched-bb"))
    )
    assert _order(ranking) == ["sched-aa", "sched-bb", "sched-cc"]
    assert [entry.rank for entry in ranking.records] == [1, 2, 3]


def test_a_finished_or_merely_related_dependent_is_not_work_to_unblock() -> None:
    """Both filters on the critical-path term, each with a live counter-example beside it.

    A closed dependent is work already done and a ``related`` dependent was never waiting,
    so neither should lift a record over one blocking a live ``blocks`` dependent.
    """
    population = _population(
        _candidate("sched-real"),
        _candidate("sched-fake"),
        _candidate("sched-live", dependencies=[("sched-real", "blocks")]),
        _candidate("sched-done", status="closed", dependencies=[("sched-fake", "blocks")]),
        _candidate("sched-gone", tombstoned=True, dependencies=[("sched-fake", "blocks")]),
        _candidate("sched-aside", dependencies=[("sched-fake", "related")]),
    )
    counts = scheduler.dependents_of(
        {record: candidate.view for record, candidate in population.items()}, VOCAB
    )
    assert counts.get("sched-real") == 1
    assert counts.get("sched-fake") is None
    assert _order(scheduler.rank(population))[0] == "sched-real"


def test_only_ready_records_are_ranked() -> None:
    """The ready rule is `differential.is_ready`'s, and every clause of it is refused here."""
    ranking = scheduler.rank(
        _population(
            _candidate("sched-ready"),
            _candidate("sched-parent"),
            _candidate("sched-child", dependencies=[("sched-parent", "parent-child")]),
            _candidate("sched-open"),
            _candidate("sched-waiting", dependencies=[("sched-open", "blocks")]),
            _candidate("sched-parked", status="deferred"),
            _candidate("sched-shut", status="closed"),
            _candidate("sched-deleted", tombstoned=True),
        )
    )
    # `sched-open` blocks one live record and leads on that; the other two are tied and
    # fall to id. Everything else is refused by one clause each.
    assert _order(ranking) == ["sched-open", "sched-child", "sched-ready"]


# --- the score, as evidence ---------------------------------------------------


def test_the_score_decodes_back_into_the_terms_that_built_it() -> None:
    """The bead's second criterion: a recorded score stays readable without the graph."""
    ranking = scheduler.rank(
        _population(
            _candidate("sched-hot", priority=0),
            _candidate("sched-w1", dependencies=[("sched-hot", "blocks")]),
            _candidate("sched-w2", dependencies=[("sched-hot", "blocks")]),
        )
    )
    hot = next(entry for entry in ranking.records if entry.record == "sched-hot")
    assert scheduler.explain(hot.score) == scheduler.ScoreTerms(priority=0, dependents=2)


@pytest.mark.parametrize("dependents", [0, 1, 7, scheduler.DEPENDENT_CEILING])
@pytest.mark.parametrize("priority", [0, 1, 2, 3, 4])
def test_every_score_in_the_band_decodes_to_its_own_terms(priority: int, dependents: int) -> None:
    """:func:`scheduler.explain` inverts :func:`scheduler.score` across br's whole band."""
    assert scheduler.explain(scheduler.score(priority, dependents)) == scheduler.ScoreTerms(
        priority=priority, dependents=dependents
    )


def test_the_critical_path_term_saturates_and_never_outranks_a_priority() -> None:
    """The bound that makes "priority dominates" unconditional rather than usually true."""
    ceiling = scheduler.DEPENDENT_CEILING
    assert scheduler.score(2, ceiling + 1) == scheduler.score(2, ceiling)
    # The strongest case for domination: a backlog record blocking the entire tracker still
    # scores under a critical one blocking nothing.
    assert scheduler.score(4, ceiling * 100) < scheduler.score(3, 0)


@pytest.mark.parametrize("priority", [None, "2", True, 1.5])
def test_a_priority_the_ledger_cannot_type_reads_as_brs_default(priority: object) -> None:
    """An unusable priority is the P2 br would have applied, never a refusal or a P1.

    ``True`` is the one that would slip through an ``isinstance(value, int)`` guard and
    silently rank a record as critical-adjacent.
    """
    fields = {} if priority is None else {"priority": priority}
    assert scheduler._priority(fields) == scheduler.DEFAULT_PRIORITY


# --- the answer's envelope ----------------------------------------------------


def test_the_answer_names_the_policy_that_produced_it() -> None:
    """A rank without its policy is uninterpretable (basicly-vkh0.3), and now ambiguous too.

    Two scorers exist in recorded history after the flip, so the schema is what tells a
    marker written under ``tracker.scheduler.v1`` from one written under this.
    """
    ranking = scheduler.rank(_population(_candidate("sched-aa")))
    assert ranking.schema == "basicly.scheduler.v1"
    assert ranking.sort == "priority ASC, dependents DESC, id ASC"
    assert "created_at" not in ranking.sort


def test_limit_keeps_the_top_of_the_order() -> None:
    """A limit truncates the answer, it does not change the ordering it truncates."""
    ranking = scheduler.rank(
        _population(_candidate("sched-cc"), _candidate("sched-aa"), _candidate("sched-bb")),
        limit=2,
    )
    assert _order(ranking) == ["sched-aa", "sched-bb"]


def test_a_negative_limit_is_refused_rather_than_slicing_from_the_wrong_end() -> None:
    """A negative slice would silently return the *worst* records, so it is refused."""
    with pytest.raises(scheduler.SchedulerError, match="negative"):
        scheduler.rank(_population(_candidate("sched-aa")), limit=-1)


# --- the ledger path ----------------------------------------------------------


def test_ranking_reads_a_ledger_end_to_end(tmp_path: Path) -> None:
    """The convenience half: priority, title and edges all come off the events."""
    events.append(
        tmp_path,
        [
            events.Draft("sched-epic", events.KIND_CREATED, {"title": "the epic", "priority": 0}),
            events.Draft("sched-epic", events.KIND_STATUS, {"status": "open"}),
            events.Draft("sched-leaf", events.KIND_CREATED, {"title": "the leaf", "priority": 3}),
            events.Draft("sched-leaf", events.KIND_STATUS, {"status": "open"}),
            events.Draft(
                "sched-leaf",
                migrate.KIND_EDGE,
                {migrate.EDGE_TO: "sched-epic", migrate.EDGE_TYPE: VOCAB.parent_child_type},
            ),
        ],
        clock=_fixed(CLOCK_A[0]),
    )
    ranking = scheduler.ranking(tmp_path)

    # The epic has a child, so `is_ready` refuses it however critical it is.
    assert _order(ranking) == ["sched-leaf"]
    assert ranking.records[0].title == "the leaf"
    assert scheduler.explain(ranking.records[0].score).priority == 3


def test_an_empty_ledger_ranks_to_an_empty_answer_carrying_its_policy(tmp_path: Path) -> None:
    """Nothing ready is a legitimate answer, and it still says which scorer said so."""
    ranking = scheduler.ranking(tmp_path)
    assert ranking.records == ()
    assert ranking.schema == scheduler.SCHEMA
