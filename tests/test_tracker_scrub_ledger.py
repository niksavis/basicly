from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from basicly import redact, tracker
from basicly.owned_store import TrackerDivergenceError

KIT_SOURCE = Path(__file__).parent.parent / tracker.KIT_TRACKER_DIR
USERNAME = "someuser"


@pytest.fixture(autouse=True)
def _as_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redact.getpass, "getuser", lambda: USERNAME)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / tracker.KIT_TRACKER_DIR).mkdir(parents=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / tracker.KIT_TRACKER_DIR / source.name)
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "dual"\n', encoding="utf-8")
    return tmp_path


def _write_events(repo: Path, events: list[dict]) -> Path:
    path = tracker.ledger_dir(repo) / "events-0001.jsonl"
    path.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def _event(repo: Path, record: str, seq: int, actor: str, payload: dict) -> dict:
    kit = tracker.kit(repo)
    return {
        "id": kit.events.event_id_for(record, "created", payload),
        "record": record,
        "seq": seq,
        "kind": "created",
        "actor": actor,
        "ts": "2026-08-15T00:00:00Z",
        "payload": payload,
    }


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_username_is_removed_from_the_actor_and_from_the_payload(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write_events(
        repo, [_event(repo, "basicly-a", 1, USERNAME, {"created_by": USERNAME, "title": "t"})]
    )

    assert tracker.scrub_ledger(repo) == 1

    event = _read(path)[0]
    assert USERNAME not in json.dumps(event)
    assert event["payload"]["title"] == "t"


def test_a_rewritten_event_re_mints_its_own_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write_events(repo, [_event(repo, "basicly-a", 1, "", {"created_by": USERNAME})])
    tracker.scrub_ledger(repo)

    event = _read(path)[0]
    kit = tracker.kit(repo)
    assert event["id"] == kit.events.event_id_for("basicly-a", "created", event["payload"])


def test_two_events_that_redact_onto_one_payload_keep_distinct_ids(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    kit = tracker.kit(repo)
    payload = {"created_by": USERNAME}
    first = _event(repo, "basicly-a", 1, "", payload)
    second = dict(first)
    second["seq"] = 2
    second["id"] = kit.events.event_id_for("basicly-a", "created", payload, generation=2)
    path = _write_events(repo, [first, second])

    tracker.scrub_ledger(repo)

    ids = [event["id"] for event in _read(path)]
    assert len(set(ids)) == 2


def test_an_event_whose_id_does_not_re_mint_stops_the_whole_rewrite(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    event = _event(repo, "basicly-a", 1, USERNAME, {"created_by": USERNAME})
    event["id"] = "basicly-a#ev-notthisone"
    path = _write_events(repo, [event])
    before = path.read_text(encoding="utf-8")

    with pytest.raises(TrackerDivergenceError):
        tracker.scrub_ledger(repo)

    assert path.read_text(encoding="utf-8") == before


def test_a_repo_with_no_ledger_is_a_no_op_and_never_loads_the_kit(tmp_path: Path) -> None:
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "external"\n', encoding="utf-8")

    assert tracker.scrub_ledger(tmp_path) == 0


def test_a_clean_ledger_is_left_byte_identical(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write_events(repo, [_event(repo, "basicly-a", 1, "", {"title": "nothing to redact"})])
    before = path.read_text(encoding="utf-8")

    assert tracker.scrub_ledger(repo) == 0
    assert path.read_text(encoding="utf-8") == before


def _hold_the_lock(repo: Path) -> Path:

    kit = tracker.kit(repo)
    path = tracker.ledger_dir(repo) / kit.events.LOCK_NAME
    path.write_text(
        json.dumps({"pid": os.getpid(), "monotonic": time.monotonic()}), encoding="utf-8"
    )
    return path


def _dirty_ledger(tmp_path: Path) -> tuple[Path, Path]:
    repo = _repo(tmp_path)
    events = [_event(repo, "basicly-a", 1, USERNAME, {"created_by": USERNAME})]
    return repo, _write_events(repo, events)


def test_a_rewrite_takes_the_writer_lock_and_touches_nothing_while_another_holds_it(
    tmp_path: Path,
) -> None:

    repo, path = _dirty_ledger(tmp_path)
    before = path.read_text(encoding="utf-8")
    _hold_the_lock(repo)
    kit = tracker.kit(repo)

    with pytest.raises(kit.events.LockUnavailableError):
        tracker.scrub_ledger(repo, lock_timeout_s=0.0)

    assert path.read_text(encoding="utf-8") == before


def test_an_append_arriving_during_a_rewrite_is_refused_by_the_lock_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo, path = _dirty_ledger(tmp_path)
    kit = tracker.kit(repo)
    ledger = tracker.ledger_dir(repo)
    outcome: list[object] = []
    publish = tracker._publish

    def _append_then_publish(tmp: Path, target: Path) -> bool:
        draft = kit.events.Draft("basicly-a", "comment", {"text": "arrived mid rewrite"})
        try:
            outcome.append(kit.events.append(ledger, [draft], lock_timeout_s=0.0))
        except kit.events.LockUnavailableError as exc:
            outcome.append(exc)
        return publish(tmp, target)

    monkeypatch.setattr(tracker, "_publish", _append_then_publish)

    assert tracker.scrub_ledger(repo) == 1

    assert isinstance(outcome[0], kit.events.LockUnavailableError)
    assert len(_read(path)) == 1


def test_an_append_queued_behind_a_rewrite_lands_once_the_lock_is_released(
    tmp_path: Path,
) -> None:
    repo, path = _dirty_ledger(tmp_path)

    assert tracker.scrub_ledger(repo) == 1

    kit = tracker.kit(repo)
    draft = kit.events.Draft("basicly-a", "comment", {"text": "queued behind the rewrite"})
    landed = kit.events.append(tracker.ledger_dir(repo), [draft], lock_timeout_s=0.0)

    ids = {event["id"] for event in _read(path)}
    assert [event.id for event in landed] and all(event.id in ids for event in landed)


def test_a_lock_hold_past_the_stale_bound_renames_nothing(tmp_path: Path) -> None:

    repo, path = _dirty_ledger(tmp_path)
    before = path.read_text(encoding="utf-8")
    kit = tracker.kit(repo)
    readings = iter([0.0, kit.events.LOCK_STALE_AFTER_S + 1.0])

    with pytest.raises(TrackerDivergenceError):
        tracker.scrub_ledger(repo, monotonic=lambda: next(readings))

    assert path.read_text(encoding="utf-8") == before
    assert not list(tracker.ledger_dir(repo).glob("*.tmp"))
