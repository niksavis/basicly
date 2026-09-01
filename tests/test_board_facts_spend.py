"""The spend a clone of this repository can read (basicly-7hebuh).

`.basicly/usage/run-records.json` is self-ignored, so the machine that ran a dispatch is the
only one that holds its run record - and every spend reader used to read that file alone.
The same dispatch also writes a `[harness-run]` marker into the ledger, which is committed,
so what is asserted here is that the reported figure comes from both stores and that each
store's own figure survives beside it.

Split from `test_board_facts.py` at the 4000-token cap: those tests assert one rule about
absences and these assert one reading, so the seam between them is the responsibility.

Driven against a `tmp_path` repository for that file's reason - a gate asserted on the live
tracker becomes a report on whatever the tracker holds today.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from basicly import board_facts, policy, run_record, tracker
from basicly.config import PolicyConfig

REPO_ROOT = Path(__file__).parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _owned_repo(root: Path, *records: str) -> Path:
    """A checkout with the kit installed and *records* open in its own ledger.

    Seeded through the kit for the reason `test_tracker_seam._owned_repo` gives.
    """
    (root / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, root / tracker.KIT_TRACKER_DIR / source.name)
    (root / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (root / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return root


def _granted(root: Path, record: str, budget: int) -> policy.Grant:
    """*record* open in a fresh ledger, carrying an L3 grant of *budget*."""
    _owned_repo(root, record)
    policy.issue_grant_guarded(
        root,
        record,
        "L3",
        budget,
        PolicyConfig(required_gates=(), max_rework=2, autonomy="L3"),
        interactive=True,
    )
    grant = policy.active_grant(root, record)
    assert grant is not None
    return grant


def _dispatch(tokens: int, at: str) -> run_record.RunRecord:
    """One measured dispatch, stamped so two of them are two samples."""
    return run_record.RunRecord(
        agent="claude",
        outcome=run_record.EXECUTED,
        returncode=0,
        duration_s=1.0,
        command=("claude",),
        timestamp=at,
        tokens=tokens,
        estimated=False,
        prompt_sha256=at.replace("-", "").replace(":", "")[:16] * 4,
        phase="build",
    )


def test_spend_is_read_from_the_ledger_where_the_checkout_has_no_run_records(
    tmp_path: Path,
) -> None:
    """The clone's case, and the whole point: `.basicly/usage/` never leaves its machine.

    The marker is written by the engine's own recorder rather than spelled here, because
    what has to travel is the thing a dispatch actually writes — a hand-authored comment
    would agree with this test and with nothing else.
    """
    grant = _granted(tmp_path, "bd-1", 8_000_000)
    run_record.record_marker(tmp_path, "bd-1", _dispatch(1234, "2026-08-07T00:00:00+00:00"))

    assert run_record.load_run_records(tmp_path) is None
    assert board_facts.grant_spend(tmp_path, "bd-1", grant) == 1234


def test_a_root_the_checkout_holds_no_dispatch_for_reports_no_spend(tmp_path: Path) -> None:
    """Absence, never a zero: a zero reads as a grant that has been used and cost nothing.

    The pair is the assertion — the same repository answers a figure for a root it holds a
    dispatch for and None for one it does not, so the guard is counting dispatches rather
    than falling back to whether any store could be read at all.
    """
    grant = _granted(tmp_path, "bd-1", 8_000_000)

    assert board_facts.grant_spend(tmp_path, "bd-1", grant) is None
    run_record.record_marker(tmp_path, "bd-1", _dispatch(50, "2026-08-07T00:00:00+00:00"))
    assert board_facts.grant_spend(tmp_path, "bd-1", grant) == 50


def test_a_local_record_the_ledger_lacks_is_counted_once_and_both_figures_are_kept(
    tmp_path: Path,
) -> None:
    """Two stores, one sample set — and the disagreement survives being resolved.

    A checkout mid-session holds run records it has not committed, and the ledger holds
    dispatches other machines ran. Adding the two would double-count every dispatch that is
    in both, and choosing one would hide the other, so the union is reported and each
    store's own figure is kept beside it for the display to name.
    """
    grant = _granted(tmp_path, "bd-1", 8_000_000)
    committed = _dispatch(1000, "2026-08-07T00:00:00+00:00")
    run_record.record_marker(tmp_path, "bd-1", committed)
    run_record.record(tmp_path, "bd-1", committed)
    run_record.record(tmp_path, "bd-1", _dispatch(7, "2026-08-07T01:00:00+00:00"))

    split = board_facts.grant_split(tmp_path, "bd-1", grant)

    assert split is not None
    assert (split.tokens, split.local, split.ledger) == (1007, 1007, 1000)
    assert split.local != split.ledger, "the uncommitted dispatch is what they differ by"
    assert split.dispatches_seen == 2


def test_two_stores_holding_the_same_dispatch_do_not_disagree(tmp_path: Path) -> None:
    """The dedup is per dispatch, so a committed record is not a second sample of itself."""
    grant = _granted(tmp_path, "bd-1", 8_000_000)
    both = _dispatch(1000, "2026-08-07T00:00:00+00:00")
    run_record.record_marker(tmp_path, "bd-1", both)
    run_record.record(tmp_path, "bd-1", both)

    split = board_facts.grant_split(tmp_path, "bd-1", grant)

    assert split is not None
    assert (split.tokens, split.local, split.ledger) == (1000, 1000, 1000)


def test_the_reported_spend_is_not_what_the_d3_ceiling_meters(tmp_path: Path) -> None:
    """The one divergence this lane leaves standing, asserted so it cannot drift silently.

    `spend_status` meters the machine-local file, so a checkout with none reads zero spent
    and halts nothing; the display reads the committed markers and reports what the session
    really cost. Moving the ceiling onto the travelling figure halts every grant already
    over it — 6 of this repository's 18 [measured 2026-09-01] — which is a decision to take
    knowingly rather than as a side effect of a display.
    """
    grant = _granted(tmp_path, "bd-1", 500)
    run_record.record_marker(tmp_path, "bd-1", _dispatch(1000, "2026-08-07T00:00:00+00:00"))

    assert board_facts.grant_spend(tmp_path, "bd-1", grant) == 1000
    assert policy.spend_status(tmp_path, "bd-1", grant=grant).halted is False
