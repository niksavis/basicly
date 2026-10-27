import argparse
import json
import os
import shlex
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from basicly import checkout, permissions

parser = argparse.ArgumentParser(
    description="Probe native Bash matching with local API fixtures and harmless commands."
)
parser.add_argument("--group", required=True)
parser.add_argument("--catalog", type=Path, default=Path(".basicly/core/permissions"))
parser.add_argument("--matrix", type=Path, default=Path("tests/fixtures/permissions_native.json"))
args = parser.parse_args()
if os.name != "posix":
    parser.error("this native Bash probe requires a POSIX shell")
root = Path(tempfile.mkdtemp(prefix="basicly-permissions-probe-"))
(root / "bin").mkdir()
for name in ["git", "gh", "basicly"]:
    p = root / "bin" / name
    p.write_text('#!/bin/sh\nprintf "NATIVE_EXECUTED %s\\n" "$*"\n')
    p.chmod(0o755)
(root / "shell-init").write_text("export PATH=" + shlex.quote(str(root / "bin")) + ':"$PATH"\n')
group = json.loads(args.matrix.read_text())[args.group]
commands = [case["command"] for case in group["cases"]]
managed = permissions.load_claude_permissions(args.catalog)
rules = list(managed.deny)
allows = (
    list(managed.allow)
    if group["kind"] == "allow"
    else ["Bash(git *)", "Bash(gh *)", "Bash(basicly *)"]
)
requests: list[dict[str, Any]] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object, **kwargs: object) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(body)
        has_results = any(
            c.get("type") == "tool_result"
            for m in body.get("messages", [])
            if isinstance(m.get("content"), list)
            for c in m["content"]
        )
        blocks = (
            [{"type": "text", "text": "Done"}]
            if has_results
            else [
                {
                    "type": "tool_use",
                    "id": f"toolu_{i}",
                    "name": "Bash",
                    "input": {"command": c, "description": "Native permission fixture"},
                }
                for i, c in enumerate(commands)
            ]
        )
        msg = {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "claude-opus-5"),
            "content": blocks,
            "stop_reason": "end_turn" if has_results else "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
        if not body.get("stream"):
            data = json.dumps(msg).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind: str, **data: Any) -> None:
            self.wfile.write(
                (
                    "event: " + kind + "\ndata: " + json.dumps({"type": kind, **data}) + "\n\n"
                ).encode()
            )

        event("message_start", message={**msg, "content": [], "stop_reason": None})
        for i, block in enumerate(blocks):
            empty = (
                {**block, "input": {}}
                if block["type"] == "tool_use"
                else {"type": "text", "text": ""}
            )
            event("content_block_start", index=i, content_block=empty)
            delta = (
                {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                if block["type"] == "tool_use"
                else {"type": "text_delta", "text": block["text"]}
            )
            event("content_block_delta", index=i, delta=delta)
            event("content_block_stop", index=i)
        event(
            "message_delta",
            delta={"stop_reason": msg["stop_reason"], "stop_sequence": None},
            usage={"output_tokens": 10},
        )
        event("message_stop")


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {
    **os.environ,
    "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
    "BASH_ENV": str(root / "shell-init"),
    "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
}
for name in ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]:
    env.pop(name, None)
try:
    result = checkout.run(
        [
            "claude",
            "--bare",
            "--setting-sources",
            "",
            "--settings",
            json.dumps({"permissions": {"allow": allows, "deny": rules}}),
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Bash",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--model",
            "claude-opus-5",
            "--output-format",
            "json",
            "-p",
            "Run the fixture.",
        ],
        env=env,
        cwd=root,
        timeout=45,
        check=False,
    )
    (root / "result.json").write_text(result.stdout)
    (root / "requests.json").write_text(json.dumps(requests, indent=2))
    reply = json.loads(result.stdout)
    results = {
        c["tool_use_id"]: c
        for b in requests
        for m in b.get("messages", [])
        if isinstance(m.get("content"), list)
        for c in m["content"]
        if c.get("type") == "tool_result"
    }
    denied = {c["tool_use_id"] for c in reply.get("permission_denials", [])}
    checked = []
    for i, case in enumerate(group["cases"]):
        key = f"toolu_{i}"
        tool = results.get(key, {})
        content = json.dumps(tool.get("content", ""))
        executed = "NATIVE_EXECUTED" in content
        passed = (
            (executed and key not in denied and not tool.get("is_error"))
            if case["allowed"]
            else (key in denied and not executed)
        )
        checked.append({**case, "passed": passed, "denied": key in denied, "result": content})
    report = {
        "group": args.group,
        "version": checkout.run(["claude", "--version"]).stdout.strip(),
        "mode": "dontAsk",
        "cases": checked,
        "passed": sum(c["passed"] for c in checked),
        "total": len(checked),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"evidence": str(root), "report": report}, indent=2))
    if result.returncode or report["passed"] != report["total"]:
        raise SystemExit(1)
finally:
    server.shutdown()
