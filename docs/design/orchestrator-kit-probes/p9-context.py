from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

PROMPT = (
    "Run the shell command `true` exactly once. Then reply with a JSON list of every token "
    "that starts with P9- that you have seen anywhere in your context, and nothing else."
)
TOKEN = re.compile(r"P9-[A-Z]+-[0-9a-f]{8}")
CODEX_REFUSAL = (
    "refused: codex runs a project hook only after its hash is trusted. Trust the hooks of a "
    "fixed scratch folder once in codex /hooks, then pass --root <that folder>"
)
HOOK = """import json, os, sys
from pathlib import Path
event, shape = sys.argv[1], sys.argv[2]
token = json.loads(Path(os.environ["P9_TOKENS"]).read_text())[event]
if os.environ.get("P9_SILENT") == "1":
    raise SystemExit(0)
data = json.loads(sys.stdin.read() or "{}")
if event == "stop":
    if not data.get("stop_hook_active"):
        print(json.dumps({"decision": "block", "reason": token + ": reply with the word stopseen"}))
elif shape == "nested":
    name = "UserPromptSubmit" if event == "ups" else "PostToolUse"
    out = {"hookEventName": name, "additionalContext": token}
    print(json.dumps({"hookSpecificOutput": out}))
else:
    print(json.dumps({"additionalContext": token}))
"""


def scrubbed(tokens: Path, silent: bool) -> dict[str, str]:
    drop = ("HERDR_", "CLAUDE_CODE_SESSION")
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    env["P9_TOKENS"] = str(tokens)
    env["P9_SILENT"] = "1" if silent else "0"
    return env


def command(hook: Path, event: str, shape: str) -> str:
    return f'python3 "{hook}" {event} {shape}'


def nested_hooks(hook: Path) -> dict[str, object]:
    def entry(event: str) -> list[dict[str, object]]:
        return [{"hooks": [{"type": "command", "command": command(hook, event, "nested")}]}]

    events = {"UserPromptSubmit": entry("ups"), "PostToolUse": entry("ptu"), "Stop": entry("stop")}
    return {"hooks": events}


def claude_project(root: Path, hook: Path) -> list[str]:
    (root / ".claude").mkdir(exist_ok=True)
    settings = root / ".claude" / "settings.json"
    settings.write_text(json.dumps(nested_hooks(hook)), encoding="utf-8")
    output = ["--output-format", "stream-json", "--verbose"]
    return ["claude", "-p", PROMPT, "--allowedTools", "Bash(true)", *output]


def codex_project(root: Path, hook: Path) -> list[str]:
    (root / ".codex").mkdir(exist_ok=True)
    (root / ".codex" / "hooks.json").write_text(json.dumps(nested_hooks(hook)), encoding="utf-8")
    return ["codex", "exec", "--json", PROMPT]


def copilot_project(root: Path, hook: Path) -> list[str]:
    def entry(event: str) -> list[dict[str, object]]:
        return [{"type": "command", "bash": command(hook, event, "flat"), "timeoutSec": 10}]

    hooks = {
        "userPromptSubmitted": entry("ups"),
        "postToolUse": entry("ptu"),
        "agentStop": entry("stop"),
    }
    folder = root / ".github" / "hooks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "p9.json").write_text(json.dumps({"version": 1, "hooks": hooks}), encoding="utf-8")
    flags = ["--allow-tool=shell(true)", "--trust-folder", "--model", "auto"]
    return ["copilot", "-p", PROMPT, *flags]


def lines(stream: str) -> list[dict[str, object]]:
    found = []
    for line in stream.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def claude_text(stream: str) -> str:
    parts = []
    for item in lines(stream):
        message = item.get("message")
        if item.get("type") == "assistant" and isinstance(message, dict):
            blocks = message.get("content", [])
            parts.extend(
                b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
    return "\n".join(parts)


def codex_text(stream: str) -> str:
    parts = []
    for item in lines(stream):
        inner = item.get("item")
        if isinstance(inner, dict) and inner.get("type") == "agent_message":
            parts.append(str(inner.get("text", "")))
    return "\n".join(parts)


BUILDERS: dict[str, Callable[[Path, Path], list[str]]] = {
    "claude": claude_project,
    "codex": codex_project,
    "copilot": copilot_project,
}
READERS: dict[str, Callable[[str], str]] = {
    "claude": claude_text,
    "codex": codex_text,
    "copilot": str,
}


def execute(
    argv: list[str], cwd: Path, env: dict[str, str] | None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # nosec B603 - argv built from fixed vendor choices, no shell
        argv, cwd=cwd, capture_output=True, text=True, env=env, timeout=300, check=False
    )


def run(vendor: str, root: Path, outside: Path, silent: bool) -> dict[str, object]:
    names = ("ups", "ptu", "stop")
    tokens = {name: f"P9-{name.upper()}-{secrets.token_hex(4)}" for name in names}
    root.mkdir(parents=True, exist_ok=True)
    execute(["git", "init", "-q", str(root)], root, None)
    hook = root / "p9_hook.py"
    hook.write_text(HOOK, encoding="utf-8")
    token_file = outside / "p9_tokens.json"
    token_file.write_text(json.dumps(tokens), encoding="utf-8")
    argv = BUILDERS[vendor](root, hook)
    version = execute([vendor, "--version"], root, None).stdout.strip()
    done = execute(argv, root, scrubbed(token_file, silent))
    answer = READERS[vendor](done.stdout)
    seen = set(TOKEN.findall(answer))
    return {
        "vendor": vendor,
        "control": silent,
        "version": version.splitlines()[0] if version else "",
        "exit": done.returncode,
        "UserPromptSubmit": tokens["ups"] in seen,
        "PostToolUse": tokens["ptu"] in seen,
        "Stop_continuation": "stopseen" in answer.lower() or tokens["stop"] in seen,
        "stderr": done.stderr.strip()[-400:] if done.returncode else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", choices=tuple(BUILDERS), required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--control", action="store_true")
    args = parser.parse_args()
    if args.vendor == "codex" and args.root is None:
        print(CODEX_REFUSAL, file=sys.stderr)
        return 2
    if shutil.which(args.vendor) is None:
        print(f"refused: {args.vendor} is not on PATH", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as folder:
        root = args.root.resolve() if args.root is not None else Path(folder) / "project"
        result = run(args.vendor, root, Path(outside), args.control)
    print(json.dumps(result))
    return 0 if result["exit"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
