from __future__ import annotations

import json
from pathlib import Path

from basicly import claude_settings

HOOKS = ".basicly/core/hooks"


def _entry(script: str) -> dict:
    return {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": f'python3 "${{CLAUDE_PROJECT_DIR}}/{script}"'}],
    }


def _settings(repo: Path, *scripts: str) -> Path:
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    hooks = {"PreToolUse": [_entry(script) for script in scripts]}
    path.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return path


def _commands(path: Path) -> str:
    return json.dumps(json.loads(path.read_text(encoding="utf-8")).get("hooks", {}))


def test_build_drops_an_entry_whose_managed_script_is_gone(tmp_path: Path) -> None:
    (tmp_path / HOOKS).mkdir(parents=True)
    (tmp_path / HOOKS / "kept-guard.py").write_text("", encoding="utf-8")
    path = _settings(tmp_path, f"{HOOKS}/gone-guard.py", f"{HOOKS}/kept-guard.py")

    claude_settings.sync_agent_hooks(tmp_path, [], HOOKS)

    assert "gone-guard.py" not in _commands(path)
    assert "kept-guard.py" in _commands(path)


def test_check_names_an_entry_whose_managed_script_is_gone(tmp_path: Path) -> None:
    (tmp_path / HOOKS).mkdir(parents=True)
    _settings(tmp_path, f"{HOOKS}/gone-guard.py")

    found = claude_settings.agent_hook_mismatches(tmp_path, [], HOOKS)

    assert found == [f"hook script {HOOKS}/gone-guard.py is missing"]
