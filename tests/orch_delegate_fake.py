import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

argv = sys.argv[1:]
name = Path(sys.argv[0]).name
cwd = Path.cwd()
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as out:
    out.write(json.dumps({"tool": name, "argv": argv, "cwd": str(cwd)}) + "\n")
if os.environ.get("FAKE_FAIL") == name:
    sys.stderr.write("ERROR: the model refused the request\n")
    sys.exit(2)
if os.environ.get("FAKE_LIMIT") == name:
    sys.stderr.write("ERROR: You've hit your usage limit. Try again at 3:05 PM.\n")
    sys.exit(1)
if name == "basicly":
    lane = cwd.parent / f"{cwd.name}.worktrees" / argv[2]
    add = ["git", "worktree", "add", "-q", "-b", f"harness/{argv[2]}", str(lane)]
    subprocess.run(add, check=True)  # nosec B603 B607
elif name == "claude":
    session = argv[argv.index("--resume" if "--resume" in argv else "--session-id") + 1]
    shown = {"result": "claude says: " + argv[1], "session_id": session, "is_error": False}
    sys.stdout.write(json.dumps(shown) + "\n")
elif name == "codex":
    thread = argv[-2] if "resume" in argv else "thread-0001"
    message = {"type": "agent_message", "text": "codex says: " + argv[-1]}
    sys.stdout.write(json.dumps({"type": "thread.started", "thread_id": thread}) + "\n")
    sys.stdout.write(json.dumps({"type": "item.completed", "item": message}) + "\n")
elif name == "copilot":
    sys.stdout.write("copilot says: " + argv[1] + "\n")
