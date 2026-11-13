from __future__ import annotations

import json
from pathlib import Path

from tests.test_kit_vault_canonical import RECORD, _redact, _source, canonical, events
from tests.test_kit_vault_view import _carry, vault_view


def _check(folder: Path, shown: list) -> None:
    folder.mkdir()
    (folder / events.INITIAL_LOG_NAME).write_text(
        "".join(events.to_json(event) + "\n" for event in shown)
    )
    assert canonical.fsck.check(folder).exit_code == 0


def test_view_keeps_holder_history_ids_and_clears_unclaimed_current_holder(tmp_path: Path) -> None:
    source = _source(tmp_path / "source")
    made = canonical.canonicalize(source, redactor=_redact, redact_holders=True)
    carried = vault_view.carried(
        _carry([json.loads(events.to_json(e)) for e in made.events]), vault_view.Want("demo")
    )
    shown = vault_view.renumbered(carried, {})
    assert [event.id for event in shown[: len(source)]] == [event.id for event in made.events]
    assert len(shown) == len(source) + 1
    assert shown[-1].actor == vault_view.HOLDER_ACTOR
    assert events.fold(shown).records[RECORD].fields["assignee"] == ""
    _check(tmp_path / "view", shown)


def test_view_mints_a_distinct_overlay_when_holder_payload_appears_in_history(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    events.append(
        source,
        [
            events.Draft(RECORD, "created", {"title": "A public record"}),
            events.Draft(RECORD, "field", {"name": "assignee", "value": "active holder"}),
            events.Draft(RECORD, "field", {"name": "assignee", "value": "older holder"}),
        ],
        actor="contributor",
        clock=lambda: 1000.0,
    )
    found = events.read_events(source)[0]
    shown = vault_view.renumbered(found, {RECORD: ("a" * 32, "session", "active holder")})
    assert shown[-1].id not in {event.id for event in found}
    assert [event.id for event in shown[:-1]] == [event.id for event in found]
    assert events.fold(shown).records[RECORD].fields["assignee"] == "active holder"
    _check(tmp_path / "view", shown)
