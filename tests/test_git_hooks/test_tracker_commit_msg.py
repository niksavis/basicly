from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from basicly import merge


def _load_kit_events():

    source = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "kit" / "tracker" / "events.py"
    )
    name = "tracker_events_for_hook_test"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_ledger(root: Path, *records: str) -> None:
    module = _load_tracker_commit_msg_module()
    ledger = root / module.LEDGER_DIR
    ledger.mkdir(parents=True)
    lines = "".join(f'{{"record":"{record}","kind":"created"}}\n' for record in records)
    (ledger / module.LEDGER_GLOB.replace("*", "0001")).write_text(lines, encoding="utf-8")


def _load_tracker_commit_msg_module():
    hooks = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks"
    script_path = hooks / "tracker-commit-msg.py"
    spec = importlib.util.spec_from_file_location("tracker_commit_msg_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_accepts_known_issue_id() -> None:
    module = _load_tracker_commit_msg_module()
    known_ids = {"basicly-idr"}
    is_valid, error = module.validate("feat(basicly): add hook (basicly-idr)", known_ids)
    assert is_valid
    assert error == ""


def test_validate_rejects_missing_issue_id() -> None:
    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook", {"basicly-idr"})
    assert not is_valid
    assert "does not reference a tracked issue id" in error


def test_validate_rejects_unknown_issue_id() -> None:
    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook (basicly-zzz)", {"basicly-idr"})
    assert not is_valid
    assert "unknown issue id" in error


def test_validate_ignores_hyphenated_words_when_a_valid_id_is_present() -> None:

    module = _load_tracker_commit_msg_module()
    message = "docs(spike): note the fork-drove-the-loop incident (basicly-idr)"
    is_valid, error = module.validate(message, {"basicly-idr"})
    assert is_valid
    assert error == ""


def test_validate_missing_id_error_does_not_name_hyphenated_words() -> None:
    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("docs: the fork-drove-the-loop incident", {"basicly-idr"})
    assert not is_valid
    assert "does not reference a tracked issue id" in error
    assert "fork-drove" not in error


def test_validate_accepts_dotted_child_id() -> None:
    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("fix(x): child work (basicly-zrj.4.1)", {"basicly-zrj.4.1"})
    assert is_valid
    assert error == ""


def test_validate_accepts_id_in_a_footer() -> None:
    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("fix(x): a thing\n\nRefs: basicly-idr", {"basicly-idr"})
    assert is_valid
    assert error == ""


def test_validate_skips_check_without_beads_workspace() -> None:
    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook (basicly-idr)", None)
    assert is_valid
    assert error == ""


def test_validate_skips_plain_message_without_beads_workspace() -> None:

    module = _load_tracker_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook", None)
    assert is_valid
    assert error == ""


def test_validate_allows_merge_and_revert_subjects() -> None:
    module = _load_tracker_commit_msg_module()
    assert module.validate("Merge branch 'main' into feature", {"basicly-idr"})[0]
    assert module.validate('Revert "bad commit"', {"basicly-idr"})[0]


def test_load_known_issue_ids_reads_the_event_log(tmp_path: Path, monkeypatch) -> None:
    module = _load_tracker_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path, "proj-abc", "proj-def")
    monkeypatch.chdir(tmp_path)
    assert module._load_known_issue_ids() == {"proj-abc", "proj-def"}


def test_load_known_issue_ids_walks_up_from_a_subdirectory(tmp_path: Path, monkeypatch) -> None:
    module = _load_tracker_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path, "proj-abc")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert module._load_known_issue_ids() == {"proj-abc"}


def test_load_known_issue_ids_returns_none_without_a_tracker(tmp_path: Path, monkeypatch) -> None:
    module = _load_tracker_commit_msg_module()
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert module._load_known_issue_ids() is None


def test_ledger_glob_matches_the_kit_contract() -> None:

    module = _load_tracker_commit_msg_module()
    assert module.LEDGER_GLOB == _load_kit_events().LOG_GLOB


def test_the_gate_binds_over_a_ledger_and_names_the_store_it_checked(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_tracker_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path, "proj-owned")
    monkeypatch.chdir(tmp_path)

    known, source = _found(module)
    assert known == {"proj-owned"}
    assert source == str(module.LEDGER_DIR / module.LEDGER_GLOB)
    assert module.validate("feat(x): a thing (proj-owned)", known, source)[0]

    is_valid, error = module.validate("feat(x): a thing (proj-nope)", known, source)
    assert not is_valid
    assert source in error


def test_an_empty_ledger_reads_as_no_tracker_rather_than_as_no_ids(
    tmp_path: Path, monkeypatch
) -> None:

    module = _load_tracker_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert module._known_ids_with_source() is None
    assert module.validate("feat(x): a thing (proj-anything)", None)[0]


def test_the_ledger_follows_the_redirect_out_of_a_worktree(tmp_path: Path, monkeypatch) -> None:

    module = _load_tracker_commit_msg_module()
    base = tmp_path / "base"
    base.mkdir()
    _write_ledger(base, "proj-fresh")

    worktree = tmp_path / "wt"
    (worktree / ".git").mkdir(parents=True)
    _write_ledger(worktree, "proj-stale")
    (worktree / module.LEDGER_DIR / module.REDIRECT_NAME).write_text(f"{base}\n", encoding="utf-8")
    monkeypatch.chdir(worktree)

    assert module._load_known_issue_ids() == {"proj-fresh"}


def test_a_dangling_redirect_falls_back_to_the_checkouts_own_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_tracker_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path, "proj-local")
    (tmp_path / module.LEDGER_DIR / module.REDIRECT_NAME).write_text(
        str(tmp_path / "gone"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert module._load_known_issue_ids() == {"proj-local"}


def test_the_hook_and_the_engine_resolve_the_redirect_alike(tmp_path: Path, monkeypatch) -> None:

    module = _load_tracker_commit_msg_module()
    base = tmp_path / "base"
    base.mkdir()
    _write_ledger(base, "proj-fresh")
    worktree = tmp_path / "wt"
    (worktree / ".git").mkdir(parents=True)
    _write_ledger(worktree, "proj-stale")
    (worktree / module.LEDGER_DIR / module.REDIRECT_NAME).write_text(f"{base}\n", encoding="utf-8")
    monkeypatch.chdir(worktree)

    assert module._load_known_issue_ids() == merge.known_bead_ids(worktree)


def _found(module) -> tuple[set[str], str]:
    found = module._known_ids_with_source()
    assert found is not None
    return found
