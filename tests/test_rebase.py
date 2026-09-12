from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import rebase


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeGit:
    def __init__(self, responses: dict[str, _Proc]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        for key in (" ".join(args), args[0]):
            if key in self.responses:
                return self.responses[key]
        raise AssertionError(f"unstubbed git subcommand {args[0]!r}: git {' '.join(args)}")


_CLEAN_PROBES = {
    "rev-list --merges main..harness/feat": _Proc(0, ""),
    "rev-parse harness/feat": _Proc(0, "abc123"),
    "ls-tree": _Proc(0, ""),
}
_REBASE_STOPPED = {"rebase": _Proc(1, "CONFLICT")}
_REBASE_RESUMED = "-c core.editor=true rebase --continue"


def _fake(monkeypatch: pytest.MonkeyPatch, **responses: _Proc) -> _FakeGit:
    fake = _FakeGit({**_CLEAN_PROBES, **responses})
    monkeypatch.setattr(rebase, "git", fake)
    return fake


def _replay(repo_root: Path) -> rebase.ReplayOutcome:
    return rebase.replay(repo_root, repo_root, "main", "harness/feat")


def _declare_generated(repo_root: Path, *paths: str, command: str = '["true"]') -> None:
    entries = "".join(f'"{path}" = {command}\n' for path in paths)
    (repo_root / "basicly.toml").write_text(
        f"[worktree.regenerate_commands]\n{entries}", encoding="utf-8"
    )


def test_a_conflict_confined_to_declared_generated_paths_is_rebuilt_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _declare_generated(tmp_path, ".basicly/generated-manifest.json")
    fake = _fake(
        monkeypatch,
        **{
            **_REBASE_STOPPED,
            "diff": _Proc(0, ".basicly/generated-manifest.json\n"),
            "add": _Proc(0),
            _REBASE_RESUMED: _Proc(0),
        },
    )
    monkeypatch.setattr(rebase, "run", lambda *_a, **_k: _Proc(0))

    outcome = _replay(tmp_path)

    assert outcome.ok is True
    assert ["rebase", "--abort"] not in fake.calls
    assert outcome.regenerated == (".basicly/generated-manifest.json",)


def test_one_undeclared_path_in_the_set_bounces_the_whole_rebase_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _declare_generated(tmp_path, ".basicly/generated-manifest.json")
    conflicts = ".basicly/generated-manifest.json\nsrc/shared.py\n"
    fake = _fake(monkeypatch, **{**_REBASE_STOPPED, "diff": _Proc(0, conflicts)})
    monkeypatch.setattr(
        rebase, "run", lambda *_a, **_k: pytest.fail("the rebuild ran on an undeclared conflict")
    )

    outcome = _replay(tmp_path)

    assert outcome.status == "rebase-conflicts"
    assert outcome.conflicts == (".basicly/generated-manifest.json", "src/shared.py")
    assert ["rebase", "--abort"] in fake.calls


def test_a_conflict_on_a_generated_path_bounces_while_nothing_is_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _fake(
        monkeypatch,
        **{**_REBASE_STOPPED, "diff": _Proc(0, ".basicly/generated-manifest.json\n")},
    )
    monkeypatch.setattr(
        rebase, "run", lambda *_a, **_k: pytest.fail("the rebuild ran with nothing declared")
    )

    outcome = _replay(tmp_path)

    assert outcome.status == "rebase-conflicts"
    assert ["rebase", "--abort"] in fake.calls


def test_a_conflict_marker_the_rebuild_left_bounces_rather_than_being_staged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _declare_generated(tmp_path, "plan.md")
    (tmp_path / "plan.md").write_text("<<<<<<< HEAD\n", encoding="utf-8")
    stubs = {"diff": _Proc(0, "plan.md\n"), "add": _Proc(0), _REBASE_RESUMED: _Proc(0)}
    fake = _fake(monkeypatch, **{**_REBASE_STOPPED, **stubs})
    monkeypatch.setattr(rebase, "run", lambda *_a, **_k: _Proc(0))

    outcome = _replay(tmp_path)

    assert outcome.status == "rebase-conflicts"
    assert ["rebase", "--abort"] in fake.calls


def test_a_failing_rebuild_bounces_rather_than_landing_a_half_resolved_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _declare_generated(tmp_path, ".basicly/generated-manifest.json")
    fake = _fake(
        monkeypatch,
        **{**_REBASE_STOPPED, "diff": _Proc(0, ".basicly/generated-manifest.json\n")},
    )
    monkeypatch.setattr(rebase, "run", lambda *_a, **_k: _Proc(1))

    outcome = _replay(tmp_path)

    assert outcome.status == "rebase-conflicts"
    assert ["rebase", "--abort"] in fake.calls


def test_a_rebase_that_will_not_finish_is_aborted_rather_than_driven_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _declare_generated(tmp_path, ".basicly/generated-manifest.json")
    fake = _fake(
        monkeypatch,
        **{
            **_REBASE_STOPPED,
            "diff": _Proc(0, ".basicly/generated-manifest.json\n"),
            "add": _Proc(0),
            _REBASE_RESUMED: _Proc(1),
        },
    )
    monkeypatch.setattr(rebase, "run", lambda *_a, **_k: _Proc(0))

    outcome = _replay(tmp_path)

    assert outcome.status == "rebase-conflicts"
    resumes = [call for call in fake.calls if call[-1] == "--continue"]
    assert len(resumes) == rebase.MAX_REGENERATED_REBASE_STEPS


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(  # nosec B603
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


@pytest.fixture
def lane_with_a_merge(tmp_path: Path) -> Path:

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester@example.invalid")
    _git(repo, "config", "user.name", "tester")
    (repo / "shared.txt").write_text("original\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "checkout", "-q", "-b", "harness/feat")
    (repo / "lane-only.txt").write_text("lane\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "lane work")

    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("base side\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base work")

    _git(repo, "checkout", "-q", "harness/feat")
    subprocess.run(  # nosec B603
        ["git", "merge", "--no-ff", "--no-commit", "main"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    (repo / "shared.txt").write_text("resolved: neither side\n", encoding="utf-8")
    (repo / "only-in-the-merge.txt").write_text("carried by the merge alone\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "merge main, resolving shared.txt")
    return repo


def test_a_plain_rebase_really_does_discard_the_resolution(lane_with_a_merge: Path) -> None:

    repo = lane_with_a_merge
    before = _git(repo, "rev-parse", "harness/feat")
    proc = subprocess.run(  # nosec B603
        ["git", "rebase", "main", "harness/feat"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    survivors = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert "only-in-the-merge.txt" not in survivors
    assert (repo / "shared.txt").read_text(encoding="utf-8") != "resolved: neither side\n"
    assert before != _git(repo, "rev-parse", "harness/feat")


def test_merge_commits_names_the_commit_a_rebase_would_skip(lane_with_a_merge: Path) -> None:
    repo = lane_with_a_merge
    carried = rebase.merge_commits(repo, "main", "harness/feat")

    assert len(carried) == 1
    assert _git(repo, "rev-list", "--merges", "main..harness/feat") == carried[0]
    assert rebase.merge_commits(repo, "main", "main") == ()


def test_replay_refuses_the_branch_and_leaves_the_resolution_in_the_tree(
    lane_with_a_merge: Path,
) -> None:
    repo = lane_with_a_merge
    before = _git(repo, "rev-parse", "harness/feat")

    outcome = rebase.replay(repo, repo, "main", "harness/feat")

    assert outcome.ok is False
    assert outcome.status == rebase.MERGE_COMMIT_ON_BRANCH
    assert "merge commit" in outcome.detail
    assert _git(repo, "rev-parse", "harness/feat") == before
    survivors = _git(repo, "ls-tree", "-r", "--name-only", "harness/feat").splitlines()
    assert "only-in-the-merge.txt" in survivors
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "resolved: neither side\n"


def test_dropped_paths_names_what_a_replay_lost(lane_with_a_merge: Path) -> None:
    repo = lane_with_a_merge
    before = _git(repo, "rev-parse", "harness/feat")
    subprocess.run(  # nosec B603
        ["git", "rebase", "main", "harness/feat"], cwd=repo, check=False, capture_output=True
    )

    assert rebase.dropped_paths(repo, before, "main") == ("only-in-the-merge.txt",)


def test_dropped_paths_ignores_a_file_base_deleted(tmp_path: Path) -> None:

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester@example.invalid")
    _git(repo, "config", "user.name", "tester")
    (repo / "doomed.txt").write_text("base will delete this\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "checkout", "-q", "-b", "harness/feat")
    (repo / "lane.txt").write_text("lane\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "lane work")
    before = _git(repo, "rev-parse", "harness/feat")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "rm", "-q", "doomed.txt")
    _git(repo, "commit", "-q", "-m", "base deletes it")

    _git(repo, "checkout", "-q", "harness/feat")
    subprocess.run(  # nosec B603
        ["git", "rebase", "main", "harness/feat"], cwd=repo, check=False, capture_output=True
    )

    assert "doomed.txt" not in _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    assert rebase.dropped_paths(repo, before, "main") == ()


def test_replay_restores_the_branch_when_the_backstop_fires(
    monkeypatch: pytest.MonkeyPatch, lane_with_a_merge: Path
) -> None:

    repo = lane_with_a_merge
    monkeypatch.setattr(rebase, "merge_commits", lambda *_a, **_k: ())
    before = _git(repo, "rev-parse", "harness/feat")

    outcome = rebase.replay(repo, repo, "main", "harness/feat")

    assert outcome.status == rebase.REPLAY_DROPPED_PATHS
    assert "only-in-the-merge.txt" in outcome.detail
    assert _git(repo, "rev-parse", "harness/feat") == before
    survivors = _git(repo, "ls-tree", "-r", "--name-only", "harness/feat").splitlines()
    assert "only-in-the-merge.txt" in survivors


def test_a_linear_branch_replays_with_nothing_to_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester@example.invalid")
    _git(repo, "config", "user.name", "tester")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "checkout", "-q", "-b", "harness/feat")
    (repo / "lane.txt").write_text("lane\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "lane work")

    _git(repo, "checkout", "-q", "main")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base work")

    outcome = rebase.replay(repo, repo, "main", "harness/feat")

    assert outcome.ok is True
    assert outcome.regenerated == ()
    survivors = _git(repo, "ls-tree", "-r", "--name-only", "harness/feat").splitlines()
    assert sorted(survivors) == ["a.txt", "base.txt", "lane.txt"]
