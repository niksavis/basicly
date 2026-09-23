from __future__ import annotations

import io
import itertools
import shutil
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

VENDORED = Path(".basicly") / "kit" / "tracker"
LEDGER = Path(".basicly") / "ledger"

FOREIGN = '#!/bin/sh\necho "somebody else was here"\n'

_loaded = itertools.count()


def _consumer(root: Path) -> Path:

    shutil.copytree(
        REPO_ROOT / KIT_RELATIVE, root / VENDORED, ignore=shutil.ignore_patterns("__pycache__")
    )
    (root / LEDGER).mkdir(parents=True)
    return root / VENDORED


def _module(root: Path):
    return _load(_consumer(root) / "install_hook.py", f"kit_hook_test_{next(_loaded)}")


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def hook(repo: Path):
    return _module(repo)


def _install(hook, root: Path) -> str:
    stream = io.StringIO()
    assert (
        hook.install(root, ledger=root / LEDGER, dry_run=False, interpreter="RUNNER", stream=stream)
        == 0
    )
    return stream.getvalue()


def _uninstall(hook, root: Path) -> str:
    stream = io.StringIO()
    assert hook.uninstall(root, dry_run=False, stream=stream) == 0
    return stream.getvalue()


def _hook_file(hook, root: Path) -> Path:
    return root / ".git" / "hooks" / hook.HOOK_NAME


def test_a_fresh_checkout_gets_a_hook_that_folds_shards(hook, repo: Path) -> None:
    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert text.startswith(hook.SHEBANG)
    assert "compact" in text
    assert hook.BEGIN in text and hook.END in text


def test_the_hook_carries_no_absolute_path_or_bare_python(hook, repo: Path) -> None:
    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert str(repo) not in text, "an absolute path breaks the next clone"
    assert "python3 " not in text, (
        "a bare python3 on Windows hits an execution alias that opens a store page"
    )
    assert "RUNNER" in text, "the control: the declared interpreter is what runs the kit"


def test_the_hook_does_nothing_unless_the_ledger_is_the_only_dirt(hook, repo: Path) -> None:
    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert ":(exclude).basicly/ledger" in text, (
        "without this guard the hook rewrites a developer's tree on every pull: it "
        "deletes shards and modifies the trunk log they never touched"
    )


def test_an_existing_hook_is_added_to_rather_than_replaced(hook, repo: Path) -> None:
    _hook_file(hook, repo).write_text(FOREIGN, encoding="utf-8")

    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert "somebody else was here" in text
    assert hook.BEGIN in text


def test_a_second_install_changes_nothing(hook, repo: Path) -> None:
    _install(hook, repo)
    once = _hook_file(hook, repo).read_text(encoding="utf-8")

    said = _install(hook, repo)

    assert _hook_file(hook, repo).read_text(encoding="utf-8") == once
    assert "already folds" in said


def test_uninstall_removes_only_the_block_it_wrote(hook, repo: Path) -> None:
    _hook_file(hook, repo).write_text(FOREIGN, encoding="utf-8")
    _install(hook, repo)

    _uninstall(hook, repo)

    assert _hook_file(hook, repo).read_text(encoding="utf-8") == FOREIGN


def test_uninstall_removes_a_hook_that_held_nothing_else(hook, repo: Path) -> None:
    _install(hook, repo)

    _uninstall(hook, repo)

    assert not _hook_file(hook, repo).exists()


def test_a_hook_this_kit_never_wrote_is_left_alone(hook, repo: Path) -> None:
    _hook_file(hook, repo).write_text(FOREIGN, encoding="utf-8")

    said = _uninstall(hook, repo)

    assert _hook_file(hook, repo).read_text(encoding="utf-8") == FOREIGN
    assert "left alone" in said


def test_a_declared_hooks_path_is_honoured(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\thooksPath = .githooks\n", encoding="utf-8"
    )
    hook = _module(tmp_path)

    assert hook.hooks_dir(tmp_path) == tmp_path / ".githooks", (
        "a repository that moved its hooks would otherwise get a file nothing runs"
    )


def test_a_linked_worktree_resolves_to_the_real_git_directory(tmp_path: Path) -> None:
    real = tmp_path / "main" / ".git"
    (real / "hooks").mkdir(parents=True)
    linked = tmp_path / "wt"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {real / 'worktrees' / 'wt'}\n", encoding="utf-8")
    hook = _module(linked)

    found = hook.hooks_dir(linked)

    assert found is not None
    assert found.name == "hooks"


def test_a_directory_that_is_not_a_checkout_is_reported_not_raised(tmp_path: Path) -> None:
    hook = _module(tmp_path)
    stream = io.StringIO()

    assert (
        hook.install(
            tmp_path,
            ledger=tmp_path / LEDGER,
            dry_run=False,
            interpreter="RUNNER",
            stream=stream,
        )
        == 0
    )

    assert "not a git checkout" in stream.getvalue()


def test_a_dry_run_writes_nothing(hook, repo: Path) -> None:
    stream = io.StringIO()

    assert (
        hook.install(
            repo,
            ledger=repo / "ledger",
            dry_run=True,
            interpreter="RUNNER",
            stream=stream,
        )
        == 0
    )

    assert not _hook_file(hook, repo).exists()
    assert "would write" in stream.getvalue()


def test_a_host_command_replaces_the_kit_call(hook, repo: Path) -> None:
    stream = io.StringIO()

    assert (
        hook.install(
            repo,
            ledger=repo / LEDGER,
            dry_run=False,
            interpreter="RUNNER",
            stream=stream,
            command="basicly tracker fold",
        )
        == 0
    )

    written = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert "basicly tracker fold" in written
    assert "RUNNER" not in written
    assert "git commit" not in written


def test_a_host_command_reports_a_failed_fold(hook, repo: Path) -> None:
    hook.install(
        repo,
        ledger=repo / LEDGER,
        dry_run=False,
        interpreter="RUNNER",
        stream=io.StringIO(),
        command="basicly tracker fold",
    )

    written = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert "are not folded" in written
    assert "`" not in written


def test_install_creates_a_missing_ledger(hook, repo: Path) -> None:
    (repo / LEDGER).rmdir()

    assert "created the ledger" in _install(hook, repo)
    assert (repo / LEDGER).is_dir()


def test_a_dry_run_names_the_missing_ledger_and_creates_nothing(hook, repo: Path) -> None:
    (repo / LEDGER).rmdir()
    stream = io.StringIO()

    hook.install(repo, ledger=repo / LEDGER, dry_run=True, interpreter="RUNNER", stream=stream)

    assert "would create the ledger" in stream.getvalue()
    assert not (repo / LEDGER).exists()
