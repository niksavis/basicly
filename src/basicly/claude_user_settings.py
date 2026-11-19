from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .projection import atomic_write_text
from .user_skills import user_home

USER_SETTINGS = Path(".claude") / "settings.json"
ENV_KEY = "env"
CODE_MODE_VAR = "CLAUDE_CODE_EXPERIMENTAL_CODE_MODE"
CODE_MODE_ON = "1"
ADVISOR_KEY = "advisorModel"
DEFAULT_ADVISOR = ""


class ClaudeUserSettingsError(ValueError):
    pass


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClaudeUserSettingsError(
            f"{path} is not valid JSON, so nothing was written; fix the file and rerun"
        ) from exc
    if not isinstance(data, dict):
        raise ClaudeUserSettingsError(f"{path} does not hold a JSON object, so nothing was written")
    return data


def settings_path(home: str, environ: Mapping[str, str]) -> Path:
    if home:
        return Path(home) / USER_SETTINGS
    config_dir = environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / USER_SETTINGS.name
    return user_home(environ) / USER_SETTINGS


def apply(path: Path, *, advisor: str, dry_run: bool = False) -> list[str]:

    settings = _load(path)
    lines = []
    env = settings.get(ENV_KEY, {})
    if not isinstance(env, dict):
        raise ClaudeUserSettingsError(
            f"{path} holds a {ENV_KEY} value that is not a JSON object, so nothing was written"
        )
    env = dict(env)
    if CODE_MODE_VAR in env:
        lines.append(f"kept {CODE_MODE_VAR}={env[CODE_MODE_VAR]!r}: a value you set stays")
    else:
        env[CODE_MODE_VAR] = CODE_MODE_ON
        settings[ENV_KEY] = env
        lines.append(f"set {CODE_MODE_VAR}={CODE_MODE_ON!r} (Code Mode, experimental)")
    if ADVISOR_KEY in settings:
        lines.append(f"kept {ADVISOR_KEY}={settings[ADVISOR_KEY]!r}: a value you set stays")
    elif advisor:
        settings[ADVISOR_KEY] = advisor
        lines.append(f"set {ADVISOR_KEY}={advisor!r} (advisor tool, experimental)")
    if dry_run:
        return [f"would {line}" if line.startswith("set") else line for line in lines]
    if any(line.startswith("set") for line in lines):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(settings, indent=2) + "\n")
    return lines


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "claude-user",
        help="Turn on Code Mode, and optionally set the advisor, in ~/.claude/settings.json",
    )
    parser.add_argument(
        "--advisor",
        default=DEFAULT_ADVISOR,
        help="advisor model alias or id to set when none is set, such as opus; default sets none",
    )
    parser.add_argument("--home", default="", help="write under this home instead")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")


def cmd_claude_user(args: argparse.Namespace) -> int:

    path = settings_path(args.home, os.environ)
    try:
        lines = apply(path, advisor=args.advisor, dry_run=args.dry_run)
    except ClaudeUserSettingsError as exc:
        print(f"claude-user: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(f"claude-user: {line}")
    print(
        f"claude-user: {path} holds these keys because Claude Code ignores "
        f"{CODE_MODE_VAR} in project settings; restart Claude Code to apply them"
    )
    return 0
