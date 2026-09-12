from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import checkout


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_main_checkout_and_worktrees_root(git_repo: Path) -> None:
    assert checkout.main_checkout(git_repo) == git_repo
    assert checkout.worktrees_root(git_repo).name == "repo.worktrees"
    assert checkout.worktrees_root(git_repo).parent == git_repo.parent


def _identity(repo: Path) -> str:
    return (repo / ".git" / "config").read_text(encoding="utf-8")


def test_a_poisoned_git_dir_really_does_outrank_cwd(git_repo: Path, tmp_path: Path) -> None:

    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)

    subprocess.run(
        ["git", "config", "user.name", "leaked"],
        cwd=other,
        env={**os.environ, "GIT_DIR": str(git_repo / ".git")},
        check=True,
        capture_output=True,
    )

    assert "leaked" in _identity(git_repo)
    assert _identity(git_repo) != before


def test_git_writes_to_cwd_when_the_inherited_environment_names_another_repo(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)
    monkeypatch.setenv("GIT_DIR", str(git_repo / ".git"))

    checkout.git(["config", "user.name", "from-cwd"], cwd=other)

    assert "from-cwd" in _identity(other)
    assert _identity(git_repo) == before


def test_an_explicit_env_is_scrubbed_too(git_repo: Path, tmp_path: Path) -> None:

    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)

    checkout.run(
        ["git", "config", "user.name", "from-cwd"],
        cwd=other,
        env={**os.environ, "GIT_DIR": str(git_repo / ".git")},
    )

    assert "from-cwd" in _identity(other)
    assert _identity(git_repo) == before


def test_is_linked_checkout_distinguishes_worktree_from_base(git_repo: Path) -> None:

    linked = git_repo.parent / "linked"
    _git(git_repo, "worktree", "add", str(linked), "-b", "harness/linked")

    assert checkout.is_linked_checkout(linked) is True
    assert checkout.is_linked_checkout(git_repo) is False
    assert checkout.is_linked_checkout(git_repo.parent) is False


def test_names_in_reads_what_a_ref_holds_under_a_directory(tmp_path: Path) -> None:
    checkout.git(["init", "-q", "-b", "probe"], cwd=tmp_path)
    checkout.git(["config", "user.email", "probe@example.invalid"], cwd=tmp_path)
    checkout.git(["config", "user.name", "probe"], cwd=tmp_path)
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "demo-1.added.md").write_text("- x\n", encoding="utf-8")
    checkout.git(["add", "-A"], cwd=tmp_path)
    checkout.git(["commit", "-qm", "seed"], cwd=tmp_path)

    assert checkout.names_in("probe", "changelog.d", cwd=tmp_path) == ("demo-1.added.md",)


def test_names_in_is_empty_where_the_question_cannot_be_asked(tmp_path: Path) -> None:

    assert checkout.names_in("no-such-ref", "changelog.d", cwd=tmp_path) == ()


_CHAIN = (
    "markdownlint.............................................................Failed\n"
    "- hook id: markdownlint\n"
    "- exit code: 1\n"
    "\n"
    "note.md:1:1 error MD018/no-missing-space-atx No space after hash\n"
    "\n"
    "protect-generated-commit.................................................Passed\n"
)
_WARNING = "warning: `VIRTUAL_ENV=/elsewhere/.venv` does not match the project environment\n"


def _two_stream_failure(tmp_path: Path, out: str, err: str) -> list[str]:

    script = tmp_path / "refuse.py"
    script.write_text(
        f"import sys\nsys.stdout.write({out!r})\nsys.stderr.write({err!r})\nsys.exit(1)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_a_hook_refusal_names_the_check_and_not_the_argv(tmp_path: Path) -> None:

    with pytest.raises(RuntimeError) as raised:
        checkout.run(_two_stream_failure(tmp_path, _CHAIN, _WARNING), cwd=tmp_path)

    message = str(raised.value)
    assert "markdownlint" in message
    assert "MD018" in message
    assert "Passed" not in message
    assert "protect-generated-commit" not in message


def test_a_failure_that_ran_no_hooks_keeps_the_plain_wording(tmp_path: Path) -> None:
    argv = _two_stream_failure(tmp_path, "", "fatal: not a git repository\n")
    with pytest.raises(RuntimeError) as raised:
        checkout.run(argv, cwd=tmp_path)

    message = str(raised.value)
    assert "command failed (1)" in message
    assert "fatal: not a git repository" in message


_SUITE_CHAIN = """pre-commit-script........................................................Failed
- hook id: pre-commit-script
- exit code: 1

FAILED: comment-density (1.07s)
checks failed: 28/32 passed in 22.47s (failed: comment-density)
==> ruff
==> mermaid

protect-generated-commit.................................................Passed
"""

_ONLY_PASSES = "identity-guard...........................................................Passed\n"


def test_only_the_failing_hook_becomes_a_refusal() -> None:
    assert [r.check for r in checkout.refusals(_CHAIN)] == ["markdownlint"]


def test_the_reason_is_the_stated_verdict_not_the_tail_of_the_block() -> None:
    (refusal,) = checkout.refusals(_SUITE_CHAIN)
    assert refusal.check == "pre-commit-script"
    assert "checks failed: 28/32" in refusal.reason
    assert "==> mermaid" not in refusal.reason


def test_output_with_no_hook_chain_is_left_to_its_caller() -> None:
    assert checkout.ran_hooks("fatal: not a git repository") is False
    assert checkout.gate_refusal("fatal: not a git repository") is None


def test_an_unidentifiable_refusal_says_so_and_names_where_the_output_went(
    tmp_path: Path,
) -> None:
    summary = checkout.gate_refusal(_ONLY_PASSES, repo_root=tmp_path)
    assert summary is not None
    assert "names no failing check" in summary
    assert checkout.GATE_OUTPUT_DUMP.as_posix() in summary
    assert (tmp_path / checkout.GATE_OUTPUT_DUMP).read_text(encoding="utf-8") == _ONLY_PASSES


def test_the_dump_is_not_named_when_it_could_not_be_written() -> None:
    summary = checkout.gate_refusal(_ONLY_PASSES)
    assert summary is not None
    assert "it was not captured" in summary
    assert checkout.GATE_OUTPUT_DUMP.as_posix() not in summary


def test_a_reformatting_hook_reports_the_only_line_it_printed() -> None:
    chain = (
        "ruff-format..............................................................Failed\n"
        "- hook id: ruff-format\n"
        "- files were modified by this hook\n"
    )
    (refusal,) = checkout.refusals(chain)
    assert refusal.reason == "files were modified by this hook"


_THREE_FAILURES = """identity-guard...........................................................Passed
pre-commit-script........................................................Failed
- hook id: pre-commit-script
- exit code: 1
- files were modified by this hook

F401 [*] `os` imported but unused
FAILED: ruff (0.04s)
Contracts: 2 kept, 1 broken.
FAILED: lint-imports (0.18s)
test-naming: src/basicly/_probe: no test file named after it
FAILED: test-naming (0.03s)
checks failed: 30/33 passed in 19.99s (failed: ruff, lint-imports, test-naming)
==> ruff
==> mermaid

catalog-lint.............................................................Passed
"""


def test_every_check_the_runner_failed_reaches_the_refusal() -> None:
    summary = checkout.gate_refusal(_THREE_FAILURES)
    assert summary is not None
    for check in ("FAILED: ruff", "FAILED: lint-imports", "FAILED: test-naming"):
        assert check in summary
    assert "checks failed: 30/33" in summary


def test_a_block_holding_only_warnings_still_names_what_refused() -> None:

    chain = (
        "pre-commit-script........................................................Failed\n"
        "- hook id: pre-commit-script\n"
        "[tester]\tWARNING\tnosec encountered (B603), but no failed test on file "
        ".basicly/core/hooks/catalog-lint.py:48\n"
        "Contracts: 3 kept, 0 broken.\n"
        "catalog-lint.............................................................Passed\n"
        "FAILED: release-notes (0.06s)\n"
        "checks failed: 32/33 passed in 21.10s (failed: release-notes)\n"
    )
    summary = checkout.gate_refusal(chain)
    assert summary is not None
    assert "FAILED: release-notes" in summary
    assert "checks failed: 32/33" in summary
