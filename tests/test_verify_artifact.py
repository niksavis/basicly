from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import policy, verify, verify_artifact
from basicly.config import PolicyConfig, VerifyCheck, VerifyConfig

if TYPE_CHECKING:
    import pytest


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _as_spawn(fake_run):

    def spawn(command, repo_root, *, capture):
        return fake_run(
            command,
            cwd=repo_root,
            env=verify.sanitised_project_env(os.environ, repo_root),
            check=False,
            capture_output=capture,
            text=capture or None,
        )

    return spawn


def _check(name: str, modes: tuple[str, ...], staged_suffix: str | None = None) -> VerifyCheck:
    return VerifyCheck(
        name=name, command=(name,), modes=frozenset(modes), staged_suffix=staged_suffix
    )


def _flaky_run(fail_first: set[str]) -> object:

    def fake_run(command, **_kw):
        name = command[0]
        if name in fail_first:
            fail_first.discard(name)
            return _Proc(1)
        return _Proc(0)

    return fake_run


def _passing_run(seen: list[dict] | None = None):

    def fake_run(_command, **kwargs):
        if seen is not None:
            seen.append(kwargs)
        return _Proc(0)

    return fake_run


def _evidence_config() -> PolicyConfig:

    return PolicyConfig(
        required_gates=("verify",),
        max_rework=2,
        evidence={"verify": verify_artifact.RUN_ARTIFACT.as_posix()},
    )


def test_a_passing_run_writes_a_non_empty_run_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "_spawn", _as_spawn(_passing_run()))
    config = VerifyConfig((_check("ruff", ("full",)),))

    report = verify.run_verify(tmp_path, "full", config)

    artifact = tmp_path / verify_artifact.RUN_ARTIFACT
    assert report.passed is True
    assert artifact.stat().st_size > 0
    recorded = json.loads(artifact.read_text(encoding="utf-8"))
    assert recorded["mode"] == "full"
    assert recorded["passed"] is True
    assert recorded["recorded_at"]
    assert recorded["checks"] == [{"name": "ruff", "status": "pass", "returncode": 0, "detail": ""}]


def test_the_run_artifact_satisfies_a_declared_evidence_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(verify, "_spawn", _as_spawn(_passing_run()))
    config = _evidence_config()

    before = policy.evidence_status(tmp_path, config, "verify")
    assert before.satisfied is False
    assert verify_artifact.RUN_ARTIFACT.as_posix() in before.reason

    verify.run_verify(tmp_path, "full", VerifyConfig((_check("ruff", ("full",)),)))

    after = policy.evidence_status(tmp_path, config, "verify")
    assert after.satisfied is True
    assert after.path == tmp_path / verify_artifact.RUN_ARTIFACT


def test_the_run_artifact_never_holds_a_checks_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    seen: list[dict] = []
    monkeypatch.setattr(verify, "_spawn", _as_spawn(_passing_run(seen)))

    verify.run_verify(tmp_path, "full", VerifyConfig((_check("ruff", ("full",)),)))

    assert [kwargs["capture_output"] for kwargs in seen] == [False]
    written = (tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8")
    assert "output" not in written


def test_the_run_artifact_records_a_failing_run_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(verify, "_spawn", _as_spawn(lambda *_a, **_kw: _Proc(2)))

    verify.run_verify(tmp_path, "full", VerifyConfig((_check("pytest", ("full",)),)))

    recorded = json.loads((tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert recorded["passed"] is False
    assert recorded["checks"][0] == {
        "name": "pytest",
        "status": "fail",
        "returncode": 2,
        "detail": "output streamed rather than captured; reproduce with: pytest",
    }


def test_a_passing_check_records_no_derived_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "_spawn", _as_spawn(lambda *_a, **_kw: _Proc(0)))

    verify.run_verify(tmp_path, "full", VerifyConfig((_check("ruff", ("full",)),)))

    recorded = json.loads((tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert recorded["checks"][0]["detail"] == ""


def test_a_failure_that_never_reached_a_process_still_records_a_reason() -> None:
    outcome = verify.CheckResult("pytest", "fail", 1)

    assert verify_artifact.recorded_detail(outcome) == "the check failed and reported nothing"


def test_a_mode_with_no_checks_still_writes_a_non_empty_artifact(tmp_path: Path) -> None:

    report = verify.run_verify(tmp_path, "full", VerifyConfig(()))

    artifact = tmp_path / verify_artifact.RUN_ARTIFACT
    assert report.results == ()
    assert artifact.stat().st_size > 0
    assert json.loads(artifact.read_text(encoding="utf-8"))["checks"] == []
    assert (artifact.parent / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert policy.evidence_status(tmp_path, _evidence_config(), "verify").satisfied is True


def test_an_unwritable_artifact_path_never_costs_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    (tmp_path / ".basicly").mkdir()
    (tmp_path / ".basicly" / "usage").write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(verify, "_spawn", _as_spawn(_passing_run()))

    report = verify.run_verify(tmp_path, "full", VerifyConfig((_check("ruff", ("full",)),)))

    assert report.passed is True
    refused = policy.evidence_status(tmp_path, _evidence_config(), "verify")
    assert refused.satisfied is False
    assert verify_artifact.RUN_ARTIFACT.as_posix() in refused.reason


def test_rerun_failures_leaves_the_runs_own_artifact_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(verify, "_spawn", _as_spawn(_flaky_run({"pytest"})))
    config = VerifyConfig((_check("pytest", ("full",)), _check("ruff", ("full",))))
    report = verify.run_verify(tmp_path, "full", config)
    assert report.passed is False

    verify.rerun_failures(report, tmp_path, "full", config, capture=True)

    recorded = json.loads((tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert [c["name"] for c in recorded["checks"]] == ["pytest", "ruff"]
    assert recorded["passed"] is False
