from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from basicly import redact, tracker

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "tracker-path-scan.py"
LEDGER = ".basicly/ledger/events-0001.jsonl"
USERNAME = "someuser"


def _load_hook():
    spec = importlib.util.spec_from_file_location("tracker_path_scan_identity", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load_hook()


@pytest.fixture
def _as_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan.getpass, "getuser", lambda: USERNAME)
    monkeypatch.setattr(redact.getpass, "getuser", lambda: USERNAME)


def _event(actor: str = "", **payload: object) -> str:
    return json.dumps(
        {
            "id": "basicly-test#ev-0",
            "record": "basicly-test",
            "seq": 1,
            "kind": "created",
            "actor": actor,
            "ts": "2026-08-15T00:00:00Z",
            "payload": dict(payload),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def test_the_ledger_is_in_scope_and_a_sibling_json_file_is_not() -> None:
    assert scan._TRACKER_GLOB.match(LEDGER)
    assert not scan._TRACKER_GLOB.match(".basicly/ledger/differential-baseline.json")


def test_a_username_in_a_ledger_event_is_reported(_as_username: None) -> None:
    hits = scan.findings(LEDGER, _event(actor=USERNAME))
    assert hits == [(LEDGER, 1, "machine-username")]


def test_a_username_nested_in_a_payload_is_reported(_as_username: None) -> None:
    assert scan.findings(LEDGER, _event(created_by=USERNAME))
    assert scan.findings(LEDGER, _event(asserted_by=USERNAME))


def test_the_redacted_equivalent_passes(_as_username: None) -> None:
    scrubbed = redact.redact_committed(_event(actor=USERNAME, created_by=USERNAME))
    assert scan.findings(LEDGER, scrubbed) == []


def test_a_name_the_username_only_prefixes_is_left_alone(_as_username: None) -> None:

    handle = USERNAME + "vis"
    assert redact.redact_machine_identity(handle) == handle
    assert scan.findings(LEDGER, _event(created_by=handle)) == []


def test_a_username_too_short_to_match_is_not_substituted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redact.getpass, "getuser", lambda: "ci")
    assert redact.redact_machine_identity("the ci pipeline") == "the ci pipeline"


def test_an_unresolvable_user_redacts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:

    def _raise() -> str:
        raise KeyError("no passwd entry")

    monkeypatch.setattr(redact.getpass, "getuser", _raise)
    assert redact.machine_identity() == ""
    assert redact.redact_machine_identity("text") == "text"


def test_paths_are_redacted_before_identity_so_the_layout_goes_too(_as_username: None) -> None:
    home = "/home" + f"/{USERNAME}/development/basicly"

    assert redact.redact_committed(home) == "<redacted:posix-home-path>"


def _kind_event(kind: str, actor: str = "", **payload: object) -> str:
    event = json.loads(_event(actor, **payload))
    return json.dumps({**event, "kind": kind}, separators=(",", ":"))


def test_the_holder_field_may_carry_the_username(_as_username: None) -> None:
    line = _kind_event("field", name="assignee", value=USERNAME)

    assert scan.findings(LEDGER, line) == []


@pytest.mark.parametrize(
    "line",
    [
        _kind_event("comment", text=f"asked {USERNAME} to review"),
        _kind_event("field", name="notes", value=USERNAME),
        _kind_event("field", actor=USERNAME, name="assignee", value=USERNAME),
        _kind_event("created", assignee="alex", title=f"for {USERNAME}"),
    ],
)
def test_the_username_outside_the_holder_field_is_still_reported(
    _as_username: None, line: str
) -> None:
    assert scan.findings(LEDGER, line) == [(LEDGER, 1, "machine-username")]


def test_the_assignee_of_a_created_event_may_carry_the_username(_as_username: None) -> None:
    line = _kind_event("created", assignee=USERNAME, title="t")

    assert scan.findings(LEDGER, line) == []


def test_a_home_path_in_the_holder_field_is_still_reported(_as_username: None) -> None:
    home = "/home" + f"/{USERNAME}/project"
    line = _kind_event("field", name="assignee", value=home)

    assert scan.findings(LEDGER, line) == [(LEDGER, 1, "posix-home-path")]


def test_the_hook_names_the_holder_field_the_tracker_and_the_kit_use() -> None:
    kit = _load_kit_events()

    assert scan._HOLDER_FIELD == tracker.HOLDER_FIELD == kit.HOLDER_FIELD
    assert scan._FIELD_KIND == kit.KIND_FIELD
    assert scan._CREATED_KIND == kit.KIND_CREATED


def _load_kit_events():
    path = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "events.py"
    name = "tracker_path_scan_identity_kit_events"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
