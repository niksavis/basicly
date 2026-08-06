"""Tests for provenance on edge events (basicly-vkh0.13).

Both acceptance criteria are dispositions rather than outputs, so each is asserted where
it would actually bite:

- **The three derivations get three labels, and the labels buy three different powers.**
  One ledger records a human's assertion, an agent's proposal and a merge queue's
  uncertain deduction, and the assertions are that the human's edge is the *only* one
  `gating_edges` returns, that the agent's is present and visible as a proposal, and that
  the uncertain one routes a decision item the engine's own queue would accept. Naming
  the gating set positively and negatively in the same fixture is what makes it
  discriminating: a module that gated everything and a module that gated nothing both
  pass a test that only counts.
- **A confirmation appends.** The log's bytes are captured before the promotion and the
  assertion is that the file grew by exactly the new line — the original is still there,
  byte for byte — while the folded edge moves from proposal to gate and its history reads
  as the two-step sequence it was.

The label a derivation *deserves* is the recorder's judgment and deliberately not this
module's: `design/work-tracker.md` §9.6 puts a confident bounce inference at ``INFERRED``,
while this bead's criterion records an uncertain one as ``AMBIGUOUS``. Both are the same
call — how sure was the deriver — so the module maps no source to any label, and the
fixture below follows the criterion.

Everything the module would otherwise take from its host is test data: the wall clock via
`events.append`'s injected seam, and the engine's decision vocabulary read from
`basicly.decisions` rather than restated, so a rename there fails here instead of leaving
this module routing to a kind the queue rejects.
"""

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


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


provenance = _load(PROVENANCE_SOURCE, "tracker_provenance")
# The sibling the module loaded for itself, never a second copy: two loads of `events.py`
# would mint two `InvalidEventError` classes and every `except` clause here would stop
# matching one of them. The module names the module name for exactly this reason.
events = provenance.events

RECORD_A = "basicly-aa11"
RECORD_B = "basicly-bb22"
RECORD_C = "basicly-cc33"
RECORD_D = "basicly-dd44.2"

CLOCK = 1_000_000_000.0

# The three derivations of the criterion, all out of one record so one landing decision
# has to choose between them.
HUMAN_EDGE = provenance.EdgeKey(RECORD_A, "blocks", RECORD_B)
AGENT_EDGE = provenance.EdgeKey(RECORD_A, "couples-with", RECORD_C)
BOUNCE_EDGE = provenance.EdgeKey(RECORD_A, "blocks", RECORD_D)


def _three_derivations() -> list[Any]:
    """One edge asserted by a human, one proposed by an agent, one deduced from a bounce."""
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
    """Append the three derivations to a fresh ledger under a fixed clock."""
    return events.append(directory, _three_derivations(), clock=lambda: CLOCK)


def _folded(directory: Path) -> Any:
    """Read the ledger back and fold its edges."""
    stored, quarantined = events.read_events(directory)
    assert quarantined == []
    return provenance.fold_edges(stored)


def _keys(states: tuple[Any, ...]) -> list[Any]:
    """The edges a selector returned, as keys."""
    return [state.key for state in states]


# --- AC1: three derivations, three labels -------------------------------------


def test_each_derivation_records_its_own_label_on_its_own_event(tmp_path: Path) -> None:
    """The label reaches the ledger line, not just the object the builder returned.

    Read back off the file rather than off the return value, because the write path
    redacts and caps a payload on the way through — a label that survived in memory and
    was mangled on disk would pass any assertion made before the round trip.
    """
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
    """The whole point: an agent's guess must not stop a landing unexamined.

    Asserted as an equality over the gating set rather than as three separate membership
    checks — a module that gated every edge and one that gated none would each satisfy a
    weaker assertion, and they are opposite defects.
    """
    _build(tmp_path)

    edge_fold = _folded(tmp_path)

    assert _keys(provenance.gating_edges(edge_fold, RECORD_A)) == [HUMAN_EDGE]
    assert _keys(provenance.gating_edges(edge_fold)) == [HUMAN_EDGE]
    assert edge_fold.edges[HUMAN_EDGE].gates is True
    assert edge_fold.edges[AGENT_EDGE].gates is False
    assert edge_fold.edges[BOUNCE_EDGE].gates is False


def test_the_agent_proposal_is_usable_but_visible_as_a_proposal(tmp_path: Path) -> None:
    """Usable means it is in the graph; a proposal means it is labelled as one.

    The failure this rules out is dropping the edge entirely — a safe-looking way to keep
    an inference from gating that also throws away the inference.
    """
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
    """`AMBIGUOUS` has a disposition path that already exists — the decision queue."""
    _build(tmp_path)

    edge_fold = _folded(tmp_path)
    requests = provenance.decision_requests(edge_fold)

    assert [request.key for request in requests] == [BOUNCE_EDGE]
    assert requests[0].record == RECORD_A
    assert BOUNCE_EDGE.as_text() in requests[0].question
    assert "merge bounced" in requests[0].detail
    assert BOUNCE_EDGE not in set(_keys(provenance.gating_edges(edge_fold)))


def test_the_routed_item_is_one_the_engines_decision_queue_accepts(tmp_path: Path) -> None:
    """Routing a decision is a claim about another component, so it is checked there.

    `decisions.enqueue` refuses a kind outside its own vocabulary, so a kit constant that
    drifted from it would produce items nothing could queue — and the kit may not import
    basicly to find that out. The test can, which is where the coupling belongs.
    """
    _build(tmp_path)

    request = provenance.decision_requests(_folded(tmp_path))[0]

    assert request.kind in decisions.KINDS
    assert provenance.DECISION_KIND in decisions.KINDS
    # The id the queue would derive is well formed and splits back to the record it is on.
    decision_id = decisions.decision_id_for(request.record, request.kind, request.question)
    assert decisions.split_decision_id(decision_id)[0] == RECORD_A


def test_the_decision_question_does_not_drift_as_the_edges_history_grows(
    tmp_path: Path,
) -> None:
    """The queue keys an item on its question, so a drifting one re-enqueues forever.

    A second, weaker assertion on the same edge changes the *detail* — which is what the
    answerer reads — and must leave the question, and therefore the item's id, alone.
    """
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


# --- AC2: a confirmation appends, and the history reads as a sequence ---------


def test_confirming_an_inferred_edge_appends_and_leaves_the_original_line_intact(
    tmp_path: Path,
) -> None:
    """The criterion, asserted on the bytes.

    The log is compared as a prefix rather than by re-parsing: an implementation that
    rewrote the original line to carry the new label could still produce a fold that looks
    right, and only the bytes distinguish "appended" from "overwritten".
    """
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
    # The original event is untouched and still says what it said.
    reread = {event.id: event for event in events.read_events(tmp_path)[0]}
    assert reread[proposal.event_id].payload[provenance.KEY_LABEL] == provenance.INFERRED
    assert len(landed) == 1
    assert landed[0].id != proposal.event_id


def test_the_confirmed_edge_gates_and_its_history_reads_as_a_sequence(
    tmp_path: Path,
) -> None:
    """Promotion is a fold over two events, not a mutation of one.

    Both halves are asserted: the disposition moved from proposal to gate, and the two
    assertions are still readable in the order they were made, each with its own actor —
    which is what "readable as a sequence rather than overwritten" has to mean to be
    checkable at all.
    """
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
    """An event id is content-derived, so a replayed confirmation is a no-op.

    Stated as a test because the same mechanism is a trap in the other direction: a second
    reviewer who genuinely reached the same conclusion needs ``generation=2`` or their
    assertion is swallowed as this one is. Both directions are asserted here.
    """
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
    """The strongest label wins (§9.6), and this is the cost of that rule made checkable.

    Asserting doubt over a confirmed edge lands in the history — where a reader and a
    later retraction kind can both see it — and changes nothing about the disposition.
    The module documents the absence of a demotion path; this is what documenting it
    means in practice.
    """
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


# --- the fold is a function of the event set ----------------------------------


def test_the_edge_fold_ignores_the_order_the_events_arrive_in(tmp_path: Path) -> None:
    """A shuffle, a reversal and the file order all fold to one set of edges and histories.

    The promotion in the fixture is what makes this discriminating: a fold that trusted
    arrival order would put the confirmation before the proposal in the reversed run, and
    the history — the thing AC2 asks to be readable as a sequence — would be backwards
    while the label still looked right.
    """
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
    """Idempotency by id, so a union merge that duplicated a line adds no assertion."""
    _build(tmp_path)
    original, _ = events.read_events(tmp_path)

    doubled = provenance.fold_edges([*original, original[1], original[1]])

    assert len(doubled.edges[AGENT_EDGE].history) == 1
    assert len(doubled.edges) == 3


def test_a_non_edge_event_is_ignored_rather_than_filtered_by_the_caller(
    tmp_path: Path,
) -> None:
    """The fold takes whatever `read_events` returned rather than a filtered list.

    Asking the caller to filter first invites a second caller who filters differently,
    which is the shape of defect this design keeps paying for.
    """
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


# --- forward compatibility, in the fail-closed direction ----------------------


def test_a_label_from_a_newer_writer_is_reported_and_routes_a_decision(
    tmp_path: Path,
) -> None:
    """The tolerant direction for a gate is the restrictive one.

    A newer writer's label is preserved and counted, and it lands on the disposition of
    the thing we are least sure about — never on the one that can hold up a landing.
    Written as a raw event because the write path refuses to mint such a label at all,
    which is the other half of the same rule and is asserted separately.
    """
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
    """It ranks below every label we know, so it neither promotes nor demotes.

    Without this the fail-closed rule would have a hole in each direction: an unknown
    label sorting high would inherit a gate, and one sorting merely *last* would knock a
    confirmed edge back to a decision.
    """
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
    """Reported and skipped, where `events.fold` would raise — and the divergence is safe.

    A skipped edge is an *absent* edge, and an absent edge can never gate anything, so the
    failure mode is a missed gate that `malformed` names rather than a wrong gate nobody
    can see. The rest of the ledger still folds, which is the availability half: one bad
    line from a foreign writer must not wedge every edge read.
    """
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
    """Validation at the trust boundary, before anything is authoritative.

    The refusal is raised as a subclass of the event log's own error, so a caller that
    wrapped build-and-append in one ``except events.LedgerError`` catches the draft
    builder too rather than having it escape the handler written for the write.
    """
    with pytest.raises(provenance.InvalidEdgeError, match=match):
        provenance.edge_draft(key, label)

    assert issubclass(provenance.InvalidEdgeError, events.InvalidEventError)
    assert issubclass(provenance.InvalidEdgeError, events.LedgerError)


# --- the size cap, and which fields it may reach ------------------------------


def test_the_structural_edge_fields_are_outside_the_size_cap(tmp_path: Path) -> None:
    """A cut through ``EXTRACTED`` would make a disposition depend on neighbouring text.

    Asserted both ways: the declaration (which keys the cap names) and the behaviour (a
    detail long enough to be cut, on an event whose label and endpoints come through
    whole and whose edge still gates).
    """
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


# --- the seam with the record fold --------------------------------------------


def test_an_edge_event_counts_in_the_records_totals_and_changes_no_record_state(
    tmp_path: Path,
) -> None:
    """`events.fold` is an older reader with respect to this kind, and that is correct.

    An edge is not a record field, so there is no record state for it to fold into — it
    is counted in the totals like every other event and reported under ``unknown_kinds``,
    which is exactly the tolerance §4.5 asks of a reader meeting a kind it does not know.
    Pinned here so the seam is visible rather than discovered by whoever writes `fsck`.
    """
    events.append(
        tmp_path,
        [events.Draft(RECORD_A, "created", {"title": "the lane"})],
        clock=lambda: CLOCK,
    )
    _build(tmp_path)

    stored, _ = events.read_events(tmp_path)
    result = events.fold(stored)

    assert result.unknown_kinds == {provenance.KIND_EDGE: 3}
    assert result.mismatched_totals == []
    assert result.records[RECORD_A].totals.events == 4
    assert result.records[RECORD_A].fields == {"title": "the lane"}


# --- the kit boundary ---------------------------------------------------------


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """The kit boundary, read off the source rather than trusted.

    Only ``events.py`` is loaded from beside it, and that happens by path with no
    ``sys.path`` mutation — a library that reordered a consumer's import path could
    shadow a module they own.
    """
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
    """Two copies of ``events.py`` would mint two exception classes that stop matching.

    The module name is public for that reason, and this is the assertion that makes it a
    contract instead of an implementation detail somebody is free to rename. The second
    load is the positive control: it shows the hazard is real rather than theoretical —
    a caller who loaded the file under a name of their own would hold an
    ``InvalidEventError`` that no ``except`` clause here would catch.
    """
    assert sys.modules[provenance.EVENTS_MODULE_NAME] is events
    assert provenance.EVENTS_MODULE_NAME not in ("events", "ids")

    rival = _load(EVENTS_SOURCE, "a_callers_own_name_for_events")

    assert rival is not events
    assert rival.InvalidEventError is not events.InvalidEventError
    assert not issubclass(provenance.InvalidEdgeError, rival.InvalidEventError)


# The subprocess asserts the kit constraint itself before asserting anything else: an
# environment that quietly still had basicly in it would make this whole section vacuous.
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
    """An environment with no basicly on PATH and nothing pointing at this repo.

    Built from empty rather than filtered, so nothing inherited can smuggle the package
    back in — no ``PYTHONPATH``, no ``VIRTUAL_ENV``. The few names copied back are what an
    interpreter needs on its own platform, which makes the platform difference test data.
    """
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
    """The kit's hard constraint, exercised the way a consumer would exercise it.

    ``-S`` drops site-packages, which is where this repo's own ``basicly`` lives, and
    ``-I`` drops ``PYTHONPATH``, the user site directory and the script's own directory.
    All three kit files are copied because the sibling loader chain is part of what is
    being proved: a consumer copies the directory, not one file.
    """
    consumer = tmp_path / "consumer" / "kit" / "tracker"
    consumer.mkdir(parents=True)
    for source in (PROVENANCE_SOURCE, EVENTS_SOURCE, IDS_SOURCE):
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
