from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from basicly import (
    board_snapshot,
    dependency_graph,
    gate_source,
    label_source,
    owned_store,
    tracker,
)
from tests.test_kit_vault_view import _carry, _push, vault_view
from tests.test_kit_vault_write import bare
from tests.test_owned_write import SHAPED, owned_repo, seed_ready
from tests.tracker_process_fixture import recorded_review

__all__ = ["bare"]
FIRST, SECOND, LOCAL = "acme-a1", "acme-b2", "acme-l9"
LABEL = "focus"
PROVIDER = "basicly-verify"
CONTEXT = "## Context"
MOMENT = datetime(2026, 11, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s1")


def _source(tmp_path: Path) -> Path:
    source = owned_repo(tmp_path / "source")
    kit = owned_store.kit(source)
    made = kit.events
    drafts = [
        made.Draft(
            FIRST,
            made.KIND_CREATED,
            {"title": "first", label_source.LABELS_FIELD: LABEL, **SHAPED},
        ),
        made.Draft(FIRST, made.KIND_STATUS, {"status": "open"}),
        made.Draft(SECOND, made.KIND_CREATED, {"title": "second"}),
        made.Draft(SECOND, made.KIND_STATUS, {"status": "open"}),
        made.Draft(
            SECOND,
            kit.migrate.KIND_EDGE,
            {
                kit.migrate.EDGE_FROM: SECOND,
                kit.migrate.EDGE_TO: FIRST,
                kit.migrate.EDGE_TYPE: "blocks",
            },
        ),
        made.Draft(FIRST, made.KIND_COMMENT, {"text": "carried through the vault"}),
    ]
    made.append(owned_store.ledger_dir(source), drafts)
    recorded_review(owned_store.kit(source, "commands"), owned_store.ledger_dir(source), FIRST)
    gate = ["gate", "report", "--gate", "verify", "--provider", PROVIDER, "--status", "pass"]
    tracker.write(source, [*gate, FIRST])
    return source


def _vault_repo(tmp_path: Path, vault: Path) -> tuple[Path, Path]:
    source = _source(tmp_path)
    found = owned_store.kit(source).read_ledger(owned_store.ledger_dir(source))
    _push(tmp_path, vault, "m1", _carry([json.loads(vault_view.events.to_json(e)) for e in found]))
    repo = owned_repo(tmp_path / "repo")
    seed_ready(repo, LOCAL)
    config = f'vault = "{vault.as_posix()}"\nprefix = "acme"\n'
    (repo / ".basicly-tracker.toml").write_text(config, encoding="utf-8")
    return source, repo


def test_present_ledger_holds_the_vault_events_in_vault_order_and_the_board_reads_it_unchanged(
    tmp_path: Path, bare: Path
) -> None:
    source, repo = _vault_repo(tmp_path, bare)

    view = owned_store.present_ledger(repo)
    shown = board_snapshot.build_document(repo, now=MOMENT)
    plain = board_snapshot.build_document(source, now=MOMENT)

    held = owned_store.kit(source).read_ledger(owned_store.ledger_dir(source))
    assert [e.id for e in vault_view.events.read_events(view)[0]] == [e.id for e in held]
    assert not view.is_relative_to(repo)
    assert {key: value for key, value in shown.items() if key != "repo"} == {
        key: value for key, value in plain.items() if key != "repo"
    }
    assert LOCAL not in json.dumps(shown)


def test_the_graph_gate_and_label_readers_read_the_vault_view(tmp_path: Path, bare: Path) -> None:
    _source_repo, repo = _vault_repo(tmp_path, bare)

    assert dependency_graph.owned_blocked(repo) == (SECOND,)
    assert label_source.owned_labelled(repo, LABEL) == {FIRST: "open"}
    assert gate_source.owned_gates(repo, FIRST) == [
        {
            gate_source.GATE_KEY: "verify",
            gate_source.PROVIDER_KEY: PROVIDER,
            gate_source.PASSED_KEY: True,
        }
    ]


def test_the_board_graph_gate_and_label_readers_read_no_ledger_folder_as_empty(
    tmp_path: Path,
) -> None:
    repo = owned_repo(tmp_path / "bare-repo")
    (repo / owned_store.LEDGER_DIR).rmdir()

    assert dependency_graph.owned_blocked(repo) == ()
    assert label_source.owned_labelled(repo, LABEL) == {}
    assert gate_source.owned_gates(repo, FIRST) == []
    assert board_snapshot._read_and_fold(repo) is not None


READS: dict[str, Callable[[Path], set[str]]] = {
    "ledger_events": lambda r: {e.record for e in tracker.ledger_events(r)[0]},
    "activity_source": lambda r: {e.record for e in tracker.activity_source(r)[0]},
    "all_records": lambda r: {str(row["id"]) for row in tracker.all_records(r)},
    "owned_record": lambda r: {i for i in (FIRST, LOCAL) if tracker.owned_record(r, i)},
    "all_comment_rows": lambda r: set(tracker.all_comment_rows(r)),
    "owned_ranking": lambda r: {
        str(row["issue"]["id"]) for row in tracker.owned_ranking(r)["recommendations"]
    },
    "confirmation_evidence": lambda r: {
        i for i in (FIRST, LOCAL) if not isinstance(tracker.confirmation_evidence(r, i), str)
    },
    "owed_of": lambda r: {
        i for i in (FIRST, LOCAL) if CONTEXT not in str(tracker.owed_of(r, i)["owed"])
    },
}


@pytest.mark.parametrize("reader", sorted(READS))
def test_a_tracker_reader_sees_the_vault_records_and_not_the_local_ledger(
    tmp_path: Path, bare: Path, reader: str
) -> None:
    _source_repo, repo = _vault_repo(tmp_path, bare)

    seen = READS[reader](repo)

    assert FIRST in seen
    assert LOCAL not in seen


def test_every_tracker_reader_resolves_its_folder_through_the_vault_view(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source_repo, repo = _vault_repo(tmp_path, bare)
    asked: list[Path] = []
    present = owned_store.present_ledger

    def counted(root: Path, **options: Any) -> Path:
        asked.append(root)
        return present(root, **options)

    monkeypatch.setattr(owned_store, "present_ledger", counted)
    calls: list[Callable[[], object]] = [
        lambda: tracker.source_loss(repo, FIRST),
        lambda: tracker.readiness(repo, {"id": FIRST}, None),
        lambda: tracker.ledger_fingerprint(repo),
        lambda: tracker.read_artifacts(repo, FIRST),
        lambda: tracker.artifact_states(repo, "plan"),
    ]

    for call in calls:
        call()

    assert asked == [repo] * len(calls)
    names = {name for name, _mtime, _size in tracker.ledger_fingerprint(repo)}
    assert names == {path.name for path in owned_store.present_ledger(repo).glob("*.jsonl")}
