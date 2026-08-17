"""Where the owned tracker store is: which rung, which directory, which kit module.

Moved out of `test_br_seam.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14), along the boundary the module itself draws — *resolution* against
*the seam*. Nothing here spawns tracker, mirrors a write or reads an event: what the dual
write and the flip then *do* with these answers stays with `tracker.run_br`, which is where
a stand-in br and a real ledger are needed to say anything.

Asserted against :mod:`basicly.owned_store` and, where a caller spells it that way,
against the `br` re-export as well — `tracker.tracker_mode` and `tracker.LEDGER_DIR` are how the
engine reaches these, so a split that left a re-export behind would pass one and fail
the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import config, owned_store, tracker, tracker_paths
from basicly.owned_store import TrackerDivergenceError, _mode_reader

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- the mode declaration -----------------------------------------------------


def test_importing_config_installs_the_mode_reader() -> None:
    """The seam is put in the repo's declared mode by an import, not by a caller.

    `basicly.tracker` cannot import `basicly.config` — `config -> runner -> run_record -> br`
    already runs the other way — so the reader is installed from above. That inversion
    is invisible at both ends, which is exactly why it is asserted here: without it the
    seam refuses every read and write, and the refusal reads nothing like a missing
    import.
    """
    assert _mode_reader == [config.load_tracker_mode]


def test_a_repo_that_declares_nothing_gets_the_owned_ledger(tmp_path: Path) -> None:
    """A consumer who never heard of this gets the only store there is.

    The default was ``external`` while a second store existed; with that deleted it has
    to be the one the engine can reach (basicly-vkh0.42.7).
    """
    assert config.load_tracker_mode(tmp_path) == owned_store.DEFAULT_TRACKER_MODE
    assert owned_store.tracker_mode(tmp_path) == owned_store.MODE_OWNED


@pytest.mark.parametrize("mode", owned_store.TRACKER_MODES)
def test_each_declared_rung_reaches_the_seam(tmp_path: Path, mode: str) -> None:
    """Every value the ladder has is readable end to end, not just the default."""
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    assert owned_store.tracker_mode(tmp_path) == mode
    assert tracker.tracker_mode(tmp_path) == mode


def test_a_mode_outside_the_ladder_is_refused(tmp_path: Path) -> None:
    """A value the engine cannot honour is an error, never a silent default.

    Defaulting a misspelled mode back to the only real one would leave the file stating
    a behaviour nothing performs, with no diff to review.
    """
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "flipped"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not one of owned"):
        config.load_tracker_mode(tmp_path)


def test_the_ladder_has_collapsed_to_its_last_rung() -> None:
    """One store, so one mode — and a value outside it states a behaviour nothing performs.

    The key survives its ladder on purpose: a consumer's committed ``mode = "owned"``
    must not be refused as an unknown name (basicly-vkh0.42.7).
    """
    assert owned_store.TRACKER_MODES == (owned_store.MODE_OWNED,)
    assert owned_store.DEFAULT_TRACKER_MODE == owned_store.MODE_OWNED


def test_with_no_reader_installed_the_mode_is_refused_not_defaulted(tmp_path: Path) -> None:
    """Unknown is refused, because answering `external` to it is a guard failing open.

    This asserted the opposite until `basicly-e2mz.23`, and the assertion was the
    defect. The fixture declares `owned` so a default matching the declaration cannot
    pass. Restored in a `finally`: the holder is process-global.
    """
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    installed = list(_mode_reader)
    try:
        owned_store.set_mode_reader(None)
        with pytest.raises(owned_store.TrackerModeUnknownError, match="not installed"):
            owned_store.tracker_mode(tmp_path)
    finally:
        owned_store.set_mode_reader(installed[0] if installed else None)
    assert _mode_reader == installed
    assert owned_store.tracker_mode(tmp_path) == owned_store.MODE_OWNED


# --- where the ledger lives ---------------------------------------------------


def test_the_ledger_is_one_per_repo_not_one_per_worktree(tmp_path: Path) -> None:
    """A lane's writes belong to the base checkout, or teardown deletes them.

    The same rule `tracker_usage.ledger_root` was given after the usage spool was
    written into worktrees and discarded at teardown (basicly-vkh0.8) — a ledger that
    did not follow the redirect would lose every write a lane made.
    """
    base = tmp_path / "base"
    (base / owned_store.LEDGER_DIR).mkdir(parents=True)
    worktree = tmp_path / "wt"
    (worktree / owned_store.LEDGER_DIR).mkdir(parents=True)
    (worktree / owned_store.LEDGER_DIR / tracker_paths.REDIRECT_NAME).write_text(
        str(base), encoding="utf-8"
    )

    assert owned_store.ledger_dir(worktree) == base / owned_store.LEDGER_DIR
    assert owned_store.ledger_dir(base) == base / owned_store.LEDGER_DIR


def test_the_ledger_sits_beside_the_other_committed_ledger_artifacts() -> None:
    """One directory, taken off one constant, so a gate cannot be pointed elsewhere.

    `.scripts/kit_deployment.py` gates this directory's ignore rules and
    `.gitattributes` pins the log's bytes there; a second literal in this module could
    drift from either without anything noticing.
    """
    ledger = owned_store.LEDGER_DIR
    assert ledger == tracker_paths.LEDGER_DIR_NAME
    assert ledger == Path(".basicly") / "ledger"
    assert tracker.LEDGER_DIR is owned_store.LEDGER_DIR


# --- reaching the installed kit -----------------------------------------------


def test_a_repo_with_no_kit_installed_is_refused_rather_than_degraded(tmp_path: Path) -> None:
    """A mode above `external` has already promised both stores hold the same facts.

    The directory is named, because the repair is installing it there — a bare "not
    found" would leave a reader guessing which of the two checkouts was asked.
    """
    with pytest.raises(TrackerDivergenceError, match="tracker kit is not installed"):
        owned_store.kit(tmp_path)


def test_the_filesystem_is_asked_before_the_cache(tmp_path: Path) -> None:
    """Otherwise a repo with no kit is answered out of some other repo's.

    This process has already loaded this repo's kit by the time the suite gets here, so
    a cache consulted first would hand that module back and the mode would look enabled
    while writing nowhere. Loading the real kit first is what makes the refusal below
    evidence rather than a coincidence.
    """
    assert owned_store.kit(REPO_ROOT).events is not None

    with pytest.raises(TrackerDivergenceError):
        owned_store.kit(tmp_path)


def test_one_kit_module_object_per_repo_however_it_is_reached() -> None:
    """Two loads of one file give two `Event` classes, and `isinstance` then lies.

    Identity, because equality would hold for two separately-loaded copies of the same
    source — which is the failure this fixed prefix exists to prevent.
    """
    assert owned_store.kit(REPO_ROOT) is owned_store.kit(REPO_ROOT)
    assert owned_store.kit(REPO_ROOT, owned_store.DEFAULT_KIT_MODULE) is owned_store.kit(REPO_ROOT)


def test_a_kit_module_beside_the_differential_is_reached_by_name() -> None:
    """The scheduler sits beside the differential rather than under it (basicly-vkh0.20)."""
    scheduler = owned_store.kit(REPO_ROOT, owned_store.SCHEDULER_KIT_MODULE)

    assert scheduler is not owned_store.kit(REPO_ROOT)
    assert scheduler is owned_store.kit(REPO_ROOT, owned_store.SCHEDULER_KIT_MODULE)


def test_a_divergence_is_a_runtime_error_a_br_caller_already_handles() -> None:
    """So the message is what `tracker.run_br` callers already print, not a new failure mode."""
    assert issubclass(TrackerDivergenceError, RuntimeError)
