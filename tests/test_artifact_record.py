"""The recorded form of a handoff artifact: rendered, found again, decoded.

Split out of `test_handoff.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14), along the boundary the modules themselves state: *recorded form*
against *judgement*. Nothing here knows what an artifact must contain, so nothing here
loads a schema or asserts a refusal — those stay with `test_handoff.py`, which owns the
contract, and the loop states that produce and consume artifacts stay with
`test_handoff_states.py`.

`tmp_path` rather than `work_repo` throughout, for the same reason: this module reads and
writes markers through the `br` seam and has no opinion about the repo it is handed, so a
repo with the catalog schemas installed would only slow the tests down without changing an
answer. The seam is faked at its own funnel — `tracker.run_br` — so a write is readable back
through the fake and a round trip is a real round trip.

`_FakeBr` and the `fake_br` fixture live here rather than in `test_handoff.py` because the
comment surface they stand in for is *this* module's seam; the contract and state suites
import them from here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import artifact_record
from tests import fake_tracker

KIND = "implementation-plan"
OTHER_KIND = "change-summary"


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stand-in for the br CLI, taught only the comment surface the marker seam uses.

    Comments are kept per issue so a write is readable back through the same fake, which
    is what makes the round trip — record, then read — a real round trip rather than two
    assertions about one dictionary. ``reply`` overrides what ``comments list`` answers,
    so a store that cannot answer can be exercised as well as one that can.
    """

    def __init__(self) -> None:
        self.comments: dict[str, list[str]] = {}
        self.reply: str | None = None

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[3])
            return _Proc("")
        if args[:2] == ["comments", "list"]:
            if self.reply is not None:
                return _Proc(self.reply)
            texts = self.comments.get(args[2], [])
            return _Proc(json.dumps([{"text": text} for text in texts]))
        raise AssertionError(f"unexpected br call: {args}")


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch) -> _FakeBr:
    """Route the marker seam's own funnel — ``tracker.run_br`` — at a stateful fake."""
    fake = _FakeBr()
    fake_tracker.install(monkeypatch, fake)
    return fake


# --- rendering: one set of facts is one string ------------------------------


def test_the_body_carries_the_kind_ahead_of_the_payload() -> None:
    """A reader asks "what did this unit hand on" once, and reads the kind off the line."""
    body = artifact_record.marker_body(KIND, {"feature": "feat"})
    assert body == f'{artifact_record.MARKER} kind={KIND} {{"feature":"feat"}}'


def test_the_same_facts_in_a_different_key_order_render_one_body() -> None:
    """Sorted keys and compact separators are what let :func:`write` compare for equality."""
    first = artifact_record.marker_body(KIND, {"a": 1, "b": 2})
    second = artifact_record.marker_body(KIND, {"b": 2, "a": 1})
    assert first == second


def test_a_rendered_body_decodes_back_to_the_payload() -> None:
    """The round trip the whole module exists for, with no store in the way."""
    payload = {"groups": [["feat.1"]], "tasks": [{"issue_id": "feat.1"}]}
    assert artifact_record.recorded_payload(artifact_record.marker_body(KIND, payload), KIND) == (
        payload
    )


# --- decoding: absent, other, and corrupted are three answers ---------------


def test_another_marker_family_is_not_an_artifact() -> None:
    """Only this family's markers are read: a policy or run marker is not a handoff."""
    assert artifact_record.recorded_payload("[harness-policy] checkpoint=decompose", KIND) is None


def test_the_other_kind_is_not_decoded_as_this_one() -> None:
    """The kind is a field, so one family carries both without either answering for the other."""
    body = artifact_record.marker_body(OTHER_KIND, {"issue_id": "i"})
    assert artifact_record.recorded_payload(body, KIND) is None
    assert artifact_record.recorded_payload(body, OTHER_KIND) == {"issue_id": "i"}


def test_a_marker_with_no_kind_field_is_not_an_artifact() -> None:
    """An older or hand-written marker in this family, with no kind, carries nothing."""
    assert artifact_record.recorded_payload(f"{artifact_record.MARKER} {{}}", KIND) is None


def test_a_payload_that_is_not_json_decodes_to_the_raw_string() -> None:
    """*Corrupted* and *absent* are different answers, and only the first may refuse a state."""
    body = f"{artifact_record.MARKER} kind={KIND} {{not json"
    assert artifact_record.recorded_payload(body, KIND) == "{not json"


# --- reading through the seam ----------------------------------------------


def test_a_unit_with_no_marker_carries_no_artifact(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The honest empty answer, distinguished below from a store that could not answer."""
    assert artifact_record.read(tmp_path, "feat", KIND) is None
    assert fake_br.comments == {}


def test_the_last_recorded_marker_wins(tmp_path: Path, fake_br: _FakeBr) -> None:
    """Re-decomposed under a changed plan, a reader gets the plan that made the children."""
    fake_br.comments["feat"] = [
        artifact_record.marker_body(KIND, {"tasks": ["superseded"]}),
        artifact_record.marker_body(KIND, {"tasks": ["current"]}),
    ]
    assert artifact_record.read(tmp_path, "feat", KIND) == {"tasks": ["current"]}


def test_a_marker_of_another_kind_does_not_answer_for_this_one(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """One family, two kinds, read apart — through the store as well as in the pure decoder."""
    fake_br.comments["i"] = [artifact_record.marker_body(OTHER_KIND, {"issue_id": "i"})]
    assert artifact_record.read(tmp_path, "i", KIND) is None
    assert artifact_record.read(tmp_path, "i", OTHER_KIND) == {"issue_id": "i"}


def test_a_store_that_cannot_answer_raises_rather_than_reading_as_absent(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The hard seam, on purpose: "no artifact, carry on" is the fail-open shape here."""
    fake_br.reply = "not json at all"
    with pytest.raises(RuntimeError):
        artifact_record.read(tmp_path, "feat", KIND)


# --- writing through the seam ----------------------------------------------


@pytest.mark.usefixtures("fake_br")
def test_a_write_is_readable_back_as_the_payload(tmp_path: Path) -> None:
    """Record then read, through the store, is the seam's whole contract."""
    artifact_record.write(tmp_path, "feat", KIND, {"feature": "feat"})
    assert artifact_record.read(tmp_path, "feat", KIND) == {"feature": "feat"}


def test_writing_the_same_artifact_twice_records_one_marker(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """A state re-entered on every advance must not bury its artifact under copies."""
    artifact_record.write(tmp_path, "feat", KIND, {"feature": "feat"})
    artifact_record.write(tmp_path, "feat", KIND, {"feature": "feat"})
    assert len(fake_br.comments["feat"]) == 1


def test_a_changed_payload_is_recorded_alongside_the_one_it_supersedes(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """Idempotence is on the whole body, so a genuinely new artifact still lands."""
    artifact_record.write(tmp_path, "feat", KIND, {"tasks": ["a"]})
    artifact_record.write(tmp_path, "feat", KIND, {"tasks": ["a", "b"]})
    assert len(fake_br.comments["feat"]) == 2
    assert artifact_record.read(tmp_path, "feat", KIND) == {"tasks": ["a", "b"]}


def test_the_same_payload_under_two_kinds_records_two_markers(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The kind is part of the body, so idempotence does not collapse the two families."""
    artifact_record.write(tmp_path, "i", KIND, {"issue_id": "i"})
    artifact_record.write(tmp_path, "i", OTHER_KIND, {"issue_id": "i"})
    assert len(fake_br.comments["i"]) == 2


@pytest.mark.usefixtures("fake_br")
def test_whatever_it_is_handed_is_recorded(tmp_path: Path) -> None:
    """Validation is the caller's and happens first; this module rules on nothing."""
    artifact_record.write(tmp_path, "feat", KIND, {"not": "a valid plan"})
    assert artifact_record.read(tmp_path, "feat", KIND) == {"not": "a valid plan"}
