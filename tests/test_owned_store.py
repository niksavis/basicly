from __future__ import annotations

from pathlib import Path

import pytest

from basicly import config, owned_store, tracker, tracker_paths
from basicly.owned_store import TrackerDivergenceError, _mode_reader

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_importing_config_installs_the_mode_reader() -> None:

    assert _mode_reader == [config.load_tracker_mode]


def test_a_repo_that_declares_nothing_gets_the_owned_ledger(tmp_path: Path) -> None:

    assert config.load_tracker_mode(tmp_path) == owned_store.DEFAULT_TRACKER_MODE
    assert owned_store.tracker_mode(tmp_path) == owned_store.MODE_OWNED


@pytest.mark.parametrize("mode", owned_store.TRACKER_MODES)
def test_each_declared_rung_reaches_the_seam(tmp_path: Path, mode: str) -> None:
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    assert owned_store.tracker_mode(tmp_path) == mode
    assert tracker.tracker_mode(tmp_path) == mode


def test_a_mode_outside_the_ladder_is_refused(tmp_path: Path) -> None:

    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "flipped"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not one of owned"):
        config.load_tracker_mode(tmp_path)


def test_the_ladder_has_collapsed_to_its_last_rung() -> None:

    assert owned_store.TRACKER_MODES == (owned_store.MODE_OWNED,)
    assert owned_store.DEFAULT_TRACKER_MODE == owned_store.MODE_OWNED


def test_with_no_reader_installed_the_mode_is_refused_not_defaulted(tmp_path: Path) -> None:

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


def test_the_ledger_is_one_per_repo_not_one_per_worktree(tmp_path: Path) -> None:

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

    ledger = owned_store.LEDGER_DIR
    assert ledger == tracker_paths.LEDGER_DIR_NAME
    assert ledger == Path(".basicly") / "ledger"
    assert tracker.LEDGER_DIR is owned_store.LEDGER_DIR


def test_a_repo_with_no_kit_installed_is_refused_rather_than_degraded(tmp_path: Path) -> None:

    with pytest.raises(TrackerDivergenceError, match="tracker kit is not installed"):
        owned_store.kit(tmp_path)


def test_the_filesystem_is_asked_before_the_cache(tmp_path: Path) -> None:

    assert owned_store.kit(REPO_ROOT).events is not None

    with pytest.raises(TrackerDivergenceError):
        owned_store.kit(tmp_path)


def test_one_kit_module_object_per_repo_however_it_is_reached() -> None:

    assert owned_store.kit(REPO_ROOT) is owned_store.kit(REPO_ROOT)
    assert owned_store.kit(REPO_ROOT, owned_store.DEFAULT_KIT_MODULE) is owned_store.kit(REPO_ROOT)


def test_a_kit_module_beside_the_differential_is_reached_by_name() -> None:
    scheduler = owned_store.kit(REPO_ROOT, owned_store.SCHEDULER_KIT_MODULE)

    assert scheduler is not owned_store.kit(REPO_ROOT)
    assert scheduler is owned_store.kit(REPO_ROOT, owned_store.SCHEDULER_KIT_MODULE)


def test_a_divergence_is_a_runtime_error_a_br_caller_already_handles() -> None:
    assert issubclass(TrackerDivergenceError, RuntimeError)
