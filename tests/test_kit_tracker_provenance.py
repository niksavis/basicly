from __future__ import annotations

import ast
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly import decisions

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
PROVENANCE_SOURCE = KIT_DIR / "provenance.py"
EVENTS_SOURCE = KIT_DIR / "events.py"
IDS_SOURCE = KIT_DIR / "ids.py"
DIFFERENTIAL_SOURCE = KIT_DIR / "differential.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


provenance = _load(PROVENANCE_SOURCE, "tracker_provenance")
events = provenance.events

differential = _load(DIFFERENTIAL_SOURCE, "tracker_differential")
migrate = differential.migrate

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
RECORD_C = "basicly-cc33"
RECORD_D = "basicly-dd44.2"

CLOCK = 1_000_000_000.0

HUMAN_EDGE = provenance.EdgeKey(RECORD_A, "blocks", RECORD_B)
AGENT_EDGE = provenance.EdgeKey(RECORD_A, "couples-with", RECORD_C)
BOUNCE_EDGE = provenance.EdgeKey(RECORD_A, "blocks", RECORD_D)


def _three_derivations() -> list[Any]:
    return [
        provenance.edge_draft(
            HUMAN_EDGE,
            provenance.EXTRACTED,
            detail="declared at decomposition",
            actor="human:owner",
        ),
        provenance.edge_draft(
            AGENT_EDGE,
            provenance.INFERRED,
            detail="both scope globs match src/basicly/cli.py",
            actor="agent:dana",
        ),
        provenance.edge_draft(
            BOUNCE_EDGE,
            provenance.AMBIGUOUS,
            detail="merge bounced; the conflict may be a rebase artifact",
            actor="queue:merge",
        ),
    ]


def _build(directory: Path) -> list[Any]:
    return events.append(directory, _three_derivations(), clock=lambda: CLOCK)


def _folded(directory: Path) -> Any:
    stored, quarantined = events.read_events(directory)
    assert quarantined == []
    return provenance.fold_edges(stored)


def _keys(states: tuple[Any, ...]) -> list[Any]:
    return [state.key for state in states]


def test_each_derivation_records_its_own_label_on_its_own_event(tmp_path: Path) -> None:

    _build(tmp_path)

    stored, _ = events.read_events(tmp_path)
    labelled = {
        (event.payload[provenance.KEY_TYPE], event.payload[provenance.KEY_TARGET]): event.payload[
            provenance.KEY_LABEL
        ]
        for event in stored
    }

    assert labelled == {
        ("blocks", RECORD_B): provenance.EXTRACTED,
        ("couples-with", RECORD_C): provenance.INFERRED,
        ("blocks", RECORD_D): provenance.AMBIGUOUS,
    }
    assert [event.kind for event in stored] == [provenance.KIND_EDGE] * 3
    assert [event.record for event in stored] == [RECORD_A] * 3


def test_only_the_human_asserted_edge_may_gate_a_landing(tmp_path: Path) -> None:

    _build(tmp_path)

    edge_fold = _folded(tmp_path)

    assert _keys(provenance.gating_edges(edge_fold, RECORD_A)) == [HUMAN_EDGE]
    assert _keys(provenance.gating_edges(edge_fold)) == [HUMAN_EDGE]
    assert edge_fold.edges[HUMAN_EDGE].gates is True
    assert edge_fold.edges[AGENT_EDGE].gates is False
    assert edge_fold.edges[BOUNCE_EDGE].gates is False


def test_the_agent_proposal_is_usable_but_visible_as_a_proposal(tmp_path: Path) -> None:

    _build(tmp_path)

    edge_fold = _folded(tmp_path)
    proposals = provenance.edges_by_disposition(edge_fold, provenance.DISPOSITION_PROPOSE)

    assert _keys(proposals) == [AGENT_EDGE]
    state = edge_fold.edges[AGENT_EDGE]
    assert state.label == provenance.INFERRED
    assert state.proposal is True
    assert state.needs_decision is False
    assert state.history[0].detail == "both scope globs match src/basicly/cli.py"
    assert state.history[0].actor == "agent:dana"


def test_the_uncertain_edge_routes_a_decision_and_never_gates(tmp_path: Path) -> None:
    _build(tmp_path)

    edge_fold = _folded(tmp_path)
    requests = provenance.decision_requests(edge_fold)

    assert [request.key for request in requests] == [BOUNCE_EDGE]
    assert requests[0].record == RECORD_A
    assert BOUNCE_EDGE.as_text() in requests[0].question
    assert "merge bounced" in requests[0].detail
    assert BOUNCE_EDGE not in set(_keys(provenance.gating_edges(edge_fold)))


def test_the_routed_item_is_one_the_engines_decision_queue_accepts(tmp_path: Path) -> None:

    _build(tmp_path)

    request = provenance.decision_requests(_folded(tmp_path))[0]

    assert request.kind in decisions.KINDS
    assert provenance.DECISION_KIND in decisions.KINDS
    decision_id = decisions.decision_id_for(request.record, request.kind, request.question)
    assert decisions.split_decision_id(decision_id)[0] == RECORD_A


def test_the_decision_question_does_not_drift_as_the_edges_history_grows(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    before = provenance.decision_requests(_folded(tmp_path))[0]

    events.append(
        tmp_path,
        [
            provenance.edge_draft(
                BOUNCE_EDGE,
                provenance.AMBIGUOUS,
                detail="bounced a second time on the same hunk",
                actor="queue:merge",
            )
        ],
        clock=lambda: CLOCK,
    )
    after = provenance.decision_requests(_folded(tmp_path))[0]

    assert after.question == before.question
    assert after.detail != before.detail
    assert "bounced a second time" in after.detail
    assert decisions.decision_id_for(
        after.record, after.kind, after.question
    ) == decisions.decision_id_for(before.record, before.kind, before.question)


def test_confirming_an_inferred_edge_appends_and_leaves_the_original_line_intact(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    before = log.read_bytes()
    proposal = _folded(tmp_path).edges[AGENT_EDGE].history[0]

    landed = events.append(
        tmp_path,
        [
            provenance.confirmation_draft(
                AGENT_EDGE, detail="owner reviewed the overlap", actor="human:owner"
            )
        ],
        clock=lambda: CLOCK,
    )

    after = log.read_bytes()
    assert after.startswith(before)
    assert after[len(before) :] == (events.to_json(landed[0]) + "\n").encode("utf-8")
    reread = {event.id: event for event in events.read_events(tmp_path)[0]}
    assert reread[proposal.event_id].payload[provenance.KEY_LABEL] == provenance.INFERRED
    assert len(landed) == 1
    assert landed[0].id != proposal.event_id


def test_the_confirmed_edge_gates_and_its_history_reads_as_a_sequence(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [
            provenance.confirmation_draft(
                AGENT_EDGE, detail="owner reviewed the overlap", actor="human:owner"
            )
        ],
        clock=lambda: CLOCK,
    )

    state = _folded(tmp_path).edges[AGENT_EDGE]

    assert [(item.label, item.actor) for item in state.history] == [
        (provenance.INFERRED, "agent:dana"),
        (provenance.EXTRACTED, "human:owner"),
    ]
    assert [item.seq for item in state.history] == [2, 4]
    assert state.label == provenance.EXTRACTED
    assert state.gates is True
    assert AGENT_EDGE in set(_keys(provenance.gating_edges(_folded(tmp_path), RECORD_A)))


def test_re_confirming_the_same_fact_appends_nothing(tmp_path: Path) -> None:

    _build(tmp_path)
    confirm = provenance.confirmation_draft(AGENT_EDGE, detail="reviewed", actor="human:owner")
    events.append(tmp_path, [confirm], clock=lambda: CLOCK)

    swallowed = events.append(tmp_path, [confirm], clock=lambda: CLOCK)
    second = events.append(
        tmp_path,
        [
            provenance.edge_draft(
                AGENT_EDGE,
                provenance.EXTRACTED,
                detail="reviewed",
                actor="human:second",
                generation=2,
            )
        ],
        clock=lambda: CLOCK,
    )

    assert swallowed == []
    assert len(second) == 1
    assert [item.actor for item in _folded(tmp_path).edges[AGENT_EDGE].history] == [
        "agent:dana",
        "human:owner",
        "human:second",
    ]


def test_a_weaker_later_label_is_recorded_and_does_not_demote_the_edge(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [
            provenance.edge_draft(
                HUMAN_EDGE,
                provenance.AMBIGUOUS,
                detail="second look was unsure",
                actor="agent:dana",
            )
        ],
        clock=lambda: CLOCK,
    )

    state = _folded(tmp_path).edges[HUMAN_EDGE]

    assert [item.label for item in state.history] == [provenance.EXTRACTED, provenance.AMBIGUOUS]
    assert state.label == provenance.EXTRACTED
    assert state.gates is True


def test_the_edge_fold_ignores_the_order_the_events_arrive_in(tmp_path: Path) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [provenance.confirmation_draft(AGENT_EDGE, detail="reviewed", actor="human:owner")],
        clock=lambda: CLOCK,
    )
    original, _ = events.read_events(tmp_path)

    def _shape(edge_fold: Any) -> dict[Any, list[tuple[str, int]]]:
        return {
            key: [(item.label, item.seq) for item in state.history]
            for key, state in edge_fold.edges.items()
        }

    shuffled = list(original)
    random.Random(20260806).shuffle(shuffled)
    baseline = _shape(provenance.fold_edges(original))

    assert baseline == _shape(provenance.fold_edges(list(reversed(original))))
    assert baseline == _shape(provenance.fold_edges(shuffled))
    assert baseline[AGENT_EDGE] == [(provenance.INFERRED, 2), (provenance.EXTRACTED, 4)]


def test_a_duplicated_edge_event_folds_once(tmp_path: Path) -> None:
    _build(tmp_path)
    original, _ = events.read_events(tmp_path)

    doubled = provenance.fold_edges([*original, original[1], original[1]])

    assert len(doubled.edges[AGENT_EDGE].history) == 1
    assert len(doubled.edges) == 3


def test_a_non_edge_event_is_ignored_rather_than_filtered_by_the_caller(
    tmp_path: Path,
) -> None:

    events.append(
        tmp_path,
        [
            events.Draft(RECORD_A, "created", {"title": "the lane"}),
            events.Draft(RECORD_A, "status", {"status": "open"}),
            *_three_derivations(),
            events.Draft(RECORD_A, "comment", {"text": "not an edge"}),
        ],
        clock=lambda: CLOCK,
    )

    edge_fold = _folded(tmp_path)

    assert len(edge_fold.edges) == 3
    assert edge_fold.malformed == []


def test_a_label_from_a_newer_writer_is_reported_and_routes_a_decision(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [
            events.Draft(
                RECORD_A,
                provenance.KIND_EDGE,
                {
                    provenance.KEY_TARGET: RECORD_C,
                    provenance.KEY_TYPE: "supersedes",
                    provenance.KEY_LABEL: "ATTESTED",
                    provenance.KEY_DETAIL: "from a writer that knows a fourth label",
                },
                actor="lane:future",
            )
        ],
        clock=lambda: CLOCK,
    )

    edge_fold = _folded(tmp_path)
    future = edge_fold.edges[provenance.EdgeKey(RECORD_A, "supersedes", RECORD_C)]

    assert edge_fold.unknown_labels == {"ATTESTED": 1}
    assert future.label == "ATTESTED"
    assert future.gates is False
    assert future.needs_decision is True
    assert provenance.EdgeKey(RECORD_A, "supersedes", RECORD_C) in {
        request.key for request in provenance.decision_requests(edge_fold)
    }


def test_an_unknown_label_cannot_outrank_a_known_one(tmp_path: Path) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [
            events.Draft(
                RECORD_A,
                provenance.KIND_EDGE,
                {
                    provenance.KEY_TARGET: RECORD_B,
                    provenance.KEY_TYPE: "blocks",
                    provenance.KEY_LABEL: "ATTESTED",
                    provenance.KEY_DETAIL: "later, and unrecognised",
                },
                actor="lane:future",
            )
        ],
        clock=lambda: CLOCK,
    )

    state = _folded(tmp_path).edges[HUMAN_EDGE]

    assert [item.label for item in state.history] == [provenance.EXTRACTED, "ATTESTED"]
    assert state.label == provenance.EXTRACTED
    assert state.gates is True
    assert provenance.strength_of("ATTESTED") < provenance.strength_of(provenance.AMBIGUOUS)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        pytest.param({provenance.KEY_TYPE: "blocks"}, "target", id="no-target"),
        pytest.param({provenance.KEY_TARGET: RECORD_B}, "edge_type", id="no-type"),
        pytest.param(
            {provenance.KEY_TARGET: RECORD_B, provenance.KEY_TYPE: "blocks"},
            "provenance",
            id="no-label",
        ),
    ],
)
def test_a_malformed_edge_event_is_named_and_never_becomes_an_edge(
    tmp_path: Path, payload: dict[str, str], reason: str
) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [events.Draft(RECORD_A, provenance.KIND_EDGE, payload, actor="lane:confused")],
        clock=lambda: CLOCK,
    )

    edge_fold = _folded(tmp_path)

    assert [item.record for item in edge_fold.malformed] == [RECORD_A]
    assert reason in edge_fold.malformed[0].reason
    assert len(edge_fold.edges) == 3
    assert _keys(provenance.gating_edges(edge_fold)) == [HUMAN_EDGE]


@pytest.mark.parametrize(
    ("key", "label", "match"),
    [
        pytest.param(HUMAN_EDGE, "TRUSTED", "must be one of", id="unknown-label"),
        pytest.param(
            provenance.EdgeKey(RECORD_A, "Blocks", RECORD_B),
            provenance.EXTRACTED,
            "must match",
            id="edge-type-shape",
        ),
        pytest.param(
            provenance.EdgeKey(RECORD_A, "blocks", "basicly-fix-the-thing"),
            provenance.EXTRACTED,
            "not a record id",
            id="slug-shaped-target",
        ),
        pytest.param(
            provenance.EdgeKey(RECORD_A, "blocks", RECORD_A),
            provenance.EXTRACTED,
            "at itself",
            id="self-edge",
        ),
    ],
)
def test_the_write_path_refuses_what_it_cannot_mean(key: Any, label: str, match: str) -> None:

    with pytest.raises(provenance.InvalidEdgeError, match=match):
        provenance.edge_draft(key, label)

    assert issubclass(provenance.InvalidEdgeError, events.InvalidEventError)
    assert issubclass(provenance.InvalidEdgeError, events.LedgerError)


ENGINE_EDGE = provenance.EdgeKey(RECORD_B, "parent-child", RECORD_C)


def _engine_dialect_draft(key: Any, label: str = provenance.EXTRACTED) -> Any:
    return events.Draft(
        key.source,
        provenance.KIND_EDGE,
        {
            migrate.EDGE_FROM: key.source,
            migrate.EDGE_TO: key.target,
            migrate.EDGE_TYPE: key.edge_type,
            provenance.KEY_LABEL: label,
        },
        actor="engine:owned-write",
    )


def test_the_engine_dialect_folds_to_the_edge_count_the_differential_reads(
    tmp_path: Path,
) -> None:

    edges = (ENGINE_EDGE, provenance.EdgeKey(RECORD_A, "blocks", RECORD_D))
    events.append(tmp_path, [_engine_dialect_draft(key) for key in edges], clock=lambda: CLOCK)
    stored, quarantined = events.read_events(tmp_path)
    assert quarantined == []

    edge_fold = provenance.fold_edges(stored)
    views = differential.views_from_events(stored)
    reference = sum(len(view.dependencies) for view in views.values())

    assert reference == len(edges)
    assert len(edge_fold.edges) == reference
    assert edge_fold.malformed == []
    assert set(edge_fold.edges) == set(edges)
    assert edge_fold.dialects == {provenance.DIALECT_ENGINE: len(edges)}
    assert _keys(provenance.gating_edges(edge_fold)) == sorted(edges)


def test_a_ledger_in_both_dialects_folds_both_and_says_which_it_read(
    tmp_path: Path,
) -> None:

    _build(tmp_path)
    events.append(tmp_path, [_engine_dialect_draft(ENGINE_EDGE)], clock=lambda: CLOCK)
    stored, _ = events.read_events(tmp_path)

    edge_fold = provenance.fold_edges(stored)

    assert edge_fold.malformed == []
    assert edge_fold.dialects == {
        provenance.DIALECT_DECLARED: 3,
        provenance.DIALECT_ENGINE: 1,
    }
    assert {HUMAN_EDGE, AGENT_EDGE, BOUNCE_EDGE, ENGINE_EDGE} == set(edge_fold.edges)
    views = differential.views_from_events(stored)
    assert sum(len(view.dependencies) for view in views.values()) == len(edge_fold.edges)
    assert differential.edge_dialects(stored) == tuple(sorted(edge_fold.dialects))


def test_the_second_dialect_is_the_engine_writers_own_spelling_and_not_a_third() -> None:

    assert provenance.ALT_KEY_TARGET == migrate.EDGE_TO
    assert provenance.ALT_KEY_TYPE == migrate.EDGE_TYPE
    assert provenance.KEY_TARGET != migrate.EDGE_TO
    assert provenance.KEY_TYPE != migrate.EDGE_TYPE
    assert provenance.DIALECT_DECLARED != provenance.DIALECT_ENGINE

    minted = provenance.edge_draft(HUMAN_EDGE, provenance.EXTRACTED)

    assert set(minted.payload) == {
        provenance.KEY_TARGET,
        provenance.KEY_TYPE,
        provenance.KEY_LABEL,
        provenance.KEY_DETAIL,
    }


def test_a_dialect_neither_writer_uses_is_refused_by_name(tmp_path: Path) -> None:

    _build(tmp_path)
    events.append(
        tmp_path,
        [
            events.Draft(
                RECORD_A,
                provenance.KIND_EDGE,
                {migrate.EDGE_TO: RECORD_C, provenance.KEY_LABEL: provenance.EXTRACTED},
                actor="lane:third",
            )
        ],
        clock=lambda: CLOCK,
    )

    stored, _ = events.read_events(tmp_path)
    edge_fold = provenance.fold_edges(stored)

    assert [item.record for item in edge_fold.malformed] == [RECORD_A]
    assert provenance.KEY_TYPE in edge_fold.malformed[0].reason
    assert edge_fold.dialects == {provenance.DIALECT_DECLARED: 3}
    assert len(edge_fold.edges) == 3
    assert provenance.edge_dialect(stored[-1].payload) == provenance.DIALECT_DECLARED


def test_the_structural_edge_fields_are_outside_the_size_cap(tmp_path: Path) -> None:

    assert provenance.KEY_DETAIL in events.TRUNCATABLE_KEYS
    for structural in (provenance.KEY_LABEL, provenance.KEY_TARGET, provenance.KEY_TYPE):
        assert structural not in events.TRUNCATABLE_KEYS

    minted = events.append(
        tmp_path,
        [
            provenance.edge_draft(
                HUMAN_EDGE, provenance.EXTRACTED, detail="d" * (events.MAX_TEXT_BYTES * 2)
            )
        ],
        clock=lambda: CLOCK,
    )

    payload = minted[0].payload
    assert payload[f"{provenance.KEY_DETAIL}_truncated"] is True
    assert payload[provenance.KEY_LABEL] == provenance.EXTRACTED
    assert payload[provenance.KEY_TARGET] == RECORD_B
    assert _folded(tmp_path).edges[HUMAN_EDGE].gates is True


def test_an_edge_event_counts_in_the_records_totals_and_changes_no_record_state(
    tmp_path: Path,
) -> None:

    events.append(
        tmp_path,
        [events.Draft(RECORD_A, "created", {"title": "the lane"})],
        clock=lambda: CLOCK,
    )
    _build(tmp_path)

    stored, _ = events.read_events(tmp_path)
    result = events.fold(stored)

    assert result.delegated_kinds == {provenance.KIND_EDGE: 3}
    assert result.mismatched_totals == []
    assert result.records[RECORD_A].totals.events == 4
    assert result.records[RECORD_A].fields == {"title": "the lane"}


def test_the_module_imports_nothing_outside_the_standard_library() -> None:

    imported: set[str] = set()
    for node in ast.walk(ast.parse(PROVENANCE_SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not {name for name in imported if name.split(".")[0] == "basicly"}
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "importlib.util",
        "pathlib",
        "re",
        "sys",
        "typing",
    }


def test_the_sibling_event_log_is_loaded_once_under_the_name_it_publishes() -> None:

    assert sys.modules[provenance.EVENTS_MODULE_NAME] is events
    assert provenance.EVENTS_MODULE_NAME not in ("events", "ids")

    rival = _load(EVENTS_SOURCE, "a_callers_own_name_for_events")

    assert rival is not events
    assert rival.InvalidEventError is not events.InvalidEventError
    assert not issubclass(provenance.InvalidEdgeError, rival.InvalidEventError)


_DRIVER = """
import importlib.util
import json
import shutil
import sys
from pathlib import Path

assert importlib.util.find_spec("basicly") is None, "basicly is importable"
assert shutil.which("basicly") is None, "basicly is on PATH"

spec = importlib.util.spec_from_file_location("tracker_provenance", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["tracker_provenance"] = module
spec.loader.exec_module(module)
events = module.events

ledger = Path(sys.argv[2])
proposed = module.EdgeKey("consumer-zz99", "blocks", "consumer-yy88")
events.append(
    ledger,
    [module.edge_draft(proposed, module.INFERRED, detail="their agent guessed", actor="theirs")],
    clock=lambda: 1_000_000_000.0,
)
found, quarantined = events.read_events(ledger)
assert quarantined == [], quarantined
before = module.fold_edges(found).edges[proposed]

events.append(
    ledger,
    [module.confirmation_draft(proposed, detail="their human agreed", actor="them")],
    clock=lambda: 1_000_000_000.0,
)
found, _ = events.read_events(ledger)
after = module.fold_edges(found).edges[proposed]

print(json.dumps({
    "before": [before.label, before.gates],
    "after": [after.label, after.gates],
    "history": [item.label for item in after.history],
}))
"""


def _pruned_env(tmp_path: Path) -> dict[str, str]:

    empty = tmp_path / "empty-path-dir"
    empty.mkdir(exist_ok=True)
    home = tmp_path / "scratch-home"
    home.mkdir(exist_ok=True)
    env = {"PATH": str(empty), "HOME": str(home), "USERPROFILE": str(home)}
    for name in ("SystemRoot", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def test_an_edge_is_asserted_confirmed_and_folded_with_no_basicly_importable(
    tmp_path: Path,
) -> None:

    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    for source in sorted(KIT_DIR.glob("*.py")):
        shutil.copy2(source, consumer / source.name)
    driver = tmp_path / "drive.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    ledger = tmp_path / "their-ledger"

    result = subprocess.run(
        [sys.executable, "-S", "-I", str(driver), str(consumer / "provenance.py"), str(ledger)],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "before": ["INFERRED", False],
        "after": ["EXTRACTED", True],
        "history": ["INFERRED", "EXTRACTED"],
    }
