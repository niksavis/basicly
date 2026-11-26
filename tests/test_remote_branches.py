from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import ci_landing, cli, merge, remote_branches, worktree
from tests.test_worktree import _init_repo

_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(where), *_ID, *args],
        capture_output=True,
        text=True,
        check=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return done.stdout.strip()


@pytest.fixture
def main(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    checkout = tmp_path / "main"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    (checkout / "a.txt").write_text("a\n", encoding="utf-8")
    _git(checkout, "add", "a.txt")
    _git(checkout, "commit", "-q", "-m", "a")
    _git(checkout, "push", "-q", "origin", "main")
    return checkout


def _lane(main: Path, name: str, path: str) -> str:
    _git(main, "switch", "-q", "-c", name, "origin/main")
    (main / path).write_text(f"{name}\n", encoding="utf-8")
    _git(main, "add", path)
    _git(main, "commit", "-q", "-m", name)
    _git(main, "push", "-q", "origin", name)
    head = _git(main, "rev-parse", "HEAD")
    _git(main, "switch", "-q", "main")
    return head


def _on_origin(main: Path, ref: str) -> bool:
    return bool(_git(main, "ls-remote", "--heads", "origin", ref))


def test_a_landed_lane_has_its_remote_branch_deleted(main: Path) -> None:
    _lane(main, "harness/done", "b.txt")
    _git(main, "merge", "-q", "--no-ff", "-m", "land", "harness/done")
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "harness/done", "main", worktree.unlanded_paths)

    assert note.startswith("deleted origin/harness/done")
    assert not _on_origin(main, "harness/done")


def test_an_unlanded_lane_keeps_its_remote_branch_and_names_the_path(main: Path) -> None:
    _lane(main, "harness/open", "c.txt")

    note = remote_branches.delete_landed(main, "harness/open", "main", worktree.unlanded_paths)

    assert note.startswith("kept origin/harness/open")
    assert "c.txt" in note
    assert _on_origin(main, "harness/open")


def test_a_replaced_lane_whose_work_landed_by_copy_has_its_remote_branch_deleted(
    main: Path,
) -> None:
    old = _lane(main, "harness/old", "d.txt")
    (main / "later.txt").write_text("later\n", encoding="utf-8")
    _git(main, "add", "later.txt")
    _git(main, "commit", "-q", "-m", "later work on main")
    _git(main, "cherry-pick", old)
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "harness/old", "main", worktree.unlanded_paths)

    assert note.startswith("deleted origin/harness/old")
    assert not _on_origin(main, "harness/old")


def test_a_branch_whose_head_is_on_the_base_is_deleted_after_the_base_changed_its_path(
    main: Path,
) -> None:
    _lane(main, "pr/lane/old", "e.txt")
    _git(main, "merge", "-q", "--no-ff", "-m", "land", "pr/lane/old")
    (main / "e.txt").write_text("changed on main\n", encoding="utf-8")
    _git(main, "commit", "-q", "-am", "main changes the path again")
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "pr/lane/old", "main", worktree.unlanded_paths)

    assert note.startswith("deleted origin/pr/lane/old")
    assert not _on_origin(main, "pr/lane/old")


def test_a_branch_with_no_remote_copy_is_left_alone(main: Path) -> None:
    assert (
        remote_branches.delete_landed(main, "harness/none", "main", worktree.unlanded_paths) == ""
    )


def test_every_land_ref_of_a_failed_ci_landing_is_dropped(main: Path) -> None:
    head = _git(main, "rev-parse", "HEAD")
    for attempt in (1, 3):
        _git(main, "push", "-q", "origin", f"{head}:refs/heads/land/feat/{attempt}")
    proofs = main / ci_landing.PROOFS
    proofs.parent.mkdir(parents=True, exist_ok=True)
    proofs.write_text(json.dumps({"feat": {"attempt": "3"}}), encoding="utf-8")

    assert ci_landing.drop_refs(main, "feat") == []

    assert [_on_origin(main, f"land/feat/{n}") for n in (1, 2, 3)] == [False, False, False]


@pytest.mark.parametrize(("mode", "dropped"), [(ci_landing.MODE, ["feat"]), ("full", [])])
def test_a_landing_that_does_not_merge_drops_its_land_refs_only_in_ci_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, dropped: list[str]
) -> None:
    calls: list[str] = []
    refused = merge.MergeResult("feat", "verify-failed", "shard 9 failed")
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.landing_lock, "hold", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(cli.merge, "merge_worktree", lambda *_a, **_k: refused)
    monkeypatch.setattr(ci_landing, "drop_refs", lambda _root, lane: calls.append(lane) or [])
    args = argparse.Namespace(name="feat", bead="basicly-x1", mode=mode, wait=0)

    assert cli._cmd_worktree_merge(args) == 1
    assert calls == dropped


def test_cleanup_of_a_landed_lane_deletes_its_remote_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setattr(worktree, "provision_deps", lambda *_a, **_kw: [])
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    session = worktree.create("done", repo_root=repo)
    (session.path / "lane.txt").write_text("lane\n", encoding="utf-8")
    _git(session.path, "add", "lane.txt")
    _git(session.path, "commit", "-q", "-m", "lane")
    _git(session.path, "push", "-q", "origin", session.branch)
    _git(repo, "merge", "-q", "--no-ff", "-m", "land", session.branch)
    _git(repo, "push", "-q", "origin", "main")

    worktree.cleanup("done", repo_root=repo)

    assert f"deleted origin/{session.branch}" in capsys.readouterr().out
    assert not _on_origin(repo, session.branch)


def test_a_lane_that_only_deletes_a_file_still_on_base_keeps_its_remote_branch(main: Path) -> None:
    _git(main, "switch", "-q", "-c", "harness/drop", "origin/main")
    _git(main, "rm", "-q", "a.txt")
    _git(main, "commit", "-q", "-m", "drop a")
    _git(main, "push", "-q", "origin", "harness/drop")
    _git(main, "switch", "-q", "main")

    note = remote_branches.delete_landed(main, "harness/drop", "main", worktree.unlanded_paths)

    assert note.startswith("kept origin/harness/drop")
    assert "a.txt" in note
    assert _on_origin(main, "harness/drop")


def _rename_lane(main: Path) -> None:
    _git(main, "switch", "-q", "-c", "harness/move", "origin/main")
    _git(main, "mv", "a.txt", "b.txt")
    _git(main, "commit", "-q", "-m", "move a")
    _git(main, "push", "-q", "origin", "harness/move")
    _git(main, "switch", "-q", "main")
    (main / "b.txt").write_text("a\n", encoding="utf-8")
    _git(main, "add", "b.txt")
    _git(main, "commit", "-q", "-m", "copy a to b")
    _git(main, "push", "-q", "origin", "main")


def test_a_rename_whose_deletion_did_not_land_keeps_the_remote_branch(main: Path) -> None:
    _rename_lane(main)

    note = remote_branches.delete_landed(main, "harness/move", "main", worktree.unlanded_paths)

    assert note.startswith("kept origin/harness/move")
    assert "a.txt" in note
    assert _on_origin(main, "harness/move")


def test_a_rename_whose_deletion_did_not_land_keeps_the_local_branch(main: Path) -> None:
    _rename_lane(main)

    reason = worktree._reclaim_branch(main, "harness/move", "main", force=False)

    assert "a.txt" in reason
    assert _git(main, "branch", "--list", "harness/move")


def test_a_remote_branch_that_moved_after_the_check_is_kept(
    main: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane(main, "harness/race", "e.txt")
    _git(main, "merge", "-q", "--no-ff", "-m", "land", "harness/race")
    _git(main, "push", "-q", "origin", "main")
    checked = remote_branches.remote_head

    def _then_moved(where: Path, branch: str) -> str | None:
        seen = checked(where, branch)
        if branch == "harness/race":
            _git(where, "switch", "-q", "harness/race")
            (where / "late.txt").write_text("late\n", encoding="utf-8")
            _git(where, "add", "late.txt")
            _git(where, "commit", "-q", "-m", "late")
            _git(where, "push", "-q", "origin", "harness/race")
            _git(where, "switch", "-q", "main")
        return seen

    monkeypatch.setattr(remote_branches, "remote_head", _then_moved)

    note = remote_branches.delete_landed(main, "harness/race", "main", worktree.unlanded_paths)

    assert note.startswith("kept origin/harness/race: git push refused the delete")
    assert _on_origin(main, "harness/race")


def test_a_base_that_moved_on_origin_but_is_not_fetched_keeps_the_branch(
    main: Path, tmp_path: Path
) -> None:
    _lane(main, "harness/stale", "f.txt")
    _git(main, "merge", "-q", "--no-ff", "-m", "land", "harness/stale")
    _git(main, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    _git(other, "commit", "-q", "--allow-empty", "-m", "elsewhere")
    _git(other, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "harness/stale", "main", worktree.unlanded_paths)

    assert "git fetch origin" in note
    assert _on_origin(main, "harness/stale")


def test_a_branch_with_no_commit_of_its_own_is_kept(main: Path) -> None:
    _git(main, "push", "-q", "origin", "main:refs/heads/harness/fresh")

    note = remote_branches.delete_landed(main, "harness/fresh", "main", worktree.unlanded_paths)

    assert note == "kept origin/harness/fresh: it has no commit of its own beyond origin/main"
    assert _on_origin(main, "harness/fresh")


def test_drop_refs_leaves_a_ref_that_only_ends_like_a_land_ref(main: Path) -> None:
    head = _git(main, "rev-parse", "HEAD")
    _git(main, "push", "-q", "origin", f"{head}:refs/heads/x/land/feat/1")
    proofs = main / ci_landing.PROOFS
    proofs.parent.mkdir(parents=True, exist_ok=True)
    proofs.write_text(json.dumps({"feat": {"attempt": "1"}}), encoding="utf-8")

    assert ci_landing.drop_refs(main, "feat") == []
    assert _on_origin(main, "x/land/feat/1")


def test_a_cleanup_that_kept_the_remote_branch_can_run_again_once_base_is_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setattr(worktree, "provision_deps", lambda *_a, **_kw: [])
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    session = worktree.create("early", repo_root=repo)
    (session.path / "lane.txt").write_text("lane\n", encoding="utf-8")
    _git(session.path, "add", "lane.txt")
    _git(session.path, "commit", "-q", "-m", "lane")
    _git(session.path, "push", "-q", "origin", session.branch)
    _git(repo, "merge", "-q", "--no-ff", "-m", "land", session.branch)

    worktree.cleanup("early", repo_root=repo)

    first = capsys.readouterr().out
    assert f"kept branch {session.branch} and its record" in first
    assert "lane.txt" in first
    assert _on_origin(repo, session.branch)

    _git(repo, "push", "-q", "origin", "main")
    worktree.cleanup("early", repo_root=repo)

    assert f"deleted origin/{session.branch}" in capsys.readouterr().out
    assert not _on_origin(repo, session.branch)
    assert worktree.load_session("early", repo) is None


@pytest.mark.parametrize("proofs", [None, {"other": {"attempt": "2"}}])
def test_drop_refs_for_a_lane_with_no_proof_entry_drops_nothing(
    main: Path, proofs: dict[str, dict[str, str]] | None
) -> None:
    if proofs is not None:
        path = main / ci_landing.PROOFS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(proofs), encoding="utf-8")

    assert ci_landing.drop_refs(main, "feat") == []


def test_a_failed_proof_records_its_attempt_before_the_push(main: Path) -> None:
    flows = main / ".github" / "workflows"
    flows.mkdir(parents=True, exist_ok=True)
    (flows / "quality-gates.yml").write_text("on:\n  workflow_dispatch:\n", encoding="utf-8")
    candidate = ci_landing.Candidate("feat", "basicly-x1", "main", _git(main, "rev-parse", "HEAD"))
    _git(main, "remote", "set-url", "--push", "origin", str(main / "missing.git"))

    with pytest.raises(ci_landing.CiLandingError):
        ci_landing.prove(main, candidate, gh=lambda _argv: "[]")

    assert ci_landing.proved(main, "feat")["attempt"] == "1"


def test_a_repository_with_no_origin_has_nothing_to_delete(main: Path) -> None:
    _git(main, "remote", "remove", "origin")

    assert remote_branches.delete_landed(main, "harness/x", "main", worktree.unlanded_paths) == ""


def test_a_failed_base_lookup_is_named_as_a_failure(
    main: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane(main, "harness/look", "g.txt")
    checked = remote_branches.remote_head
    monkeypatch.setattr(
        remote_branches,
        "remote_head",
        lambda where, branch: None if branch == "main" else checked(where, branch),
    )

    note = remote_branches.delete_landed(main, "harness/look", "main", worktree.unlanded_paths)

    assert (
        note == "kept origin/harness/look: git ls-remote origin failed for the base main, "
        "so it was not compared"
    )


def test_a_lane_landed_by_fast_forward_has_its_remote_branch_deleted(main: Path) -> None:
    started = _git(main, "rev-parse", "origin/main")
    _lane(main, "harness/ff", "ff.txt")
    _git(main, "merge", "-q", "--ff-only", "harness/ff")
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(
        main, "harness/ff", "main", worktree.unlanded_paths, started
    )

    assert note.startswith("deleted origin/harness/ff")
    assert not _on_origin(main, "harness/ff")


def test_a_fresh_branch_still_at_its_start_commit_is_kept(main: Path) -> None:
    started = _git(main, "rev-parse", "origin/main")
    _git(main, "push", "-q", "origin", "main:refs/heads/harness/new")

    note = remote_branches.delete_landed(
        main, "harness/new", "main", worktree.unlanded_paths, started
    )

    assert note == "kept origin/harness/new: it has no commit of its own beyond origin/main"
