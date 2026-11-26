from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_orch_delegate import _args, _calls, _session, world

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))

console = importlib.import_module("orch_relay.console")
delegate = importlib.import_module("orch_relay.delegate")
log = importlib.import_module("orch_lessons.log")

__all__ = ["world"]

READ = {"vendor": "claude", "record": "app-x1", "prompt": "p", "billing": "console"}


@pytest.fixture
def billed(world: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    settings = tmp_path / "console-settings.json"
    settings.write_text("{}", encoding="utf-8")
    profile = tmp_path / "console-profile"
    profile.mkdir()
    config = {
        "settings": str(settings),
        "profile": str(profile),
        "run_cap_usd": 0.05,
        "month_budget_usd": 1,
        "tiers": {
            "cheap": {"model": "haiku", "effort": "low", "mode": "bare"},
            "coding": {"model": "sonnet", "effort": "medium", "mode": "profile"},
        },
    }
    world.base.mkdir()
    (world.base / console.FILE).write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("FAKE_COST", "0.0123")
    return SimpleNamespace(world=world, settings=settings, profile=profile, config=config)


def _ledger(world: SimpleNamespace) -> list[dict[str, Any]]:
    lines = (world.base / console.LEDGER).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if json.loads(line)["state"] == "settled"]


def _run(world: SimpleNamespace, **given: Any) -> list[str]:
    return delegate.run(_args(**{**READ, **given}), world.repo, world.base, world.ports)


def test_a_bare_read_only_console_run_caps_the_spend_and_records_its_cost(billed: Any) -> None:
    world = billed.world
    lines = _run(world, read_only=True, tier="cheap")

    run = _calls(world, "claude")[0]
    tail = ["--max-budget-usd", "0.05", "--effort", "low", "--bare", "--settings"]
    assert run["argv"][-7:] == [*tail, str(billed.settings)]
    assert run["argv"][run["argv"].index("--model") + 1] == "haiku"
    assert run["profile"] == ""
    [entry] = _ledger(world)
    pending, settled = (world.base / console.LEDGER).read_text(encoding="utf-8").splitlines()
    assert json.loads(pending)["state"] == "pending"
    assert json.loads(pending)["cost_usd"] == 0.05
    assert json.loads(pending)["id"] == json.loads(settled)["id"]
    assert sorted(entry) == sorted(console.ENTRY)
    assert (entry["cost_usd"], entry["estimated"], entry["tier"]) == (0.0123, False, "cheap")
    assert entry["session"] == _session(lines)
    assert (
        lines[-1]
        == f"spend: 0.012300 USD this run (measured); 0.0123 of 1.00 USD in {entry['month']}"
    )


def test_a_profile_console_run_works_in_its_lane_with_the_profile_and_resumes_on_console(
    billed: Any,
) -> None:
    world = billed.world
    session = _session(_run(world, model="sonnet"))
    delegate.run(_args(resume=session, prompt="again"), world.repo, world.base, world.ports)

    first, again = _calls(world, "claude")
    lane = Path(first["cwd"])
    assert (lane / ".git").is_file()
    assert [first["profile"], again["profile"]] == [str(billed.profile)] * 2
    assert "--bare" not in first["argv"]
    assert again["argv"][-4:] == ["--max-budget-usd", "0.05", "--effort", "medium"]
    assert [e["tier"] for e in _ledger(world)] == ["coding", "coding"]


def test_a_failed_console_run_with_no_cost_is_charged_the_run_cap(
    billed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FAIL", "claude")

    with pytest.raises(log.RefusedError, match="claude exited 2"):
        _run(billed.world, read_only=True, tier="cheap")

    [entry] = _ledger(billed.world)
    assert (entry["cost_usd"], entry["estimated"]) == (0.05, True)


def test_a_run_that_could_pass_the_month_budget_is_refused_by_name_before_it_starts(
    billed: Any,
) -> None:
    world = billed.world
    first = {"id": "a", "cost_usd": 0.5, "month": console.month()}
    spent = {"id": "a", "cost_usd": 0.96, "month": console.month()}
    old = {"id": "b", "cost_usd": 50, "month": "2000-01"}
    lines = [json.dumps(first), json.dumps(spent), json.dumps(old)]
    (world.base / console.LEDGER).write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(log.RefusedError) as refused:
        _run(world, read_only=True, tier="cheap")

    assert str(refused.value) == (
        f"console budget: 0.9600 USD spent in {console.month()} of the 1.00 USD month budget; "
        "a run may cost up to 0.05 USD, so orch delegate starts none"
    )
    assert not world.calls.exists()


@pytest.mark.parametrize(
    ("change", "given", "named"),
    [
        ({"tiers": None}, {"tier": "cheap"}, "has no valid 'tiers'"),
        ({"run_cap_usd": None}, {"tier": "cheap"}, "has no valid 'run_cap_usd'"),
        ({"month_budget_usd": 0}, {"tier": "cheap"}, "has no valid 'month_budget_usd'"),
        ({"settings": None}, {"tier": "cheap"}, "has no valid 'settings'"),
        ({"settings": "/no/such/settings.json"}, {"tier": "cheap"}, "does not exist"),
        ({}, {"model": "opus"}, "'opus' is not allowed by the Console config"),
        ({}, {"tier": "top"}, "'top' is not in the Console config"),
        ({}, {}, "needs --tier or --model"),
        ({}, {"tier": "cheap", "read_only": False}, "bare is for --read-only work"),
        ({}, {"tier": "coding"}, "runs in a profile, which needs a lane"),
    ],
)
def test_a_console_run_with_a_missing_or_wrong_setting_is_refused_by_name_before_any_turn(
    billed: Any, change: dict[str, Any], given: dict[str, Any], named: str
) -> None:
    config = {k: v for k, v in {**billed.config, **change}.items() if v is not None}
    world = billed.world
    (world.base / console.FILE).write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(log.RefusedError, match=named):
        _run(world, **{"read_only": True, **given})

    assert not world.calls.exists()


def test_no_console_config_and_a_broken_ledger_line_are_refused_by_name(billed: Any) -> None:
    world = billed.world
    (world.base / console.LEDGER).write_text("not json\n", encoding="utf-8")

    with pytest.raises(log.RefusedError, match="line 1 is not a spend entry"):
        _run(world, read_only=True, tier="cheap")
    (world.base / console.FILE).unlink()
    with pytest.raises(log.RefusedError, match="no Console config at"):
        _run(world, read_only=True, tier="cheap")

    assert not world.calls.exists()


def test_a_pending_reservation_counts_at_the_run_cap_and_the_lock_refuses_a_waiter(
    billed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = billed.world
    plan = console.plan(world.base, "cheap", None, read_only=True)
    tight = console.Plan(**{**plan.__dict__, "budget": 0.08})
    console.reserve(world.base, tight, "app-x1", "s1")

    with pytest.raises(log.RefusedError, match=r"console budget: 0\.0500 USD spent"):
        console.reserve(world.base, tight, "app-x1", "s2")
    monkeypatch.setattr(console, "LOCK_WAIT", 0.2)
    with console._locked(world.base), pytest.raises(log.RefusedError, match="stayed held"):
        console.reserve(world.base, plan, "app-x1", "s3")

    assert console.spent(world.base, console.month()) == 0.05
