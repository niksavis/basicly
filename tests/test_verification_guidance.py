from __future__ import annotations

import re
from pathlib import Path

import pytest

VERIFICATION = (
    Path(__file__).parent.parent / ".agents/skills/harness-loop/references/verification.md"
).read_text(encoding="utf-8")


@pytest.fixture(params=["AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md"])
def baseline(request: pytest.FixtureRequest) -> str:
    return (Path(__file__).parent.parent / request.param).read_text(encoding="utf-8")


def test_commands_put_focused_checks_before_required_full_verification(baseline: str) -> None:
    commands = baseline.split("## Commands\n", 1)[1].split("\n## ", 1)[0]
    assert "whole suite then one file" not in commands
    assert commands.index("uv run pytest tests/test_cli.py -q") < commands.index(
        "uv run basicly verify --mode full"
    )


@pytest.mark.parametrize(
    "required",
    [
        pytest.param(("focused checks while editing", "cheap gates first"), id="small-code-edit"),
        pytest.param(
            (
                "tracker-only or phase-only",
                "rerun affected checks",
                "existing engine",
                "equivalent trusted inputs",
            ),
            id="tracker-only-update",
        ),
        pytest.param(
            ("source or environment inputs change", "rerun affected checks"), id="changed-inputs"
        ),
        pytest.param(("check fails", "fix its cause", "rerun"), id="failed-check"),
        pytest.param(
            ("required full verification", "integrated candidate"), id="release-candidate"
        ),
    ],
)
def test_the_verification_reference_states_the_required_action_for_each_scenario(
    required: tuple[str, ...],
) -> None:
    assert any(all(term in line for term in required) for line in VERIFICATION.splitlines())


def test_the_quality_gate_points_each_scenario_at_the_verification_reference(
    baseline: str,
) -> None:
    quality = re.split(r"\n#{2,3} ", baseline.split("### Quality Gate\n", 1)[1], maxsplit=1)[0]
    assert "`harness-loop` skill's verification reference" in quality


def test_verification_guidance_preserves_mandatory_gates(baseline: str) -> None:
    assert "Keep all required gates enabled" in baseline
    assert "never defeat a gate to make it pass" in baseline
    assert "Do not skip or weaken a test, a lint or a type check" in baseline
    assert (
        "a commit or phase transition alone does not invalidate equivalent evidence"
        in VERIFICATION.lower()
    )
