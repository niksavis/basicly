from __future__ import annotations

import json
import os
import statistics
import subprocess  # nosec B404
import sys
import tempfile
import time
from pathlib import Path

BUDGET_MS = 50.0
RUNS = 20
PACKAGE = Path(__file__).resolve().parents[3] / "packages/basicly-orch"
EVENTS = {
    "SessionStart": {"hook_event_name": "SessionStart"},
    "UserPromptSubmit": {"hook_event_name": "UserPromptSubmit"},
    "PermissionRequest": {
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
    },
    "PostToolUse": {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
    "Stop": {"hook_event_name": "Stop", "background_tasks": [{"type": "shell"}]},
}


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        fake = Path(folder) / "herdr"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HERDR_ENV": "1",
            "HERDR_PANE_ID": "w0:pBench",
            "HERDR_BIN_PATH": str(fake),
        }
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from orch_state.reporter import main; raise SystemExit(main())"
        )
        worst = 0.0
        for name, payload in EVENTS.items():
            samples = []
            for _ in range(RUNS):
                start = time.perf_counter()
                subprocess.run(  # noqa: S603 # nosec B603 - current interpreter, literal argv, no shell
                    [sys.executable, "-I", "-c", code, str(PACKAGE)],
                    input=json.dumps(payload),
                    text=True,
                    env=env,
                    check=True,
                    capture_output=True,
                )
                samples.append((time.perf_counter() - start) * 1000)
            median, peak = statistics.median(samples), max(samples)
            worst = max(worst, peak)
            print(f"{name}: median {median:.1f} ms, max {peak:.1f} ms")
    verdict = "below" if worst < BUDGET_MS else "above"
    print(f"all events: max {worst:.1f} ms, {verdict} the {BUDGET_MS:.0f} ms budget")
    return 0 if worst < BUDGET_MS else 1


if __name__ == "__main__":
    raise SystemExit(main())
