from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path

WAIT = """import sys, time
from pathlib import Path
flag, limit = Path(sys.argv[1]), float(sys.argv[2])
start = time.monotonic()
while time.monotonic() - start < limit:
    if flag.exists():
        print(f"mail arrived after {time.monotonic() - start:.0f} s")
        raise SystemExit(0)
    time.sleep(2)
print(f"no mail after {limit:.0f} s")
"""
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 900
COMMAND = f"python3 wait.py mail.flag {LIMIT}"
PROMPT = (
    f"Run exactly this shell command in the foreground and wait for it to finish: {COMMAND} . "
    "Then reply with one line: the command's output, or the error you got."
)


def scrubbed() -> dict[str, str]:
    drop = ("HERDR_", "CLAUDE_CODE_SESSION")
    return {k: v for k, v in os.environ.items() if not k.startswith(drop)}


def argv(vendor: str, usage: Path) -> list[str]:
    if vendor == "codex":
        return [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "-m",
            "gpt-6-luna",
            PROMPT,
        ]
    rule = "--allow-tool=shell(python3:*)"
    return ["copilot", "-p", PROMPT, rule, "--model", "auto", "--usage-output-file", str(usage)]


def tokens(vendor: str, stdout: str, usage: Path) -> object:
    if vendor == "codex":
        found = []
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") == "turn.completed":
                found.append(item.get("usage"))
        return found
    return json.loads(usage.read_text(encoding="utf-8")) if usage.exists() else None


def last_text(vendor: str, stdout: str) -> str:
    if vendor != "codex":
        return stdout.strip()[-300:]
    texts = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line).get("item", {})
        except ValueError, AttributeError:
            continue
        if isinstance(item, dict) and item.get("type") == "agent_message":
            texts.append(str(item.get("text", "")))
    return texts[-1][-300:] if texts else ""


def event_counts(stdout: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        kind = str(item.get("type", "")) + ":" + str((item.get("item") or {}).get("type", ""))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def run(vendor: str, mail_after: float | None) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "wait.py").write_text(WAIT, encoding="utf-8")
        usage = root / "usage.json"
        start = time.monotonic()
        timer = None
        if mail_after is not None:
            timer = threading.Timer(mail_after, (root / "mail.flag").write_text, args=("mail",))
            timer.start()
        done = subprocess.run(  # noqa: S603 # nosec B603 - argv built from a fixed vendor choice, no shell
            argv(vendor, usage),
            cwd=root,
            capture_output=True,
            text=True,
            env=scrubbed(),
            timeout=LIMIT + 300,
            check=False,
        )
        if timer is not None:
            timer.cancel()
        wall = time.monotonic() - start
        return {
            "mail_after_s": mail_after,
            "exit": done.returncode,
            "wall_s": round(wall, 1),
            "reply": last_text(vendor, done.stdout),
            "tokens": tokens(vendor, done.stdout, usage),
            "stderr": done.stderr.strip()[-300:] if done.returncode else "",
            "events": event_counts(done.stdout),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", choices=("codex", "copilot"), required=True)
    parser.add_argument("--mail-after", type=float)
    parser.add_argument("--limit", type=int, default=900)
    args = parser.parse_args()
    print(json.dumps({"vendor": args.vendor, **run(args.vendor, args.mail_after)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
