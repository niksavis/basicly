from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tests import test_tracker_plugin

package = test_tracker_plugin.package

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / ".basicly/core/kit/tracker"
BOOT = (
    "import importlib.util,sys; "
    "s=importlib.util.spec_from_file_location('basicly_tracker',sys.argv[1]); "
    "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
    "raise SystemExit(m.main(sys.argv[2:]))"
)


class Client:
    def __init__(self, argv: list[str], root: Path, session: str = "agent-test") -> None:
        env = os.environ.copy()
        env.update(
            HOME=str(root.parent / "home"),
            LOCALAPPDATA=str(root.parent / "state"),
            XDG_STATE_HOME=str(root.parent / "state"),
            BASICLY_HOLDER=session,
            BASICLY_TRACKER_SESSION=session,
            BASICLY_TRACKER_STATE=str(root.parent / "states" / session),
            BASICLY_TRACKER_EPHEMERAL="1",
        )
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.messages: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.identity = 0

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.identity += 1
        self.send({"jsonrpc": "2.0", "id": self.identity, "method": method, "params": params})
        reply = json.loads(self.messages.get(timeout=120))
        assert reply["id"] == self.identity
        return reply

    def start(self) -> None:
        reply = self.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "consumer-test", "version": "1"},
            },
        )
        assert reply["result"]["protocolVersion"] == "2025-11-25"
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tool(self, name: str, **arguments: Any) -> dict[str, Any]:
        return self.request("tools/call", {"name": "tracker_" + name, "arguments": arguments})[
            "result"
        ]

    def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            assert self.process.wait(timeout=30) == 0
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=30)
            self.reader.join(timeout=30)
            for stream in (self.process.stdout, self.process.stderr):
                if stream:
                    stream.close()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            sys.executable,
            str(KIT / "cli.py"),
            "create",
            str(root / ".basicly/ledger"),
            "--prefix",
            "demo",
            "--title",
            "Known record",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(KIT / "cli.py"),
            "config",
            str(root / ".basicly/ledger"),
            "set",
            "prefix",
            "demo",
        ],
        check=True,
        capture_output=True,
    )
    return root


def command(root: Path, installed: Path | None = None) -> list[str]:
    prefix = [sys.executable, "-I", "-S"]
    if installed:
        prefix += ["-c", BOOT, str(installed / "__init__.py"), "mcp"]
    else:
        prefix += [str(KIT / "mcp_server.py")]
    return [*prefix, "--repository", str(root)]


@pytest.mark.parametrize("packaged", [False, True])
def test_agent_discovers_tools_and_completes_a_record_without_an_engine(
    package: Path, tmp_path: Path, packaged: bool
) -> None:
    root = repository(tmp_path)
    client = Client(command(root, package if packaged else None), root)
    try:
        client.start()
        tools = client.request("tools/list", {})["result"]["tools"]
        assert {x["name"] for x in tools} >= {
            "tracker_ready",
            "tracker_create",
            "tracker_claim",
            "tracker_confirm",
            "tracker_close",
        }
        criterion = "When confirmed, the record shall close."
        created = client.tool(
            "create",
            title="Agent delivery",
            description="## Context\n\nAn agent needs a tracked delivery.",
            acceptance=criterion,
            fields={"issue_type": "task", "size": "small"},
        )
        assert not created["isError"], created
        record = created["structuredContent"]["report"]["record"]
        refused = client.tool("close", record=record, reason="Premature completion")
        assert refused["isError"]
        claimed = client.tool("claim", record=record)
        assert not claimed["isError"], claimed
        confirmed = client.tool(
            "confirm",
            record=record,
            evidence={
                "checks": [
                    {
                        "criterion": criterion,
                        "command": ["consumer-test"],
                        "result": "The consumer observed the required state.",
                        "exit_code": 0,
                    }
                ]
            },
        )
        assert not confirmed["isError"], confirmed
        assert not client.tool("close", record=record, reason="The consumer confirmed delivery.")[
            "isError"
        ]
        shown = client.tool("show", record=record)
        assert shown["structuredContent"]["report"]["status"] == "closed"
    finally:
        client.close()


def test_agent_cannot_override_repository_identity_or_inject_options(tmp_path: Path) -> None:
    root = repository(tmp_path)
    client = Client(command(root), root)
    try:
        assert "error" in client.request("tools/list", {})
        client.start()
        for args in (
            {"repository": "elsewhere"},
            {"vault": "elsewhere"},
            {"title": ["wrong"]},
            {"title": "bad\0input"},
        ):
            assert client.tool("create", **args)["isError"]
        assert client.tool("show", record="--help")["isError"]
        assert client.tool("delete", record="demo-a1")["isError"]
        assert not client.tool("list", query="--vault=elsewhere")["isError"]
        assert client.tool("list")["structuredContent"]["report"]["count"] == 1
        (root / ".basicly-tracker.toml").write_text('vault = "elsewhere"\nprefix = "other"\n')
        changed = client.tool("list")
        assert changed["isError"] and "Reconnect" in changed["content"][0]["text"]
    finally:
        client.close()


def test_independent_agent_sessions_cannot_claim_each_others_vault_work(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    bare = tmp_path / "vault.git"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    env = os.environ.copy()
    env.update(BASICLY_TRACKER_SESSION="seed", BASICLY_TRACKER_STATE=str(tmp_path / "seed-state"))
    initialized = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(KIT / "cli.py"),
            "vault-init",
            "--vault",
            str(bare),
            "--prefix",
            "demo",
            "--yes",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr
    first, second = Client(command(root), root, "first"), Client(command(root), root, "second")
    try:
        first.start()
        second.start()
        created = first.tool(
            "create",
            title="Exclusive agent work",
            description="## Context\n\nConcurrent agents need exclusive ownership.",
            acceptance="When claimed, another agent shall refuse the same work.",
            fields={"issue_type": "task", "size": "small"},
        )
        assert not created["isError"], created
        record = created["structuredContent"]["report"]["record"]
        claimed = first.tool("claim", record=record)
        assert not claimed["isError"], claimed
        refused = second.tool("claim", record=record)
        assert refused["isError"], refused
        assert not first.tool("unassign", record=record)["isError"]
        claimed = second.tool("claim", record=record)
        assert not claimed["isError"], claimed
        assert first.tool("unassign", record=record)["isError"]
    finally:
        try:
            first.close()
        finally:
            second.close()


def test_transport_refuses_invalid_json_and_continues_with_valid_requests(tmp_path: Path) -> None:
    root = repository(tmp_path)
    messages = [
        "not-json",
        "NaN",
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "consumer", "version": "1"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    done = subprocess.run(
        command(root),
        input="\n".join(messages) + "\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [reply["error"]["code"] for reply in replies[:2]] == [-32700, -32700]
    assert replies[2]["id"] == 1
    assert replies[3]["id"] == 2 and replies[3]["result"]["tools"]
