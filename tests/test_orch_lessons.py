from __future__ import annotations

import importlib
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "basicly-orch"))

log = importlib.import_module("orch_lessons.log")
cli = importlib.import_module("orch_lessons.cli")
doctor = importlib.import_module("orch_lessons.doctor")

T0 = datetime(2026, 11, 18, 19, 0, tzinfo=UTC)


def clean(_: str) -> None:
    return None


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    hooks = tmp_path / ".basicly" / "core" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(REPO / ".basicly" / "core" / "hooks" / "secret-scan.py", hooks / "secret-scan.py")
    log.init(tmp_path / ".orchestrator" / "lessons")
    return tmp_path


def run(repo: Path, *argv: str, member: str = "orchestrator") -> int:
    return cli.run(list(argv), repo, {"ORCH_MEMBER": member})


def events(repo: Path) -> list[dict[str, object]]:
    path = repo / ".orchestrator" / "lessons" / log.LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_add_stamps_the_clock_time_and_the_member(tmp_path: Path) -> None:
    root = log.init(tmp_path)
    lesson_id = log.Writer(root, clean, "orchestrator", lambda: T0).add("w", "r", "e", "rec-1")
    (only,) = log.lessons(root)
    assert (only.id, only.time, only.member) == (lesson_id, T0, "orchestrator")


def test_add_has_no_time_argument() -> None:
    with pytest.raises(SystemExit):
        cli.parser().parse_args([
            "lesson",
            "add",
            "--what",
            "w",
            "--requirement",
            "r",
            "--enforcement",
            "e",
            "--record",
            "x",
            "--time",
            "now",
        ])


def test_scanner_flag_refuses_and_appends_nothing(repo: Path) -> None:
    flagged = "token" + " = 'abcdefghijklmnopqrstu'"
    with pytest.raises(log.RefusedError, match=r"secret scanner"):
        run(
            repo,
            "lesson",
            "add",
            "--what",
            flagged,
            "--requirement",
            "r",
            "--enforcement",
            "e",
            "--record",
            "x",
        )
    assert events(repo) == []


def test_scanner_pass_appends_one_line(repo: Path) -> None:
    assert (
        run(
            repo,
            "lesson",
            "add",
            "--what",
            "w",
            "--requirement",
            "r",
            "--enforcement",
            "e",
            "--record",
            "x",
        )
        == 0
    )
    assert [e["event"] for e in events(repo)] == ["add"]


def test_no_scanner_refuses_by_name(repo: Path) -> None:
    (repo / ".basicly" / "core" / "hooks" / "secret-scan.py").unlink()
    with pytest.raises(log.RefusedError, match=r"secret-scan\.py is missing"):
        run(
            repo,
            "lesson",
            "add",
            "--what",
            "w",
            "--requirement",
            "r",
            "--enforcement",
            "e",
            "--record",
            "x",
        )


def test_export_writes_only_open_requirements_and_changes_no_file(repo: Path) -> None:
    target = repo / "agent.yaml"
    target.write_text("name: orchestrator\n", encoding="utf-8")
    run(
        repo,
        "lesson",
        "add",
        "--what",
        "private detail",
        "--requirement",
        "Keep A.",
        "--enforcement",
        "test A",
        "--record",
        "x",
    )
    run(
        repo,
        "lesson",
        "add",
        "--what",
        "w",
        "--requirement",
        "Keep B.",
        "--enforcement",
        "test B",
        "--record",
        "y",
    )
    closed = events(repo)[1]["id"]
    run(repo, "lesson", "close", str(closed), "--change", "abc123")
    out = repo / "lessons.patch"
    assert run(repo, "lessons", "export", "--target", str(target), "--out", str(out)) == 0
    patch = out.read_text(encoding="utf-8")
    assert "+- Keep A. Enforced by: test A" in patch
    assert "Keep B." not in patch and "private detail" not in patch
    assert target.read_text(encoding="utf-8") == "name: orchestrator\n"


def test_doctor_names_open_lessons_older_than_14_days(tmp_path: Path) -> None:
    root = log.init(tmp_path)
    log.Writer(root, clean, "o", lambda: T0).add("w", "old", "e", "x")
    log.Writer(root, clean, "o", lambda: T0 + timedelta(days=10)).add("w", "new", "e", "x")
    later = T0 + timedelta(days=15)
    assert [lesson.requirement for lesson in log.stale(root, clock=lambda: later)] == ["old"]


@pytest.mark.parametrize(
    ("rule", "refused"),
    [
        ("Bash(orch:*)", True),
        ("Bash(orch)", True),
        ("Bash(orch *)", True),
        ("Bash", True),
        ("Bash(orch-wait)", False),
        ("Bash(orch-wait:*)", False),
        ("Bash(git status *)", False),
        ("Read", False),
    ],
)
def test_doctor_refuses_an_allow_rule_that_lets_a_worker_run_orch(rule: str, refused: bool) -> None:
    assert doctor.allows_orch(rule) is refused


def test_doctor_names_the_file_and_rule_and_reports_no_wake_adapter(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"permissions": {"allow": ["Bash(orch-wait)", "Bash(orch:*)"]}}),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    report = doctor.run(tmp_path, {"HOME": str(home), "PATH": str(home)})
    assert report.failed
    assert report.lines[0] == (
        f"{settings}: allow rule 'Bash(orch:*)' lets a worker run orch; "
        "allow orch-wait by name instead"
    )
    assert report.lines[1].startswith("wake adapter: none")
    assert report.lines[2].startswith("lessons: not checked")
