"""Tests for the runner run-record (basicly-z6dh).

The record is the keystone correlation artifact: per dispatched run it captures
duration + exit outcome + agent (model/token fields reserved), keyed by bead id,
written atomically into the self-ignored ``.basicly/usage/``. These tests pin
that shape and — crucially — that only metadata is persisted (no prompt body).
"""

from __future__ import annotations

import json
from pathlib import Path

from basicly import run_record
from basicly.run_record import (
    EXECUTED,
    FAILED,
    HANDOFF,
    REDACTED_PROMPT,
    RUN_RECORDS_FILE,
    RunRecord,
)


def _records(repo_root: Path) -> dict:
    return json.loads((repo_root / RUN_RECORDS_FILE).read_text(encoding="utf-8"))


# --- outcome_of -------------------------------------------------------------


def test_outcome_of_labels_handoff_executed_and_failed() -> None:
    """A handoff is HANDOFF; a real run is EXECUTED only on a zero exit."""
    assert run_record.outcome_of(handoff=True, returncode=None) == HANDOFF
    assert run_record.outcome_of(handoff=False, returncode=0) == EXECUTED
    assert run_record.outcome_of(handoff=False, returncode=2) == FAILED
    assert run_record.outcome_of(handoff=False, returncode=None) == FAILED


# --- build_record -----------------------------------------------------------


def test_build_record_derives_outcome_stamps_time_and_reserves_fields() -> None:
    """build_record fills outcome + timestamp; model defaults null, token/cost stay reserved."""
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.5,
        command=("claude", "-p", REDACTED_PROMPT),
    )
    assert entry.agent == "claude"
    assert entry.outcome == EXECUTED
    assert entry.duration_s == 1.5
    assert entry.timestamp  # ISO stamp present
    assert entry.model is None and entry.tokens is None and entry.cost is None


def test_build_record_stamps_model_provenance() -> None:
    """A pinned model is recorded as provenance (basicly-45ld); token/cost stay null."""
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude", "--model", "opus", "-p", REDACTED_PROMPT),
        model="opus",
    )
    assert entry.model == "opus"
    assert entry.tokens is None and entry.cost is None


# --- record (write) ---------------------------------------------------------


def test_record_writes_keyed_list_and_self_ignores(tmp_path: Path) -> None:
    """A record lands under its bead id, and the usage dir self-ignores."""
    entry = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.1, command=("claude",)
    )
    run_record.record(tmp_path, "basicly-z6dh.1", entry)

    data = _records(tmp_path)
    assert list(data) == ["basicly-z6dh.1"]
    assert data["basicly-z6dh.1"][0]["outcome"] == EXECUTED
    # The usage dir self-ignores exactly like the tool-usage telemetry.
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_record_accumulates_history_per_bead(tmp_path: Path) -> None:
    """Re-dispatching the same bead appends, keeping the run history in order."""
    first = run_record.build_record(
        agent="claude", handoff=False, returncode=2, duration_s=0.1, command=("claude",)
    )
    second = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.2, command=("claude",)
    )
    run_record.record(tmp_path, "i", first)
    run_record.record(tmp_path, "i", second)

    history = _records(tmp_path)["i"]
    assert [r["outcome"] for r in history] == [FAILED, EXECUTED]


def test_record_never_persists_the_raw_prompt(tmp_path: Path) -> None:
    """A record only ever carries the redacted command, never the prompt body."""
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=0.1,
        command=("claude", "-p", REDACTED_PROMPT),
    )
    run_record.record(tmp_path, "i", entry)
    blob = (tmp_path / RUN_RECORDS_FILE).read_text(encoding="utf-8")
    assert REDACTED_PROMPT in blob
    assert "implement the work" not in blob  # no dispatch-prompt text leaked


def test_record_restarts_on_a_corrupt_file(tmp_path: Path) -> None:
    """A corrupt records file restarts empty rather than failing the write."""
    (tmp_path / ".basicly/usage").mkdir(parents=True)
    (tmp_path / RUN_RECORDS_FILE).write_text("{ not json", encoding="utf-8")
    entry = run_record.build_record(
        agent="codex", handoff=True, returncode=None, duration_s=None, command=()
    )
    run_record.record(tmp_path, "i", entry)
    assert _records(tmp_path)["i"][0]["outcome"] == HANDOFF


def test_record_restarts_on_a_wrong_shaped_value(tmp_path: Path) -> None:
    """A valid-JSON but wrong-shaped bead value restarts that bead, never raising."""
    (tmp_path / ".basicly/usage").mkdir(parents=True)
    # Externally tampered: the value is a string, not the expected list.
    (tmp_path / RUN_RECORDS_FILE).write_text('{"i": "tampered"}', encoding="utf-8")
    entry = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.1, command=("claude",)
    )
    run_record.record(tmp_path, "i", entry)  # must not raise AttributeError
    assert _records(tmp_path)["i"][0]["outcome"] == EXECUTED


# --- load_run_records (read) ------------------------------------------------


def test_load_run_records_none_when_absent(tmp_path: Path) -> None:
    """No file yet reads back as None (the hook/loop has not run)."""
    assert run_record.load_run_records(tmp_path) is None


def test_load_run_records_round_trips(tmp_path: Path) -> None:
    """A written record reads back with its fields intact (command as a list)."""
    entry = RunRecord(
        agent="copilot",
        outcome=EXECUTED,
        returncode=0,
        duration_s=3.0,
        command=("copilot", "-p", REDACTED_PROMPT),
        timestamp="2026-07-17T00:00:00+00:00",
    )
    run_record.record(tmp_path, "i", entry)
    loaded = run_record.load_run_records(tmp_path)
    assert loaded is not None
    assert loaded["i"][0]["agent"] == "copilot"
    assert loaded["i"][0]["command"] == ["copilot", "-p", REDACTED_PROMPT]


# --- latest_record (attribution source, basicly-140a) -----------------------


def test_latest_record_none_when_absent(tmp_path: Path) -> None:
    """No file, or no history for the bead, reads back as None."""
    assert run_record.latest_record(tmp_path, "i") is None


def test_latest_record_returns_the_most_recent_with_model(tmp_path: Path) -> None:
    """The last-appended record for the bead comes back rebuilt, model included."""
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", REDACTED_PROMPT),
        ),
    )
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="codex",
            handoff=False,
            returncode=0,
            duration_s=2.0,
            command=("codex", "exec", REDACTED_PROMPT),
            model="o4",
        ),
    )
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None
    assert latest.agent == "codex" and latest.model == "o4"


def test_latest_record_tolerates_an_unknown_key(tmp_path: Path) -> None:
    """An on-disk record with a field this version does not know still loads."""
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", REDACTED_PROMPT),
        ),
    )
    records_file = tmp_path / run_record.RUN_RECORDS_FILE
    data = json.loads(records_file.read_text(encoding="utf-8"))
    data["i"][0]["future_field"] = "xyz"  # a newer writer added a field
    records_file.write_text(json.dumps(data), encoding="utf-8")
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None and latest.agent == "claude"
