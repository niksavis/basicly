"""The identity half of the tracker scan and its redactor (basicly-r166).

Split from ``test_tracker_path_scan.py`` because that module had 637 tokens of
size headroom and these cases need more; the ``test_<module>_<aspect>`` name is
the derived form the ``test-naming`` gate accepts.

The username under test is always injected, never the one the host happens to
have: a test that asserted against the real `getpass.getuser()` would pass on the
machine that wrote it and assert nothing anywhere else.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from basicly import redact

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
    """Make both the hook and the redactor believe this machine is :data:`USERNAME`."""
    monkeypatch.setattr(scan.getpass, "getuser", lambda: USERNAME)
    monkeypatch.setattr(redact.getpass, "getuser", lambda: USERNAME)


def _event(actor: str = "", **payload: object) -> str:
    """One ledger event serialized the way the kit writes it."""
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
    """The glob widened for this bead; without the ledger the leak was invisible."""
    assert scan._TRACKER_GLOB.match(LEDGER)
    assert scan._TRACKER_GLOB.match(".beads/issues.jsonl")
    assert not scan._TRACKER_GLOB.match(".basicly/ledger/differential-baseline.json")


def test_a_username_in_a_ledger_event_is_reported(_as_username: None) -> None:
    """The positive control: without it the check below cannot tell a gate from a no-op."""
    hits = scan.findings(LEDGER, _event(actor=USERNAME))
    assert hits == [(LEDGER, 1, "machine-username")]


def test_a_username_nested_in_a_payload_is_reported(_as_username: None) -> None:
    """`created_by` and `asserted_by` sit inside the payload, not on the event."""
    assert scan.findings(LEDGER, _event(created_by=USERNAME))
    assert scan.findings(LEDGER, _event(asserted_by=USERNAME))


def test_the_redacted_equivalent_passes(_as_username: None) -> None:
    """The other half of the control: the repair the message names has to satisfy it."""
    scrubbed = redact.redact_committed(_event(actor=USERNAME, created_by=USERNAME))
    assert scan.findings(LEDGER, scrubbed) == []


def test_a_name_the_username_only_prefixes_is_left_alone(_as_username: None) -> None:
    """Word-bounded, so the repo's own published handle survives its own gate.

    `someuser` against `someuservis` is this repo's real case: the OS username is a
    prefix of the git identity that appears in every install URL it ships.
    """
    handle = USERNAME + "vis"
    assert redact.redact_machine_identity(handle) == handle
    assert scan.findings(LEDGER, _event(created_by=handle)) == []


def test_a_username_too_short_to_match_is_not_substituted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A two-character name inside ordinary words would shred the text it redacts."""
    monkeypatch.setattr(redact.getpass, "getuser", lambda: "ci")
    assert redact.redact_machine_identity("the ci pipeline") == "the ci pipeline"


def test_an_unresolvable_user_redacts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container with no passwd entry raises rather than returning a name."""

    def _raise() -> str:
        raise KeyError("no passwd entry")

    monkeypatch.setattr(redact.getpass, "getuser", _raise)
    assert redact.machine_identity() == ""
    assert redact.redact_machine_identity("text") == "text"


def test_paths_are_redacted_before_identity_so_the_layout_goes_too(_as_username: None) -> None:
    """The ordering `redact_committed` documents, asserted rather than described."""
    home = "/home" + f"/{USERNAME}/development/basicly"

    assert redact.redact_committed(home) == "<redacted:posix-home-path>"
