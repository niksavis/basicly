from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_tracker_mcp import Client

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.fresh_runtime


def test_installed_tracker_and_agent_tools_complete_vault_work_without_the_harness(
    tmp_path: Path,
) -> None:
    built = subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            str(ROOT / "packages/basicly-tracker"),
            "--out-dir",
            str(tmp_path / "dist"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], capture_output=True, check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheel = next((tmp_path / "dist").glob("*.whl"))
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
        capture_output=True,
        check=True,
    )
    missing = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('basicly') is None",
        ],
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 0, missing.stderr
    entry = python.parent / ("basicly-tracker.exe" if os.name == "nt" else "basicly-tracker")
    root, bare = tmp_path / "project", tmp_path / "vault.git"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        BASICLY_TRACKER_STATE=str(tmp_path / "state"),
        BASICLY_HOLDER="consumer",
        BASICLY_TRACKER_SESSION="consumer",
        BASICLY_TRACKER_EPHEMERAL="1",
    )

    def run(*args: str) -> dict:
        done = subprocess.run(
            [str(entry), *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, (done.stdout, done.stderr)
        return json.loads(done.stdout)

    assert run("vault-init", "--vault", str(bare), "--prefix", "demo", "--yes")["pushed"]
    criterion = "When confirmed, the record shall close."
    created = run(
        "create",
        ".basicly/ledger",
        "--title",
        "Standalone delivery",
        "--field",
        "issue_type=task",
        "--field",
        "size=small",
        "--description",
        "## Context\n\nA standalone consumer needs tracked work.",
        "--acceptance",
        criterion,
    )
    record = created["record"]
    run("claim", record)
    run("update", ".basicly/ledger", record, "--status", "in_progress")
    run("comment", record, "--text", "The standalone consumer reached confirmation.")
    run(
        "confirm",
        ".basicly/ledger",
        record,
        "--evidence",
        json.dumps({
            "checks": [
                {
                    "criterion": criterion,
                    "command": ["consumer-test"],
                    "result": "The standalone consumer observed the required state.",
                    "exit_code": 0,
                }
            ]
        }),
    )
    run("close", ".basicly/ledger", record, "--reason", "The consumer confirmed delivery.")
    run("sync")
    assert run("show", ".basicly/ledger", record)["status"] == "closed"
    run("fsck")
    client = Client([str(entry), "mcp", "--repository", str(root)], root)
    try:
        client.start()
        shown = client.tool("show", record=record)
        assert not shown["isError"], shown
        assert shown["structuredContent"]["report"]["status"] == "closed"
        for foreign in ("other-a1", "demo-other-a1"):
            refused = client.tool("show", record=foreign)
            assert refused["isError"] and "another project" in refused["content"][0]["text"]
    finally:
        client.close()
