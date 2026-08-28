"""`basicly session start`: the derived orientation that replaces a written handover.

Its own file rather than `tests/test_cli.py`, which has 1746 tokens of size headroom
left [measured 2026-08-28, `.scripts/headroom.py`]; `test_cli_<aspect>.py` is the
derived name the `test-naming` gate accepts.

Every fixture here seeds a real ledger through the kit, because the whole claim under
test is that no line is authored: a stubbed reader would assert the renderer and leave
the derivation unmeasured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli, policy, run_record, tracker
from tests.flipped_tracker import flipped_repo, seed_records

if TYPE_CHECKING:
    import pytest

# One decision-record index, in the document's own shape: a decision the tree holds, one
# it does not, and one qualified. The command must separate the first from the other two.
ARCHITECTURE = """# Architecture

## 38. Decision records

| Id | Title | Status | Governs |
| --- | --- | --- | --- |
| D-01 | Authority is asymmetric | accepted | invariants |
| D-34 | One kind for prose | **proposed** | the tracker |
| D-39 | The plugin is a second channel | accepted, unbuilt | install |
"""


def _with_decisions(repo: Path, text: str = ARCHITECTURE) -> Path:
    """Give *repo* a decision-record document at the path the command reads."""
    path = repo / cli.DECISION_RECORDS_DOC
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(repo: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run the command from *repo*, which is the only way it learns which repo it is in."""
    monkeypatch.chdir(repo)
    return cli.main(["session", "start", *argv])


def _seeded(tmp_path: Path) -> Path:
    """A ledger holding one ready record, one blocked by it, and one granted root."""
    repo = flipped_repo(tmp_path)
    seed_records(
        repo,
        [
            {"id": "basicly-aaa", "status": "open", "title": "the ready one", "priority": "1"},
            {
                "id": "basicly-bbb",
                "status": "open",
                "title": "the blocked one",
                "dependencies": [{"id": "basicly-aaa", "type": "blocks"}],
            },
            {
                "id": "basicly-ccc",
                "status": "open",
                "title": "the granted root",
                "comments": [{"text": f"{policy.MARKER} grant level=L3 budget=1000000"}],
            },
        ],
    )
    return repo


def test_the_orientation_prints_ready_blocked_grants_and_decision_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The four things a session needs, and the handover was the only place holding them."""
    repo = _seeded(tmp_path)
    _with_decisions(repo)

    assert _run(repo, monkeypatch, "--rows", "5") == 0

    out = capsys.readouterr().out
    assert "basicly-aaa" in out and "the ready one" in out
    assert "basicly-bbb" in out and "basicly-aaa (open)" in out
    assert "basicly-ccc" in out and "1,000,000" in out
    assert "D-34" in out and "**proposed**" in out


def test_the_ranking_policy_is_printed_beside_the_ready_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rank with no policy beside it is uninterpretable, which is the kit's own rule."""
    repo = _seeded(tmp_path)

    assert _run(repo, monkeypatch) == 0

    assert "priority ASC, dependents DESC, id ASC" in capsys.readouterr().out


def test_a_decision_the_tree_holds_is_not_reported_as_a_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`accepted` and nothing else is the whole discriminator; a qualifier is a target."""
    repo = _seeded(tmp_path)
    _with_decisions(repo)

    assert _run(repo, monkeypatch, "--json") == 0

    targets = json.loads(capsys.readouterr().out)["decisions"]
    assert [row["record"] for row in targets["records"]] == ["D-34", "D-39"]
    assert targets["decisions"] == 3


def test_a_repository_with_no_decision_document_says_so_rather_than_none_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A consumer repository has no architecture document, and no targets is a claim."""
    repo = _seeded(tmp_path)

    assert _run(repo, monkeypatch) == 0

    assert f"no {cli.DECISION_RECORDS_DOC.as_posix()} in this repository" in capsys.readouterr().out


def test_an_empty_ledger_says_so_instead_of_drawing_an_empty_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty Ready table reads as nothing to do; a fresh consumer has nothing filed."""
    repo = flipped_repo(tmp_path)

    assert _run(repo, monkeypatch) == 0

    out = capsys.readouterr().out
    assert "ledger: empty" in out
    assert "Ready (" not in out and "Grants (" not in out


def test_a_repository_with_no_owned_tracker_reports_no_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read-only means it cannot install a tracker to have something to say."""
    assert _run(tmp_path, monkeypatch) == 0

    assert "ledger: none" in capsys.readouterr().out


def test_a_grant_on_a_closed_root_is_not_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A grant dies with its session, so a closed root's marker is history."""
    repo = flipped_repo(tmp_path)
    seed_records(
        repo,
        [
            {
                "id": "basicly-ddd",
                "status": "closed",
                "title": "the finished root",
                "comments": [{"text": f"{policy.MARKER} grant level=L3 budget=1000000"}],
            }
        ],
    )

    assert _run(repo, monkeypatch) == 0

    assert "grants: none live" in capsys.readouterr().out


def test_the_remaining_budget_is_the_budget_less_what_this_checkout_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The figure the grant bounds, and the one a handover restated by hand."""
    repo = _seeded(tmp_path)
    run_record.record(
        repo,
        "basicly-ccc",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", "<prompt-redacted>"),
            model="claude-opus-5",
            model_tier="high",
            tokens=250_000,
        ),
    )

    assert _run(repo, monkeypatch, "--json") == 0

    grant = json.loads(capsys.readouterr().out)["grants"]["records"][0]
    assert grant == {
        "record": "basicly-ccc",
        "level": "L3",
        "budget": 1_000_000,
        "spent": 250_000,
        "remaining": 750_000,
    }


def test_a_checkout_with_no_run_records_reports_the_spend_unknown_not_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run records are per-checkout: a fresh worktree drawing the full budget lies."""
    repo = _seeded(tmp_path)

    assert _run(repo, monkeypatch) == 0

    out = capsys.readouterr().out
    assert "unknown" in out
    assert "spend is unknown where this checkout holds no run records" in out


def test_the_json_payload_carries_every_section_the_tables_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scripted surface, mirroring `status --json`: sections keyed, counts explicit."""
    repo = _seeded(tmp_path)
    _with_decisions(repo)

    assert _run(repo, monkeypatch, "--json") == 0

    payload = json.loads(capsys.readouterr().out)
    assert sorted(payload) == ["blocked", "decisions", "grants", "ready", "tracker"]
    assert payload["tracker"] == {"present": True, "records": 3, "ready": 2, "blocked": 1}
    assert payload["ready"]["records"][0]["record"] == "basicly-aaa"
    assert payload["blocked"]["records"][0]["record"] == "basicly-bbb"


def test_the_command_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read-only is the contract, asserted on the bytes rather than on the intent.

    A write here would land at the moment a session is least able to notice it.
    """
    repo = _seeded(tmp_path)
    _with_decisions(repo)
    before = {path: path.read_bytes() for path in sorted(tracker.ledger_dir(repo).glob("*.jsonl"))}

    assert _run(repo, monkeypatch) == 0

    assert {
        path: path.read_bytes() for path in sorted(tracker.ledger_dir(repo).glob("*.jsonl"))
    } == before


def test_a_section_with_nothing_in_it_says_so_rather_than_drawing_an_empty_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty frame reads as a shape the report found, when it found nothing.

    Nothing ready is a finding rather than a blank — it means every open record is held.
    """
    repo = flipped_repo(tmp_path)
    seed_records(repo, [{"id": "basicly-eee", "status": "closed", "title": "done"}])

    assert _run(repo, monkeypatch) == 0

    out = capsys.readouterr().out
    assert "ledger: 1 record," in out
    assert "ready: none" in out and "blocked: none" in out
    assert "Ready (" not in out and "Blocked (" not in out
