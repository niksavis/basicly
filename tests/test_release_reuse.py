from __future__ import annotations

import importlib.util
import subprocess  # nosec B404
import sys
from pathlib import Path
from types import ModuleType

from basicly import release

SCRIPT = Path(__file__).resolve().parent.parent / ".scripts" / "release_reuse.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_reuse", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_reuse"] = module
    spec.loader.exec_module(module)
    return module


reuse = _load()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, path: str, text: str, subject: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.invalid")
    green = _commit(repo, "src/app.py", "VALUE = 1\n", "feat: value")
    return repo, green


def _release(repo: Path, path: str = release.VERSION_FILE.as_posix()) -> None:
    _commit(repo, path, '__version__ = "1.0.1"\n', release.COMMIT_SUBJECT)
    _git(repo, "tag", "v1.0.1")


def test_a_release_on_top_of_green_ci_and_ledger_commits_reuses_it(tmp_path: Path) -> None:
    repo, green = _repo(tmp_path)
    _commit(repo, ".basicly/ledger/pending-main.jsonl", "{}\n", "chore(tracker): note")
    _commit(repo, "CHANGELOG.md", "# Changelog\n", "docs(changelog): summary")
    _release(repo)

    ok, reason = reuse.reusable(repo, "v1.0.1", green=lambda sha: sha == green)

    assert ok, reason


def test_a_code_commit_after_the_green_run_needs_the_full_checks(tmp_path: Path) -> None:
    repo, green = _repo(tmp_path)
    _commit(repo, "src/app.py", "VALUE = 2\n", "fix: value")
    _release(repo)

    ok, reason = reuse.reusable(repo, "v1.0.1", green=lambda sha: sha == green)

    assert not ok
    assert "changes more than the ledger" in reason


def test_a_merge_after_the_green_run_needs_the_full_checks(tmp_path: Path) -> None:
    repo, green = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "src/side.py", "SIDE = 1\n", "feat: side")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, ".basicly/ledger/pending-main.jsonl", "{}\n", "chore(tracker): note")
    _git(repo, "merge", "-q", "--no-ff", "-m", "chore: merge", "side")
    _release(repo)

    ok, _ = reuse.reusable(repo, "v1.0.1", green=lambda sha: sha == green)

    assert not ok


def test_a_tag_on_any_other_commit_needs_the_full_checks(tmp_path: Path) -> None:
    repo, green = _repo(tmp_path)
    _git(repo, "tag", "v1.0.1")

    ok, reason = reuse.reusable(repo, "v1.0.1", green=lambda sha: sha == green)

    assert not ok
    assert "does not point at a release commit" in reason


def test_a_release_commit_that_changes_code_needs_the_full_checks(tmp_path: Path) -> None:
    repo, green = _repo(tmp_path)
    _release(repo, "src/basicly/cli.py")

    ok, reason = reuse.reusable(repo, "v1.0.1", green=lambda sha: sha == green)

    assert not ok
    assert "src/basicly/cli.py" in reason
