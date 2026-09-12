from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import config, owned_store, owned_write, redact

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
RECORD = "at5-1"

IDENTITY_PLACEHOLDER = f"<redacted:{redact.IDENTITY_RULE}>"


def owned_repo(tmp_path: Path) -> Path:

    target = tmp_path / owned_store.KIT_TRACKER_DIR
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, target / source.name)
    (tmp_path / owned_store.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(
        f'[tracker]\nmode = "{owned_store.MODE_OWNED}"\nprefix = "at5"\n', encoding="utf-8"
    )
    assert config.load_tracker_mode(tmp_path) == owned_store.MODE_OWNED
    return tmp_path


def seed(repo: Path, record: str) -> None:
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})],
    )


def events_of(repo: Path, record: str) -> list[Any]:
    kit = owned_store.kit(repo)
    return [
        event for event in kit.read_ledger(owned_store.ledger_dir(repo)) if event.record == record
    ]


@pytest.fixture
def no_br(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def test_a_dispatched_agent_is_recorded_instead_of_the_operating_system_user() -> None:

    resolved = owned_write.resolved_actor({owned_write.AGENT_ENV_VAR: "claude"})
    assert resolved == f"{owned_write.AGENT_ACTOR}claude"
    assert owned_write.OPERATOR_ACTOR not in resolved


def test_an_undispatched_write_records_the_masked_operator_and_never_the_username() -> None:

    name = redact.machine_identity()
    if not name:
        pytest.skip("this host has no username the identity rule can word-bound")
    resolved = owned_write.resolved_actor({})
    assert resolved == f"{owned_write.OPERATOR_ACTOR}{IDENTITY_PLACEHOLDER}"
    assert name not in resolved


def test_an_identity_the_redactor_cannot_mask_records_the_reason_not_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(redact, "machine_identity", lambda: "")
    resolved = owned_write.resolved_actor({})
    assert resolved == owned_write.UNRESOLVED_ACTOR
    assert resolved, "an unresolved actor is a reason, never an empty field"


def test_an_actor_taken_from_the_environment_is_redacted_and_capped() -> None:

    leaked = owned_write.resolved_actor({
        owned_write.AGENT_ENV_VAR: "API_TO" + "KEN=" + "abcdefghij"
    })
    assert "abcdefghij" not in leaked
    assert leaked.startswith(owned_write.AGENT_ACTOR)
    long_name = "a" * (owned_write.MAX_ACTOR_CHARS * 2)
    capped = owned_write.resolved_actor({owned_write.AGENT_ENV_VAR: long_name})
    assert len(capped) == len(owned_write.AGENT_ACTOR) + owned_write.MAX_ACTOR_CHARS


def test_a_newline_in_the_environment_cannot_reach_the_ledger_line() -> None:
    resolved = owned_write.resolved_actor({owned_write.AGENT_ENV_VAR: " claude\n  code\t"})
    assert resolved == f"{owned_write.AGENT_ACTOR}claude code"


@pytest.mark.usefixtures("no_br")
def test_a_write_through_the_seam_carries_the_agent_onto_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv(owned_write.AGENT_ENV_VAR, "codex")
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--status", "in_progress"])

    kit = owned_store.kit(repo)
    actors = [event.actor for event in events_of(repo, RECORD)]
    assert actors == [kit.events.UNATTRIBUTED_ACTOR, f"{owned_write.AGENT_ACTOR}codex"]


@pytest.mark.usefixtures("no_br")
def test_a_create_through_the_seam_carries_the_agent_onto_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(owned_write.AGENT_ENV_VAR, "copilot")
    repo = owned_repo(tmp_path)

    minted = owned_write.create(repo, ["create", "a new record", "-t", "task"])

    written = events_of(repo, minted)
    assert written, "the create appended nothing, so the assertion below would be vacuous"
    assert {event.actor for event in written} == {f"{owned_write.AGENT_ACTOR}copilot"}


@pytest.mark.usefixtures("no_br")
def test_no_event_the_seam_writes_can_carry_an_empty_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.delenv(owned_write.AGENT_ENV_VAR, raising=False)
    monkeypatch.setattr(redact, "machine_identity", lambda: "")
    repo = owned_repo(tmp_path)
    record = owned_write.create(repo, ["create", "a new record", "-t", "task"])
    owned_write.append(repo, ["update", record, "--status", "in_progress"])
    owned_write.append(repo, ["comments", "add", record, "a note"])

    actors = [event.actor for event in events_of(repo, record)]
    assert actors, "no event was appended, so the assertion below would be vacuous"
    assert set(actors) == {owned_write.UNRESOLVED_ACTOR}
