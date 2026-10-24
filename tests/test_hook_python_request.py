from __future__ import annotations

import base64
from pathlib import Path

import yaml

from basicly import claude_settings, codex_hooks, lefthook_config
from basicly.hooks import HookSpec

REQUEST = "--python 3.14"
SHELL_METACHARACTERS = set("<>|&;`")
SPEC = HookSpec("gate", "gate.py", "pre-commit", always_run=True)
HOOKS_RELPATH = ".basicly/core/hooks"


def test_the_git_launcher_requests_the_supported_python() -> None:
    launcher = lefthook_config.render_git_launcher("pre-commit", Path(HOOKS_RELPATH), b"")

    assert REQUEST in launcher.decode("utf-8")


def test_the_lefthook_dispatcher_requests_the_supported_python() -> None:
    assert REQUEST in lefthook_config.managed_config([SPEC], HOOKS_RELPATH)


def test_lefthook_run_lines_carry_no_shell_metacharacters() -> None:
    config = yaml.safe_load(lefthook_config.managed_config([SPEC], HOOKS_RELPATH))
    runs = [
        command["run"]
        for stage in config.values()
        if isinstance(stage, dict)
        for command in stage.get("commands", {}).values()
    ]

    assert runs
    assert all(not SHELL_METACHARACTERS & set(run) for run in runs)


def test_the_claude_hook_command_requests_the_supported_python() -> None:
    assert REQUEST in claude_settings.HOOK_INTERPRETER


def test_both_codex_launchers_request_the_supported_python() -> None:
    encoded = codex_hooks.windows_command(f"{HOOKS_RELPATH}/lefthook-runtime.py", "pretooluse")
    script = base64.b64decode(encoded.rsplit(" ", 1)[1]).decode("utf-16-le")
    codex_spec = HookSpec("guard", "guard.py", "pretooluse", manager="codex", matcher="*")
    merged = codex_hooks.merge_hooks({}, [codex_spec], HOOKS_RELPATH)
    commands = [
        hook["command"]
        for groups in merged["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    ]

    assert REQUEST in script
    assert commands
    assert all(REQUEST in command for command in commands)
