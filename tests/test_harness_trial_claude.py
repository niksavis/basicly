from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from basicly import runner
from tests.test_harness_trial_runner import load_trial_runner

SESSION = "00000000-0000-0000-0000-000000000001"
MODEL = "claude-opus-5-5"


def module() -> Any:
    load_trial_runner()
    return importlib.import_module("harness_trial_claude")


def installation(workspace: Path) -> None:
    files = {
        ".claude/CLAUDE.md": "Unique repository instructions",
        ".claude/skills/example/SKILL.md": (
            "---\nname: example\ndescription: Unique repository skill\n---\nPerform the check.\n"
        ),
        ".git/hooks/pre-commit": "gate",
        ".git/hooks/commit-msg": "gate",
        "basicly.toml": '[[verify.checks]]\nname="contract"\ncommand=["contract"]\n',
    }
    for name, text in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def native_trace(
    workspace: Path, sessions: Path, *, loaded: bool
) -> tuple[Path, list[dict[str, Any]], runner.RunResult]:
    path = sessions / "project" / f"{SESSION}.jsonl"
    path.parent.mkdir(parents=True)
    context = (
        f"Contents of {workspace / '.claude/CLAUDE.md'} "
        "(project instructions, checked into the codebase):\n"
        "Unique repository instructions\n"
        "The following skills are available for use with the Skill tool:\n"
        "- example: Unique repository skill\n"
        if loaded
        else "Ordinary host context"
    )
    rows = [
        {"type": "user", "sessionId": SESSION, "cwd": str(workspace)},
        {
            "type": "api-request-shape",
            "sessionId": SESSION,
            "shapeHash": "shape",
            "shape": {"system": [{"type": "text", "text": "Host prompt"}]},
        },
        {
            "type": "api-request-blob",
            "sessionId": SESSION,
            "hash": "initial",
            "message": {"role": "user", "content": [{"type": "text", "text": context}]},
        },
        {
            "type": "api-request",
            "sessionId": SESSION,
            "querySource": "sdk",
            "shapeHash": "shape",
            "keep": 0,
            "tail": ["initial"],
            "params": {"model": MODEL, "output_config": {"effort": "high"}},
        },
        {
            "type": "api-request-blob",
            "sessionId": SESSION,
            "hash": "later",
            "message": {"role": "user", "content": "Unique repository instructions"},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    initial = {
        "type": "system",
        "subtype": "init",
        "cwd": str(workspace),
        "session_id": SESSION,
        "model": MODEL,
        "permissionMode": "acceptEdits",
        "skills": ["example"] if loaded else [],
    }
    result = runner.RunResult("claude", (), True, returncode=0, stdout=json.dumps(initial))
    return path, rows, result


@pytest.mark.parametrize("mode", ["plain", "gates-only", "full-basicly"])
def test_claude_value_arm_controls_loading_without_changing_installed_gates(
    tmp_path: Path, mode: str
) -> None:
    load_trial_runner()
    arms = importlib.import_module("harness_trial_arms")
    if mode != "plain":
        installation(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    spec = runner.RunnerSpec("claude", command=("claude", "-p", "{prompt}"), model=MODEL)

    selected, conditions = arms.configure_value_arm(spec, mode, tmp_path)

    assert ("--disable-slash-commands" in selected.command) is (mode != "full-basicly")
    settings = json.loads(selected.command[selected.command.index("--settings") + 1])
    assert settings["autoMemoryEnabled"] is False
    assert bool(settings.get("claudeMdExcludes")) is (mode != "full-basicly")
    assert selected.command[selected.command.index("--permission-mode") + 1] == "acceptEdits"
    assert selected.command[selected.command.index("--permission-prompts") + 1] == "none"
    assert conditions["installed"] is (mode != "plain")
    assert conditions["loading_verified"] is False
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("loaded", [False, True])
def test_claude_loading_reads_only_the_initial_bound_context(tmp_path: Path, loaded: bool) -> None:
    workspace = tmp_path / "workspace"
    installation(workspace)
    _, _, result = native_trace(workspace, tmp_path / "sessions", loaded=loaded)

    evidence = module().loading_evidence(workspace, SESSION, result, sessions=tmp_path / "sessions")

    assert evidence["instructions_loaded"] is loaded
    assert bool(evidence["project_skills_loaded"]) is loaded
    assert evidence["native_models"] == [MODEL]
    assert evidence["native_effort"] == ["high"]
    assert evidence["native_permission_mode"] == "acceptEdits"
    assert evidence["expected_project_skills"] == [".claude/skills/example/SKILL.md"]
    assert str(workspace) not in json.dumps(evidence)
    assert "Unique repository instructions" not in json.dumps(evidence)


@pytest.mark.parametrize("damage", ["session", "cwd", "shape", "blob", "initial", "effort"])
def test_claude_loading_refuses_unbound_or_incomplete_evidence(tmp_path: Path, damage: str) -> None:
    workspace = tmp_path / "workspace"
    installation(workspace)
    path, rows, result = native_trace(workspace, tmp_path / "sessions", loaded=True)
    if damage == "session":
        rows[3]["sessionId"] = "00000000-0000-0000-0000-000000000002"
    elif damage == "cwd":
        rows[0]["cwd"] = str(tmp_path / "other")
    elif damage == "shape":
        rows[3]["shapeHash"] = "missing"
    elif damage == "blob":
        rows[3]["tail"] = ["missing"]
    elif damage == "initial":
        rows[3]["keep"] = 1
    else:
        rows[3]["params"].pop("output_config")
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="native Claude"):
        module().loading_evidence(workspace, SESSION, result, sessions=tmp_path / "sessions")


@pytest.mark.parametrize("mode", ["plain", "gates-only", "full-basicly"])
@pytest.mark.parametrize(
    "changed", ["none", "instructions", "model", "effort", "permissions", "skills"]
)
def test_claude_value_verification_discriminates_declared_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, changed: str
) -> None:
    workspace = tmp_path / "workspace"
    installation(workspace)
    path, rows, result = native_trace(
        workspace, tmp_path / "config/projects", loaded=mode == "full-basicly"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    if changed == "model":
        rows[3]["params"]["model"] = "claude-sonnet-5-5"
    elif changed == "effort":
        rows[3]["params"]["output_config"]["effort"] = "medium"
    elif changed in {"permissions", "skills"}:
        initial = json.loads(result.stdout)
        if changed == "permissions":
            initial["permissionMode"] = "bypassPermissions"
        else:
            initial["skills"] = [] if mode == "full-basicly" else ["example"]
            if mode != "full-basicly":
                rows[2]["message"]["content"][0]["text"] += "\n- example: Unique repository skill"
        result = runner.RunResult("claude", (), True, stdout=json.dumps(initial))
    elif changed == "instructions":
        rows[2]["message"]["content"][0]["text"] = (
            "- example: Unique repository skill"
            if mode == "full-basicly"
            else (
                f"Contents of {workspace / 'CLAUDE.md'} (project instructions):\n"
                "Unique repository instructions"
            )
        )
    path.write_text("\n".join(json.dumps(row) for row in rows))
    load_trial_runner()
    arms = importlib.import_module("harness_trial_arms")
    report = {
        "session_id": SESSION,
        "effort": "high",
        "arm_conditions": {"mode": mode, "loading_verified": False},
    }
    spec = runner.RunnerSpec("claude", model=MODEL)
    if changed == "none":
        arms.verify_value_loading(spec, workspace, report, result)
        assert report["arm_conditions"]["loading_verified"] is True
    else:
        with pytest.raises(ValueError, match="declared comparison arm"):
            arms.verify_value_loading(spec, workspace, report, result)
        assert report["arm_conditions"]["loading_verified"] is False


@pytest.mark.parametrize(
    "damage", ["type", "cwd", "hash", "reference", "model", "effort", "skills"]
)
def test_claude_loading_refuses_malformed_native_fields(tmp_path: Path, damage: str) -> None:
    workspace = tmp_path / "workspace"
    installation(workspace)
    path, rows, result = native_trace(workspace, tmp_path / "sessions", loaded=True)
    if damage == "type":
        rows[0]["type"] = 1
    elif damage == "cwd":
        rows[0]["cwd"] = None
    elif damage == "hash":
        rows[1]["shapeHash"] = []
    elif damage == "reference":
        rows[3]["tail"] = [[]]
    elif damage == "model":
        rows[3]["params"]["model"] = []
    elif damage == "effort":
        rows[3]["params"]["output_config"]["effort"] = []
    else:
        initial = json.loads(result.stdout)
        initial["skills"] = None
        result = runner.RunResult("claude", (), True, stdout=json.dumps(initial))
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="native Claude"):
        module().loading_evidence(workspace, SESSION, result, sessions=tmp_path / "sessions")


def test_claude_loading_does_not_treat_quoted_instruction_text_as_loaded_memory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    installation(workspace)
    path, rows, result = native_trace(workspace, tmp_path / "sessions", loaded=False)
    rows[2]["message"]["content"][0]["text"] = "Please discuss Unique repository instructions"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    evidence = module().loading_evidence(workspace, SESSION, result, sessions=tmp_path / "sessions")
    assert evidence["instructions_loaded"] is False
