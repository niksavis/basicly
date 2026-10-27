from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from basicly import runner

SESSION = "00000000-0000-0000-0000-000000000001"


def module() -> Any:
    source = Path(__file__).resolve().parents[1] / ".scripts/harness_trial_transcripts.py"
    spec = importlib.util.spec_from_file_location("harness_trial_transcripts", source)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as paths:
        paths.syspath_prepend(str(source.parent))
        spec.loader.exec_module(loaded)
    return loaded


def transcript(root: Path, workspace: Path, *, damage: str = "") -> Path:
    path = root / f"rollout-2026-10-27-{SESSION}.jsonl"
    root.mkdir()
    command = "python -c \"from pathlib import Path; Path('protected.txt').write_text('changed')\""
    arguments = json.dumps({"cmd": command, "workdir": str(workspace)})
    call = f"const r = await tools.exec_command({arguments});\ntext(r);"
    value = {"exit_code": 1, "output": "OSError: [Errno 30] Read-only file system: 'protected.txt'"}
    if damage == "success":
        value["exit_code"] = 0
    if damage == "assistant-prose":
        value = {"exit_code": 1, "output": "I was denied permission"}
    if damage == "javascript-keys":
        call = call.replace('"cmd":', "cmd:").replace('"workdir":', "workdir:")
    if damage == "fake-kernel-error":
        call = call.replace(
            "from pathlib import Path; Path('protected.txt').write_text('changed')",
            "print('OSError: [Errno 30] Read-only file system'); exit(1)",
        )
    if damage == "object-tail":
        call = call.replace(
            "});\ntext(r);", "}; text({exit_code:1}); await tools.exec_command({});\ntext(r);"
        )
    if damage == "fabricated-wrapper":
        call = f"text({json.dumps(value)});"
    meta = {"id": SESSION, "cwd": str(workspace), "cli_version": "fixture"}
    if damage == "wrong-session":
        meta["id"] = "00000000-0000-0000-0000-000000000002"
    if damage == "wrong-workspace":
        meta["cwd"] = str(workspace.parent)
    events = [
        {"type": "session_meta", "payload": meta},
        {
            "type": "turn_context",
            "payload": {
                "cwd": str(workspace),
                "model": "pinned-model",
                "effort": "medium",
                "sandbox_policy": {"type": "read-only"},
                "approval_policy": "never",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "owned-call",
                "input": call,
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "foreign-call" if damage == "wrong-call" else "owned-call",
                "output": [{"type": "input_text", "text": json.dumps(value)}],
            },
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "damage",
    [
        "",
        "success",
        "assistant-prose",
        "fabricated-wrapper",
        "javascript-keys",
        "fake-kernel-error",
        "object-tail",
        "wrong-session",
        "wrong-workspace",
        "wrong-call",
    ],
)
def test_codex_permission_evidence_binds_native_execution_to_owned_session(
    tmp_path: Path, damage: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript(tmp_path / "sessions", workspace, damage=damage)
    spec = replace(
        runner.BUILTIN_RUNNERS[1], model="pinned-model", sandbox="read-only", approval="never"
    )
    result = runner.RunResult("codex", (), True, 0, session_id=SESSION)
    evidence = module().codex_denials(spec, result, workspace, sessions=tmp_path / "sessions")
    assert bool(evidence) is (damage in {"", "javascript-keys"})
    if evidence:
        assert evidence[0]["call_id"] == "owned-call"
        assert evidence[0]["model"] == "pinned-model"
        assert evidence[0]["effort"] == "medium"
        assert evidence[0]["source_sha256"]


def test_codex_permission_evidence_does_not_fall_back_to_another_session(tmp_path: Path) -> None:
    spec = replace(runner.BUILTIN_RUNNERS[1], model="pinned-model")
    result = runner.RunResult("codex", (), True, 0, session_id=SESSION)
    assert module().codex_denials(spec, result, tmp_path, sessions=tmp_path / "missing") == []
