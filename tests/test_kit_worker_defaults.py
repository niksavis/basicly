from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_kit_claude_hook import (
    HOOK,
    RESOLVER,
    _consumer_repo,
    _definition,
    _payload,
    _pruned_env,
    _run_hook,
    _vendored_kit,
    kit,
)


@pytest.mark.parametrize("setting", ["maximum", "huge", " MAXIMUM "])
@pytest.mark.parametrize("origin", ["environment", "argument"])
def test_invalid_worker_defaults_agree_between_cli_and_hook(
    tmp_path: Path, setting: str, origin: str
) -> None:
    consumer = _consumer_repo(tmp_path)
    env = _pruned_env(tmp_path, {kit.DEFAULT_TIER_ENV: setting})
    command = [
        sys.executable,
        "-S",
        "-I",
        str(RESOLVER),
        "--host",
        "claude",
        "--root",
        str(consumer),
    ]
    if origin == "argument":
        command.extend(["--default-tier", setting])
        env[kit.DEFAULT_TIER_ENV] = "high"
    result = subprocess.run(
        command, cwd=consumer, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 1, result.stdout
    resolved = json.loads(result.stdout)
    assert resolved["model"] is None
    assert resolved["alias"] is None
    assert resolved["source"] == "default"
    hook = _run_hook(
        _payload(consumer, subagent_type="general-purpose"),
        cwd=consumer,
        tmp_path=tmp_path,
        env_extra={kit.DEFAULT_TIER_ENV: setting},
    )
    assert hook.returncode == 0
    warning = json.loads(hook.stdout)["systemMessage"]
    assert warning == resolved["reason"]
    assert "low, medium, high" in warning


@pytest.mark.parametrize("tier", ["low", "medium", "high"])
def test_worker_defaults_keep_resolving(tier: str) -> None:
    resolver = kit.TierResolver(
        {
            "tiers": {
                tier: {
                    "vendors": {
                        "anthropic": {
                            "surfaces": {
                                "anthropic": {"status": "available", "model": "fixture-model"}
                            }
                        }
                    }
                }
            }
        },
        default_tier=tier,
    )
    result = resolver.resolve("claude")
    assert result.model == "fixture-model"
    assert result.source == "default"


@pytest.mark.parametrize("source", ["argument", "definition"])
def test_explicit_maximum_overrides_an_invalid_default(tmp_path: Path, source: str) -> None:
    consumer = _consumer_repo(tmp_path)
    resolver = kit.TierResolver.discover(consumer, default_tier="maximum")
    assert resolver is not None
    definition = _definition(consumer / "advisor.md", tier="maximum")
    result = resolver.resolve(
        "claude", tier="maximum" if source == "argument" else None, definition=definition
    )
    assert result.model is not None
    assert result.alias == "fable"
    assert result.source == source


def test_an_explicit_maximum_model_warns_without_changing_or_denying_input(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)
    result = _run_hook(_payload(consumer, model="fable"), cwd=consumer, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert set(output) == {"systemMessage"}
    assert "maximum" in output["systemMessage"]
    assert "advice or orchestration" in output["systemMessage"]
    assert "worker" in output["systemMessage"]


@pytest.mark.parametrize("setting", ["medium", "maximum"])
def test_a_copied_consumer_kit_keeps_cli_and_hook_defaults_consistent(
    tmp_path: Path, setting: str
) -> None:
    consumer = tmp_path / "consumer"
    copied = _vendored_kit(consumer)
    env = _pruned_env(tmp_path, {kit.DEFAULT_TIER_ENV: setting})
    cli = subprocess.run(
        [sys.executable, "-S", "-I", str(copied / RESOLVER.name), "--host", "claude"],
        cwd=consumer,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = _payload(consumer, subagent_type="general-purpose")
    hook = subprocess.run(
        [sys.executable, "-S", "-I", str(copied / HOOK.name)],
        input=json.dumps(payload),
        cwd=consumer,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert hook.returncode == 0, hook.stderr
    resolved = json.loads(cli.stdout)
    output = json.loads(hook.stdout)
    if setting == "maximum":
        assert cli.returncode == 1
        assert resolved["model"] is None
        assert resolved["reason"] == output["systemMessage"]
    else:
        assert cli.returncode == 0, cli.stderr
        assert (
            resolved["alias"] == output["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"
        )
