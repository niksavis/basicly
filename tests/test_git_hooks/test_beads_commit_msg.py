from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_kit_events():
    """The tracker kit's ``events`` module, loaded from its source path.

    The hook may not reach into the kit, so it carries its own spelling of the
    log glob. This is what pins the two together.
    """
    source = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "kit" / "tracker" / "events.py"
    )
    name = "tracker_events_for_hook_test"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: a dataclass resolves its own annotations
    # through ``sys.modules[cls.__module__]``, so an unregistered module raises
    # on the first ``@dataclass`` in the file rather than on use.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_ledger(root: Path, *records: str) -> None:
    """Seed *root*'s owned ledger with one event per record id."""
    module = _load_beads_commit_msg_module()
    ledger = root / module.LEDGER_DIR
    ledger.mkdir(parents=True)
    lines = "".join(f'{{"record":"{record}","kind":"created"}}\n' for record in records)
    (ledger / module.LEDGER_GLOB.replace("*", "0001")).write_text(lines, encoding="utf-8")


def _load_beads_commit_msg_module():
    """Load the beads-commit-msg hook module from its script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "beads-commit-msg.py"
    )
    spec = importlib.util.spec_from_file_location("beads_commit_msg_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_accepts_known_issue_id() -> None:
    """A message referencing a known issue id should pass."""
    module = _load_beads_commit_msg_module()
    known_ids = {"basicly-idr"}
    is_valid, error = module.validate("feat(basicly): add hook (basicly-idr)", known_ids)
    assert is_valid
    assert error == ""


def test_validate_rejects_missing_issue_id() -> None:
    """A message with no issue-id-shaped token should fail."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook", {"basicly-idr"})
    assert not is_valid
    assert "does not reference a tracked issue id" in error


def test_validate_rejects_unknown_issue_id() -> None:
    """A message referencing an id absent from known ids should fail."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook (basicly-zzz)", {"basicly-idr"})
    assert not is_valid
    assert "unknown issue id" in error


def test_validate_ignores_hyphenated_words_when_a_valid_id_is_present() -> None:
    """Hyphenated description words are never mistaken for ids (basicly-jms0).

    Detection is prefix-anchored like br's own commit scanner, so a phrase such
    as "fork-drove-the-loop" is not a candidate and cannot shadow the real id.
    """
    module = _load_beads_commit_msg_module()
    message = "docs(spike): note the fork-drove-the-loop incident (basicly-idr)"
    is_valid, error = module.validate(message, {"basicly-idr"})
    assert is_valid
    assert error == ""


def test_validate_missing_id_error_does_not_name_hyphenated_words() -> None:
    """A commit with no id reports the accurate 'no id' error, not 'fork-drove' (basicly-jms0)."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("docs: the fork-drove-the-loop incident", {"basicly-idr"})
    assert not is_valid
    assert "does not reference a tracked issue id" in error
    assert "fork-drove" not in error


def test_validate_accepts_dotted_child_id() -> None:
    """A dotted child id is matched in full (basicly-jms0)."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("fix(x): child work (basicly-zrj.4.1)", {"basicly-zrj.4.1"})
    assert is_valid
    assert error == ""


def test_validate_accepts_id_in_a_footer() -> None:
    """The id is matched anywhere by word boundary, so a git-trailer footer works."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("fix(x): a thing\n\nRefs: basicly-idr", {"basicly-idr"})
    assert is_valid
    assert error == ""


def test_validate_skips_check_without_beads_workspace() -> None:
    """When known_ids is None (no .beads workspace), any candidate id passes."""
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook (basicly-idr)", None)
    assert is_valid
    assert error == ""


def test_validate_skips_plain_message_without_beads_workspace() -> None:
    """Without a workspace, a message with no issue id at all passes.

    Regression (basicly-zrj.13.1): the no-candidates rejection used to run
    before the workspace check, blocking every commit in beads-less consumers.
    """
    module = _load_beads_commit_msg_module()
    is_valid, error = module.validate("feat(basicly): add hook", None)
    assert is_valid
    assert error == ""


def test_validate_allows_merge_and_revert_subjects() -> None:
    """Merge and auto-generated revert subjects should be allowed."""
    module = _load_beads_commit_msg_module()
    assert module.validate("Merge branch 'main' into feature", {"basicly-idr"})[0]
    assert module.validate('Revert "bad commit"', {"basicly-idr"})[0]


def test_load_known_issue_ids_reads_jsonl(tmp_path: Path, monkeypatch) -> None:
    """Ids come from cwd, never from the script's own relocatable location."""
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "issues.jsonl").write_text(
        '{"id":"proj-abc","title":"x"}\n{"id":"proj-def","title":"y"}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert module._load_known_issue_ids() == {"proj-abc", "proj-def"}


def test_load_known_issue_ids_walks_up_from_a_subdirectory(tmp_path: Path, monkeypatch) -> None:
    """Direct invocation from a subdirectory still finds the repo root."""
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "issues.jsonl").write_text('{"id":"proj-abc"}\n', encoding="utf-8")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert module._load_known_issue_ids() == {"proj-abc"}


def test_load_known_issue_ids_returns_none_without_workspace(tmp_path: Path, monkeypatch) -> None:
    """_load_known_issue_ids should return None when no issues.jsonl exists."""
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert module._load_known_issue_ids() is None


def test_load_known_issue_ids_follows_beads_redirect(tmp_path: Path, monkeypatch) -> None:
    """A worktree's .beads/redirect resolves ids from the shared base tracker.

    A fresh id exists only in the redirected JSONL (the worktree copy is stale
    or absent), so the hook must read the same dir ``br`` does (basicly-c9e5).
    """
    module = _load_beads_commit_msg_module()
    base_beads = tmp_path / "base" / ".beads"
    base_beads.mkdir(parents=True)
    (base_beads / "issues.jsonl").write_text('{"id":"proj-fresh"}\n', encoding="utf-8")

    worktree = tmp_path / "wt"
    (worktree / ".git").mkdir(parents=True)
    (worktree / ".beads").mkdir()
    (worktree / ".beads" / "issues.jsonl").write_text('{"id":"proj-old"}\n', encoding="utf-8")
    (worktree / ".beads" / "redirect").write_text(f"{base_beads}\n", encoding="utf-8")
    monkeypatch.chdir(worktree)
    assert module._load_known_issue_ids() == {"proj-fresh"}


def test_load_known_issue_ids_ignores_dangling_redirect(tmp_path: Path, monkeypatch) -> None:
    """A redirect to a missing dir falls back to the local .beads."""
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"proj-local"}\n', encoding="utf-8")
    (beads / "redirect").write_text(str(tmp_path / "gone" / ".beads"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert module._load_known_issue_ids() == {"proj-local"}


def test_ledger_glob_matches_the_kit_contract() -> None:
    """The hook's own spelling of the log glob is the kit's (basicly-vkh0.42.1).

    A drift here does not fail loudly: the glob would match nothing, the ledger
    would look empty, and the gate would fall through to a store that is about
    to be deleted — or refuse every commit once it is gone.
    """
    module = _load_beads_commit_msg_module()
    assert module.LEDGER_GLOB == _load_kit_events().LOG_GLOB


def test_known_ids_come_from_the_ledger_with_no_external_store(tmp_path: Path, monkeypatch) -> None:
    """The shape after the deletion: a ledger, no export, and the gate still binds."""
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path, "proj-owned")
    monkeypatch.chdir(tmp_path)

    assert module._load_known_issue_ids() == {"proj-owned"}
    assert module.validate("feat(x): a thing (proj-owned)", *_found(module))[0]


def test_the_ledger_wins_when_both_stores_exist(tmp_path: Path, monkeypatch) -> None:
    """Under a dual write the owned ledger answers, and the refusal says so."""
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path, "proj-owned")
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"proj-external"}\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    known, source = module._known_ids_with_source()
    assert known == {"proj-owned"}
    assert source == str(module.LEDGER_DIR / module.LEDGER_GLOB)

    is_valid, error = module.validate("feat(x): a thing (proj-external)", known, source)
    assert not is_valid
    assert source in error


def test_an_empty_ledger_falls_back_rather_than_refusing(tmp_path: Path, monkeypatch) -> None:
    """An empty store is not an empty id set (basicly-vkh0.42.1).

    Reading it as authoritative would report every id as unknown and refuse
    every commit, which is the one failure mode a commit gate must not have.
    """
    module = _load_beads_commit_msg_module()
    (tmp_path / ".git").mkdir()
    _write_ledger(tmp_path)
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"proj-external"}\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    known, source = module._known_ids_with_source()
    assert known == {"proj-external"}
    assert source.endswith("issues.jsonl")


def test_the_ledger_follows_the_redirect_out_of_a_worktree(tmp_path: Path, monkeypatch) -> None:
    """One store per repository, never one per worktree — the ledger too."""
    module = _load_beads_commit_msg_module()
    base = tmp_path / "base"
    base_beads = base / ".beads"
    base_beads.mkdir(parents=True)
    _write_ledger(base, "proj-fresh")

    worktree = tmp_path / "wt"
    (worktree / ".git").mkdir(parents=True)
    (worktree / ".beads").mkdir()
    (worktree / ".beads" / "redirect").write_text(f"{base_beads}\n", encoding="utf-8")
    _write_ledger(worktree, "proj-stale")
    monkeypatch.chdir(worktree)

    assert module._load_known_issue_ids() == {"proj-fresh"}


def _found(module) -> tuple[set[str], str]:
    """The hook's own id lookup, unpacked for a caller that passes both on."""
    found = module._known_ids_with_source()
    assert found is not None
    return found
