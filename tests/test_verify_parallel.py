from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from basicly import usage, verify
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
    counters = json.loads((tmp_path / usage.VERIFY_CHECKS_FILE).read_text(encoding="utf-8"))
    assert {name: entry["count"] for name, entry in counters.items()} == {
        "waiter": 1,
        "signaller": 1,
    }


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
