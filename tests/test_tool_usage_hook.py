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


# --- Interactive tracker-surface ledger (basicly-vkh0.1) ----------------------

TRACKER_SPOOL = Path(".basicly/usage/tracker-usage.jsonl")


def _optin(cwd: Path) -> Path:
    """Opt the temp repo into tracker recording (the committed ledger dir is the switch)."""
    (cwd / ".basicly/ledger").mkdir(parents=True, exist_ok=True)
    return cwd


def _tracker(cwd: Path) -> list[dict]:
    raw = (cwd / TRACKER_SPOOL).read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def test_interactive_br_call_is_recorded_with_its_surface(tmp_path: Path) -> None:
    """The engine seam never sees a br call typed in a shell; this hook is that half."""
    _optin(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": "br list --json --limit 5"}}
    assert _run(payload, tmp_path).returncode == 0

    entries = _tracker(tmp_path)
    assert len(entries) == 1
    assert entries[0]["binary"] == "br"
    assert entries[0]["subcommand"] == "list"
    assert entries[0]["flags"] == ["--json", "--limit"]
    assert entries[0]["site"] == "interactive"
    # PostToolUse carries no timing, so the field is absent rather than zero.
    assert "duration_ms" not in entries[0]


def test_tracker_ledger_records_no_argument_values(tmp_path: Path) -> None:
    """A value is an issue title or a home directory; the ledger is committed."""
    _optin(tmp_path)
    command = 'br create "Fix the thing" -t bug --db=/home/someone/beads.db'
    _optin(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0

    raw = (tmp_path / TRACKER_SPOOL).read_text(encoding="utf-8")
    assert "someone" not in raw
    assert "Fix the thing" not in raw
    assert _tracker(tmp_path)[0]["flags"] == ["--db", "-t"]


def test_tracker_ledger_sees_a_call_behind_a_wrapper_and_in_a_pipeline(tmp_path: Path) -> None:
    """`uv run br ...` and a call after `&&` are both real usage."""
    _optin(tmp_path)
    command = "uv run br ready && br dep add a b | jq ."
    _optin(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0

    assert [(e["binary"], e["subcommand"]) for e in _tracker(tmp_path)] == [
        ("br", "ready"),
        ("br", "dep add"),
    ]


def test_tracker_ledger_sees_a_call_behind_a_wrapper_flag_value(tmp_path: Path) -> None:
    """`uv run --directory <worktree> br show ...` is real tracker usage.

    The unskipped flag value hid the head from the ledger too, so every lane's br
    calls were dropped from the surface measurement (basicly-m0p1).
    """
    _optin(tmp_path)
    command = "uv run --directory /repos/basicly.worktrees/basicly-m0p1 br show basicly-m0p1"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    assert [(e["binary"], e["subcommand"]) for e in _tracker(tmp_path)] == [("br", "show")]


def test_bv_is_recorded_alongside_br(tmp_path: Path) -> None:
    """Both binaries are in scope: the freeze covers the whole tracker surface."""
    _optin(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": "bv show basicly-1"}}
    assert _run(payload, tmp_path).returncode == 0
    assert _tracker(tmp_path)[0]["binary"] == "bv"


def test_a_non_tracker_command_writes_no_tracker_ledger(tmp_path: Path) -> None:
    """The spool exists only once there is tracker usage to record."""
    _optin(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": "rg -n foo src"}}
    assert _run(payload, tmp_path).returncode == 0
    assert not (tmp_path / TRACKER_SPOOL).exists()


def test_a_br_mention_inside_a_heredoc_is_not_usage(tmp_path: Path) -> None:
    """Heredoc bodies are data; counting them would inflate the surface with prose."""
    _optin(tmp_path)
    command = "cat <<'EOF'\nbr create should not count\nEOF\nbr ready"
    _optin(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    assert [e["subcommand"] for e in _tracker(tmp_path)] == ["ready"]
