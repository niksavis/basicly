from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

SESSION = "BASICLY_TRACKER_SESSION"


def environment(base: Mapping[str, str], session: str | None = None) -> dict[str, str]:
    chosen = f"agent-{uuid.uuid4().hex}" if session is None else session
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", chosen):
        raise ValueError("--session must use letters, digits, dots, underscores or hyphens.")
    return {**base, SESSION: chosen, "AI_AGENT": base.get("AI_AGENT") or "basicly-tracker"}


def _executable(command: str, env: Mapping[str, str]) -> str:
    executable = shutil.which(command, path=env.get("PATH"))
    if executable is None:
        raise ValueError("The agent executable is not on PATH. Pass its executable path.")
    if executable.lower().endswith((".bat", ".cmd")):
        raise ValueError("Pass the agent executable or interpreter instead of a shell shim.")
    return str(Path(executable).absolute())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Launch a coding agent with its own tracker session."
    )
    parser.add_argument("--session", help="An explicit existing session for an intentional resume.")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("name the agent executable after --, as in agent -- codex")
    try:
        env = environment(os.environ, args.session)
        executable = _executable(command[0], env)
        done = subprocess.run([executable, *command[1:]], env=env, check=False)  # noqa: S603 — A validated foreground executable receives literal argv and a copied environment.
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"tracker: {exc}\n")
        return 2
    else:
        return done.returncode
