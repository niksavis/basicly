"""Tests for the tool-usage counting hook (.basicly/core/hooks/tool-usage.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "tool-usage.py"
USAGE_FILE = Path(".basicly/usage/tool-usage.json")


def _run(payload: object, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _stats(cwd: Path) -> dict:
    return json.loads((cwd / USAGE_FILE).read_text(encoding="utf-8"))


def test_claude_payload_counts_every_pipeline_segment(tmp_path: Path) -> None:
    """A Claude PostToolUse Bash payload increments each segment head once."""
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rg -n foo src | jq '.x' && fd -e py"},
    }
    proc = _run(payload, tmp_path)
    assert proc.returncode == 0, proc.stderr

    stats = _stats(tmp_path)
    assert {tool: entry["count"] for tool, entry in stats.items()} == {"rg": 1, "jq": 1, "fd": 1}
    assert all(entry["last_used"] for entry in stats.values())
    # The usage dir ignores itself so the data never enters git.
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_copilot_payload_shape_is_counted(tmp_path: Path) -> None:
    """The Copilot postToolUse camelCase shape feeds the same counters."""
    payload = {"toolName": "bash", "toolArgs": {"command": "yq '.a' file.yaml"}}
    assert _run(payload, tmp_path).returncode == 0
    assert _stats(tmp_path)["yq"]["count"] == 1


def test_wrappers_count_wrapper_and_tool(tmp_path: Path) -> None:
    """`uv run pytest -q` counts uv and pytest; builtins and env vars are skipped."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "cd /tmp && FOO=1 uv run pytest -q; echo done"},
    }
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"uv": 1, "pytest": 1}


def test_skill_invocations_count_under_skill_prefix(tmp_path: Path) -> None:
    """A Claude Skill payload records a skill:<name> entry; bad shapes do not."""
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": "conventional-commits"},
    }
    assert _run(payload, tmp_path).returncode == 0
    assert _stats(tmp_path)["skill:conventional-commits"]["count"] == 1

    assert _run({"tool_name": "Skill", "tool_input": {}}, tmp_path).returncode == 0
    assert len(_stats(tmp_path)) == 1


def test_counts_accumulate_across_invocations(tmp_path: Path) -> None:
    """Counters survive between hook invocations (and thus between sessions)."""
    payload = {"tool_name": "Bash", "tool_input": {"command": "rg foo"}}
    _run(payload, tmp_path)
    _run(payload, tmp_path)
    assert _stats(tmp_path)["rg"]["count"] == 2


def test_non_shell_tools_and_garbage_never_fail(tmp_path: Path) -> None:
    """Edits, corrupt stdin, and a corrupt counter file all exit 0 quietly."""
    assert _run({"tool_name": "Edit", "tool_input": {"file_path": "x"}}, tmp_path).returncode == 0
    assert not (tmp_path / USAGE_FILE).exists()

    assert _run("not json at all", tmp_path).returncode == 0

    (tmp_path / USAGE_FILE).parent.mkdir(parents=True)
    (tmp_path / USAGE_FILE).write_text("{corrupt", encoding="utf-8")
    payload = {"tool_name": "Bash", "tool_input": {"command": "bat file"}}
    assert _run(payload, tmp_path).returncode == 0
    assert _stats(tmp_path)["bat"]["count"] == 1  # restarted clean


def test_heredoc_bodies_are_not_counted(tmp_path: Path) -> None:
    """Here-document content lines never register as tools (basicly-587)."""
    command = "python3 - <<'PYEOF'\nt = p.read_text()\n- bullet line\nassert t\nPYEOF\nrg foo"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"python3": 1, "rg": 1}
