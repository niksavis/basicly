"""The owned ledger's identity scrub (basicly-r166).

Its own module because ``tests/test_br_seam.py`` is frozen with two tokens of
size headroom; ``test_br_<aspect>`` is the derived name the ``test-naming`` gate
accepts.

The username is injected, never read from the host: a test that asserted against
the real ``getpass.getuser()`` would pass on the machine that wrote it and assert
nothing on any other.
"""

from __future__ import annotations

import json
import shutil
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
    """A checkout with the tracker kit installed and an empty ledger directory."""
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
    """One event carrying the id the kit would have minted for it."""
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
    """Both sites the leak was measured at: 840 events carried it on one, 3,972 on the other."""
    repo = _repo(tmp_path)
    path = _write_events(
        repo, [_event(repo, "basicly-a", 1, USERNAME, {"created_by": USERNAME, "title": "t"})]
    )

    assert tracker.scrub_ledger(repo) == 1

    event = _read(path)[0]
    assert USERNAME not in json.dumps(event)
    assert event["payload"]["title"] == "t"


def test_a_rewritten_event_re_mints_its_own_id(tmp_path: Path) -> None:
    """Without this the scrub leaves every touched event failing its own consistency check."""
    repo = _repo(tmp_path)
    path = _write_events(repo, [_event(repo, "basicly-a", 1, "", {"created_by": USERNAME})])
    tracker.scrub_ledger(repo)

    event = _read(path)[0]
    kit = tracker.kit(repo)
    assert event["id"] == kit.events.event_id_for("basicly-a", "created", event["payload"])


def test_two_events_that_redact_onto_one_payload_keep_distinct_ids(tmp_path: Path) -> None:
    """The generation counter runs over the redacted payloads, not only the stored ones."""
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
    """Fail closed: an underivable generation means the re-mint would invent an id."""
    repo = _repo(tmp_path)
    event = _event(repo, "basicly-a", 1, USERNAME, {"created_by": USERNAME})
    event["id"] = "basicly-a#ev-notthisone"
    path = _write_events(repo, [event])
    before = path.read_text(encoding="utf-8")

    with pytest.raises(TrackerDivergenceError):
        tracker.scrub_ledger(repo)

    assert path.read_text(encoding="utf-8") == before


def test_a_repo_with_no_ledger_is_a_no_op_and_never_loads_the_kit(tmp_path: Path) -> None:
    """It runs on the commit path, so an `external` repo must not be a failed landing."""
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "external"\n', encoding="utf-8")

    assert tracker.scrub_ledger(tmp_path) == 0


def test_a_clean_ledger_is_left_byte_identical(tmp_path: Path) -> None:
    """It runs on every tracker commit, so a no-change pass must not churn the file."""
    repo = _repo(tmp_path)
    path = _write_events(repo, [_event(repo, "basicly-a", 1, "", {"title": "nothing to redact"})])
    before = path.read_text(encoding="utf-8")

    assert tracker.scrub_ledger(repo) == 0
    assert path.read_text(encoding="utf-8") == before
