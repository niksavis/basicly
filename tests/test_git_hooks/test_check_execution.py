from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from basicly.config import load_verify_config

WAIT_FOR = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "deadline = time.monotonic() + {seconds}\n"
    "while not Path({name!r}).exists():\n"
    "    if time.monotonic() > deadline:\n"
    "        sys.exit({missing})\n"
    "    time.sleep(0.02)\n"
    "print('saw {name}')\n"
)


def _runner():
    path = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "check_runner.py"
    spec = importlib.util.spec_from_file_location("check_runner_execution_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure(repo: Path, verify: str, checks: list[tuple[str, str]]) -> None:
    body = verify + "".join(
        f"[[verify.checks]]\nname = {json.dumps(name)}\n"
        f'command = {json.dumps([sys.executable, "-c", code])}\nmodes = ["fast"]\n'
        for name, code in checks
    )
    (repo / "basicly.toml").write_text(body, encoding="utf-8")


def _wait(name: str, seconds: float, missing: int) -> str:
    return WAIT_FOR.format(name=name, seconds=seconds, missing=missing)


def _touch(name: str, code: int = 0) -> str:
    return (
        f"from pathlib import Path; Path({name!r}).touch(); "
        f"print('made {name}'); raise SystemExit({code})"
    )


def test_hook_jobs_runs_independent_checks_at_the_same_time(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(
        tmp_path,
        "[verify]\nhook_jobs = 2\n",
        [("waiter", _wait("signal", 30, 3)), ("signaller", _touch("signal"))],
    )

    assert _runner().run_checks(tmp_path, "fast") == 0

    out = capsys.readouterr().out
    assert out.index("==> waiter") < out.index("saw signal") < out.index("==> signaller")
    assert out.index("==> signaller") < out.index("made signal")
    assert "checks passed: 2/2" in out


def test_without_hook_jobs_checks_run_one_after_another(tmp_path: Path) -> None:
    _configure(
        tmp_path,
        "",
        [("waiter", _wait("signal", 0.5, 3)), ("signaller", _touch("signal"))],
    )

    assert _runner().run_checks(tmp_path, "fast") == 1


def test_a_parallel_failure_under_fail_fast_starts_no_queued_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(
        tmp_path,
        "[verify]\nfail_fast = true\nhook_jobs = 2\n",
        [
            ("failing", _touch("failed", 7)),
            ("holding", _wait("failed", 30, 3) + "time.sleep(1)\n"),
            ("queued", _touch("queued")),
        ],
    )

    assert _runner().run_checks(tmp_path, "fast") == 1

    captured = capsys.readouterr()
    assert not (tmp_path / "queued").exists()
    assert "==> queued NOT RUN: not run after failed check failing" in captured.out
    assert "FAILED: failing" in captured.err
    assert "1/2 passed" in captured.err


@pytest.mark.parametrize("value", ["0", "true", '"2"', "1.5"])
def test_hook_jobs_refuses_a_value_that_is_not_a_positive_whole_number(
    tmp_path: Path, value: str
) -> None:
    _configure(tmp_path, f"[verify]\nhook_jobs = {value}\n", [("made", _touch("made"))])

    with pytest.raises(SystemExit, match=r"verify\.hook_jobs must be a whole number of at least 1"):
        _runner().run_checks(tmp_path, "fast")
    assert not (tmp_path / "made").exists()


def test_the_engine_accepts_hook_jobs_in_its_configuration(tmp_path: Path) -> None:
    _configure(tmp_path, "[verify]\nhook_jobs = 4\n", [("made", _touch("made"))])

    assert [check.name for check in load_verify_config(tmp_path).checks] == ["made"]
