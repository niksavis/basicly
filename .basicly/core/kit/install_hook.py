"""Install the tier injection hook into the host that will actually run it.

The deliberate opt-in step of the portable kit (basicly-wbsz.3). The two files
beside this one answer *which model* a tier means and *how* to rewrite a spawn;
this one wires the second into a host's settings, under the same constraint as
both: **no basicly**. No ``import basicly``, nothing on ``PATH``, no third-party
package, no network. A consumer who copied the kit into an unrelated project
runs this and is done.

Run it::

    python3 install_hook.py                 # this repository's own settings
    python3 install_hook.py --user          # every repository on this machine
    python3 install_hook.py --dry-run       # print what it would write

**It is asymmetric by host, and says so rather than pretending otherwise.**

- **Claude Code**: installs. A ``PreToolUse`` hook matching the ``Agent`` tool,
  written into ``.claude/settings.json`` — the repository's by default, because a
  repo-scoped harness should not change how every other project on the machine
  spawns agents. ``--user`` is the deliberate opt-in to that wider scope, and is
  safe because ``claude_tier_hook.py`` only answers for a directory tree that has
  its own committed map.
- **GitHub Copilot CLI**: installs **nothing**, and reports why. Copilot cannot
  intercept a spawn today: a repo-level ``.github/hooks/*.json`` hook never fired
  across three probes (basicly-wbsz), and on 1.0.77 there is no hook surface at
  all — no ``hooks`` directory under ``~/.copilot``, no hook key in its
  ``settings.json``, no hook option in ``--help`` (measured on basicly-wbsz.3).
  Copilot's working path is static frontmatter plus session-level
  ``copilot --model``. Reporting a successful install for a hook that will never
  fire would be worse than declining.

Re-running converges: a group running this kit's hook is stripped and rewritten
rather than appended, so the second run changes nothing and duplicates nothing.
Hooks the consumer wrote themselves are matched by the script they run, never by
position, and are left untouched.

A ``settings.json`` that exists but cannot be parsed is **refused, never
overwritten** — it is the consumer's file and a mangled one is a worse outcome
than an uninstalled hook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HOOK_FILENAME = "claude_tier_hook.py"

# Where each host keeps the settings this installer writes, relative to the
# scope root. Claude is the only entry today; the shape is a table rather than a
# branch so a host that grows a hook surface is a row, not a rewrite.
CLAUDE_SETTINGS = Path(".claude") / "settings.json"

# Honoured so a caller can dictate the user-level location instead of this
# module guessing it per platform — which is also what makes the user-scope path
# testable without touching the developer's own configuration.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
USER_CONFIG_DIRNAME = ".claude"

HOOK_EVENT = "PreToolUse"
HOOK_MATCHER = "Agent"
HOOKS_KEY = "hooks"

# Hosts this kit knows about but cannot install for, and the measured reason.
# Kept as data so `--host copilot` gets an answer rather than an unknown-host
# error, which would read as "you typed it wrong" instead of "it cannot work".
CANNOT_INTERCEPT = {
    "copilot": (
        "the Copilot CLI exposes no hook surface that fires for a spawn "
        "(repo-level .github/hooks never fired across three probes, and 1.0.77 "
        "has no hooks directory, no hook setting and no hook option); use static "
        "frontmatter plus `copilot --model` instead"
    ),
}

HOSTS = ("claude", *sorted(CANNOT_INTERCEPT))


def hook_command(hook_path: Path, interpreter: str | None = None) -> str:
    """The shell command the host runs for one spawn.

    Both halves are absolute and forward-slashed. A relative path would break the
    moment a spawn happened in a subdirectory, and a backslash would be eaten by
    the shell that runs this — the reason the kit's own tests pin the rendering
    rather than the platform.
    """
    python = Path(interpreter or sys.executable).as_posix()
    return f'"{python}" "{Path(hook_path).resolve().as_posix()}"'


def settings_path(root: Path, *, user: bool) -> Path:
    """The settings file to write for the requested scope."""
    if not user:
        return Path(root) / CLAUDE_SETTINGS
    configured = os.environ.get(CONFIG_DIR_ENV, "").strip()
    base = Path(configured) if configured else Path.home() / USER_CONFIG_DIRNAME
    return base / CLAUDE_SETTINGS.name


def load_settings(path: Path) -> dict:
    """The settings at *path*, or an empty mapping when there are none yet.

    Raises:
        ValueError: when the file exists but is not a JSON object. Refusing is
            the point — this is the consumer's file, and overwriting it to
            install a convenience would be the worse failure.
    """
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{path} is not valid JSON ({error}); refusing to overwrite it") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} does not contain a JSON object; refusing to overwrite it")
    return parsed


def _runs_our_hook(group: object) -> bool:
    """Whether one hook group runs this kit's hook.

    Matched by the hook's filename inside the command, which is what makes a
    re-run converge. A consumer's own hook is left alone because it does not run
    this script.
    """
    if not isinstance(group, dict):
        return False
    for hook in group.get(HOOKS_KEY) or []:
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str) and HOOK_FILENAME in command:
                return True
    return False


def merge_hook(settings: dict, command: str) -> dict:
    """Settings with this kit's hook installed exactly once.

    Strips any group already running the kit's hook and appends a fresh one, so
    the result is the same whether it is the first run or the fifth, and a
    changed interpreter path replaces the old entry instead of racing it.
    """
    merged = dict(settings)
    section = merged.get(HOOKS_KEY)
    section = dict(section) if isinstance(section, dict) else {}
    existing = section.get(HOOK_EVENT)
    kept = (
        [group for group in existing if not _runs_our_hook(group)]
        if isinstance(existing, list)
        else []
    )
    kept.append({"matcher": HOOK_MATCHER, HOOKS_KEY: [{"type": "command", "command": command}]})
    section[HOOK_EVENT] = kept
    merged[HOOKS_KEY] = section
    return merged


def install_claude(root: Path, *, user: bool, dry_run: bool) -> tuple[bool, str]:
    """Install the hook for Claude Code; return ``(installed, message)``."""
    hook = Path(__file__).resolve().parent / HOOK_FILENAME
    if not hook.is_file():
        return False, f"claude: {hook} is missing, so there is no hook to install"
    path = settings_path(root, user=user)
    scope = "user" if user else "project"
    current = load_settings(path)
    updated = merge_hook(current, hook_command(hook))
    if updated == current:
        return True, f"claude: already installed ({scope} scope) in {path}"
    if dry_run:
        return True, f"claude: would write the {HOOK_EVENT}/{HOOK_MATCHER} hook to {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    return True, f"claude: wrote the {HOOK_EVENT}/{HOOK_MATCHER} hook ({scope} scope) to {path}"


def install(hosts: list[str], root: Path, *, user: bool, dry_run: bool) -> tuple[bool, list[str]]:
    """Install for each requested host; return ``(any_installed, report lines)``."""
    lines = []
    installed = False
    for host in hosts:
        reason = CANNOT_INTERCEPT.get(host)
        if reason is not None:
            lines.append(f"{host}: nothing installed - {reason}")
            continue
        ok, message = install_claude(root, user=user, dry_run=dry_run)
        installed = installed or ok
        lines.append(message)
    return installed, lines


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the tier injection hook into a coding agent's settings."
    )
    parser.add_argument(
        "--host",
        action="append",
        choices=HOSTS,
        help="host to install for; repeatable (default: every known host)",
    )
    parser.add_argument(
        "--user",
        action="store_true",
        help="install for every repository on this machine instead of just this one",
    )
    parser.add_argument("--root", help="repository to install into (default: cwd)")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be written and write nothing"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Install, report what happened per host, and exit non-zero if nothing did.

    The exit status is the part a script can branch on: a run that only declined
    must not look like a run that installed.
    """
    args = _parse_args(argv)
    root = Path(args.root) if args.root else Path.cwd()
    try:
        installed, lines = install(
            list(args.host or HOSTS), root, user=args.user, dry_run=args.dry_run
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    if not installed:
        print("nothing was installed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
