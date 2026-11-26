from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from basicly import cli, owned_store, tracker
from tests.test_kit_vault_share import bare
from tests.test_kit_vault_view import _carry, _ev, _push, _record, vault_view
from tests.test_vault_readers import FIRST, LOCAL, SECOND, _vault_repo

__all__ = ["bare"]
OTHER = "other-x1"


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s1")


def test_the_board_command_pins_metadata_and_each_project_record(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo = _vault_repo(tmp_path, bare)
    writer = _push(
        tmp_path,
        bare,
        "other-writer",
        [
            *_carry(
                _record(
                    OTHER,
                    title="Other project",
                    priority=3,
                    size="small",
                    description="## Context\nShow work from every project.",
                    acceptance_criteria="When listed, the title appears.",
                )
            ),
            {"kind": "claim", "record": FIRST},
        ],
    )
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"read_at", "vault_head", "records"}
    assert datetime.fromisoformat(report["read_at"]).tzinfo is not None
    assert (
        report["vault_head"]
        == vault_view.git_runner.run(["rev-parse", "refs/heads/main"], bare).strip()
    )
    rows = {row["id"]: row for row in report["records"]}
    assert set(rows) == {FIRST, SECOND, OTHER}
    assert LOCAL not in rows
    assert all(
        set(row) == {"id", "title", "status", "holder", "priority", "project", "ready"}
        for row in rows.values()
    )
    assert rows[FIRST]["holder"] == {"writer": writer, "session": "s1"}
    assert rows[SECOND]["holder"] is None
    assert rows[SECOND]["ready"] is False
    assert rows[OTHER] == {
        "id": OTHER,
        "title": "Other project",
        "status": "open",
        "holder": None,
        "priority": 3,
        "project": "other",
        "ready": True,
    }


def test_own_unshared_records_are_marked_without_inventing_a_canonical_holder(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo = _vault_repo(tmp_path, bare)
    context = owned_store.vault_context(repo)
    assert context is not None
    view, clone, _ = context
    view.vault_clone.private(clone.folder)
    hooks = owned_store.packaged_kit("vault_hooks")
    (clone.folder / hooks.vault_share.SHARED_AT).write_text(f"{time.time()}\n")
    before = vault_view.git_runner.run(["rev-parse", "refs/heads/main"], bare).strip()
    made = tracker.create_record(
        repo,
        [
            "create",
            "Pending board item",
            "--type",
            "task",
            "--description",
            "## Context\nSee my own work before sharing.",
            "--acceptance",
            "When read, the title appears.",
        ],
    )
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0

    report = json.loads(capsys.readouterr().out)
    row = next(row for row in report["records"] if row["id"] == made)
    assert row["title"] == "Pending board item"
    assert row["holder"] is None
    assert row["sharing"] == "pending"
    assert report["vault_head"] == before
    assert vault_view.git_runner.run(["rev-parse", "refs/heads/main"], bare).strip() == before


def test_a_publication_during_the_read_does_not_mix_head_records_and_holders(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo = _vault_repo(tmp_path, bare)
    monkeypatch.chdir(repo)
    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0
    original = json.loads(capsys.readouterr().out)
    context = owned_store.vault_context(repo)
    assert context is not None
    view, clone, _ = context
    scheduler = owned_store.packaged_kit("scheduler")
    candidates = scheduler.candidates_from_events
    writer = []

    def publish(found: list, template: object) -> dict:
        if not writer:
            writer.append(
                _push(
                    tmp_path,
                    bare,
                    "during-read",
                    [*_carry(_record(OTHER)), {"kind": "claim", "record": FIRST}],
                )
            )
            view.vault_clone.fetch(clone, clock=time.time)
        return candidates(found, template)

    monkeypatch.setattr(scheduler, "candidates_from_events", publish)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0

    captured = json.loads(capsys.readouterr().out)
    assert captured["vault_head"] == original["vault_head"]
    assert captured["records"] == original["records"]
    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["vault_head"] != original["vault_head"]
    rows = {row["id"]: row for row in current["records"]}
    assert OTHER in rows
    assert rows[FIRST]["holder"] == {"writer": writer[0], "session": "s1"}


def test_all_projects_without_a_vault_names_the_required_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 1

    assert ".basicly-tracker.toml" in capsys.readouterr().err


def test_board_filters_search_carried_comment_history_and_allow_an_empty_limit(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo = _vault_repo(tmp_path, bare)
    monkeypatch.chdir(repo)
    command = ["tracker", "list", "--json", "--all-projects"]

    assert cli.main([*command, "--query", "carried through the vault", "--status", "open"]) == 0

    found = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in found["records"]] == [FIRST]
    assert cli.main([*command, "--limit", "0"]) == 0
    empty = json.loads(capsys.readouterr().out)
    assert empty["records"] == []
    assert empty["vault_head"] == found["vault_head"]


def test_unsupported_raw_comments_refuse_instead_of_disappearing_from_the_board(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, repo = _vault_repo(tmp_path, bare)
    queue = owned_store.packaged_kit("vault_queue")
    _push(tmp_path, bare, "legacy-comment", [queue.comment(FIRST, "An unprojected raw comment")])
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 1

    output = capsys.readouterr()
    assert not output.out
    assert "1 legacy vault comments" in output.err
    assert "basicly-pvdo68d" in output.err


@pytest.mark.parametrize("constraint", ["project-template", "cross-project-blocker"])
def test_project_readiness_uses_its_template_and_cross_project_dependencies(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    constraint: str,
) -> None:
    _, repo = _vault_repo(tmp_path, bare)
    carried = _carry(
        _record(
            OTHER,
            title="Other work",
            size="small",
            description="## Context\nOffer only ready work.",
            acceptance_criteria="When ready, the work appears.",
        )
    )
    if constraint == "project-template":
        templates = owned_store.packaged_kit("vault_template")
        raw = {"prefix": "other", "sections": ["## Constraints"]}
        carried[0].update({templates.FIELD: raw, templates.IDENTITY: templates.identity(raw)})
    else:
        carried.extend(
            _carry([_ev(OTHER, "edge", {"from": OTHER, "to": FIRST, "type": "blocks"}, 3)])
        )
    _push(tmp_path, bare, "constrained-project", carried)
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0

    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["records"]}
    assert rows[OTHER]["ready"] is False
    assert rows[FIRST]["ready"] is True


@pytest.mark.parametrize("scenario", [(False, False), (False, True), (True, False), (True, True)])
def test_a_reviewed_task_keeps_its_cross_project_dependency_context(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: tuple[bool, bool],
) -> None:
    dependency_closed, current_review = scenario
    _, repo = _vault_repo(tmp_path, bare)
    dependency = "acme-z9"
    criterion = "When the dependency is complete, this work shall be offered."
    raw = [
        *_record(
            OTHER,
            description="## Context\nCross-project work needs a reviewed plan.",
            acceptance_criteria=criterion,
        ),
        *_record(
            dependency,
            description="## Context\nThe prerequisite is delivered.",
            acceptance_criteria="When delivered, the dependency shall close.",
            size="small",
        ),
        _ev(OTHER, "edge", {"from": OTHER, "to": dependency, "type": "blocks"}, 3),
    ]
    if dependency_closed:
        raw.extend([
            _ev(dependency, "status", {"status": "closed"}, 3),
            _ev(dependency, "field", {"name": "close_resolution", "value": "completed"}, 4),
        ])
    events = owned_store.packaged_kit("events")
    process = owned_store.packaged_kit("process_evidence")
    evidence = {
        "checks": [
            {
                "criterion": criterion,
                "command": ["python3", "check.py"],
                "expected": "The prerequisite is closed.",
            }
        ]
    }
    review = process.review_draft(
        [events.from_json(json.dumps(line)) for line in raw], OTHER, evidence, "t"
    )
    raw.append(_ev(OTHER, "field", review.payload, 4))
    if not current_review:
        raw.append(_ev(OTHER, "field", {"name": "title", "value": "Changed after review"}, 5))
    _push(tmp_path, bare, "reviewed-project", _carry(raw))
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "list", "--json", "--all-projects"]) == 0

    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["records"]}
    assert rows[OTHER]["ready"] is dependency_closed
    ledger = owned_store.present_ledger(repo, all_projects=True)
    found = events.read_events(ledger)[0]
    assert process.confirmation_report(found, OTHER)["review_current"] is current_review
