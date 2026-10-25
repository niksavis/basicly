from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import affected, pyright_inputs, pytest_inputs
from .hook_runtime import load as _hook_runtime
from .runner import sanitised_project_env


def spawn(
    command: Sequence[str], repo_root: Path, *, capture: bool, controlled: str = ""
) -> subprocess.CompletedProcess[str]:

    command = _hook_runtime("command_paths").resolve_command(command, repo_root)
    env = sanitised_project_env(affected.environment(repo_root), repo_root)
    if controlled == "pyright":
        env = pyright_inputs.execution_environment(repo_root, env)
    elif controlled == "pytest":
        command, env = pytest_inputs.execution(repo_root, command, env)
    if capture:
        return subprocess.run(  # noqa: S603 — repo-declared argv, list form, no shell
            command,
            cwd=repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
        )
    with subprocess.Popen(  # noqa: S603 — same argv, same trust boundary
        command,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        bufsize=1,
    ) as proc:
        kept: list[str] = []
        for line in proc.stdout or ():
            sys.stdout.write(line)
            kept.append(line)
        sys.stdout.flush()
    return subprocess.CompletedProcess(list(command), proc.returncode, "".join(kept), "")
