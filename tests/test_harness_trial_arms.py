from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import runner


def load_trial_runner() -> Any:
    path = Path(__file__).resolve().parents[1] / ".scripts/harness_trial_runner.py"
    spec = importlib.util.spec_from_file_location("harness_trial_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as paths:
        paths.syspath_prepend(str(path.parent))
        spec.loader.exec_module(module)
    return module


def case() -> dict[str, Any]:
    return {
        "id": "synthetic-control",
        "files": {"value.txt": "before"},
        "prompt": "fixture",
        "check": [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text() == 'after'",
        ],
    }


def identity() -> dict[str, str]:
    return {
        "id": "test",
        "family": "fixture",
        "model": "fixture",
        "effort": "none",
        "arm": "control",
    }


@pytest.mark.parametrize(
    "surface",
    [
        "AGENTS.md",
        ".basicly/core/policy.txt",
        ".agents/skills/example/SKILL.md",
        ".git/hooks/pre-commit",
    ],
)
def test_plain_value_arm_refuses_contamination_before_dispatch(
    tmp_path: Path, surface: str
) -> None:
    module = load_trial_runner()
    selected = {**case(), "arm_mode": "plain", "files": {**case()["files"], surface: "unexpected"}}
    if surface.startswith(".git/"):
        selected["files"].pop(surface)
        selected["setup"] = [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('.git/hooks/pre-commit').write_text('unexpected')",
            ]
        ]
    report = module.execute_trial(
        runner.RunnerSpec("codex"), selected, tmp_path / "trial", identity()
    )
    assert report["outcome"] == "infrastructure_error"
    assert "plain arm contains" in report["error"]
    assert not (tmp_path / "trial/agent.stdout").exists()


def test_value_setup_installs_only_treated_arms(tmp_path: Path) -> None:
    module = load_trial_runner()
    select_setup = importlib.import_module("harness_trial_arms").select_setup

    selected = {
        **case(),
        "task_setup": [[sys.executable, "-c", "from pathlib import Path; Path('common').touch()"]],
        "harness_setup": [
            [sys.executable, "-c", "from pathlib import Path; Path('installed').touch()"]
        ],
    }
    for mode in ["plain", "gates-only", "full-basicly"]:
        workspace = tmp_path / mode
        module.write_fixture(workspace, selected["files"])
        assert module.prepare_case(workspace, select_setup(selected, mode), {}, 10)
        assert (workspace / "common").exists()
        assert (workspace / "installed").exists() == (mode != "plain")


def test_gates_only_keeps_installed_gates_and_suppresses_project_guidance(tmp_path: Path) -> None:
    load_trial_runner()
    configure_value_arm = importlib.import_module("harness_trial_arms").configure_value_arm

    files = {
        "AGENTS.md": "Installed policy.",
        ".agents/skills/example/SKILL.md": "Installed skill.",
        ".git/hooks/pre-commit": "pre-commit gate",
        ".git/hooks/commit-msg": "commit gate",
        "basicly.toml": (
            '[[verify.checks]]\nname="contract"\n'
            'command=["python", "test_contract.py"]\nmodes=["staged"]\n'
        ),
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    spec = runner.RunnerSpec(
        "codex", command=("codex", "exec"), sandbox="workspace-write", approval="never"
    )
    gates, gate_evidence = configure_value_arm(spec, "gates-only", tmp_path)
    full, full_evidence = configure_value_arm(spec, "full-basicly", tmp_path)
    assert gates.sandbox == full.sandbox == "workspace-write"
    assert gates.approval == full.approval == "never"
    assert "project_doc_max_bytes=0" in gates.command
    assert any("skills.config=[" in arg and "enabled=false" in arg for arg in gates.command)
    assert full.command == spec.command
    assert gate_evidence["hook_sha256"] == full_evidence["hook_sha256"]
    assert gate_evidence["configuration_sha256"] == full_evidence["configuration_sha256"]
    assert gate_evidence["loading_verified"] is False
    assert all((tmp_path / name).read_text() == text for name, text in files.items())


def test_value_arm_refuses_vacuous_verification(tmp_path: Path) -> None:
    load_trial_runner()
    configure_value_arm = importlib.import_module("harness_trial_arms").configure_value_arm

    for name in ["pre-commit", "commit-msg"]:
        path = tmp_path / ".git/hooks" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (tmp_path / "basicly.toml").write_text("[verify]\n")
    with pytest.raises(ValueError, match="configured verification checks"):
        configure_value_arm(runner.RunnerSpec("codex"), "gates-only", tmp_path)


def test_value_arm_refuses_changed_task_inputs(tmp_path: Path) -> None:
    module = load_trial_runner()
    (tmp_path / "value.txt").write_text("after")
    report = {"task_files": {"value.txt": "file", "unchanged-hash": "before"}}
    with pytest.raises(ValueError, match="changed task input files"):
        module.bind_value_arm(
            runner.RunnerSpec("codex"),
            tmp_path,
            {**case(), "arm_mode": "plain"},
            report,
            timeout=10,
        )


def test_plain_value_arm_requires_a_starting_commit(tmp_path: Path) -> None:
    module = load_trial_runner()
    (tmp_path / "value.txt").write_text("before")
    selected = {**case(), "arm_mode": "plain"}
    report = {"task_files": module.fingerprint(tmp_path, ["value.txt"])}
    with pytest.raises(ValueError, match="starting Git commit"):
        module.bind_value_arm(runner.RunnerSpec("codex"), tmp_path, selected, report, timeout=10)


@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("aliased", [False, True])
def test_value_loading_uses_initial_native_context(
    tmp_path: Path, loaded: bool, aliased: bool
) -> None:
    load_trial_runner()
    observe = importlib.import_module("harness_trial_arms").loading_evidence
    workspace = tmp_path / "workspace"
    skill = workspace / ".agents/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Example skill")
    (workspace / "AGENTS.md").write_text("Unique repository instructions")
    session = "00000000-0000-0000-0000-000000000001"
    initial = (
        "Unique repository instructions\n" + str(skill) if loaded else "Common host instructions"
    )
    if loaded and aliased:
        initial = (
            "Unique repository instructions\n"
            + f"- `r6` = `{workspace / '.agents/skills'}`\n"
            + "- example: Example skill. (file: r6/example/SKILL.md)"
        )
    events = [
        {"type": "session_meta", "payload": {"id": session, "cwd": str(workspace)}},
        {
            "type": "turn_context",
            "payload": {
                "model": "fixture-model",
                "effort": "medium",
                "approval_policy": "never",
                "sandbox_policy": {"type": "workspace-write", "network_access": False},
            },
        },
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "developer", "content": [{"text": initial}]},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"text": "Start work."}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"text": "Unique repository instructions\n" + str(skill)}],
            },
        },
    ]
    trace = tmp_path / f"rollout-{session}.jsonl"
    trace.write_text("\n".join(json.dumps(event) for event in events))
    result = observe(workspace, session, "fixture-model", sessions=tmp_path)
    assert result["instructions_loaded"] is loaded
    assert bool(result["project_skills_loaded"]) is loaded
    assert result["native_model_verified"] is True
    assert result["native_effort"] == ["medium"]
    assert result["native_approval"] == ["never"]
    assert "Unique repository instructions" not in json.dumps(result)
    with pytest.raises(ValueError, match="does not belong"):
        observe(tmp_path / "other", session, "fixture-model", sessions=tmp_path)
