from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import cli, dispatch_brief, owned_store, tracker
from tests import flipped_tracker


def fixture(tmp_path: Path) -> Path:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(
        repo, "basicly-task", title="Resume the parser", requirements="Preserve zero."
    )
    kit = tracker.kit(repo, "events")
    entries = [
        ("agent:first", "[model handoff] Historical finished step."),
        ("operator", "Active correction: reject strings too."),
        ("agent:first", "[model handoff] Keep negative numbers; next run acceptance."),
        (
            "agent:second",
            "[model handoff] Keep the requested preview running; ownership is recorded.",
        ),
        ("agent:first", "[session handover 2026-10-26] Old session report."),
        ("agent:first", "[session handover 2026-10-27] Current blocker: consumer check."),
        ("operator", "New clarification after handoff: preserve empty input."),
    ]
    for actor, text in entries:
        kit.append(
            tracker.ledger_dir(repo),
            [kit.Draft("basicly-task", kit.KIND_COMMENT, {"text": text})],
            actor=actor,
        )
    return repo


def append_legacy_cut_note(repo: Path, text: str) -> None:
    kit = tracker.kit(repo, "events")
    payload = kit.prepare_payload({"text": text}, kind="dispatch")
    assert payload["text_truncated"] is True
    kit.append(tracker.ledger_dir(repo), [kit.Draft("basicly-task", "comment", payload)])


def test_current_view_keeps_contract_clarifications_and_latest_attributed_handoffs(
    tmp_path: Path,
) -> None:
    repo = fixture(tmp_path)
    module = owned_store.packaged_kit("record_view")
    full = module.read_record(tracker.ledger_dir(repo), "basicly-task")
    current = module.read_view(tracker.ledger_dir(repo), "basicly-task", view="current")
    assert current["fields"] == full["fields"]
    assert current["status"] == full["status"]
    notes = current["notes"]
    assert len(notes) == 5
    assert all(set(note) == {"id", "seq", "at", "writer", "text"} for note in notes)
    rendered = json.dumps(current)
    assert "Active correction: reject strings too." in rendered
    assert "Keep negative numbers" in rendered
    assert "preview running" in rendered
    assert "consumer check" in rendered
    assert "preserve empty input" in rendered
    assert "Historical finished step" not in rendered
    assert "Old session report" not in rendered
    assert current["complete_contract"] is False
    assert current["source_loss"] == []
    assert current["history_reference"]["omitted_notes"] == 2
    historical = module.read_view(
        tracker.ledger_dir(repo),
        "basicly-task",
        view="history",
        snapshot=current["snapshot_identity"],
    )
    assert historical["comment_log"] == full["comment_log"]
    assert len(historical["comment_log"]) == 7


def test_current_history_retrieval_refuses_a_changed_snapshot(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    module = owned_store.packaged_kit("record_view")
    current = module.read_view(tracker.ledger_dir(repo), "basicly-task", view="current")
    tracker.add_comment(repo, "basicly-task", "A new required correction.")
    with pytest.raises(ValueError, match="snapshot changed"):
        module.read_view(
            tracker.ledger_dir(repo),
            "basicly-task",
            view="history",
            snapshot=current["snapshot_identity"],
        )


def test_current_view_does_not_truncate_ordinary_notes_or_unknown_markers(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    marker = "Required fact: " + "界" * 1000
    kit = tracker.kit(repo, "events")
    kit.append(
        tracker.ledger_dir(repo),
        [kit.Draft("basicly-task", kit.KIND_COMMENT, {"text": marker})],
        actor="operator",
        max_text_bytes=16384,
    )
    tracker.add_comment(
        repo, "basicly-task", 'Quoted data: "[model handoff] must not replace another note"'
    )
    module = owned_store.packaged_kit("record_view")
    current = module.read_view(tracker.ledger_dir(repo), "basicly-task", view="current")
    assert marker in json.dumps(current, ensure_ascii=False)
    assert "Quoted data" in json.dumps(current)
    assert len(current["notes"]) == 7


def test_engine_show_current_and_history_share_canonical_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = fixture(tmp_path)
    monkeypatch.chdir(repo)
    assert cli.main(["tracker", "show", "basicly-task", "--view", "current"]) == 0
    current = json.loads(capsys.readouterr().out)
    assert (
        cli.main([
            "tracker",
            "show",
            "basicly-task",
            "--view",
            "history",
            "--snapshot",
            current["snapshot_identity"],
        ])
        == 0
    )
    history = json.loads(capsys.readouterr().out)
    assert len(history["comment_log"]) == 7
    assert history["snapshot_identity"] == current["snapshot_identity"]
    tracker.add_comment(repo, "basicly-task", "Changed input")
    assert (
        cli.main([
            "tracker",
            "show",
            "basicly-task",
            "--view",
            "history",
            "--snapshot",
            current["snapshot_identity"],
        ])
        == 1
    )
    output = capsys.readouterr()
    assert not output.out
    assert "snapshot changed" in output.err


def test_current_view_exposes_content_already_truncated_by_its_writer(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    append_legacy_cut_note(repo, "source-loss-witness " + "界" * 2000)
    module = owned_store.packaged_kit("record_view")
    current = module.read_view(tracker.ledger_dir(repo), "basicly-task", view="current")
    cut = next(note for note in current["notes"] if note["text"].startswith("source-loss-witness"))
    assert cut["text_truncated"] is True
    assert cut["text_original_length_bytes"] > len(cut["text"].encode("utf-8"))
    assert current["source_loss"][0]["id"] == cut["id"]
    assert "cannot be recovered" in current["source_loss"][0]["reason"]


def test_standalone_current_and_history_views_need_no_engine(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    source = Path(__file__).resolve().parents[1] / ".basicly/core/kit/tracker"
    copied = tmp_path / "standalone"
    shutil.copytree(source, copied, ignore=shutil.ignore_patterns("__pycache__"))
    command = [
        sys.executable,
        "-I",
        "-S",
        str(copied / "cli.py"),
        "show",
        str(tracker.ledger_dir(repo)),
        "basicly-task",
    ]
    result = subprocess.run(
        [*command, "--view", "current"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    current = json.loads(result.stdout)
    assert len(current["notes"]) == 5
    full = subprocess.run(
        [*command, "--view", "history", "--snapshot", current["snapshot_identity"]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert full.returncode == 0, full.stderr
    assert len(json.loads(full.stdout)["comment_log"]) == 7
    tracker.add_comment(repo, "basicly-task", "A concurrent writer added a clarification.")
    stale = subprocess.run(
        [*command, "--view", "history", "--snapshot", current["snapshot_identity"]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert stale.returncode != 0
    assert "snapshot changed" in stale.stdout + stale.stderr


def test_large_collection_of_clarifications_is_never_silently_trimmed(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    entries = ["required-" + str(index) + ": " + "界" * 1000 for index in range(8)]
    for text in entries:
        tracker.add_comment(repo, "basicly-task", text)
    current = owned_store.packaged_kit("record_view").read_view(
        tracker.ledger_dir(repo), "basicly-task", view="current"
    )
    rendered = json.dumps(current, ensure_ascii=False)
    assert len(rendered.encode("utf-8")) > 16384
    assert all(text in rendered for text in entries)


def test_dispatch_brief_discloses_source_loss_instead_of_claiming_full_recovery(
    tmp_path: Path,
) -> None:
    repo = fixture(tmp_path)
    append_legacy_cut_note(repo, "required-source-witness " + "界" * 2000)
    full = dispatch_brief.task_view(repo, "basicly-task", view="full")
    assert full["contract"]["source_loss"]
    prompt = dispatch_brief.task_prompt(repo, "basicly-task")
    assert "original content cannot be recovered" in prompt
    assert "do not treat cut text as a complete original contract" in prompt


def test_current_view_preserves_concurrent_handoffs_from_the_same_writer(tmp_path: Path) -> None:
    repo = fixture(tmp_path)
    kit = tracker.kit(repo, "events")
    ledger = tracker.ledger_dir(repo)
    other = tmp_path / "other-ledger"
    shutil.copytree(ledger, other)
    kit.append(
        ledger,
        [
            kit.Draft(
                "basicly-task",
                kit.KIND_COMMENT,
                {"text": "[model handoff] Concurrent left finding."},
            )
        ],
        actor="agent:first",
    )
    right = kit.append(
        other,
        [
            kit.Draft(
                "basicly-task",
                kit.KIND_COMMENT,
                {"text": "[model handoff] Concurrent right finding."},
            )
        ],
        actor="agent:first",
    )
    (ledger / "pending-fork.jsonl").write_text(
        "".join(kit.to_json(event) + "\n" for event in right), encoding="utf-8"
    )
    current = owned_store.packaged_kit("record_view").read_view(
        ledger, "basicly-task", view="current"
    )
    rendered = json.dumps(current)
    assert "Concurrent left finding" in rendered
    assert "Concurrent right finding" in rendered


def test_brief_refuses_changes_during_source_loss_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fixture(tmp_path)
    module = owned_store.packaged_kit("record_view")
    original = module.read_source_loss

    def changed(directory: Path, record: str) -> list[dict[str, object]]:
        tracker.add_comment(repo, record, "Concurrent correction during source loss collection.")
        return original(directory, record)

    monkeypatch.setattr(module, "read_source_loss", changed)
    with pytest.raises(ValueError, match="snapshot changed"):
        dispatch_brief.task_prompt(repo, "basicly-task")
