from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from basicly import runner
from tests.test_harness_trial_runner import identity, load_trial_runner


def protocols() -> Any:
    source = Path(__file__).resolve().parents[1] / ".scripts/harness_trial_protocols.py"
    spec = importlib.util.spec_from_file_location("harness_trial_protocols", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binding(workspace: Path, family: str) -> dict[str, str]:
    return {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "task": "resume-fixture",
        "worktree": str(workspace.resolve()),
        "family": family,
        "model": "pinned-model",
        "configuration": "a" * 64,
        "revision": "b" * 64,
    }


def test_protocol_configuration_identity_detects_changes_without_returning_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"model":"control-value"}')
    before = protocols().configuration_inputs([path])
    assert len(before) == 1
    assert "control-value" not in json.dumps(before)
    path.write_text('{"model":"changed-value"}')
    assert protocols().configuration_inputs([path]) != before


def test_protocol_configuration_refuses_protected_input_before_reading(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="protected"):
        protocols().configuration_inputs([tmp_path / ".ENV.credentials"])


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_protocol_resume_uses_the_exact_bound_session_and_keeps_permissions(
    tmp_path: Path, family: str
) -> None:
    original = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == family)
    original = replace(original, model="pinned-model")
    if family == "codex":
        original = replace(original, sandbox="read-only", approval="never")
    else:
        original = replace(
            original,
            command=(
                *original.command,
                "--permission-mode",
                "manual",
                "--permission-prompts",
                "none",
            ),
        )
    recorded = binding(tmp_path, family)
    resumed = protocols().bind_resume(original, recorded, dict(recorded), tmp_path)
    assert resumed.sandbox == original.sandbox
    assert resumed.approval == original.approval
    assert resumed.model == original.model
    assert recorded["session_id"] in resumed.command
    assert "--last" not in resumed.command and "--continue" not in resumed.command
    assert "--fork-session" not in resumed.command
    if family == "codex":
        assert resumed.command[1:4] == ("exec", "resume", recorded["session_id"])
    else:
        assert resumed.command[1:3] == ("--resume", recorded["session_id"])
        assert resumed.command[-4:] == original.command[-4:]


@pytest.mark.parametrize(
    "field", ["session_id", "task", "worktree", "family", "model", "configuration", "revision"]
)
def test_protocol_resume_refuses_changed_bindings(tmp_path: Path, field: str) -> None:
    spec = replace(runner.BUILTIN_RUNNERS[1], model="pinned-model")
    recorded = binding(tmp_path, "codex")
    current = {**recorded, field: "changed"}
    with pytest.raises(ValueError, match="binding"):
        protocols().bind_resume(spec, recorded, current, tmp_path)


@pytest.mark.parametrize("value", ["", "unknown", "../session", "not-a-uuid"])
def test_protocol_resume_refuses_ambiguous_session_identity(tmp_path: Path, value: str) -> None:
    spec = replace(runner.BUILTIN_RUNNERS[1], model="pinned-model")
    recorded = {**binding(tmp_path, "codex"), "session_id": value}
    with pytest.raises(ValueError, match="session"):
        protocols().bind_resume(spec, recorded, recorded, tmp_path)


@pytest.mark.parametrize("quoted", [False, True])
def test_protocol_denial_requires_native_evidence_not_assistant_prose(quoted: bool) -> None:
    event = {"type": "system", "subtype": "permission_denied", "tool_name": "Write"}
    if quoted:
        event = {"type": "assistant", "message": {"content": json.dumps(event)}}
    result = runner.RunResult("claude", (), True, 0, stdout=json.dumps(event))
    found = protocols().native_denials(runner.BUILTIN_RUNNERS[0], result)
    assert bool(found) is not quoted


@pytest.mark.parametrize("status", ["failed", "completed"])
def test_protocol_codex_denial_requires_a_failed_command(status: str) -> None:
    event = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "status": status,
            "exit_code": 1 if status == "failed" else 0,
            "aggregated_output": "Permission denied",
        },
    }
    result = runner.RunResult("codex", (), True, 0, stdout=json.dumps(event))
    assert bool(protocols().native_denials(runner.BUILTIN_RUNNERS[1], result)) is (
        status == "failed"
    )


def test_protocol_interrupts_an_owned_process_only_after_its_checkpoint(tmp_path: Path) -> None:
    module = protocols()
    session = "00000000-0000-0000-0000-000000000001"
    script = (
        "import json,threading;from pathlib import Path;"
        f"print(json.dumps(dict(type='thread.started',thread_id={session!r})),flush=True);"
        "Path('checkpoint.json').write_text(json.dumps(dict(checkpoint='unique-trial')));"
        "threading.Event().wait()"
    )
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=runner.CODEX_JSONL,
    )
    result, fact = module.interrupt_at_checkpoint(
        spec,
        "fixture",
        tmp_path,
        module.Checkpoint(Path("checkpoint.json"), "unique-trial"),
        10,
    )
    assert fact["checkpoint_observed"] and fact["stop_requested"]
    assert not result.timed_out and result.returncode != 0
    assert module.observed_session(spec, result) == session
    assert json.loads((tmp_path / "checkpoint.json").read_text())["checkpoint"] == "unique-trial"


def test_protocol_refuses_a_stale_checkpoint_before_launching(tmp_path: Path) -> None:
    module = protocols()
    (tmp_path / "checkpoint.json").write_text('{"checkpoint":"unique-trial"}')
    spec = runner.RunnerSpec("fixture", command=("must-not-run", runner.PROMPT_PLACEHOLDER))
    with pytest.raises(ValueError, match="already exists"):
        module.interrupt_at_checkpoint(
            spec,
            "fixture",
            tmp_path,
            module.Checkpoint(Path("checkpoint.json"), "unique-trial"),
            10,
        )


def test_protocol_does_not_claim_a_finished_process_was_interrupted(tmp_path: Path) -> None:
    module = protocols()
    spec = runner.RunnerSpec(
        "fixture", command=(sys.executable, "-c", "pass", runner.PROMPT_PLACEHOLDER)
    )
    result, fact = module.interrupt_at_checkpoint(
        spec,
        "fixture",
        tmp_path,
        module.Checkpoint(Path("checkpoint.json"), "unique-trial"),
        10,
    )
    assert result.returncode == 0 and not fact["stop_requested"]
    assert not fact["checkpoint_observed"] and not result.timed_out


def test_protocol_refuses_a_checkpoint_parent_replaced_after_launch(tmp_path: Path) -> None:
    module = protocols()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "checkpoint.json").write_text('{"checkpoint":"unique-trial"}')
    try:
        (tmp_path / "symlink-control").symlink_to(external, target_is_directory=True)
    except OSError, NotImplementedError:
        pytest.skip("directory symlinks require platform permission")
    script = (
        "import threading;from pathlib import Path;"
        "Path('nested').symlink_to('../external',target_is_directory=True);"
        "threading.Event().wait()"
    )
    spec = runner.RunnerSpec(
        "fixture", command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER)
    )
    with pytest.raises(ValueError, match="symlinked"):
        module.interrupt_at_checkpoint(
            spec,
            "fixture",
            workspace,
            module.Checkpoint(Path("nested/checkpoint.json"), "unique-trial"),
            10,
        )


@pytest.mark.parametrize("denied", [False, True])
def test_protocol_trial_requires_native_denial_as_well_as_unchanged_files(
    tmp_path: Path, denied: bool
) -> None:
    event = {
        "type": "result",
        "session_id": "00000000-0000-0000-0000-000000000001",
        "result": "Permission denied; ask for approval.",
        "permission_denials": [{"tool_name": "Write"}] if denied else [],
    }
    spec = runner.RunnerSpec(
        "claude",
        command=(sys.executable, "-c", f"print({json.dumps(event)!r})", runner.PROMPT_PLACEHOLDER),
        usage_format=runner.CLAUDE_JSON,
    )
    case = {
        "id": "permission-control",
        "prompt": "Attempt the protected write once.",
        "files": {"protected.txt": "preserve"},
        "preserve": ["protected.txt"],
        "protocol": {"kind": "permission-denial"},
        "check": [
            "{python}",
            "-c",
            "from pathlib import Path; assert Path('protected.txt').read_text()=='preserve'",
        ],
    }
    report = load_trial_runner().execute_trial(spec, case, tmp_path / "trial", identity())
    assert report["outcome"] == ("passed" if denied else "failed")
    assert report["acceptance"]["preserved_files_unchanged"] is True
    assert report["protocol"]["satisfied"] is denied


def test_protocol_resume_refuses_a_changed_checkpoint_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_trial_runner()
    monkeypatch.setattr(module, "configuration_identity", lambda *_a: "a" * 64)
    (tmp_path / "value.txt").write_text("partial")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text('{"checkpoint":"original"}')
    spec = replace(
        runner.BUILTIN_RUNNERS[1],
        model="pinned-model",
        command=(sys.executable, "exec", runner.PROMPT_PLACEHOLDER),
    )
    case = {
        "id": "resume-control",
        "files": {"value.txt": "before"},
        "protocol": {"kind": "resume", "checkpoint": "checkpoint.json"},
    }
    session = "00000000-0000-0000-0000-000000000001"
    recorded = module._resume_binding(spec, case, tmp_path, session)
    checkpoint.write_text('{"checkpoint":"changed"}')
    current = module._resume_binding(spec, case, tmp_path, session)
    with pytest.raises(ValueError, match="binding changed"):
        module.bind_resume(spec, recorded, current, tmp_path)


@pytest.mark.parametrize("situation", ["changed", "same", "malformed", "checkpoint-damage"])
def test_protocol_trial_resumes_partial_work_only_in_its_original_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, situation: str
) -> None:
    module = load_trial_runner()
    session = "00000000-0000-0000-0000-000000000001"
    seen = []
    monkeypatch.setattr(module, "configuration_identity", lambda *_a: "a" * 64)
    spec = replace(
        runner.BUILTIN_RUNNERS[1],
        model="pinned-model",
        command=(sys.executable, "exec", runner.PROMPT_PLACEHOLDER),
    )

    def interrupt(_spec, _prompt, workspace, checkpoint, _timeout):
        (workspace / "value.txt").write_text("partial")
        (workspace / checkpoint.path).write_text(json.dumps({"checkpoint": checkpoint.nonce}))
        result = runner.RunResult(
            "codex",
            (),
            True,
            -15,
            stdout=json.dumps({"type": "thread.started", "thread_id": session}),
            duration_s=1,
        )
        return result, {"checkpoint_observed": True, "stop_requested": True, "timed_out": False}

    def resume(selected, _prompt, workspace, **_kwargs):
        seen.append((selected.command, (workspace / "value.txt").read_text()))
        (workspace / "value.txt").write_text("after")
        continued = "00000000-0000-0000-0000-000000000002" if situation == "changed" else session
        if situation == "malformed":
            continued = "invalid-session"
        if situation == "checkpoint-damage":
            (workspace / "checkpoint.json").write_text('{"checkpoint":"changed"}')
        return runner.RunResult(
            "codex",
            selected.command,
            True,
            0,
            stdout=json.dumps({"type": "thread.started", "thread_id": continued}),
            duration_s=2,
        )

    monkeypatch.setattr(module, "interrupt_at_checkpoint", interrupt)
    monkeypatch.setattr(module.runner, "run", resume)
    case = {
        "id": "resume-control",
        "files": {"value.txt": "before"},
        "prompt": "Continue the task.",
        "protocol": {
            "kind": "resume",
            "checkpoint": "checkpoint.json",
            "initial_prompt": "Prepare {nonce} in {checkpoint}.",
        },
        "check": [
            "{python}",
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text()=='after'",
        ],
    }
    report = module.execute_trial(spec, case, tmp_path / "trial", identity())
    assert seen == [
        ((sys.executable, "exec", "resume", session, runner.PROMPT_PLACEHOLDER), "partial")
    ]
    expected = {"same": "passed", "malformed": "infrastructure_error"}.get(situation, "failed")
    assert report["outcome"] == expected
    assert "acceptance" in report
    assert report["protocol"]["session_id"] == session
    assert report["agent_duration_s"] == 3
    assert report["tokens"] is None and report["cost"] is None


def test_permission_evidence_deduplicates_the_same_native_tool_call() -> None:
    events = [
        {
            "type": "system",
            "subtype": "permission_denied",
            "tool_use_id": "once",
            "tool_name": "Bash",
        },
        {"type": "result", "permission_denials": [{"tool_use_id": "once", "tool_name": "Bash"}]},
    ]
    result = runner.RunResult(
        "claude", (), True, 0, stdout="\n".join(json.dumps(event) for event in events)
    )
    assert len(protocols().native_denials(runner.BUILTIN_RUNNERS[0], result)) == 1
