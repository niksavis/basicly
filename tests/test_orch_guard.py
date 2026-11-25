from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".basicly" / "core" / "kit" / "orch"))

hook = importlib.import_module("orch_guard.hook")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for folder in (tmp_path, tmp_path / "project"):
        (folder / ".git").mkdir(parents=True)
        (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return tmp_path


def event(workspace: Path, path: Path, **extra: object) -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path)},
        "cwd": str(workspace),
        "agent_type": "orchestrator",
        **extra,
    }


def test_refuses_an_orchestrator_edit_inside_a_project_repository(workspace: Path) -> None:
    message = hook.refusal(event(workspace, workspace / "project" / "src" / "app.py"))
    assert message is not None
    assert "belongs to the project repository" in message


@pytest.mark.parametrize(
    "change",
    [
        {"agent_type": None},
        {"agent_type": "reviewer"},
        {"agent_id": "a1"},
        {"tool_name": "Read"},
    ],
    ids=["no-agent", "other-agent", "subagent", "read-tool"],
)
def test_allows_everything_but_an_orchestrator_main_session_write(
    workspace: Path, change: dict[str, object]
) -> None:
    assert hook.refusal(event(workspace, workspace / "project" / "app.py", **change)) is None


def test_allows_an_orchestrator_write_in_its_own_workspace(workspace: Path) -> None:
    assert hook.refusal(event(workspace, workspace / "ARCHITECTURE.md")) is None


def test_allows_a_write_under_an_empty_git_folder_that_is_no_repository(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    cache = tmp_path_factory.mktemp("cache")
    (cache / ".git").mkdir()
    assert hook.refusal(event(workspace, cache / "orchestrator" / "prompt.md")) is None


def test_refuses_a_write_inside_a_linked_worktree_of_a_project(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    lane = tmp_path_factory.mktemp("lane")
    (lane / ".git").write_text("gitdir: /elsewhere/.git/worktrees/lane\n", encoding="utf-8")
    message = hook.refusal(event(workspace, lane / "src" / "app.py"))
    assert message is not None
    assert str(lane) in message
