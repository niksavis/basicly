from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_kit_claude_hook import (
    AGENT_NAME,
    DECLARED_TIER,
    MAP,
    REFERENCE_MAP,
    _consumer_repo,
    _payload,
    _run_hook,
)


def test_a_tier_the_map_marks_unavailable_refuses_the_spawn(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)
    installed = consumer / ".basicly" / "core" / "models" / MAP.name
    doctored = json.loads(json.dumps(REFERENCE_MAP))
    cell = doctored["tiers"][DECLARED_TIER]["vendors"]["anthropic"]["surfaces"]["anthropic"]
    cell["status"] = "unavailable"
    cell.pop("model", None)
    installed.write_text(json.dumps(doctored), encoding="utf-8")

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert DECLARED_TIER in specific["permissionDecisionReason"]
    assert AGENT_NAME in specific["permissionDecisionReason"]
    assert "model map" in specific["permissionDecisionReason"]


@pytest.mark.parametrize("mapping", ["not json", "{}", "[]"])
def test_an_unreadable_map_refuses_a_declared_tier(mapping: str, tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)
    installed = consumer / ".basicly" / "core" / "models" / MAP.name
    installed.write_text(mapping, encoding="utf-8")

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert DECLARED_TIER in specific["permissionDecisionReason"]


def test_an_unknown_declared_tier_refuses_the_spawn(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path, tier="unsupported-tier")

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert "unsupported-tier" in specific["permissionDecisionReason"]


def test_an_explicit_model_still_works_with_an_unreadable_map(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)
    installed = consumer / ".basicly" / "core" / "models" / MAP.name
    installed.write_text("not json", encoding="utf-8")

    result = _run_hook(_payload(consumer, model="opus"), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
