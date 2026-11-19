from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from pathlib import Path

SESSION = "orch-p8-probe"
WAIT = """import sys, time
from pathlib import Path
flag = Path(sys.argv[1])
end = time.monotonic() + float(sys.argv[2])
while time.monotonic() < end:
    if flag.exists():
        print("mail arrived")
        raise SystemExit(0)
    time.sleep(2)
print("no mail")
"""
HOOK = """import json, os, sys, time
data = json.loads(sys.stdin.read() or "{}")
record = {"t": time.time(), "event": data.get("hook_event_name")}
record["transcript"] = data.get("transcript_path")
with open(os.environ["P8_LOG"], "a") as log:
    log.write(json.dumps(record) + "\\n")
"""
PROMPT = (
    "Run `python3 wait.py mail.flag 900` with the Bash tool in the background "
    "(run_in_background true). Do not wait for it. "
    "End your turn by replying with the word waiting. "
    "When it finishes later, reply with the word woke and nothing else."
)


def tool(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # nosec B603 B607 - literal argv from PATH, no shell
        list(argv), capture_output=True, text=True, check=False, timeout=30
    )


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return tool("tmux", *args)


def events(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def stops(log: Path) -> list[dict[str, object]]:
    return [e for e in events(log) if e.get("event") == "Stop"]


def wait_for(predicate, limit: float) -> bool:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(1)
    return False


def usage_after(transcript: Path, since: float) -> dict[str, int]:
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "messages": 0,
    }
    for line in transcript.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        message = item.get("message")
        stamp = item.get("timestamp", "")
        if item.get("type") != "assistant" or not isinstance(message, dict) or not stamp:
            continue
        when = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        if when < since - 1:
            continue
        usage = message.get("usage", {})
        for key in total:
            if key != "messages":
                total[key] += int(usage.get(key, 0) or 0)
        total["messages"] += 1
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle", type=float, default=60.0)
    idle = parser.parse_args().idle
    if shutil.which("tmux") is None or shutil.which("claude") is None:
        print("refused: tmux and claude must be on PATH", file=sys.stderr)
        return 2
    report: dict[str, object] = {"vendor": "claude"}
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "project"
        root.mkdir()
        log = Path(outside) / "p8.jsonl"
        hook = Path(outside) / "p8_hook.py"
        hook.write_text(HOOK, encoding="utf-8")
        (root / "wait.py").write_text(WAIT, encoding="utf-8")
        entry = [{"hooks": [{"type": "command", "command": f'python3 "{hook}"'}]}]
        (root / ".claude").mkdir()
        hooks = {"SessionStart": entry, "Stop": entry, "UserPromptSubmit": entry}
        (root / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": hooks}), encoding="utf-8"
        )
        report["version"] = tool("claude", "--version").stdout.strip()
        drop = [f"-u{k}" for k in os.environ if k.startswith(("HERDR_", "CLAUDE_CODE_SESSION"))]
        launch = [
            "env",
            *drop,
            f"P8_LOG={log}",
            "claude",
            "--allowedTools",
            "Bash(python3 wait.py mail.flag 900)",
            PROMPT,
        ]
        tmux("kill-session", "-t", SESSION)
        tmux("new-session", "-d", "-s", SESSION, "-x", "200", "-y", "50", "-c", str(root), *launch)
        try:
            if wait_for(
                lambda: "trust" in tmux("capture-pane", "-p", "-t", SESSION).stdout.lower(), 20
            ):
                report["trust_dialog"] = "shown; the person answers it in the tmux window"
                print(f"answer the trust dialog: tmux attach -t {SESSION}", file=sys.stderr)
            if not wait_for(lambda: len(stops(log)) >= 1, 240):
                report["stop"] = "first turn did not end within 240 s"
                report["screen"] = tmux("capture-pane", "-p", "-t", SESSION).stdout[-1500:]
                print(json.dumps(report, indent=1))
                return 1
            first = stops(log)[0]
            time.sleep(idle)
            report["turns_while_idle"] = len(stops(log)) - 1
            mailed = time.time()
            (root / "mail.flag").write_text("mail", encoding="utf-8")
            woke = wait_for(lambda: len(stops(log)) >= 2, 120)
            report["woke"] = woke
            if woke:
                second = stops(log)[1]
                report["wake_latency_s"] = round(float(str(second["t"])) - mailed, 1)
                transcript = Path(str(first["transcript"]))
                report["wake_turn_usage"] = usage_after(transcript, mailed)
            report["screen_tail"] = tmux("capture-pane", "-p", "-t", SESSION).stdout.strip()[-600:]
        finally:
            tmux("kill-session", "-t", SESSION)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
