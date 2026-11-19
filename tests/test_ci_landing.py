from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import ci_landing, landing_guard, tracker
from tests import flipped_tracker


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, path: str, text: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"chore: {path}")
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = flipped_tracker.flipped_repo(tmp_path / "repo")
    with (repo / ".gitignore").open("a", encoding="utf-8") as ignore:
        ignore.write(".basicly/usage/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "remote", "add", "origin", str(origin))
    _commit(repo, "src/app.py", "VALUE = 1\n")
    _git(repo, "push", "-q", "origin", "main")
    return repo


class Gh:
    def __init__(self, conclusion: str = "success", attempt: int = 1) -> None:
        self.calls: list[list[str]] = []
        self.history: dict[str, list[dict]] = {}
        self.dispatched = False
        self.state = {
            "databaseId": 7,
            "url": "https://example.invalid/runs/7",
            "status": "completed",
            "conclusion": conclusion,
            "attempt": attempt,
            "jobs": [{"name": "Quality gates shard 3/12", "conclusion": conclusion}],
        }

    def __call__(self, argv: list[str]) -> str | None:
        self.calls.append(argv)
        if argv[:2] == ["workflow", "run"]:
            self.dispatched = True
            return ""
        if argv[:2] == ["run", "list"] and not self.dispatched:
            return json.dumps(self.history.get(argv[argv.index("--commit") + 1], []))
        if argv[:2] == ["run", "list"]:
            return json.dumps([self.state])
        return json.dumps(self.state)


CLOCK = ci_landing.Clock(sleep=lambda _s: None, monotonic=lambda: 0.0)


def _candidate(repo: Path) -> ci_landing.Candidate:
    head = _git(repo, "rev-parse", "HEAD")
    return ci_landing.Candidate("lane-a", "acme-1", head, head)


def test_a_proved_candidate_is_pushed_to_its_own_ref_and_its_run_recorded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gh = Gh()

    proof = ci_landing.prove(repo, _candidate(repo), gh, CLOCK)

    assert proof["run"] == "https://example.invalid/runs/7"
    assert proof["ref"] == "land/lane-a/1"
    assert _git(repo, "ls-remote", "origin", "refs/heads/land/lane-a/1")
    assert gh.calls[1][:5] == ["workflow", "run", "quality-gates.yml", "--ref", "land/lane-a/1"]


def test_a_proved_tree_is_reused_when_only_the_ledger_moved(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)
    _commit(repo, ".basicly/ledger/pending-main.jsonl", "{}\n")

    def refuse(argv: list[str]) -> str | None:
        pytest.fail(f"a new run was started: {argv}")

    assert ci_landing.prove(repo, _candidate(repo), refuse, CLOCK)["ref"] == "land/lane-a/1"


def test_a_code_change_needs_a_new_run_on_a_new_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)
    _commit(repo, "src/app.py", "VALUE = 2\n")

    assert ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)["ref"] == "land/lane-a/2"


@pytest.mark.parametrize(
    ("gh", "expected"),
    [
        (Gh("failure"), "ended failure in Quality gates shard 3/12"),
        (Gh("cancelled"), "ended cancelled"),
        (Gh("success", attempt=2), "succeeded only on attempt 2"),
        (lambda _argv: None, "cannot read earlier quality-gates.yml runs"),
        (lambda argv: "[]" if argv[:2] == ["run", "list"] else None, "cannot dispatch"),
    ],
)
def test_a_failed_unreadable_or_rerun_proof_is_refused_by_name(
    tmp_path: Path, gh: ci_landing.Gh, expected: str
) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ci_landing.CiLandingError, match=expected):
        ci_landing.prove(repo, _candidate(repo), gh, CLOCK)
    assert "run" not in ci_landing.proved(repo, "lane-a")


def test_a_run_that_never_finishes_is_refused_after_the_limit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gh = Gh()
    gh.state["status"] = "in_progress"
    ticks = iter(range(0, 10_000, 600))
    clock = ci_landing.Clock(sleep=lambda _s: None, monotonic=lambda: float(next(ticks)))
    with pytest.raises(ci_landing.CiLandingError, match="did not finish in 15 min"):
        ci_landing.prove(repo, _candidate(repo), gh, clock)


def test_two_ci_landings_in_a_row_leave_the_destination_verified(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for record in ("acme-1", "acme-2"):
        flipped_tracker.seed(repo, record, title=record)
    tracker.add_artifact(repo, "acme-1", "landing-verification", {"status": "unverified"})
    assert landing_guard.blocker(repo, "acme-2")
    ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)

    notes = ci_landing.finish(repo, "lane-a", "acme-1")

    assert notes == []
    assert landing_guard.blocker(repo, "acme-2") == ""
    assert not _git(repo, "ls-remote", "origin", "refs/heads/land/lane-a/1")
    assert _git(repo, "ls-remote", "origin", "refs/heads/main")


def test_a_landed_tree_unlike_the_proved_one_stays_unverified_and_unpushed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    flipped_tracker.seed(repo, "acme-1", title="acme-1")
    ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)
    pushed = _git(repo, "ls-remote", "origin", "refs/heads/main")
    _commit(repo, "src/app.py", "VALUE = 3\n")

    notes = ci_landing.finish(repo, "lane-a", "acme-1")

    assert len(notes) == 1
    assert "differs from the proved tree" in notes[0]
    assert _git(repo, "ls-remote", "origin", "refs/heads/main") == pushed


def _runs(by_commit: dict[str, list[dict]]) -> ci_landing.Gh:
    def answer(argv: list[str]) -> str | None:
        return json.dumps(by_commit.get(argv[argv.index("--commit") + 1], []))

    return answer


def _merged(repo: Path) -> tuple[str, str]:
    _git(repo, "checkout", "-q", "-b", "lane")
    proved = _commit(repo, "src/lane.py", "LANE = 1\n")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "chore(worktree): merge", "lane")
    return proved, _commit(repo, ".basicly/ledger/pending-main.jsonl", "{}\n")


def test_a_push_of_a_ci_proved_merge_reuses_the_lane_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proved, head = _merged(repo)
    green = {"databaseId": 7, "url": "lane-run", "conclusion": "success", "attempt": 1}

    assert ci_landing.reusable(repo, head, "99", _runs({proved: [green]})) == "lane-run"


@pytest.mark.parametrize(
    "run",
    [
        {"databaseId": 7, "url": "lane-run", "conclusion": "success", "attempt": 2},
        {"databaseId": 7, "url": "lane-run", "conclusion": "failure", "attempt": 1},
        {"databaseId": 99, "url": "this-run", "conclusion": "success", "attempt": 1},
    ],
)
def test_a_rerun_a_failure_or_the_run_itself_is_not_reuse(tmp_path: Path, run: dict) -> None:
    repo = _repo(tmp_path)
    proved, head = _merged(repo)

    assert ci_landing.reusable(repo, head, "99", _runs({proved: [run]})) == ""


def test_a_code_commit_on_top_of_a_proved_merge_runs_the_checks(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proved, _ = _merged(repo)
    head = _commit(repo, "src/app.py", "VALUE = 9\n")
    green = {"databaseId": 7, "url": "lane-run", "conclusion": "success", "attempt": 1}

    assert ci_landing.reusable(repo, head, "99", _runs({proved: [green]})) == ""


def test_a_run_that_gh_cannot_read_yet_is_waited_for(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gh = Gh()
    views = iter([None, None])

    def flaky(argv: list[str]) -> str | None:
        if argv[:2] == ["run", "view"]:
            return next(views, gh(argv))
        return gh(argv)

    assert ci_landing.prove(repo, _candidate(repo), flaky, CLOCK)["ref"] == "land/lane-a/1"


def test_a_run_gh_never_reads_is_refused_after_the_limit_by_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gh = Gh()

    def unread(argv: list[str]) -> str | None:
        return None if argv[:2] == ["run", "view"] else gh(argv)

    ticks = iter(range(0, 10_000, 600))
    clock = ci_landing.Clock(sleep=lambda _s: None, monotonic=lambda: float(next(ticks)))
    with pytest.raises(ci_landing.CiLandingError, match="gh could not read it"):
        ci_landing.prove(repo, _candidate(repo), unread, clock)


def _ended(conclusion: str) -> dict:
    return {"databaseId": 5, "url": "earlier-run", "status": "completed", "conclusion": conclusion}


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_a_head_whose_run_already_ended_badly_is_refused_before_any_push(
    tmp_path: Path, conclusion: str
) -> None:
    repo = _repo(tmp_path)
    gh = Gh()
    gh.history[_git(repo, "rev-parse", "HEAD")] = [_ended(conclusion)]

    with pytest.raises(ci_landing.CiLandingError, match=f"earlier-run already ended {conclusion}"):
        ci_landing.prove(repo, _candidate(repo), gh, CLOCK)
    assert not gh.dispatched
    assert not _git(repo, "ls-remote", "origin", "refs/heads/land/lane-a/1")


def test_a_failed_tree_moved_only_in_the_ledger_is_still_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    failed = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(ci_landing.CiLandingError):
        ci_landing.prove(repo, _candidate(repo), Gh("failure"), CLOCK)
    _commit(repo, ".basicly/ledger/pending-main.jsonl", "{}\n")
    gh = Gh()
    gh.history[failed] = [_ended("failure")]

    with pytest.raises(ci_landing.CiLandingError, match="already ended failure"):
        ci_landing.prove(repo, _candidate(repo), gh, CLOCK)
    assert not gh.dispatched


def test_a_changed_lane_after_a_failure_gets_a_new_branch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ci_landing.CiLandingError):
        ci_landing.prove(repo, _candidate(repo), Gh("failure"), CLOCK)
    _commit(repo, "src/app.py", "VALUE = 5\n")

    assert ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)["ref"] == "land/lane-a/2"
