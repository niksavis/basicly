from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from basicly import hook_runtime, verify
from basicly.config import load_verify_config
from basicly.verify_artifact import RUN_ARTIFACT

WAIT_FOR = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "deadline = time.monotonic() + {seconds}\n"
    "while not Path({name!r}).exists():\n"
    "    if time.monotonic() > deadline:\n"
    "        sys.exit(3)\n"
    "    time.sleep(0.02)\n"
    "print('saw {name}')\n"
)


def _wait(name: str, seconds: float = 30) -> str:
    return WAIT_FOR.format(name=name, seconds=seconds)


def _touch(name: str, code: int = 0) -> str:
    return (
        f"from pathlib import Path; Path({name!r}).touch(); "
        f"print('made {name}'); raise SystemExit({code})"
    )


def _configure(root: Path, verify_table: str, checks: list[tuple[str, str]]) -> None:
    (root / "basicly.toml").write_text(
        verify_table
        + "".join(
            f"[[verify.checks]]\nname = {json.dumps(name)}\n"
            f'command = {json.dumps([sys.executable, "-c", code])}\nmodes = ["full"]\n'
            for name, code in checks
        ),
        encoding="utf-8",
    )


def test_jobs_run_independent_checks_at_the_same_time_and_report_in_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(
        tmp_path,
        "[verify]\njobs = 2\n",
        [("waiter", _wait("signal")), ("signaller", _touch("signal"))],
    )

    report = verify.run_verify(tmp_path, "full")

    assert report.passed
    assert [(r.name, r.status) for r in report.results] == [
        ("waiter", "pass"),
        ("signaller", "pass"),
    ]
    out = capsys.readouterr().out
    assert out.index("==> waiter") < out.index("saw signal") < out.index("==> signaller")
    assert not (tmp_path / ".basicly/usage/verify-checks.json").exists()


def test_without_jobs_the_same_checks_run_one_after_another(tmp_path: Path) -> None:
    _configure(tmp_path, "", [("waiter", _wait("signal", 0.5)), ("signaller", _touch("signal"))])

    report = verify.run_verify(tmp_path, "full")

    assert [(r.name, r.status) for r in report.results] == [
        ("waiter", "fail"),
        ("signaller", "pass"),
    ]


def test_a_parallel_failure_keeps_its_output_in_a_log_the_detail_names(tmp_path: Path) -> None:
    _configure(
        tmp_path,
        "[verify]\njobs = 2\n",
        [("broken", "print('the reason it broke'); raise SystemExit(5)"), ("fine", "pass")],
    )

    report = verify.run_verify(tmp_path, "full")

    broken = report.results[0]
    assert (broken.status, broken.returncode) == ("fail", 5)
    log = tmp_path / broken.detail.split()[-1]
    assert "the reason it broke" in log.read_text(encoding="utf-8")
    assert report.results[1].status == "pass"


def test_a_parallel_failure_under_fail_fast_starts_no_queued_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(
        tmp_path,
        "[verify]\nfail_fast = true\njobs = 2\n",
        [
            ("failing", _touch("failed", 7)),
            ("holding", _wait("failed") + "time.sleep(1)\n"),
            ("queued", _touch("queued")),
        ],
    )

    report = verify.run_verify(tmp_path, "full")

    assert not report.passed
    assert [(r.name, r.status) for r in report.results] == [
        ("failing", "fail"),
        ("holding", "pass"),
        ("queued", "blocked"),
    ]
    assert report.results[2].detail == "not run after failed check failing"
    assert not (tmp_path / "queued").exists()
    assert "==> queued NOT RUN: not run after failed check failing" in capsys.readouterr().out
    recorded = json.loads((tmp_path / RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert [row["status"] for row in recorded["checks"]] == ["fail", "pass", "blocked"]


@pytest.mark.parametrize("value", ["0", "true", '"2"', "1.5"])
def test_jobs_refuses_a_value_that_is_not_a_positive_whole_number(
    tmp_path: Path, value: str
) -> None:
    _configure(tmp_path, f"[verify]\njobs = {value}\n", [("made", _touch("made"))])

    with pytest.raises(ValueError, match=r"verify\.jobs must be a whole number of at least 1"):
        load_verify_config(tmp_path)


@pytest.mark.parametrize("jobs", [1, 2])
def test_the_engine_fills_the_worker_placeholder_for_its_job_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], jobs: int
) -> None:
    printer = [sys.executable, "-c", "import sys; print('workers=' + sys.argv[1])", "{workers}"]
    (tmp_path / "basicly.toml").write_text(
        f'[verify]\njobs = {jobs}\n[[verify.checks]]\nname = "printer"\n'
        f'command = {json.dumps(printer)}\nmodes = ["full"]\n',
        encoding="utf-8",
    )

    report = verify.run_verify(tmp_path, "full")

    assert report.passed
    expected = hook_runtime.load("cpu_share").workers(jobs)
    assert f"workers={expected}" in capsys.readouterr().out
    assert report.results[0].command[-1] == "{workers}"


HOLD = (
    "import time; from pathlib import Path; marker = Path({name!r}); marker.touch(); "
    "time.sleep(0.5); marker.unlink()"
)
ALONE = (
    "import sys; from pathlib import Path; sys.exit(3 if any(Path('.').glob('*-running')) else 0)"
)


SEES_OVERLAP = (
    "import sys, time; from pathlib import Path; deadline = time.monotonic() + 30\n"
    "while time.monotonic() < deadline:\n"
    "    if any(Path('.').glob('*-running')):\n"
    "        Path('seen').touch(); sys.exit(3)\n"
    "    time.sleep(0.02)\n"
)
HOLD_UNTIL_SEEN = (
    "import time; from pathlib import Path; marker = Path({name!r}); marker.touch(); "
    "deadline = time.monotonic() + 30\n"
    "while not Path('seen').exists() and time.monotonic() < deadline:\n"
    "    time.sleep(0.02)\n"
    "marker.unlink()\n"
)


def test_an_exclusive_check_runs_alone_in_the_engine(tmp_path: Path) -> None:
    _configure(
        tmp_path,
        "[verify]\njobs = 3\n",
        [
            ("before", HOLD.format(name="before-running")),
            ("alone", ALONE),
            ("after", HOLD.format(name="after-running")),
        ],
    )
    toml = tmp_path / "basicly.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            'name = "alone"\n', 'name = "alone"\nexclusive = true\n'
        ),
        encoding="utf-8",
    )

    assert verify.run_verify(tmp_path, "full").passed


def test_without_the_exclusive_flag_the_engine_overlaps_the_same_checks(tmp_path: Path) -> None:
    _configure(
        tmp_path,
        "[verify]\njobs = 3\n",
        [("before", HOLD_UNTIL_SEEN.format(name="before-running")), ("alone", SEES_OVERLAP)],
    )

    assert not verify.run_verify(tmp_path, "full").passed
