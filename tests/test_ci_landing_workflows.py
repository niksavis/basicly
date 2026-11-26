from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from basicly import ci_landing
from tests.test_ci_landing import CLOCK, NO_INPUT, Gh, _candidate, _git, _repo, _workflow

if TYPE_CHECKING:
    from pathlib import Path


def _dispatches(gh: Gh) -> list[list[str]]:
    return [argv for argv in gh.calls if argv[:2] == ["workflow", "run"]]


def test_a_repository_with_only_basicly_gates_dispatches_it_without_a_base_input(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / ".github" / "workflows" / "quality-gates.yml").unlink()
    _workflow(repo, "basicly-gates.yml", NO_INPUT)
    gh = Gh()

    ci_landing.prove(repo, _candidate(repo), gh, CLOCK)

    assert _dispatches(gh) == [["workflow", "run", "basicly-gates.yml", "--ref", "land/lane-a/1"]]
    assert all("quality-gates.yml" not in argv for argv in gh.calls)


def test_two_dispatchable_workflows_with_no_list_are_refused_with_the_key_line(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _workflow(repo, "basicly-gates.yml", NO_INPUT)
    gh = Gh()

    with pytest.raises(ci_landing.CiLandingError) as refused:
        ci_landing.prove(repo, _candidate(repo), gh, CLOCK)

    assert '[landing] workflows = ["quality-gates.yml", "basicly-gates.yml"]' in str(refused.value)
    assert gh.calls == []
    assert not _git(repo, "ls-remote", "origin", "refs/heads/land/lane-a/1")


def test_the_listed_workflows_of_a_terminal_like_repository_are_all_dispatched_and_proved(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _workflow(repo, "basicly-gates.yml", NO_INPUT)
    _workflow(repo, "quality-gates-macos.yml", NO_INPUT)
    _workflow(repo, "pages.yml", NO_INPUT)
    with (repo / "basicly.toml").open("a", encoding="utf-8") as config:
        config.write(
            '[landing]\nworkflows = ["quality-gates.yml", "basicly-gates.yml", '
            '"quality-gates-macos.yml"]\n'
        )
    gh = Gh()

    proof = ci_landing.prove(repo, _candidate(repo), gh, CLOCK)

    assert [argv[2] for argv in _dispatches(gh)] == [
        "quality-gates.yml",
        "basicly-gates.yml",
        "quality-gates-macos.yml",
    ]
    assert _dispatches(gh)[0][-2:] == ["-f", f"base={_candidate(repo).base}"]
    assert len(proof["run"].split()) == 3


def test_a_listed_workflow_with_no_dispatch_trigger_is_refused_by_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with (repo / "basicly.toml").open("a", encoding="utf-8") as config:
        config.write('[landing]\nworkflows = ["missing.yml"]\n')

    with pytest.raises(ci_landing.CiLandingError, match=r"names missing\.yml, but"):
        ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)


def test_a_repository_with_no_dispatchable_workflow_is_refused_by_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".github" / "workflows" / "quality-gates.yml").unlink()

    with pytest.raises(ci_landing.CiLandingError, match=r"no workflow in \.github/workflows"):
        ci_landing.prove(repo, _candidate(repo), Gh(), CLOCK)


@pytest.mark.parametrize(
    ("logged_in", "named", "not_named"),
    [
        (True, "gh is logged in, so gh refused this call; run gh workflow run", "gh auth login"),
        (False, "run gh auth login", "gh is logged in"),
    ],
)
def test_a_refused_dispatch_names_the_real_gh_cause(
    tmp_path: Path, logged_in: bool, named: str, not_named: str
) -> None:
    repo = _repo(tmp_path)
    gh = Gh()

    def refusing(argv: list[str]) -> str | None:
        if argv[:2] == ["workflow", "run"]:
            return None
        if argv[:2] == ["auth", "status"]:
            return "" if logged_in else None
        return gh(argv)

    with pytest.raises(ci_landing.CiLandingError) as refused:
        ci_landing.prove(repo, _candidate(repo), refusing, CLOCK)

    assert named in str(refused.value)
    assert not_named not in str(refused.value)
