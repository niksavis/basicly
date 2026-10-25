from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from basicly import checkout, landing_guard, tracker
from tests import flipped_tracker
from tests.test_merge import _add_source, _git_here, _lane_worktree, _seed_repo


@pytest.fixture
def landing(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    session = _lane_worktree(tmp_path, repo)
    _add_source(session.path, "b")
    return repo, session, landing_guard.capture(repo, session, "full")


def test_guarded_publication_keeps_exact_parents_tree_and_existing_hooks(landing) -> None:
    repo, session, subject = landing
    normal = "from pathlib import Path; Path('.git/normal-hook-ran').write_text('ran')"
    _git_here(repo, "config", "hook.existing-msg.event", "commit-msg")
    _git_here(
        repo, "config", "hook.existing-msg.command", shlex.join([sys.executable, "-c", normal])
    )
    subject = landing_guard.capture(repo, session, "full")
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "guarded merge"]
    )
    assert result.returncode == 0, result.stderr
    assert _git_here(repo, "show", "-s", "--format=%P%n%T", "HEAD").splitlines() == [
        f"{subject.destination} {subject.candidate}",
        subject.tree,
    ]
    assert (repo / ".git/normal-hook-ran").read_text() == "ran"
    assert not _git_here(repo, "status", "--porcelain")


@pytest.mark.parametrize(
    "change", ["branch", "destination", "source", "untracked", "runtime", "policy"]
)
def test_changes_after_capture_are_refused_before_publication(landing, change: str) -> None:
    repo, session, subject = landing
    if change == "branch":
        _git_here(session.path, "commit", "--allow-empty", "-m", "candidate moved")
    elif change == "destination":
        _git_here(repo, "commit", "--allow-empty", "-m", "destination moved")
    elif change == "source":
        (session.path / "sources/a.txt").write_text("changed")
    elif change == "untracked":
        (session.path / "untracked.txt").write_text("new input")
    elif change == "runtime":
        path = session.path / ".venv/Lib/site-packages"
        path.mkdir(parents=True)
        (path / "runtime.py").write_text("changed")
    else:
        (session.path / "basicly.toml").write_text("[verify]\nfail_fast=false\n")
    before = _git_here(repo, "rev-parse", "HEAD")
    assert landing_guard.changed(repo, subject)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == before


def test_destination_movement_in_the_merge_call_is_rejected_by_the_locked_guard(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _session, subject = landing
    assert not landing_guard.changed(repo, subject)
    original = checkout.git
    moved = []

    def publish(args, **kwargs):
        if "merge" in args:
            original(["commit", "--allow-empty", "-m", "destination raced"], cwd=repo)
            moved.append(original(["rev-parse", "HEAD"], cwd=repo).stdout.strip())
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", publish)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse race"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == moved[0]


def test_changed_branch_after_precheck_cannot_substitute_the_merged_candidate(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, session, subject = landing
    original = checkout.git

    def publish(args, **kwargs):
        if "merge" in args:
            original(["commit", "--allow-empty", "-m", "candidate raced"], cwd=session.path)
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", publish)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse substitution"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination


def test_unverified_destination_blocks_other_landings_and_close_until_reverified(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    flipped_tracker.flipped_repo(repo)
    flipped_tracker.seed(repo, "basicly-owner", title="failed landing")
    landing_guard.record_verification(repo, "basicly-owner", passed=False)
    assert "basicly-owner" in landing_guard.blocker(repo, "basicly-other")
    assert landing_guard.blocker(repo, "basicly-owner") == ""
    assert landing_guard.blocker(repo, "basicly-owner", allow_owner=False)
    landing_guard.record_verification(
        repo, "basicly-owner", passed=True, proof=landing_guard.destination_inputs(repo, "full")
    )
    assert not landing_guard.blocker(repo, "basicly-other")
    fact = tracker.read_artifacts(repo, "basicly-owner")["landing-verification"]
    assert isinstance(fact, dict)
    assert fact["status"] == "verified"


def test_disabled_reference_event_refuses_before_checkout_mutation(landing) -> None:
    repo, _session, subject = landing
    _git_here(repo, "config", "hook.reference-transaction.enabled", "false")
    with pytest.raises(landing_guard.GuardUnavailableError, match="disabled"):
        landing_guard.guarded_merge(
            repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse"]
        )
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination
    assert not _git_here(repo, "status", "--porcelain")


def test_disabling_the_reference_event_after_probe_cannot_bypass_publication_guard(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _session, subject = landing
    original = checkout.git

    def publish(args, **kwargs):
        if "merge" in args:
            original(["config", "hook.reference-transaction.enabled", "false"], cwd=repo)
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", publish)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination
