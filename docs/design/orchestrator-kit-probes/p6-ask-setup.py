from __future__ import annotations

import argparse
import json
import stat
import subprocess  # nosec B404
import sys
from pathlib import Path

COMMAND = "orch-approve-probe"

PROBE = """#!/bin/sh
echo "orch-approve-probe ran: $*"
"""

COPILOT_HOOK = """#!/bin/sh
input=$(cat)
case "$input" in
  *orch-approve-probe*)
    printf '%s' '{"permissionDecision":"ask",'
    printf '%s' '"permissionDecisionReason":"probe P6: this command always asks"}' ;;
esac
exit 0
"""


def write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def setup(root: Path) -> list[Path]:
    probe = root / "bin" / COMMAND
    claude = root / ".claude" / "settings.json"
    codex = root / ".codex" / "rules" / "p6.rules"
    hook = root / ".github" / "hooks" / "p6-ask.sh"
    copilot = root / ".github" / "hooks" / "p6.json"
    write(probe, PROBE, executable=True)
    write(claude, json.dumps({"permissions": {"ask": [f"Bash({COMMAND} *)"]}}, indent=2) + "\n")
    write(codex, f'prefix_rule(pattern = ["{COMMAND}"], decision = "prompt")\n')
    write(hook, COPILOT_HOOK, executable=True)
    entry = {"type": "command", "bash": str(hook), "timeoutSec": 30}
    write(copilot, json.dumps({"version": 1, "hooks": {"preToolUse": [entry]}}, indent=2) + "\n")
    return [probe, claude, codex, hook, copilot]


def dry_run(root: Path) -> dict[str, object]:
    rules = str(root / ".codex" / "rules" / "p6.rules")
    argv = ["codex", "execpolicy", "check", "--rules", rules, "--", COMMAND, "x"]
    codex = subprocess.run(  # noqa: S603 # nosec B603 B607 - fixed argv, no shell
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    hook = root / ".github" / "hooks" / "p6-ask.sh"
    sample = json.dumps({"toolName": "bash", "toolArgs": json.dumps({"command": f"{COMMAND} x"})})
    other = json.dumps({"toolName": "bash", "toolArgs": json.dumps({"command": "ls"})})
    asks = subprocess.run([str(hook)], input=sample, capture_output=True, text=True, check=False)  # noqa: S603 # nosec B603
    quiet = subprocess.run([str(hook)], input=other, capture_output=True, text=True, check=False)  # noqa: S603 # nosec B603
    return {
        "codex_execpolicy": codex.stdout.strip() or codex.stderr.strip(),
        "copilot_hook_on_probe": asks.stdout.strip(),
        "copilot_hook_on_other": quiet.stdout.strip(),
        "claude_settings": json.loads(
            (root / ".claude" / "settings.json").read_text(encoding="utf-8")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for path in setup(args.root):
        print(f"wrote {path}")
    if args.dry_run:
        print(json.dumps(dry_run(args.root), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
