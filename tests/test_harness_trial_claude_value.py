from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import runner
from tests.test_harness_trial_claude import MODEL, SESSION, native_trace
from tests.test_harness_trial_results import load_results
from tests.test_harness_trial_runner import case, identity, load_trial_runner


@pytest.mark.parametrize("mode", ["plain", "gates-only", "full-basicly"])
@pytest.mark.parametrize("trace_available", [False, True])
def test_claude_value_trials_accept_only_verified_loading_and_retain_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, trace_available: bool
) -> None:
    module = load_trial_runner()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    files = {
        ".claude/CLAUDE.md": "Unique repository instructions",
        ".claude/skills/example/SKILL.md": (
            "---\nname: example\ndescription: Unique repository skill\n---\nPerform the check.\n"
        ),
        ".git/hooks/pre-commit": "gate",
        ".git/hooks/commit-msg": "gate",
        "basicly.toml": '[[verify.checks]]\nname="contract"\ncommand=["contract"]\n',
    }
    script = (
        "from pathlib import Path; "
        f"files={files!r}; "
        "[(Path(name).parent.mkdir(parents=True,exist_ok=True),Path(name).write_text(text)) "
        "for name,text in files.items()]"
    )
    selected = {
        **case(),
        "arm_mode": mode,
        "setup": [
            [
                "git",
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            *([[sys.executable, "-c", script]] if mode != "plain" else []),
        ],
    }

    def run_fixture(
        spec: runner.RunnerSpec, prompt: str, cwd: Path, **kwargs: Any
    ) -> runner.RunResult:
        assert "--permission-prompts" in spec.command
        assert prompt == selected["prompt"]
        assert kwargs["capture_usage"] is True
        path, _, result = native_trace(
            cwd, tmp_path / "config/projects", loaded=mode == "full-basicly"
        )
        if not trace_available:
            path.unlink()
        (cwd / "value.txt").write_text("after")
        events = [
            json.loads(result.stdout),
            {"type": "assistant", "message": {"model": MODEL, "content": []}},
            {
                "type": "result",
                "session_id": SESSION,
                "total_cost_usd": 0.25,
                "usage": {"input_tokens": 30, "output_tokens": 11},
                "modelUsage": {MODEL: {}},
            },
        ]
        return runner.RunResult(
            "claude",
            spec.command,
            True,
            returncode=0,
            stdout="\n".join(json.dumps(event) for event in events),
        )

    monkeypatch.setattr(runner, "run", run_fixture)
    spec = runner.RunnerSpec(
        "claude",
        command=("claude", "-p", "{prompt}"),
        model=MODEL,
        usage_format=runner.CLAUDE_STREAM_JSON,
    )
    report = module.execute_trial(
        spec,
        selected,
        tmp_path / "trial",
        {**identity(), "family": "claude", "model": MODEL, "effort": "high", "arm": mode},
    )

    assert report["outcome"] == ("passed" if trace_available else "infrastructure_error")
    assert report["arm_conditions"]["loading_verified"] is trace_available
    assert report["acceptance"]["outcome"] == ("passed" if trace_available else "failed")
    assert report["tokens"] == 41
    assert report["cost"] == 0.25
    summary = load_results().summarize([report])[0]
    assert summary["tokens_per_accepted"] == (41 if trace_available else None)
    assert summary["accepted"] == (1 if trace_available else 0)
