"""Reading a curator's reply into the release record a shipped unit carries.

The dispatch itself is `test_loop.py`'s — what this file asserts is the part that
decides whether a release note's claims have evidence behind them: which replies become
an artifact, which are refused, and which are recorded as having bound nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import artifact_record, curate, handoff

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "release-record.schema.json"

GOOD = {
    "claims": [
        {
            "claim": "the seam refuses a write it cannot mirror",
            "evidence": [{"kind": "test", "reference": "tests/test_br_mode_guard.py"}],
        }
    ],
    "unsupported": [],
    "post_ship_action": "push the tag once the owner approves",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout carrying the release-record schema, so both ends of the contract run."""
    schemas = tmp_path / ".basicly" / "core" / "schemas"
    schemas.mkdir(parents=True)
    source = REPO_ROOT / ".basicly" / "core" / "schemas" / SCHEMA
    (schemas / SCHEMA).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


# --- reading the reply ----------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps(GOOD),
        f"Here is the record.\n\n```json\n{json.dumps(GOOD)}\n```\n\nThat is all.",
        f"prose before {json.dumps(GOOD)} prose after",
    ],
)
def test_the_object_is_located_rather_than_assumed_to_be_the_whole_reply(reply: str) -> None:
    """A judge that wraps its answer in prose has still answered.

    Strictly parsing the whole reply would refuse the friendliest form and record
    "bound no claims" for a curator that bound them all — a silent downgrade of the
    one thing this artifact exists to state.
    """
    payload = curate.payload_from_reply(reply, "basicly-a")

    assert payload is not None
    assert payload["claims"] == GOOD["claims"]


@pytest.mark.parametrize(
    "reply",
    ["I could not bind these claims to anything.", "", "{not json at all}", "[1, 2, 3]"],
)
def test_a_reply_carrying_no_object_binds_nothing(reply: str) -> None:
    """None is the stated answer, and the control on the parametrised case above.

    An empty artifact would validate and assert that the release makes no claims, which
    is a different statement from "nobody managed to check".
    """
    assert curate.payload_from_reply(reply, "basicly-a") is None


def test_the_engine_supplies_the_two_fields_it_already_knows() -> None:
    """A judge that mistypes either turns a judgement failure into a schema failure."""
    payload = curate.payload_from_reply(
        json.dumps({**GOOD, "schema_version": 99, "issue": "wrong"}), "basicly-a"
    )

    assert payload is not None
    assert payload["schema_version"] == handoff.SCHEMA_VERSION
    assert payload["issue"] == "basicly-a"


# --- recording it ----------------------------------------------------------------


def test_a_bound_record_is_written_and_counted(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact reaches the store, and the ship's detail line says how much it bound."""
    written: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        artifact_record,
        "write",
        lambda _root, issue, kind, payload: written.append((issue, kind, payload)),
    )

    said = curate.record(repo, "basicly-a", json.dumps(GOOD))

    assert written == [
        (
            "basicly-a",
            handoff.RELEASE_RECORD,
            curate.payload_from_reply(json.dumps(GOOD), "basicly-a"),
        )
    ]
    assert said == "release record: 1 claim(s) bound, 0 unsupported"


def test_a_claim_with_no_evidence_is_refused_by_the_schema_not_recorded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The known-bad input: an unevidenced claim is exactly what this artifact refuses.

    Without this the pass above would hold against a `record` that writes anything the
    curator returns, which is the failure the whole contract exists to prevent.
    """
    monkeypatch.setattr(
        artifact_record, "write", lambda *_a, **_k: pytest.fail("an invalid record was written")
    )
    bad = {**GOOD, "claims": [{"claim": "it works", "evidence": []}]}

    said = curate.record(repo, "basicly-a", json.dumps(bad))

    assert said.startswith("the release record was refused")


def test_a_repo_without_the_schema_records_nothing_and_says_nothing(tmp_path: Path) -> None:
    """A consumer who has not installed the contract runs neither end of it."""
    assert curate.record(tmp_path, "basicly-a", json.dumps(GOOD)) == ""


def test_a_curator_that_bound_nothing_is_reported_rather_than_hidden(repo: Path) -> None:
    """Three outcomes have to be distinguishable, and silence reads as success."""
    assert curate.record(repo, "basicly-a", "I found no evidence.") == "the curator bound no claims"
