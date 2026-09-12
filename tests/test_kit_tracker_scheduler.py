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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scheduler = _load(KIT_DIR / "scheduler.py", "tracker_scheduler")
differential = scheduler.differential
events = scheduler.events
migrate = differential.migrate

VOCAB = differential.DEFAULT_VOCABULARY

CLOCK_A = (1_000_000_000.0, 1_000_000_060.0, 1_000_000_120.0)
CLOCK_B = tuple(reversed(CLOCK_A))

GRAPH = (("sched-aa01", 2), ("sched-bb02", 2), ("sched-cc03", 2))


def _view(
    record: str,
    *,
    status: str = "open",
    dependencies: Sequence[tuple[str, str]] = (),
    tombstoned: bool = False,
) -> Any:
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


def _fixed(stamp: float) -> Callable[[], float]:
    return lambda: stamp


def _iso(stamp: float) -> str:
    return datetime.datetime.fromtimestamp(stamp, tz=datetime.UTC).isoformat()


def _write(directory: Path, stamps: Sequence[float]) -> None:

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

    folded = events.fold(ledger_events)

    def key(record: str) -> tuple[Any, Any, str]:
        fields: Mapping[str, Any] = folded.records[record].fields
        return (fields.get("priority"), fields.get("created_at"), record)

    return sorted(folded.records, key=key)


def test_two_clocks_move_the_age_ordering_and_leave_the_owned_ranking_identical(
    tmp_path: Path,
) -> None:

    early, late = tmp_path / "early", tmp_path / "late"
    _write(early, CLOCK_A)
    _write(late, CLOCK_B)

    events_early = differential.read_ledger(early)
    events_late = differential.read_ledger(late)
    assert [event.ts for event in events_early] != [event.ts for event in events_late]

    assert _age_ordered(events_early) == ["sched-aa01", "sched-bb02", "sched-cc03"]
    assert _age_ordered(events_late) == ["sched-cc03", "sched-bb02", "sched-aa01"]

    assert scheduler.ranking(early) == scheduler.ranking(late)
    assert _order(scheduler.ranking(early)) == ["sched-aa01", "sched-bb02", "sched-cc03"]


def test_the_candidate_cannot_reach_the_age_the_ledger_holds(tmp_path: Path) -> None:

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


def test_priority_outranks_the_critical_path() -> None:
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
    ranking = scheduler.rank(
        _population(
            _candidate("sched-zzzz"),
            _candidate("sched-aaaa"),
            _candidate("sched-w1", dependencies=[("sched-zzzz", "blocks")]),
            _candidate("sched-w2", dependencies=[("sched-zzzz", "blocks")]),
        )
    )
    assert _order(ranking)[:2] == ["sched-zzzz", "sched-aaaa"]


def test_id_breaks_a_full_tie_so_the_order_is_total() -> None:
    ranking = scheduler.rank(
        _population(_candidate("sched-cc"), _candidate("sched-aa"), _candidate("sched-bb"))
    )
    assert _order(ranking) == ["sched-aa", "sched-bb", "sched-cc"]
    assert [entry.rank for entry in ranking.records] == [1, 2, 3]


def test_a_finished_or_merely_related_dependent_is_not_work_to_unblock() -> None:

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
    assert _order(ranking) == ["sched-open", "sched-child", "sched-ready"]


def test_the_score_decodes_back_into_the_terms_that_built_it() -> None:
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
    assert scheduler.explain(scheduler.score(priority, dependents)) == scheduler.ScoreTerms(
        priority=priority, dependents=dependents
    )


def test_the_critical_path_term_saturates_and_never_outranks_a_priority() -> None:
    ceiling = scheduler.DEPENDENT_CEILING
    assert scheduler.score(2, ceiling + 1) == scheduler.score(2, ceiling)
    assert scheduler.score(4, ceiling * 100) < scheduler.score(3, 0)


@pytest.mark.parametrize("priority", [None, "2", True, 1.5])
def test_a_priority_the_ledger_cannot_type_reads_as_brs_default(priority: object) -> None:

    fields = {} if priority is None else {"priority": priority}
    assert scheduler._priority(fields) == scheduler.DEFAULT_PRIORITY


def test_the_answer_names_the_policy_that_produced_it() -> None:

    ranking = scheduler.rank(_population(_candidate("sched-aa")))
    assert ranking.schema == "basicly.scheduler.v1"
    assert ranking.sort == "priority ASC, dependents DESC, id ASC"
    assert "created_at" not in ranking.sort


def test_limit_keeps_the_top_of_the_order() -> None:
    ranking = scheduler.rank(
        _population(_candidate("sched-cc"), _candidate("sched-aa"), _candidate("sched-bb")),
        limit=2,
    )
    assert _order(ranking) == ["sched-aa", "sched-bb"]


def test_a_negative_limit_is_refused_rather_than_slicing_from_the_wrong_end() -> None:
    with pytest.raises(scheduler.SchedulerError, match="negative"):
        scheduler.rank(_population(_candidate("sched-aa")), limit=-1)


def test_ranking_reads_a_ledger_end_to_end(tmp_path: Path) -> None:
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

    assert _order(ranking) == ["sched-leaf"]
    assert ranking.records[0].title == "the leaf"
    assert scheduler.explain(ranking.records[0].score).priority == 3


def test_an_empty_ledger_ranks_to_an_empty_answer_carrying_its_policy(tmp_path: Path) -> None:
    ranking = scheduler.ranking(tmp_path)
    assert ranking.records == ()
    assert ranking.schema == scheduler.SCHEMA
