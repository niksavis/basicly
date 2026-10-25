from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from basicly.config import load_technology_selection

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".basicly/core/hooks/comment_ratchet.py"
HOOK = ROOT / ".basicly/core/hooks/no-comments.py"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    return tmp_path


def seed(repo: Path, source: str, name: str = "a.py") -> None:
    (repo / name).write_text(source, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "--allow-empty", "-qm", "seed")


def staged(repo: Path, source: str, name: str = "a.py") -> None:
    (repo / name).write_text(source, encoding="utf-8")
    git(repo, "add", name)


def ratchet(repo: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *paths],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_unchanged_prose_survives_code_edits(repo: Path) -> None:
    seed(repo, '# legacy\n"""ordinary string"""\nx = 1\n')
    staged(repo, '# legacy\n"""ordinary string"""\nx = 2\n')
    result = ratchet(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("# legacy\nx = 1\n", "# changed\nx = 1\n"),
        ("# legacy\nx = 1\n", "# legacy\n# legacy\nx = 1\n"),
        ('"""\nlegacy\n"""\nx = 1\n', '"""\nchanged\n"""\nx = 1\n'),
        ('x = 1\n"""\nprose\n"""\n', '"""\nprose\n"""\n'),
        ("x = 1\n", "# Ő\nx = 1\n"),
        ("x = 1\n", 'def f():\n    """new prose"""\n    return 1\n'),
    ],
)
def test_new_prose_is_refused(repo: Path, before: str, after: str) -> None:
    seed(repo, before)
    staged(repo, after)
    result = ratchet(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "a.py:" in result.stdout


def test_staged_prose_is_not_hidden_by_clean_working_bytes(repo: Path) -> None:
    seed(repo, "x = 1\n")
    staged(repo, "# new prose\nx = 1\n")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert ratchet(repo).returncode == 1


def test_unstaged_prose_does_not_change_a_clean_index(repo: Path) -> None:
    seed(repo, "x = 1\n")
    (repo / "a.py").write_text("# unstaged prose\nx = 1\n", encoding="utf-8")
    assert ratchet(repo).returncode == 0


def test_rename_keeps_its_legacy_allowance(repo: Path) -> None:
    seed(repo, "# legacy\nx = 1\n")
    git(repo, "mv", "a.py", "b.py")
    result = ratchet(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_copy_does_not_receive_a_legacy_allowance(repo: Path) -> None:
    seed(repo, "# legacy\nx = 1\n")
    staged(repo, "# legacy\nx = 1\n", "b.py")
    assert ratchet(repo).returncode == 1


def test_removed_prose_cannot_return_in_a_later_commit(repo: Path) -> None:
    seed(repo, "# legacy\nx = 1\n")
    staged(repo, "x = 1\n")
    assert ratchet(repo).returncode == 0
    git(repo, "commit", "-qm", "remove prose")
    staged(repo, "# legacy\nx = 1\n")
    assert ratchet(repo).returncode == 1


def test_initial_commit_checks_every_added_comment(repo: Path) -> None:
    staged(repo, "# new prose\nx = 1\n")
    assert ratchet(repo).returncode == 1


def test_directives_remain_allowed(repo: Path) -> None:
    staged(repo, "# noqa: F401\nimport os\n")
    result = ratchet(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_staged_source_is_refused_without_writing(repo: Path) -> None:
    seed(repo, "x = 1\n")
    staged(repo, '"""unterminated\n')
    before = (repo / "a.py").read_bytes()
    result = ratchet(repo)
    assert result.returncode != 0
    assert (repo / "a.py").read_bytes() == before


def test_git_failure_is_refused(tmp_path: Path) -> None:
    result = ratchet(tmp_path)
    assert result.returncode != 0
    assert "ratchet" in result.stderr


def test_hook_defaults_to_strict_and_accepts_an_explicit_transition_policy(repo: Path) -> None:
    seed(repo, "# legacy\nx = 1\n")
    staged(repo, "# legacy\nx = 2\n")
    command = [sys.executable, str(HOOK), "a.py"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    strict = subprocess.run(command, cwd=repo, env=env, capture_output=True, check=False)
    assert strict.returncode == 1
    (repo / "basicly.toml").write_text(
        '[hooks.comments]\npolicy = "no-comment-ratchet"\n', encoding="utf-8"
    )
    transition = subprocess.run(command, cwd=repo, env=env, capture_output=True, check=False)
    assert transition.returncode == 0, transition.stdout + transition.stderr


def test_unknown_policy_is_refused_even_for_clean_code(repo: Path) -> None:
    staged(repo, "x = 1\n")
    (repo / "basicly.toml").write_text('[hooks.comments]\npolicy = "none"\n', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(HOOK), "a.py"], cwd=repo, capture_output=True, check=False
    )
    assert result.returncode != 0


def test_data_renamed_into_code_gets_no_legacy_allowance(repo: Path) -> None:
    seed(repo, "# prose\nx = 1\n", "a.txt")
    git(repo, "mv", "a.txt", "a.py")
    assert ratchet(repo).returncode == 1


@pytest.mark.parametrize("policy", ['"none"', "false", '["no-comments"]'])
def test_engine_refuses_an_invalid_comment_policy_before_install(
    tmp_path: Path, policy: str
) -> None:
    (tmp_path / "basicly.toml").write_text(
        f"[hooks.comments]\npolicy = {policy}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"hooks\.comments\.policy"):
        load_technology_selection(tmp_path)


def test_replacing_a_symlink_with_code_has_no_legacy_allowance(repo: Path) -> None:
    target = repo / "target"
    target.write_text("target.py\n")
    blob = git(repo, "hash-object", "-w", "target").stdout.decode("ascii").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},a.py")
    git(repo, "commit", "-qm", "seed symlink")
    staged(repo, "# new prose\nx = 1\n")
    result = ratchet(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "a.py:" in result.stdout
