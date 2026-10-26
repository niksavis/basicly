from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import verify
from basicly.config import VerifyCheck, VerifyConfig
from basicly.verify_artifact import RUN_ARTIFACT
from basicly.verify_receipt import InputFingerprint


def _config() -> VerifyConfig:
    return VerifyConfig((
        VerifyCheck(
            "types", ("pyright", "--pythonplatform", "Linux"), frozenset({"full"}), reuse="pyright"
        ),
        VerifyCheck("tests", ("pytest",), frozenset({"full"})),
    ))


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def spawn(command, _root, **_kw):
        calls.append(command[0])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(verify, "_spawn", spawn)
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: "unchanged")
    return calls


def test_only_proven_checks_reuse_and_other_checks_execute(tmp_path: Path, runs: list[str]) -> None:
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    landed = verify.run_verify(tmp_path, "full", _config(), reuse=origin)

    assert runs == ["pyright", "pytest", "pytest"]
    assert landed.passed
    assert landed.results[0].reused_from == origin.run_id
    assert landed.results[1].reused_from == ""


def test_source_evidence_carries_the_receipt_provenance(tmp_path: Path, runs: list[str]) -> None:
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    body = json.loads((tmp_path / RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert runs == ["pyright", "pytest"]
    assert body["receipts"] == [
        {
            "name": "types",
            "command": ["pyright", "--pythonplatform", "Linux"],
            "mode": "full",
            "inputs": "unchanged",
            "origin": origin.run_id,
        }
    ]


def test_ordinary_verify_never_reuses(tmp_path: Path, runs: list[str]) -> None:
    verify.run_verify(tmp_path, "full", _config())
    verify.run_verify(tmp_path, "full", _config())
    assert runs == ["pyright", "pytest", "pyright", "pytest"]


def test_changed_inputs_execute_fresh(
    tmp_path: Path, runs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: "changed")

    landed = verify.run_verify(tmp_path, "full", _config(), reuse=origin)

    assert runs == ["pyright", "pytest", "pyright", "pytest"]
    assert not landed.results[0].reused_from
    assert "changed" in landed.results[0].reuse_reason


def test_inputs_mutated_by_other_checks_revoke_borrowed_results(
    tmp_path: Path, runs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    fingerprints = iter(("unchanged", "changed"))
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: next(fingerprints))

    landed = verify.run_verify(tmp_path, "full", _config(), reuse=origin)

    assert runs == ["pyright", "pytest", "pytest", "pyright"]
    assert all(not result.reused_from for result in landed.results)


def test_failed_source_report_cannot_supply_receipts(
    tmp_path: Path, runs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(command, _root, **_kw):
        runs.append(command[0])
        return subprocess.CompletedProcess(command, 1, "failed", "")

    monkeypatch.setattr(verify, "_spawn", fail)
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    landed = verify.run_verify(tmp_path, "full", _config(), reuse=origin)
    assert runs == ["pyright", "pytest", "pyright", "pytest"]
    assert not landed.passed
    assert not origin.receipts


@pytest.mark.parametrize("difference", ["command", "mode", "missing", "unsupported", "skipped"])
def test_ineligible_proof_executes_fresh(tmp_path: Path, runs: list[str], difference: str) -> None:
    config = _config()
    origin = verify.run_verify(tmp_path, "full", config, capture_receipts=True)
    mode = "full"
    if difference == "command":
        config = VerifyConfig((
            replace(config.checks[0], command=("pyright", "--pythonplatform", "Darwin")),
        ))
    elif difference == "mode":
        mode = "fast"
        config = VerifyConfig((replace(config.checks[0], modes=frozenset({"fast"})),))
    elif difference == "missing":
        origin = replace(origin, receipts=())
    elif difference == "unsupported":
        config = VerifyConfig((replace(config.checks[0], command=("pyright",)),))
    else:
        origin = replace(origin, results=(replace(origin.results[0], status="skip"),))

    landed = verify.run_verify(tmp_path, mode, config, reuse=origin)

    assert runs.count("pyright") == 2
    assert not landed.results[0].reused_from
    assert landed.results[0].reuse_reason


def test_unsupported_fingerprint_executes_and_explains_fallback(
    tmp_path: Path, runs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_root):
        raise verify.pyright_inputs.UnsupportedInputsError("unknown runtime")

    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", unavailable)
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    landed = verify.run_verify(tmp_path, "full", _config(), reuse=origin)

    assert runs == ["pyright", "pytest", "pyright", "pytest"]
    assert not origin.receipts
    assert landed.results[0].reuse_reason == "unknown runtime"


def test_revoked_reuse_preserves_the_fresh_failure(
    tmp_path: Path, runs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    fingerprints = iter(("unchanged", "changed"))
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: next(fingerprints))

    def spawn(command, _root, **_kw):
        runs.append(command[0])
        return subprocess.CompletedProcess(command, int(command[0] == "pyright"), "", "")

    monkeypatch.setattr(verify, "_spawn", spawn)
    landed = verify.run_verify(tmp_path, "full", _config(), reuse=origin)
    assert not landed.passed
    assert landed.failures == ("types",)


@pytest.mark.parametrize("component", ["workspace", "runtime", "environment", "policy"])
def test_receipt_miss_explains_actual_changed_components_without_secret_values(
    tmp_path: Path, runs: list[str], monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    payload = {
        "workspace": "source",
        "runtime": "runtime",
        "environment": "secret-original",
        "policy": "policy",
    }
    monkeypatch.setattr(
        verify.pyright_inputs, "fingerprint", lambda _root: InputFingerprint(payload)
    )
    origin = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    payload[component] = "secret-new-value"
    current = verify.run_verify(tmp_path, "full", _config(), reuse=origin)
    assert current.results[0].reuse_reason == f"changed input components: {component}"
    assert runs == ["pyright", "pytest", "pyright", "pytest"]
    artifact = (tmp_path / RUN_ARTIFACT).read_text()
    assert "secret-new-value" not in artifact
    assert "secret-original" not in artifact
