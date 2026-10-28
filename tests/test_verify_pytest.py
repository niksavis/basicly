from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import pyright_inputs, verify
from basicly.config import VerifyCheck, VerifyConfig

COMMAND = (
    "pytest",
    "-q",
    "-n",
    "{workers}",
    "--durations=25",
    "-m",
    "not live_ledger and not fresh_runtime",
)


def _config() -> VerifyConfig:
    return VerifyConfig(
        (
            VerifyCheck("pytest", COMMAND, frozenset({"full"}), reuse="pytest"),
            VerifyCheck("ledger", ("ledger-check",), frozenset({"full"})),
        ),
        cache=True,
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def spawn(command, _root, **_kwargs):
        calls.append(command[0])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(verify, "_spawn", spawn)
    monkeypatch.setattr(verify.pytest_inputs, "fingerprint", lambda _root: "trusted")
    return calls


def test_passing_code_suite_reuses_but_live_ledger_check_executes(
    tmp_path: Path, calls: list[str]
) -> None:
    first = verify.run_verify(tmp_path, "full", _config())
    second = verify.run_verify(tmp_path, "full", _config())
    assert calls == ["pytest", "ledger-check", "ledger-check"]
    assert second.results[0].reused_from == first.run_id
    assert second.results[1].reused_from == ""


def test_changed_code_runs_again_and_preserves_the_failure(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify.run_verify(tmp_path, "full", _config())
    monkeypatch.setattr(verify.pytest_inputs, "fingerprint", lambda _root: "changed")

    def fail(command, _root, **_kwargs):
        calls.append(command[0])
        return subprocess.CompletedProcess(command, int(command[0] == "pytest"), "failed", "")

    monkeypatch.setattr(verify, "_spawn", fail)
    result = verify.run_verify(tmp_path, "full", _config())
    assert calls.count("pytest") == 2
    assert result.failures == ("pytest",)
    assert not result.results[0].reused_from


def test_unsupported_runtime_executes_fresh(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify.run_verify(tmp_path, "full", _config())

    def unavailable(_root):
        raise pyright_inputs.UnsupportedInputsError("unknown test runtime")

    monkeypatch.setattr(verify.pytest_inputs, "fingerprint", unavailable)
    result = verify.run_verify(tmp_path, "full", _config())
    assert calls.count("pytest") == 2
    assert result.results[0].reuse_reason == "unknown test runtime"


def test_mutation_during_verification_revokes_reuse(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify.run_verify(tmp_path, "full", _config())
    fingerprints = iter(("trusted", "changed"))
    monkeypatch.setattr(verify.pytest_inputs, "fingerprint", lambda _root: next(fingerprints))
    result = verify.run_verify(tmp_path, "full", _config())
    assert calls == ["pytest", "ledger-check", "ledger-check", "pytest"]
    assert not result.results[0].reused_from


def test_pytest_and_compiler_proofs_are_independent(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: "compiler")
    compiler = VerifyCheck(
        "types", ("pyright", "--pythonplatform", "Linux"), frozenset({"full"}), reuse="pyright"
    )
    config = replace(_config(), checks=(*_config().checks, compiler))
    origin = verify.run_verify(tmp_path, "full", config, capture_receipts=True)
    monkeypatch.setattr(verify.pytest_inputs, "fingerprint", lambda _root: "changed")
    result = verify.run_verify(tmp_path, "full", config, reuse=origin)
    assert calls.count("pytest") == 2
    assert calls.count("pyright") == 1
    assert result.results[2].reused_from == origin.run_id


@pytest.mark.parametrize(
    ("case", "controlled"),
    [
        (("pytest", "full", COMMAND), True),
        ((None, "full", COMMAND), False),
        (("pytest", "fast", COMMAND), False),
        (("pytest", "full", ("pytest", "-q")), False),
    ],
)
def test_only_the_opted_in_supported_suite_controls_its_execution_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str | None, str, tuple[str, ...]],
    controlled: bool,
) -> None:
    recipe, mode, command = case
    environment = {
        "PATH": "tools",
        "HTTP_PROXY": "rotating-transport",
        "CODEX_TOOL_CALL_ID": "call-1",
        "NO_PROXY": "localhost",
        "PYTHONWARNINGS": "default",
    }
    observed: list[dict[str, str]] = []

    def execute(argv, **kwargs):
        observed.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(verify.affected, "environment", lambda _root: environment)
    monkeypatch.setattr(verify.subprocess, "run", execute)
    check = VerifyCheck("tests", command, frozenset({mode}), reuse=recipe)
    result = verify.run_check(check, tmp_path, mode, capture=True)
    assert result.status == "pass"
    expected = (
        verify.pytest_inputs.execution_environment(environment) if controlled else environment
    )
    assert observed == [expected]
    assert observed[0]["PYTHONWARNINGS"] == "default"
    assert observed[0]["NO_PROXY"] == "localhost"
    assert "HTTP_PROXY" in environment
