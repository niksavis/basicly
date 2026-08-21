"""Who the engine's own writes are made under (basicly-at5tph).

Split off `tests/test_owned_write.py` under the `test_<module>_<aspect>.py` convention
`check_test_naming.py` records, because that module measured 3,929 of its 4,000 permitted
tokens before this bead's first edit and is not on the frozen list, so it can never take
another test.

The defect these tests refuse: `actor` was declared on the event record and populated by
nothing, so 2,608 of this repository's own events name no writer and "every state change is
attributable" read as true because nothing reported otherwise.

`BR_AGENT_NAME` is injected on every test rather than read, and that is not tidiness —
`runner.br_attribution_env` sets it in every dispatched agent's environment, `conftest.py`
scrubs `GIT_*` and the colour variables but not `BR_*`, and this suite is itself run inside
a dispatch. A test that read the ambient value would pass here for the wrong reason and
answer differently on a laptop.
"""

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

# What `redact.redact_machine_identity` substitutes for this machine's username. Derived from
# the redactor rather than written out, so a rule rename cannot leave this asserting a string
# nothing produces any more.
IDENTITY_PLACEHOLDER = f"<redacted:{redact.IDENTITY_RULE}>"


def owned_repo(tmp_path: Path) -> Path:
    """A checkout with the tracker kit installed and the owned mode declared.

    `test_owned_write.owned_repo`'s shape. Duplicated rather than imported: no test module in
    this tree imports another, and a cross-import would make the split that created this file
    load the module it was split out of.
    """
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
    """Open *record* in the ledger with no actor, the way every inherited event was written."""
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})],
    )


def events_of(repo: Path, record: str) -> list[Any]:
    """Every ledger event naming *record*, in the order they were appended."""
    kit = owned_store.kit(repo)
    return [
        event for event in kit.read_ledger(owned_store.ledger_dir(repo)) if event.record == record
    ]


@pytest.fixture
def no_br(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spawn is a failure rather than a fallback, so no write can silently go elsewhere."""

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


# --- the resolver -------------------------------------------------------------


def test_a_dispatched_agent_is_recorded_instead_of_the_operating_system_user() -> None:
    """The overlay `runner.br_attribution_env` writes is the one thing that names the runner.

    The operator branch is asserted absent as well as the agent branch present: a resolver
    that appended both would satisfy a bare ``in`` and leak the identity it exists to mask.
    """
    resolved = owned_write.resolved_actor({owned_write.AGENT_ENV_VAR: "claude"})
    assert resolved == f"{owned_write.AGENT_ACTOR}claude"
    assert owned_write.OPERATOR_ACTOR not in resolved


def test_an_undispatched_write_records_the_masked_operator_and_never_the_username() -> None:
    """Only the redactor's placeholder reaches the store — R6 keeps the username out of it.

    Skipped rather than faked when this host has no username long enough for the rule to
    word-bound: the next test is that case, and asserting it here would test the same branch
    twice while claiming to test two.
    """
    name = redact.machine_identity()
    if not name:
        pytest.skip("this host has no username the identity rule can word-bound")
    resolved = owned_write.resolved_actor({})
    assert resolved == f"{owned_write.OPERATOR_ACTOR}{IDENTITY_PLACEHOLDER}"
    assert name not in resolved


def test_an_identity_the_redactor_cannot_mask_records_the_reason_not_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unresolvable case states why, because an empty field cannot.

    Injected at `redact.machine_identity`, which is the seam that already answers ``""`` for
    both the name too short to word-bound and the host with no username at all.
    """
    monkeypatch.setattr(redact, "machine_identity", lambda: "")
    resolved = owned_write.resolved_actor({})
    assert resolved == owned_write.UNRESOLVED_ACTOR
    assert resolved, "an unresolved actor is a reason, never an empty field"


def test_an_actor_taken_from_the_environment_is_redacted_and_capped() -> None:
    """The environment is a trust boundary: this is the one event field set from ambient input.

    The shape asserted is one `redact_committed` actually promises — an upper-case credential
    assignment, which is what an environment leaks. A lower-case unquoted ``token=value`` is
    **not** redacted by any rule and is deliberately not claimed here (probed 2026-08-21). The
    fake is assembled by concatenation so no line of this file is a full match for the scanner.
    """
    leaked = owned_write.resolved_actor({
        owned_write.AGENT_ENV_VAR: "API_TO" + "KEN=" + "abcdefghij"
    })
    assert "abcdefghij" not in leaked
    assert leaked.startswith(owned_write.AGENT_ACTOR)
    long_name = "a" * (owned_write.MAX_ACTOR_CHARS * 2)
    capped = owned_write.resolved_actor({owned_write.AGENT_ENV_VAR: long_name})
    assert len(capped) == len(owned_write.AGENT_ACTOR) + owned_write.MAX_ACTOR_CHARS


def test_a_newline_in_the_environment_cannot_reach_the_ledger_line() -> None:
    """One ledger line per event is the log's whole shape; ambient input may not fold it."""
    resolved = owned_write.resolved_actor({owned_write.AGENT_ENV_VAR: " claude\n  code\t"})
    assert resolved == f"{owned_write.AGENT_ACTOR}claude code"


# --- the seam -----------------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_a_write_through_the_seam_carries_the_agent_onto_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read back off the ledger, not off the return value: the field has to survive the JSON.

    The seeded ``status`` event is the control. It is appended through the kit with no actor,
    so it holds `events.UNATTRIBUTED_ACTOR` while the seam's own write holds the agent — which
    is what makes this discriminating: a fold that stamped every line alike would pass on the
    seam's line alone.
    """
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
    """`create` is the second append and mints its own id, so it cannot come through the first."""
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
    """The property, over every write path, with the resolver unable to answer.

    `machine_identity` is emptied *and* the overlay removed, which is the worst case both
    branches fail in — and the one that used to write ``""``.
    """
    monkeypatch.delenv(owned_write.AGENT_ENV_VAR, raising=False)
    monkeypatch.setattr(redact, "machine_identity", lambda: "")
    repo = owned_repo(tmp_path)
    record = owned_write.create(repo, ["create", "a new record", "-t", "task"])
    owned_write.append(repo, ["update", record, "--status", "in_progress"])
    owned_write.append(repo, ["comments", "add", record, "a note"])

    actors = [event.actor for event in events_of(repo, record)]
    assert actors, "no event was appended, so the assertion below would be vacuous"
    assert set(actors) == {owned_write.UNRESOLVED_ACTOR}
