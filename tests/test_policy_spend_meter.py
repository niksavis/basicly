from __future__ import annotations

from pathlib import Path

from basicly import policy, run_record, runner
from basicly.runner import BUILTIN_RUNNERS, RunResult

_KILLED_STREAM = "\n".join([
    '{"type":"system","subtype":"init","tools":[]}',
    '{"type":"assistant","message":{"usage":{"input_tokens":4,'
    '"cache_creation_input_tokens":5960,"cache_read_input_tokens":0,"output_tokens":91}}}',
    '{"type":"assistant","message":{"usage":{"input_tokens":2,'
    '"cache_creation_input_tokens":40,"cache_read_input_tokens":15496,"output_tokens":17}}}',
    '{"type":"assistant","message":{"usage":{"input_tok',
])
_KILLED_TOKENS = 4 + 5960 + 0 + 91 + 2 + 40 + 15496 + 17

_LANE = "basicly-lane"
_MODEL = "claude-sonnet-5"


def _claude() -> runner.RunnerSpec:
    return next(spec for spec in BUILTIN_RUNNERS if spec.name == "claude")


def _killed(stdout: str) -> RunResult:
    return RunResult(
        "claude",
        ("claude", "--model", _MODEL, "-p", "<prompt-redacted>"),
        executed=True,
        returncode=None,
        stdout=stdout,
        stderr="",
        timed_out=True,
        stopped=runner.StopReason(runner.SPEND_BOUND, "the grant's remainder was reached"),
    )


def _recorded(repo_root: Path, stdout: str) -> None:
    spec = _claude()
    result = _killed(stdout)
    usage = runner.extract_usage(spec, result)
    assert usage is not None
    run_record.record(
        repo_root,
        _LANE,
        run_record.build_record(
            agent=spec.name,
            handoff=False,
            returncode=None,
            duration_s=1770.6,
            command=result.command,
            model=_MODEL,
            model_tier="medium",
            tokens=usage.tokens,
            cost=usage.cost,
            estimated=usage.estimated,
            stopped_bound=runner.SPEND_BOUND,
        ),
    )


def test_a_killed_streaming_lane_leaves_its_grant_delegable(tmp_path: Path) -> None:

    _recorded(tmp_path, _KILLED_STREAM)
    grant = policy.Grant(level="L3", token_budget=100_000)

    status = policy.spend_status(tmp_path, "root", grant=grant, ids=(_LANE,))

    assert status.unmetered_dispatches == 0
    assert status.halted is False
    assert status.spent_tokens == _KILLED_TOKENS
    assert status.remaining_tokens == 100_000 - _KILLED_TOKENS


def test_a_dispatch_that_reported_nothing_is_named_with_its_model_in_the_decline(
    tmp_path: Path,
) -> None:

    _recorded(tmp_path, '{"type":"system","subtype":"init","tools":[]}')
    grant = policy.Grant(level="L3", token_budget=100_000)

    status = policy.spend_status(tmp_path, "root", grant=grant, ids=(_LANE,))

    assert status.unmetered_dispatches == 1
    assert status.halted is True
    assert f"{_LANE} on {_MODEL}" in status.detail


def test_an_unstarted_dispatch_is_named_by_nothing_because_it_halts_nothing(
    tmp_path: Path,
) -> None:
    run_record.record(
        tmp_path,
        _LANE,
        run_record.build_record(
            agent="claude",
            handoff=False,
            started=False,
            returncode=None,
            duration_s=None,
            command=("claude",),
            model=_MODEL,
            tokens=12,
            estimated=True,
        ),
    )

    meter = policy.session_spend(tmp_path, "root", ids=(_LANE,))

    assert (meter.unmetered_dispatches, meter.unmetered_labels) == (0, ())
    assert (meter.measured_tokens, meter.estimated_tokens) == (0, 12)
