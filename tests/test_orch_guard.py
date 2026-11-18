from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "basicly-orch"))

hook = importlib.import_module("orch_guard.hook")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "project" / ".git").mkdir(parents=True)
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
