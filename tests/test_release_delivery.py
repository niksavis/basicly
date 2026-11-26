from __future__ import annotations

from pathlib import Path

import pytest

from basicly import delivery_pr, release, release_delivery
from tests.delivery_pr_fixture import CLOCK, RECORD, Gh, _git, _repo
from tests.release_repo_fixture import seeded

TAG = "v9.9.9"
CUT = release_delivery.Release(TAG, "2026-11-26", RECORD)


def _release_commit(repo: Path) -> str:
    (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-q", "-m", "chore(release): bump version")
    return _git(repo, "rev-parse", "HEAD")


def test_a_release_merges_through_a_pull_request_and_tags_the_merge_commit(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    head = _release_commit(repo)
    gh = Gh(repo)

    steps = release_delivery.deliver(repo, CUT, gh=gh, clock=CLOCK)

    merged = _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]
    assert _git(repo, "rev-parse", f"{merged}^2") == head
    assert _git(repo, "rev-parse", f"{TAG}^{{commit}}") == merged
    assert _git(repo, "cat-file", "-t", TAG) == "tag"
    assert _git(repo, "ls-remote", "origin", f"refs/tags/{TAG}")
    assert steps[0].endswith(f"tagged {TAG} on it and pushed the tag")
    pushes = [call for call in gh.calls if call[:2] == ["run", "list"] and "push" in call]
    assert [call[call.index("--commit") + 1] for call in pushes] == [merged]


def test_a_red_release_run_creates_no_tag(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release_commit(repo)
    pushed = _git(repo, "ls-remote", "origin", "refs/heads/main")

    with pytest.raises(delivery_pr.PrDeliveryError, match="ended failure"):
        release_delivery.deliver(repo, CUT, gh=Gh(repo, "failure"), clock=CLOCK)

    assert _git(repo, "tag", "--list", TAG) == ""
    assert not _git(repo, "ls-remote", "origin", f"refs/tags/{TAG}")
    assert _git(repo, "ls-remote", "origin", "refs/heads/main") == pushed


def test_a_release_for_a_pr_delivery_commits_and_leaves_the_tag_to_the_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "_regenerate", lambda _root: None)
    repo = seeded(tmp_path)
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", tag=False)

    assert not result.refused and not result.tagged
    assert _git(repo, "log", "--format=%s", "-1") == f"{release.COMMIT_SUBJECT} (fx-1)"
    assert _git(repo, "tag", "--list", "v0.6.0") == ""
    assert "the pr delivery tags the merge commit with v0.6.0" in result.steps[-1]


def test_a_red_push_run_on_the_merge_commit_creates_no_tag(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _release_commit(repo)
    gh = Gh(repo)
    merge = gh._merge

    def merge_then_fail_main(argv: list[str]) -> str | None:
        done = merge(argv)
        gh.history[gh.merged] = [
            {**gh.run, "databaseId": 9, "conclusion": "failure", "url": "main-run"}
        ]
        return done

    gh._merge = merge_then_fail_main

    with pytest.raises(delivery_pr.PrDeliveryError, match="push run on main is not green"):
        release_delivery.deliver(repo, CUT, gh=gh, clock=CLOCK)

    assert _git(repo, "tag", "--list", TAG) == ""
    assert not _git(repo, "ls-remote", "origin", f"refs/tags/{TAG}")
