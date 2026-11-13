from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.test_kit_vault_canonical import RECORD, _source, events
from tests.test_kit_vault_migrate import _plan, vault_migrate
from tests.test_kit_vault_share import _machine, bare
from tests.test_kit_vault_view import _push

__all__ = ["bare"]


def test_independent_imports_have_identical_canonical_wrappers_with_different_clocks(
    tmp_path: Path,
    bare: Path,
) -> None:
    second = tmp_path / "second.git"
    vault_migrate.git_runner.run(
        ["clone", "--bare", str(bare), str(second)], tmp_path, allow_file=True
    )
    source = tmp_path / "source"
    _source(source)
    outputs = []
    for index, target in enumerate((bare, second)):
        clone, _ = _machine(tmp_path, target, f"import-{index}")
        plan = vault_migrate.Import(clone, lambda at=1000.0 + index: at, True)
        vault_migrate.ledger_to_vault(source, plan)
        valid = vault_migrate.vault_clone.valid_events(clone)
        outputs.append(json.dumps(valid, sort_keys=True, ensure_ascii=False))
    assert outputs[0] == outputs[1]


def test_export_keeps_the_same_current_holder_name_as_the_vault_view(
    tmp_path: Path,
    bare: Path,
) -> None:
    source = tmp_path / "source"
    _source(source)
    events.append(
        source,
        [events.Draft(RECORD, "status", {"status": "open"})],
        actor="contributor",
        clock=lambda: 3000.0,
    )
    vault_migrate.ledger_to_vault(source, _plan(tmp_path, bare))
    _push(
        tmp_path,
        bare,
        "claim-writer",
        [{"kind": "claim", "record": RECORD, "payload": {"name": "A display name"}}],
    )
    clone, _ = _machine(tmp_path, bare, "reader")
    view = vault_migrate.vault_view.present(
        clone, vault_migrate.vault_view.Want("demo"), clock=time.time
    )
    target = tmp_path / "export"
    vault_migrate.from_vault(clone, "demo", target, clock=time.time)
    viewed = events.read_events(view)[0]
    written = events.read_events(target)[0]
    assert events.fold(viewed).records[RECORD].fields["assignee"] == "A display name"
    assert [events.to_json(event) for event in viewed] == [
        events.to_json(event) for event in written
    ]
    assert vault_migrate.canonical.fsck.check(target).exit_code == 0


def test_export_refuses_a_nonempty_destination_before_fetching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "export"
    target.mkdir()
    (target / "template.json").write_text('{"prefix":"other"}')

    def no_fetch(*_args: object, **_kwargs: object) -> None:
        pytest.fail("an invalid export fetched a vault")

    monkeypatch.setattr(vault_migrate.vault_clone, "fetch", no_fetch)
    with pytest.raises(vault_migrate.MigrateError, match="not empty"):
        vault_migrate.from_vault(None, "demo", target, clock=time.time)
    assert (target / "template.json").read_text() == '{"prefix":"other"}'


@pytest.mark.parametrize(
    "stamp", ["1970-01-01T00:00:01Z", "1970-01-01T02:00:01+02:00", "1970-01-01T00:00:01"]
)
def test_wrapper_time_uses_the_source_instant_without_a_new_datetime_alias(stamp: str) -> None:
    payload = {"title": "A public record"}
    event = events.Event(
        events.event_id_for(RECORD, "created", payload),
        RECORD,
        1,
        "created",
        "contributor",
        stamp,
        payload,
    )
    _, lines = vault_migrate.lines_of([event])
    assert lines[0]["at"] == 1.0
