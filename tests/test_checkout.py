"""Tests for where a checkout stands in git's worktree layout, and for what refused.

Driven against a real git repo rather than a stubbed ``git``: every answer here
is a reading of ``rev-parse``/``worktree list`` output, so a fake would be
asserting this module's idea of git rather than git's.

The refusal fixtures below hold to the same rule and are **observed**: the chain
text is what ``uv run pre-commit run --all-files`` printed in this worktree on
2026-08-22. An invented fixture is how a parser comes to key on text its producer
never emits.
"""

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
    """A real git repo (named ``repo``) with one commit on ``main``."""
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
    """The sibling worktrees root is ``<repo>.worktrees`` next to the checkout."""
    assert checkout.main_checkout(git_repo) == git_repo
    assert checkout.worktrees_root(git_repo).name == "repo.worktrees"
    assert checkout.worktrees_root(git_repo).parent == git_repo.parent


def _identity(repo: Path) -> str:
    return (repo / ".git" / "config").read_text(encoding="utf-8")


def test_a_poisoned_git_dir_really_does_outrank_cwd(git_repo: Path, tmp_path: Path) -> None:
    """Positive control for the two below: without it they cannot tell a fix from a no-op.

    A raw ``subprocess`` git carrying ``GIT_DIR`` writes into the repository the variable
    names and ignores the ``cwd`` it was handed, which is the whole incident in one call.
    """
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
    """Every engine git call goes through :func:`checkout.run`, so the scrub goes there.

    The harness runs from git hooks, and a hook in a linked worktree is handed a
    ``GIT_DIR`` (basicly-e2mz.16) — under which ``cwd=`` decides nothing at all.
    """
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)
    monkeypatch.setenv("GIT_DIR", str(git_repo / ".git"))

    checkout.git(["config", "user.name", "from-cwd"], cwd=other)

    assert "from-cwd" in _identity(other)
    assert _identity(git_repo) == before


def test_an_explicit_env_is_scrubbed_too(git_repo: Path, tmp_path: Path) -> None:
    """The *env=* branch is scrubbed as well, and it is not a hypothetical one.

    ``release`` builds its env as ``dict(os.environ)`` plus a PYTHONPATH, so an ambient
    ``GIT_DIR`` reaches the child by that path even with the inherited branch covered.
    """
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
    """A linked worktree reports True; the primary checkout and a non-repo False.

    The linked tree is added with plain ``git worktree add`` rather than through
    ``worktree.create``: the distinction under test is git's own, so nothing the
    harness does to provision a tree should be able to affect the answer.
    """
    linked = git_repo.parent / "linked"
    _git(git_repo, "worktree", "add", str(linked), "-b", "harness/linked")

    assert checkout.is_linked_checkout(linked) is True
    assert checkout.is_linked_checkout(git_repo) is False
    assert checkout.is_linked_checkout(git_repo.parent) is False  # not a repo


def test_names_in_reads_what_a_ref_holds_under_a_directory(tmp_path: Path) -> None:
    """The reader behind `release-notes` naming a fragment the base branch already has."""
    checkout.git(["init", "-q", "-b", "probe"], cwd=tmp_path)
    checkout.git(["config", "user.email", "probe@example.invalid"], cwd=tmp_path)
    checkout.git(["config", "user.name", "probe"], cwd=tmp_path)
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "demo-1.added.md").write_text("- x\n", encoding="utf-8")
    checkout.git(["add", "-A"], cwd=tmp_path)
    checkout.git(["commit", "-qm", "seed"], cwd=tmp_path)

    assert checkout.names_in("probe", "changelog.d", cwd=tmp_path) == ("demo-1.added.md",)


def test_names_in_is_empty_where_the_question_cannot_be_asked(tmp_path: Path) -> None:
    """No git, no such ref and no such directory are one answer: nothing to report.

    A raise here would make an absent remote fatal for every caller asking what another
    branch holds, which is the common case in a fresh clone.
    """
    assert checkout.names_in("no-such-ref", "changelog.d", cwd=tmp_path) == ()


# The observed shape of a refusal: pre-commit reports the chain on stdout and something
# else — here `uv`, always in this repo — warns on stderr. Trimmed from the real run.
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
    """An argv that exits 1 after writing *out* to stdout and *err* to stderr.

    A real subprocess through :func:`checkout.run` rather than a hand-built
    ``CompletedProcess``: the defect was in which stream the wrapper read, so a fake that
    supplies both streams itself would assert the fake's idea of the split.
    """
    script = tmp_path / "refuse.py"
    script.write_text(
        f"import sys\nsys.stdout.write({out!r})\nsys.stderr.write({err!r})\nsys.exit(1)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_a_hook_refusal_names_the_check_and_not_the_argv(tmp_path: Path) -> None:
    """The reported defect: three lane closes named no check (basicly-fi1i7z).

    The warning on stderr is the whole regression. ``stderr or stdout`` preferred it and
    discarded the chain, so the only text an operator got was a `uv` warning about a
    virtualenv — and something writes to stderr on every run in this repo.
    """
    with pytest.raises(RuntimeError) as raised:
        checkout.run(_two_stream_failure(tmp_path, _CHAIN, _WARNING), cwd=tmp_path)

    message = str(raised.value)
    assert "markdownlint" in message
    assert "MD018" in message
    # The check that ran last passed; quoting it is what sent a reader to audit it.
    assert "Passed" not in message
    assert "protect-generated-commit" not in message


def test_a_failure_that_ran_no_hooks_keeps_the_plain_wording(tmp_path: Path) -> None:
    """Most of this module's traffic is ``rev-parse``; it has no check to name."""
    argv = _two_stream_failure(tmp_path, "", "fatal: not a git repository\n")
    with pytest.raises(RuntimeError) as raised:
        checkout.run(argv, cwd=tmp_path)

    message = str(raised.value)
    assert "command failed (1)" in message
    assert "fatal: not a git repository" in message


# This repo's own hook wraps the whole verify suite, so its block ends on a list of check
# names while the answer sits earlier. The tail heuristic this refuted was the first design.
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
    """Two hooks passed after the failure in `_CHAIN`; neither is a refusal."""
    assert [r.check for r in checkout.refusals(_CHAIN)] == ["markdownlint"]


def test_the_reason_is_the_stated_verdict_not_the_tail_of_the_block() -> None:
    """A hook that wraps a suite states its verdict before it lists what it ran."""
    (refusal,) = checkout.refusals(_SUITE_CHAIN)
    assert refusal.check == "pre-commit-script"
    assert "checks failed: 28/32" in refusal.reason
    assert "==> mermaid" not in refusal.reason


def test_output_with_no_hook_chain_is_left_to_its_caller() -> None:
    """None is "not my question", and is distinct from the admission below."""
    assert checkout.ran_hooks("fatal: not a git repository") is False
    assert checkout.gate_refusal("fatal: not a git repository") is None


def test_an_unidentifiable_refusal_says_so_and_names_where_the_output_went(
    tmp_path: Path,
) -> None:
    """The honest fallback: a chain ran, nothing in it failed identifiably."""
    summary = checkout.gate_refusal(_ONLY_PASSES, repo_root=tmp_path)
    assert summary is not None
    assert "names no failing check" in summary
    assert checkout.GATE_OUTPUT_DUMP.as_posix() in summary
    assert (tmp_path / checkout.GATE_OUTPUT_DUMP).read_text(encoding="utf-8") == _ONLY_PASSES


def test_the_dump_is_not_named_when_it_could_not_be_written() -> None:
    """A message naming no path is degraded; losing the refusal to an OSError is not."""
    summary = checkout.gate_refusal(_ONLY_PASSES)
    assert summary is not None
    assert "it was not captured" in summary
    assert checkout.GATE_OUTPUT_DUMP.as_posix() not in summary


def test_a_reformatting_hook_reports_the_only_line_it_printed() -> None:
    """A formatter fails with this annotation and frequently no output at all."""
    chain = (
        "ruff-format..............................................................Failed\n"
        "- hook id: ruff-format\n"
        "- files were modified by this hook\n"
    )
    (refusal,) = checkout.refusals(chain)
    assert refusal.reason == "files were modified by this hook"


# Captured from a real refused `git commit` in this repo on 2026-08-27 by staging a module
# ruff, lint-imports and test-naming all reject, and reading `subprocess` streams the way
# `checkout.run` does. Trimmed to the verdict lines and one line of each check's own
# output; the ordering is the observation — git sends every hook stream to stderr, and the
# runner's `==> <check>` progress lines land after its `checks failed:` summary because
# they are on its block-buffered stdout while the summary is on its stderr.
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
    """`_REASON_LINES` kept the last three stated lines, so the first failure fell off."""
    summary = checkout.gate_refusal(_THREE_FAILURES)
    assert summary is not None
    for check in ("FAILED: ruff", "FAILED: lint-imports", "FAILED: test-naming"):
        assert check in summary
    assert "checks failed: 30/33" in summary


def test_a_block_holding_only_warnings_still_names_what_refused() -> None:
    """The shape that cost basicly-j7spdb a re-dispatch: bandit's warning as the reason.

    Which block the runner's verdict lines land in is an artefact of two streams
    interleaving, so the reader must not depend on it — here they sit past the next
    hook's verdict line, where the block reader cannot see them at all.
    """
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
