from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import cli, owned_write

GIT = (
    "git",
    "-c",
    "user.email=t@example.invalid",
    "-c",
    "user.name=t",
    "-c",
    "commit.gpgsign=false",
)
RULE = "events-*.jsonl -text merge=union"
LOG = ".basicly/ledger/events-0001.jsonl"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [*GIT, *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _repo(tmp_path: Path, attribute: str | None) -> Path:
    _git(tmp_path, "init", "-q", ".")
    if attribute is not None:
        (tmp_path / ".gitattributes").write_text(attribute + "\n", encoding="utf-8")
    (tmp_path / ".basicly" / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / LOG).write_text('{"id":"e1"}\n', encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _two_branches_append_and_merge(repo: Path) -> subprocess.CompletedProcess[str]:
    log = repo / LOG
    _git(repo, "checkout", "-qb", "agent-a")
    log.write_text(log.read_text(encoding="utf-8") + '{"id":"a1"}\n', encoding="utf-8")
    _git(repo, "commit", "-qam", "a")
    _git(repo, "checkout", "-q", "HEAD~1")
    _git(repo, "checkout", "-qb", "agent-b")
    log.write_text('{"id":"e1"}\n{"id":"b1"}\n', encoding="utf-8")
    _git(repo, "commit", "-qam", "b")
    _git(repo, "checkout", "-q", "agent-a")
    return _git(repo, "merge", "--no-edit", "agent-b")


def test_a_parallel_append_conflicts_without_the_attribute(tmp_path: Path) -> None:
    repo = _repo(tmp_path, None)

    merged = _two_branches_append_and_merge(repo)

    assert merged.returncode != 0
    assert "<<<<<<<" in (repo / LOG).read_text(encoding="utf-8")


def test_text_alone_is_not_enough(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "events-*.jsonl -text")

    merged = _two_branches_append_and_merge(repo)

    assert merged.returncode != 0


def test_the_rule_the_install_writes_makes_the_same_merge_clean(tmp_path: Path) -> None:
    repo = _repo(tmp_path, RULE)

    merged = _two_branches_append_and_merge(repo)

    assert merged.returncode == 0, merged.stdout + merged.stderr
    kept = (repo / LOG).read_text(encoding="utf-8")
    for ident in ("e1", "a1", "b1"):
        assert ident in kept


def test_the_rule_is_derived_from_the_kit_and_not_spelled_again(work_repo: Path) -> None:
    assert owned_write.ledger_git_rules(work_repo) == (RULE,)


def test_a_repository_with_no_kit_gets_no_rule(tmp_path: Path) -> None:
    assert owned_write.ledger_git_rules(tmp_path) == ()


def test_the_scaffold_writes_the_rule_after_any_star_rule(work_repo: Path) -> None:
    path = work_repo / ".gitattributes"
    path.write_text("* text=auto\n", encoding="utf-8")

    cli._scaffold_ledger_attributes(work_repo)

    lines = [line for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    assert lines[-1] == RULE
    assert lines.index("* text=auto") < lines.index(RULE)


def test_the_scaffold_is_idempotent(work_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli._scaffold_ledger_attributes(work_repo)
    capsys.readouterr()

    cli._scaffold_ledger_attributes(work_repo)

    assert capsys.readouterr().out == ""


def test_the_scaffold_refuses_rather_than_converging_without_the_rule(work_repo: Path) -> None:
    (work_repo / ".gitattributes").unlink(missing_ok=True)
    (work_repo / ".gitattributes").mkdir()

    with pytest.raises(SystemExit, match="conflicts on every parallel append"):
        cli._scaffold_ledger_attributes(work_repo)


def test_the_two_install_paths_promise_the_same_thing() -> None:
    shim = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "basicly-tracker"
        / "basicly_tracker"
        / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "-text merge=union" in shim
    assert "-text merge=union" in (
        Path(__file__).resolve().parents[1] / "src" / "basicly" / "owned_write.py"
    ).read_text(encoding="utf-8")
