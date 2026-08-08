"""Tests for the run artifact a verify run leaves for the evidence gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import policy, verify, verify_artifact
from basicly.config import PolicyConfig, VerifyCheck, VerifyConfig

if TYPE_CHECKING:
    import pytest


class _Proc:
    """Minimal stand-in for a CompletedProcess with a chosen return code."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _check(name: str, modes: tuple[str, ...], staged_suffix: str | None = None) -> VerifyCheck:
    return VerifyCheck(
        name=name, command=(name,), modes=frozenset(modes), staged_suffix=staged_suffix
    )


def _flaky_run(fail_first: set[str]) -> object:
    """A subprocess.run stand-in where each named command fails once, then passes."""

    def fake_run(command, **_kw):
        name = command[0]
        if name in fail_first:
            fail_first.discard(name)
            return _Proc(1)
        return _Proc(0)

    return fake_run


def _passing_run(seen: list[dict] | None = None):
    """A ``subprocess.run`` stand-in that passes and, given *seen*, records its kwargs."""

    def fake_run(_command, **kwargs):
        if seen is not None:
            seen.append(kwargs)
        return _Proc(0)

    return fake_run


def _evidence_config() -> PolicyConfig:
    """A policy declaring the verify phase's evidence artifact, as a consumer would.

    ``as_posix`` because the declaration is a line of TOML a human writes, and a
    backslash-separated path is not what they would type on any platform.
    """
    return PolicyConfig(
        required_gates=("verify",),
        max_rework=2,
        evidence={"verify": verify_artifact.RUN_ARTIFACT.as_posix()},
    )


def test_a_passing_run_writes_a_non_empty_run_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The producer m4zv.13 deliberately left out: a passing run now records itself."""
    monkeypatch.setattr(verify.subprocess, "run", _passing_run())
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
    """The whole point of the bead: declaring the artifact for verify now passes.

    Asserted against the state *before* the run as well, because "satisfied" is
    the answer this gate gives whenever a phase declares nothing — so a check that
    only looked at the after state would pass with no producer at all.
    """
    monkeypatch.setattr(verify.subprocess, "run", _passing_run())
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
    """Live output must not regress: the run still streams, and the record is metadata.

    Both halves matter. ``capture_output`` staying off is what keeps a consumer
    watching the gate seeing each tool's own output, and it is also why there is
    no stdout here to leak into a file the redaction rule keeps metadata-only.
    """
    seen: list[dict] = []
    monkeypatch.setattr(verify.subprocess, "run", _passing_run(seen))

    verify.run_verify(tmp_path, "full", VerifyConfig((_check("ruff", ("full",)),)))

    assert [kwargs["capture_output"] for kwargs in seen] == [False]
    written = (tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8")
    assert "output" not in written


def test_the_run_artifact_records_a_failing_run_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is the record of a run, not of a pass — otherwise presence means two things."""
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_kw: _Proc(2))

    verify.run_verify(tmp_path, "full", VerifyConfig((_check("pytest", ("full",)),)))

    recorded = json.loads((tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert recorded["passed"] is False
    assert recorded["checks"][0] == {
        "name": "pytest",
        "status": "fail",
        "returncode": 2,
        "detail": "",
    }


def test_a_mode_with_no_checks_still_writes_a_non_empty_artifact(tmp_path: Path) -> None:
    """An empty file fails the gate, so "nothing to run" must still be a written fact.

    This run also passes nothing, so ``usage.record_verify_check`` never fires and
    the self-ignoring ``.gitignore`` asserted here can only have come from the
    artifact writer — which is what keeps a file rewritten by every run from being
    the dirt that refuses a landing.
    """
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
    """The verdict is what the caller asked for; the gate is what notices the absence.

    A plain file where the usage directory belongs makes the write fail on every
    platform without a chmod. The failure is not swallowed into nothing: with no
    artifact on disk the evidence gate refuses the advance and names the path.
    """
    (tmp_path / ".basicly").mkdir()
    (tmp_path / ".basicly" / "usage").write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(verify.subprocess, "run", _passing_run())

    report = verify.run_verify(tmp_path, "full", VerifyConfig((_check("ruff", ("full",)),)))

    assert report.passed is True
    refused = policy.evidence_status(tmp_path, _evidence_config(), "verify")
    assert refused.satisfied is False
    assert verify_artifact.RUN_ARTIFACT.as_posix() in refused.reason


def test_rerun_failures_leaves_the_runs_own_artifact_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic re-run covers only the failed checks and must not overwrite the run.

    ``merge`` re-runs failures to tell an unreliable gate from a merit failure. If
    that subset rewrote the artifact, the evidence for a two-check run would be a
    record of one check — a run that never happened.
    """
    monkeypatch.setattr(verify.subprocess, "run", _flaky_run({"pytest"}))
    config = VerifyConfig((_check("pytest", ("full",)), _check("ruff", ("full",))))
    report = verify.run_verify(tmp_path, "full", config)
    assert report.passed is False

    verify.rerun_failures(report, tmp_path, "full", config, capture=True)

    recorded = json.loads((tmp_path / verify_artifact.RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert [c["name"] for c in recorded["checks"]] == ["pytest", "ruff"]
    assert recorded["passed"] is False
