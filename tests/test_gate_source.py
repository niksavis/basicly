"""The gate query's store (basicly-vkh0.27).

The claim: the fold is the only answer, and it is asserted with a spawn wired to fail the
test rather than merely with an absent binary — "the store could not answer and the read
silently degraded to no rows" would satisfy a weaker assertion and is exactly the failure
mode a flipped read can have.

The dual rung's own tests left with the second store (basicly-vkh0.42.7): a comparison
needs two things to compare. The gate rows are written through the real write seam, not
by appending events by hand, because a gate event this seam cannot read back is exactly
what a hand-built fixture would hide.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import gate_source, policy, tracker
from basicly.config import PolicyConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
ISSUE = "gate-0001"

# The engine's own provider: a foreign one on a required gate is disregarded, so a test
# using one would derive `missing` on a record it had just recorded a pass for.
ENGINE_PROVIDER = "basicly-verify"


def _repo(tmp_path: Path) -> Path:
    """A checkout with the kit installed, its ledger declared, and :data:`ISSUE` open.

    The record is opened rather than left implicit, and by hand rather than through the
    seam: the write seam refuses a write naming a record the ledger does not hold
    (`owned_write._refuse_a_write_to_an_absent_record`), so a gate report against an id
    nothing ever created is a refusal now instead of the fixture shortcut it was. Only
    the *record* is hand-built — the gate rows still go through the seam, for the reason
    the module docstring gives.
    """
    (tmp_path / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / tracker.KIT_TRACKER_DIR / source.name)
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(tmp_path)
    kit.events.append(
        tracker.ledger_dir(tmp_path),
        [kit.events.Draft(ISSUE, kit.events.KIND_STATUS, {"status": "open"})],
    )
    return tmp_path


def _report(repo: Path, *, gate: str = "verify", status: str = "pass") -> None:
    """One gate report, through the write seam, so the ledger records it as the engine would."""
    flags = ["--gate", gate, "--provider", ENGINE_PROVIDER, "--status", status]
    tracker.write(repo, ["gate", "report", *flags, ISSUE])


@pytest.fixture(autouse=True)
def refuse_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any process start fail the test rather than fall back to a second store."""

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


# --- the fold is the answer ----------------------------------------------------


def test_the_engine_reads_a_green_gate_off_the_fold(tmp_path: Path) -> None:
    """The criterion at the surface that asks it: `policy.gate_status`, not the seam.

    The engine's question is *may this advance*, and the classification behind it — whose
    provider counts, which gate is required — has to survive unchanged, which a test of
    the seam's row list alone would not show.
    """
    repo = _repo(tmp_path)
    _report(repo)

    assert gate_source.read_gates(repo, ISSUE) == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": True}
    ]

    status = policy.gate_status(repo, ISSUE, PolicyConfig(required_gates=("verify",), max_rework=2))

    assert status.can_advance
    assert status.required_passed == ("verify",)


def test_a_gate_no_store_recorded_is_not_green(tmp_path: Path) -> None:
    """Fail closed: an empty fold answers *missing*, never an advance."""
    repo = _repo(tmp_path)

    status = policy.gate_status(repo, ISSUE, PolicyConfig(required_gates=("verify",), max_rework=2))

    assert not status.can_advance
    assert status.required_missing == ("verify",)


def test_a_failing_gate_reads_back_as_failing(tmp_path: Path) -> None:
    """The control on the green case: the verdict is carried, not assumed."""
    repo = _repo(tmp_path)
    _report(repo, status="fail")

    assert gate_source.read_gates(repo, ISSUE) == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": False}
    ]


def test_a_tombstoned_record_answers_no_gates(tmp_path: Path) -> None:
    """A deletion is an event here and an absence to every reader.

    Serving a deleted record's verdicts would advance work somebody removed.
    """
    repo = _repo(tmp_path)
    _report(repo)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [kit.events.Draft(ISSUE, kit.events.KIND_TOMBSTONE, {})],
    )

    assert gate_source.read_gates(repo, ISSUE) == []


# --- the confinement ----------------------------------------------------------


def test_the_store_is_reached_only_from_its_own_seam_modules() -> None:
    """Which modules may reach the store, in either spelling of its names.

    `test_br_seam.test_no_module_outside_the_seam_reads_the_owned_store` guards the
    ``br.``-prefixed spelling against every module but `br` itself. The seam modules below
    reach the same functions through :mod:`basicly.owned_store` directly, so they need the
    second spelling probed and themselves named — an exemption a reviewer sees, rather
    than one the first guard's literal strings happen not to catch.

    The expected list is the seam modules themselves rather than empty, which is what
    makes the zero on every other module evidence: a probe matching nothing at all would
    report the same empty list. `br` is left out because it *is* the seam and reaches
    these names unqualified anyway.
    """
    root = REPO_ROOT / "src" / "basicly"
    reaching = [
        f"{prefix}{name}("
        for prefix in ("br.", "owned_store.")
        for name in ("owned_record", "tracker_mode", "ledger_dir", "kit")
    ]
    branching = sorted(
        path.name
        for path in sorted(root.glob("*.py"))
        if path.name != "tracker.py"
        if any(name in path.read_text(encoding="utf-8") for name in reaching)
    )

    assert branching == [
        # The board's reference producer, and C12 of `docs/requirements/harness-board.md`
        # names it the one permitted exception: every *consumer* reads a snapshot document
        # and nothing else, while the producer reads the store because that is its whole job
        # (basicly-rn0o.2).
        "board_snapshot.py",
        "dependency_graph.py",
        "gate_source.py",
        "label_source.py",
        "owned_write.py",
        # The read verbs `basicly tracker` prints, which resolve the ledger's location
        # from the engine so a consumer never retypes it (basicly-vkh0.42.7).
        "tracker_query.py",
    ]
