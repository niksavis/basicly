from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_orch_delegate import _args, world

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))

adapters = importlib.import_module("orch_relay.adapters")
delegate = importlib.import_module("orch_relay.delegate")
failures = importlib.import_module("orch_relay.failures")
log = importlib.import_module("orch_lessons.log")
runner = importlib.import_module("runner")

FIXTURES = sorted((ROOT / "tests" / "fixtures" / "delegate_failures").glob("*.json"))

__all__ = ["world"]


def test_a_failed_turn_is_saved_with_the_prompt_hidden_and_named_in_the_refusal(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FAIL", "claude")

    with pytest.raises(log.RefusedError, match="the failure is saved in") as refused:
        delegate.run(
            _args(vendor="claude", record="app-x1", prompt="secret plan"),
            world.repo,
            world.base,
            world.ports,
        )

    [saved] = (world.base / delegate.FOLDER / failures.FOLDER).glob("*-claude-*.json")
    value = json.loads(saved.read_text(encoding="utf-8"))
    assert str(saved) in str(refused.value)
    assert value["source"] == "captured"
    assert (value["vendor"], value["exit_code"]) == ("claude", 2)
    assert value["argv"][:3] == ["claude", "-p", "PROMPT"]
    assert "secret plan" not in saved.read_text(encoding="utf-8")
    assert value["stderr"] == "ERROR: the model refused the request\n"
    assert world.tracker.comments == []


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_a_saved_failure_replays_to_its_expected_outcome(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    done = runner.Done(value["exit_code"], value["stdout"], value["stderr"])

    limited = adapters.usage_limit(value["vendor"], done)

    assert value["expect"]["outcome"] == "usage_limit"
    assert limited is not None
    assert str(limited).endswith(f"it resets {value['expect']['resets']}")


def test_only_the_fake_fixture_is_marked_fake_and_every_fixture_is_redacted() -> None:
    sources = [json.loads(p.read_text(encoding="utf-8"))["source"] for p in FIXTURES]

    assert sources.count("fake") == 1
    for path in FIXTURES:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert failures.redact(value, ROOT, "") == value


def test_redaction_hides_home_paths_the_user_name_and_tokens() -> None:
    token = "ghp_" + "a1B2" * 9
    value = {
        "stderr": f"open /home/alice/work/x.py failed for alice with {token}",
        "argv": ["codex", "exec", "PROMPT"],
    }

    cleaned = failures.redact(value, ROOT, "alice")

    text = json.dumps(cleaned)
    assert "alice" not in text
    assert token not in text
    assert "redacted:github-token" in text
    assert "redacted:posix-home-path" in text
    assert cleaned["argv"] == ["codex", "exec", "PROMPT"]


def test_redaction_refuses_a_repository_without_the_tracker_kit(tmp_path: Path) -> None:
    with pytest.raises(log.RefusedError, match=r"redact\.py is missing"):
        failures.redact({}, tmp_path, "alice")
