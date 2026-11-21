from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))
ZERO = "0" * 40


def _load(path: Path, name: str) -> ModuleType:
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = _load(ROOT / ".basicly/core/hooks/lefthook-runtime.py", "basicly_lefthook_runtime_test")
note = _load(ROOT / ".basicly/core/kit/orch/orch_state/note.py", "basicly_orch_note_test")


def _git(repo: Path, *args: str) -> str:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=clean
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "one")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    pushed = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "two")
    return repo, pushed, _git(repo, "rev-parse", "HEAD")


def test_a_push_that_only_deletes_or_adds_no_commit_runs_no_pre_push_job(tmp_path: Path) -> None:
    repo, pushed, _new = _repo(tmp_path)
    delete = f"(delete) {ZERO} refs/heads/old {pushed}\n"
    same = f"refs/heads/main {pushed} refs/heads/copy {ZERO}\n"

    assert runtime.adds_commits(repo, delete) is False
    assert runtime.adds_commits(repo, same) is False


def test_a_push_with_a_new_commit_runs_the_pre_push_jobs(tmp_path: Path) -> None:
    repo, pushed, new = _repo(tmp_path)

    assert runtime.adds_commits(repo, f"refs/heads/main {new} refs/heads/main {pushed}\n")
    assert runtime.adds_commits(repo, "an unreadable line\n")
    assert runtime.adds_commits(repo, "")


def _orchestrator_store(root: Path) -> None:
    lessons = root / ".orchestrator" / "lessons"
    lessons.mkdir(parents=True)
    marker = importlib.import_module("orch_lessons.log").MARKER
    (lessons / marker).write_text("", encoding="utf-8")


def test_a_note_writes_item_and_last_only_inside_an_orchestrator_store(tmp_path: Path) -> None:
    _orchestrator_store(tmp_path)
    env = {"ORCH_SESSION_ID": "s1"}

    assert note.note(tmp_path, env, item="acme-1", last="CI red", ok=False)
    saved = json.loads((tmp_path / ".orchestrator/sessions/s1.json").read_text(encoding="utf-8"))
    assert (saved["item"], saved["last"], saved["last_ok"]) == ("acme-1", "CI red", False)
    assert note.note(tmp_path / "elsewhere", {}, last="x") is False
