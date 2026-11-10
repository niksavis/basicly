from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_kit_claude_hook import (
    HOOK,
    _consumer_repo,
    _definition,
    _payload,
    _run_hook,
    _updated_input,
    kit,
)

DEFAULT_TIER_ENV = kit.DEFAULT_TIER_ENV
WORKER_ALIAS = kit.HOST_MODEL_ALIASES["claude"]["medium"]


def _untiered_spawn(cwd: Path, **tool_input: object) -> dict:
    spawn = {"subagent_type": "general-purpose", **tool_input}
    payload = _payload(cwd, **spawn)
    if spawn["subagent_type"] is None:
        payload["tool_input"].pop("subagent_type")
    return payload


@pytest.mark.parametrize(
    ("tool_input", "env", "expected_model", "warned_value"),
    [
        ({}, {DEFAULT_TIER_ENV: "medium"}, WORKER_ALIAS, None),
        ({"subagent_type": None}, {DEFAULT_TIER_ENV: "medium"}, WORKER_ALIAS, None),
        ({"model": "opus"}, {DEFAULT_TIER_ENV: "medium"}, None, None),
        ({}, {}, None, None),
        ({}, {DEFAULT_TIER_ENV: "huge"}, None, "huge"),
        ({}, {DEFAULT_TIER_ENV: "maximum"}, None, "maximum"),
        (
            {},
            {DEFAULT_TIER_ENV: "medium", "CLAUDE_CODE_SUBAGENT_MODEL": "haiku"},
            WORKER_ALIAS,
            None,
        ),
        ({"subagent_type": "Explore"}, {DEFAULT_TIER_ENV: "medium"}, None, None),
    ],
    ids=[
        "default-applied-to-general-purpose",
        "default-applied-when-agent-type-omitted",
        "explicit-model-kept",
        "variable-unset",
        "invalid-value-warns-and-changes-nothing",
        "maximum-warns-and-changes-nothing",
        "default-applied-with-subagent-model-variable-set",
        "another-agent-type-left-alone",
    ],
)
def test_the_default_tier_reaches_only_an_untiered_general_spawn(
    tool_input: dict,
    env: dict[str, str],
    expected_model: str | None,
    warned_value: str | None,
    tmp_path: Path,
) -> None:
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(
        _untiered_spawn(consumer, **tool_input), cwd=consumer, tmp_path=tmp_path, env_extra=env
    )

    assert result.returncode == 0, result.stderr
    if expected_model is not None:
        assert _updated_input(result)["model"] == expected_model
    elif warned_value is not None:
        output = json.loads(result.stdout)
        assert set(output) == {"systemMessage"}
        message = output["systemMessage"]
        assert DEFAULT_TIER_ENV in message
        assert repr(warned_value) in message
        assert "low, medium, high" in message
    else:
        assert result.stdout == ""


def test_a_resolver_fault_never_blocks_an_agent_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "tier_resolver", kit)
    spec = importlib.util.spec_from_file_location("claude_tier_hook_under_test", HOOK)
    assert spec and spec.loader
    hook: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    reached: list[Path] = []

    def broken_find_map(root: Path, **_: object) -> Path:
        reached.append(root)
        raise RuntimeError("resolver fault")

    broken = ModuleType("broken_tier_resolver")
    broken.__dict__.update(vars(kit))
    monkeypatch.setattr(broken, "find_map", broken_find_map)
    monkeypatch.setattr(hook, "tier_resolver", broken)
    monkeypatch.setenv(DEFAULT_TIER_ENV, "medium")
    monkeypatch.delenv("CLAUDE_CODE_SUBAGENT_MODEL", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_untiered_spawn(tmp_path))))

    assert hook.main() == 0
    assert reached, "the fault was never raised, so the test proves nothing"
    assert capsys.readouterr().out == ""


def test_an_invalid_default_never_cancels_a_tier_the_definition_declares(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)
    _definition(consumer / ".claude/agents/general-purpose.md", tier="high")

    result = _run_hook(
        _untiered_spawn(consumer),
        cwd=consumer,
        tmp_path=tmp_path,
        env_extra={DEFAULT_TIER_ENV: "maximum"},
    )

    assert result.returncode == 0, result.stderr
    assert _updated_input(result)["model"] == kit.HOST_MODEL_ALIASES["claude"]["high"]


def test_an_invalid_default_is_silent_where_the_hook_has_no_map(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()

    result = _run_hook(
        _untiered_spawn(bare), cwd=bare, tmp_path=tmp_path, env_extra={DEFAULT_TIER_ENV: "huge"}
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
