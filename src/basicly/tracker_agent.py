from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

SESSION = "BASICLY_TRACKER_SESSION"


def _run(
    command: list[str], env: Mapping[str, str], *, cwd: Path | None = None, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — A resolved foreground executable or current interpreter receives literal argv; a shell would reinterpret client arguments.
        command,
        env=dict(env),
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        timeout=90 if capture else None,
        check=False,
    )


def write_launch(root: Path, *, version: str, tool: str = "basicly-tracker") -> Path:
    if tool not in {"basicly", "basicly-tracker"} or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Tracker bootstrap needs a released basicly or basicly-tracker version.")
    target = root / ".basicly/tracker-bootstrap.py"
    if (root / ".basicly").is_symlink() or target.is_symlink():
        raise ValueError("The tracker bootstrap path is a symlink; preserve it before onboarding.")
    if target.exists() and 'BOOTSTRAP_OWNER = "basicly-tracker"' not in target.read_text("utf-8"):
        raise ValueError(
            "The tracker bootstrap path holds an unmanaged file; preserve it before onboarding."
        )
    source = (
        f"https://github.com/niksavis/basicly/releases/download/v{version}/"
        f"basicly-{version}-py3-none-any.whl"
        if tool == "basicly"
        else f"git+https://github.com/niksavis/basicly@v{version}#subdirectory=packages/basicly-tracker"
    )
    here = Path(__file__).resolve().parent
    template = next(
        (
            p
            for p in (here / "tracker_bootstrap_launch.py", here / "bootstrap_launch.py")
            if p.is_file()
        ),
        None,
    )
    if template is None:
        raise ValueError("The tracker bootstrap template is missing; reinstall the tracker.")
    text = template.read_text("utf-8")
    if text.count('SOURCE = ""') != 1 or text.count('TOOL = "basicly-tracker"') != 1:
        raise ValueError("The tracker bootstrap template is invalid; reinstall the tracker.")
    literal = json.dumps(source)
    source_line = f"SOURCE = {literal}"
    if len(source_line) > 100:
        source_line = f"SOURCE = (\n    {literal}\n)"
    text = text.replace('SOURCE = ""', source_line)
    text = text.replace('TOOL = "basicly-tracker"', f"TOOL = {json.dumps(tool)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text("utf-8") != text:
        target.write_text(text, encoding="utf-8")
    return target


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


def memory_environment(repository: Path, kit: Path, env: Mapping[str, str]) -> dict[str, str]:
    script = kit / "bootstrap.py"
    if not script.is_file():
        raise ValueError(
            "Tracker startup is unavailable. Reinstall basicly-tracker with uv and run "
            "repository onboarding before starting implementation."
        )
    done = _run(
        [sys.executable, str(script), "--repository", str(repository)],
        cwd=repository,
        env=env,
        capture=True,
    )
    if done.returncode != 0:
        raise ValueError(
            done.stderr.strip() or "Tracker bootstrap refused startup; run it directly."
        )
    report = json.loads(done.stdout)
    if report.get("ready") is not True:
        raise ValueError("Tracker bootstrap established no memory. Run repository onboarding.")
    return {
        **env,
        "BASICLY_TRACKER_REPOSITORY": str(report["repository"]),
        "BASICLY_TRACKER_MEMORY": str(report["memory"]),
    }


def main(argv: list[str], *, kit: Path) -> int:
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
        env = memory_environment(Path.cwd(), kit, env)
        done = _run([executable, *command[1:]], env)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"tracker: {exc}\n")
        return 2
    else:
        return done.returncode
