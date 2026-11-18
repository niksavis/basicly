from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

HOOK = """import json, os, sys
from pathlib import Path
data = json.loads(sys.stdin.read() or "{}")
chain = []
pid = os.getppid()
while pid > 1 and len(chain) < 6:
    stat = Path(f"/proc/{pid}/stat").read_text()
    name = stat[stat.index("(") + 1:stat.rindex(")")]
    fields = stat[stat.rindex(")") + 2:].split()
    chain.append({"pid": pid, "name": name, "start": int(fields[19])})
    pid = int(fields[1])
env = {k: v for k, v in os.environ.items() if "SESSION" in k or "THREAD" in k}
record = {"input": data, "parents": chain, "env": env}
with open(os.environ["P7_LOG"], "a") as log:
    log.write(json.dumps(record) + "\\n")
"""
KEYS = ("session_id", "sessionId", "source", "hook_event_name", "session_title", "name")


def scrubbed(log: Path) -> dict[str, str]:
    drop = ("HERDR_", "CLAUDE_CODE_SESSION")
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    env["P7_LOG"] = str(log)
    env["GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS"] = "true"
    return env


def execute(
    argv: list[str], cwd: Path, env: dict[str, str] | None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # nosec B603 - argv built from fixed vendor choices, no shell
        argv, cwd=cwd, capture_output=True, text=True, env=env, timeout=300, check=False
    )


def claude_setup(root: Path, hook: Path) -> None:
    entry = [{"hooks": [{"type": "command", "command": f'python3 "{hook}"'}]}]
    (root / ".claude").mkdir(exist_ok=True)
    settings = {"hooks": {"SessionStart": entry}}
    (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def copilot_setup(root: Path, hook: Path) -> None:
    entry = [{"type": "command", "bash": f'python3 "{hook}"', "timeoutSec": 10}]
    folder = root / ".github" / "hooks"
    folder.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "hooks": {"sessionStart": entry}}
    (folder / "p7.json").write_text(json.dumps(document), encoding="utf-8")


def claude_runs(first: str | None) -> list[tuple[str, list[str]]]:
    base = ["claude", "-p", "Reply with the word ok."]
    if first is None:
        return [("start", [*base, "--name", "p7-probe"])]
    return [
        ("resume", [*base, "--resume", first]),
        ("fork", [*base, "--resume", first, "--fork-session"]),
    ]


def copilot_runs(first: str | None) -> list[tuple[str, list[str]]]:
    base = ["copilot", "-p", "Reply with the word ok.", "--trust-folder", "--model", "auto"]
    if first is None:
        return [("start", [*base, "--name", "p7-probe"])]
    resume = ["copilot", "-p", "Reply with the word ok.", "--model", "auto", f"--resume={first}"]
    return [("resume", resume)]


SETUPS = {"claude": claude_setup, "copilot": copilot_setup}
RUNS = {"claude": claude_runs, "copilot": copilot_runs}


def entries(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def summary(record: dict[str, object]) -> dict[str, object]:
    data = record.get("input")
    data = data if isinstance(data, dict) else {}
    parents = record.get("parents")
    parents = parents if isinstance(parents, list) else []
    return {
        "input_keys": sorted(data),
        "ids": {k: data[k] for k in KEYS if k in data},
        "parents": [f"{p['name']}:{p['pid']}" for p in parents if isinstance(p, dict)],
        "env_keys": sorted(record.get("env", {})),
    }


def session_id(record: dict[str, object]) -> str | None:
    data = record.get("input")
    if not isinstance(data, dict):
        return None
    value = data.get("session_id") or data.get("sessionId")
    return str(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", choices=tuple(SETUPS), required=True)
    vendor = parser.parse_args().vendor
    if shutil.which(vendor) is None:
        print(f"refused: {vendor} is not on PATH", file=sys.stderr)
        return 2
    report: dict[str, object] = {"vendor": vendor}
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "project"
        root.mkdir()
        execute(["git", "init", "-q", str(root)], root, None)
        hook = Path(outside) / "p7_hook.py"
        hook.write_text(HOOK, encoding="utf-8")
        SETUPS[vendor](root, hook)
        log = Path(outside) / "p7.jsonl"
        report["version"] = (
            execute([vendor, "--version"], root, None).stdout.strip().splitlines()[0]
        )
        first = None
        for label, argv in RUNS[vendor](None):
            done = execute(argv, root, scrubbed(log))
            seen = entries(log)
            report[label] = {"exit": done.returncode, "hooks": [summary(r) for r in seen]}
            first = session_id(seen[-1]) if seen else None
        if first is None:
            report["stop"] = "no session id from the start run"
        else:
            for label, argv in RUNS[vendor](first):
                before = len(entries(log))
                done = execute(argv, root, scrubbed(log))
                report[label] = {
                    "exit": done.returncode,
                    "stderr": done.stderr.strip()[-300:] if done.returncode else "",
                    "hooks": [summary(r) for r in entries(log)[before:]],
                }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
