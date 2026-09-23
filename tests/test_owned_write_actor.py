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


MARKERS = ("BR_AGENT_NAME", "AI_AGENT", "CLAUDECODE")


def _only(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in MARKERS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.usefixtures("no_br")
def test_a_write_through_the_seam_carries_the_agent_onto_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _only(monkeypatch, BR_AGENT_NAME="codex")
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--status", "in_progress"])

    kit = owned_store.kit(repo)
    actors = [event.actor for event in events_of(repo, RECORD)]
    assert actors == [kit.events.UNATTRIBUTED_ACTOR, "agent:codex"]


@pytest.mark.usefixtures("no_br")
def test_a_create_through_the_seam_carries_the_agent_onto_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only(monkeypatch, BR_AGENT_NAME="copilot")
    repo = owned_repo(tmp_path)

    minted = owned_write.create(repo, ["create", "a new record", "-t", "task"])

    written = events_of(repo, minted)
    assert written, "the create appended nothing, so the assertion below would be vacuous"
    assert {event.actor for event in written} == {"agent:copilot"}


@pytest.mark.usefixtures("no_br")
def test_a_person_is_recorded_as_the_operator_class_and_never_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _only(monkeypatch)
    repo = owned_repo(tmp_path)
    record = owned_write.create(repo, ["create", "a new record", "-t", "task"])
    owned_write.append(repo, ["update", record, "--status", "in_progress"])
    owned_write.append(repo, ["comments", "add", record, "a note"])

    actors = [event.actor for event in events_of(repo, record)]
    assert actors, "no event was appended, so the assertion below would be vacuous"
    assert set(actors) == {"operator"}
    name = redact.machine_identity()
    assert not name or all(name not in actor for actor in actors)
