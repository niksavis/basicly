from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from basicly import config, verify

HOOKS = Path(__file__).resolve().parents[1] / ".basicly/core/hooks"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"test_{name}", HOOKS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("result", [0, 1, 2, 125])
def test_engine_failure_is_final_and_only_unavailable_engine_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: int
) -> None:
    hook = load("pre-push")
    monkeypatch.setenv("CI", "true")
    calls: list[str] = []
    (tmp_path / "basicly.toml").write_text("[verify]\ncache = true\n")
    monkeypatch.setattr(hook, "project_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "ledger_write_in_flight", lambda _: False)
    monkeypatch.setattr(hook, "cache_savings", lambda _: 10)
    monkeypatch.setattr(
        hook.subprocess, "run", lambda *a, **_kw: subprocess.CompletedProcess(a[0], result)
    )
    monkeypatch.setattr(hook, "run_checks", lambda *_a: calls.append("native") or 0)
    assert hook.main() == (0 if result == 125 else result)
    assert calls == (["native"] if result == 125 else [])


@pytest.mark.parametrize("enabled", [False, True])
def test_no_profitable_cache_does_not_start_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    hook = load("pre-push")
    monkeypatch.setenv("CI", "true")
    (tmp_path / "basicly.toml").write_text(f"[verify]\ncache={str(enabled).lower()}\n")
    monkeypatch.setattr(hook, "project_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "ledger_write_in_flight", lambda _: False)
    monkeypatch.setattr(hook.subprocess, "run", lambda *_a, **_kw: pytest.fail("started engine"))
    monkeypatch.setattr(hook, "run_checks", lambda *_a: 0)
    assert hook.main() == 0


@pytest.mark.parametrize(
    "overhead, durations, expected",
    [
        (2, [1, 3], 2),
        (2, [0.2, 0.3], 0),
        (-1, [10], 0),
        (1, [float("nan")], 0),
    ],
)
def test_cost_hint_keeps_cheap_or_invalid_evidence_on_fresh_execution(
    tmp_path: Path, overhead: float, durations: list[float], expected: float
) -> None:
    engine = load("engine_verify")
    path = tmp_path / ".basicly/usage/verify-cache.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"overhead_s": overhead, "checks": [{"duration_s": d} for d in durations]})
    )
    assert engine.cache_savings(tmp_path) == expected


def test_cli_evidence_is_reused_by_push_and_changed_inputs_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = load("engine_verify")
    (tmp_path / "basicly.toml").write_text(
        '[verify]\ncache=true\n[[verify.checks]]\nname="types"\n'
        'command=["pyright","--pythonplatform","Linux"]\nmodes=["full"]\nreuse="pyright"\n'
        '[[verify.checks]]\nname="other"\ncommand=["other"]\nmodes=["full"]\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(engine, "project_root", lambda: tmp_path)
    inputs = ["a" * 64]
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _: inputs[0])
    calls: list[str] = []

    def execute(command, _root, **_kwargs):
        calls.append(command[0])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verify, "_spawn", execute)
    assert verify.run_verify(tmp_path, "full").passed
    assert engine.main() == 0
    assert calls == ["pyright", "other", "other"]
    inputs[0] = "b" * 64
    assert engine.main() == 0
    assert calls == ["pyright", "other", "other", "pyright", "other"]


def test_consumer_without_engine_returns_fallback_without_running_checks(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(HOOKS / "engine_verify.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 125, result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mismatch", ["inventory", "fail_fast", "cache"])
def test_engine_overrides_cannot_change_native_push_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    engine = load("engine_verify")
    (tmp_path / "basicly.toml").write_text(
        '[verify]\ncache=true\n[[verify.checks]]\nname="check"\ncommand=["check"]\nmodes=["full"]\n'
    )
    settings = config.load_verify_config(tmp_path)
    if mismatch == "inventory":
        settings = replace(settings, checks=())
    elif mismatch == "fail_fast":
        settings = replace(settings, fail_fast=True)
    else:
        settings = replace(settings, cache=False)
    monkeypatch.setattr(engine, "project_root", lambda: tmp_path)
    monkeypatch.setattr(config, "load_verify_config", lambda _: settings)
    monkeypatch.setattr(verify, "run_verify", lambda *_a: pytest.fail("ran changed inventory"))
    assert engine.main() == 125
