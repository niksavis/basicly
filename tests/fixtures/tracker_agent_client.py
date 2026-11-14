from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

package, root = Path(sys.argv[1]), Path(sys.argv[2])
session = os.environ["BASICLY_TRACKER_SESSION"]
kit = package / "kit"
root.mkdir()


def run(argv: list[str], *, success: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False, timeout=60)
    if success and done.returncode:
        raise ValueError(f"Consumer command failed: {done.stdout} {done.stderr}")
    return done


run(["git", "init", "-q", "-b", "main"])
bare = root.parent / "vault.git"
run(["git", "init", "--bare", "-q", "-b", "main", str(bare)])
cli = [sys.executable, "-I", "-S", str(kit / "cli.py")]
run([*cli, "vault-init", "--vault", str(bare), "--prefix", "demo", "--yes"])
if not (root / ".basicly/ledger").is_file():
    raise ValueError("Fresh vault attachment did not preserve its structural guard.")
local_kit = root / ".basicly/core/kit/tracker"
shutil.copytree(kit, local_kit)
run(["git", "add", ".basicly", ".basicly-tracker.toml"])
run(["git", "commit", "-q", "-m", "test: initialize the consumer"])
run([
    sys.executable,
    "-I",
    "-S",
    str(local_kit / "install_hook.py"),
    "--root",
    ".",
    "--ledger",
    ".basicly/ledger",
])
boot = (
    "import importlib.util,sys; "
    "s=importlib.util.spec_from_file_location('basicly_tracker',sys.argv[1]); "
    "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
    "raise SystemExit(m.main(sys.argv[2:]))"
)
process = subprocess.Popen(
    [
        sys.executable,
        "-I",
        "-S",
        "-c",
        boot,
        str(package / "__init__.py"),
        "mcp",
        "--repository",
        str(root),
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
identities = itertools.count(1)


def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
    identity = next(identities)
    if process.stdin is None or process.stdout is None:
        raise ValueError("The consumer transport did not open.")
    process.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": identity, "method": method, "params": params}) + "\n"
    )
    process.stdin.flush()
    reply = json.loads(process.stdout.readline())
    if reply.get("id") != identity or "error" in reply:
        raise ValueError("The consumer received an invalid protocol response.")
    return reply["result"]


def tool(name: str, **arguments: Any) -> dict[str, Any]:
    report = request("tools/call", {"name": "tracker_" + name, "arguments": arguments})
    if report["isError"]:
        raise ValueError(json.dumps(report))
    return report["structuredContent"]["report"]


try:
    request(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "foreground-consumer", "version": "1"},
        },
    )
    if process.stdin is None:
        raise ValueError("The consumer transport did not open.")
    process.stdin.write(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    )
    process.stdin.flush()
    record = tool(
        "create",
        title="Foreground agent delivery",
        description="## Context\n\nThe launched agent needs tracker and hook identity.",
        acceptance="When the agent claims work, its Git hook shall accept its commit.",
        fields={"issue_type": "task", "size": "small"},
    )["record"]
    (root / "work.txt").write_text("Tracked consumer work.\n")
    run(["git", "add", "work.txt"])
    missing_id = run(["git", "commit", "-q", "-m", "feat: deliver agent work"], success=False)
    if missing_id.returncode == 0:
        raise ValueError("The real Git hook accepted a missing record ID without a claim.")
    tool("claim", record=record)
    tool("update", record=record, status="in_progress")
    tool("sync")
    run(["git", "commit", "-q", "-m", f"feat: deliver agent work ({record})"])
    print(
        json.dumps({
            "session": session,
            "missing_id_commit_exit": missing_id.returncode,
            "claimed_commit_exit": 0,
            "guard_preserved": (root / ".basicly/ledger").is_file(),
        })
    )
finally:
    if process.stdin:
        process.stdin.close()
    try:
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
