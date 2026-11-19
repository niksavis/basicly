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


def test_end_session_refuses_a_session_with_no_lesson_and_the_doctor_flags_one(
    tmp_path: Path,
) -> None:
    root = log.init(tmp_path)
    writer = log.Writer(root, clean, "o", lambda: T0)
    with pytest.raises(log.RefusedError, match=r"wrote no lesson in this session"):
        writer.end_session()
    writer.add("w", "r", "e", "x")
    assert writer.end_session() == 1
    with pytest.raises(log.RefusedError, match=r"wrote no lesson"):
        writer.end_session()
    assert log.empty_session_ends(root) == []
    log.append(root, {"event": log.SESSION_END, "time": "t", "member": "p", "lessons": 0})
    environ = {"ORCH_LESSONS_ROOT": str(root)}
    lines, failed = doctor.lesson_lines(tmp_path, environ)
    assert failed and lines[0] == "session of p ended t with no lesson"


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
        ("Bash(orch *)", True),
        ("Bash(orch*)", True),
        ("Bash(orch approve *)", True),
        ("Bash", True),
        ("Bash(orch)", False),
        ("Bash(orch-wait)", False),
        ("Bash(orch-wait:*)", False),
        ("Bash(git status *)", False),
        ("Read", False),
    ],
)
def test_doctor_refuses_an_allow_rule_that_lets_a_session_run_orch_approve(
    rule: str, refused: bool
) -> None:
    assert doctor.allows_orch(rule) is refused


@pytest.fixture
def worker(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    (tmp_path / ".git").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    return tmp_path, {"HOME": str(home), "PATH": str(home), "ORCH_LESSONS_ROOT": str(home)}


def settings(repo: Path, permissions: dict[str, list[str]]) -> Path:
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"permissions": permissions}), encoding="utf-8")
    return path


def test_doctor_names_the_file_and_rule_and_reports_no_wake_adapter(
    worker: tuple[Path, dict[str, str]],
) -> None:
    repo, environ = worker
    path = settings(repo, {"allow": ["Bash(orch-wait)", "Bash(orch:*)"]})
    report = doctor.run(repo, environ)
    assert report.failed
    assert report.lines[0] == (
        f"{path}: allow rule 'Bash(orch:*)' lets a session run orch approve; add the deny rule "
        "'Bash(orch approve *)', which wins over every allow rule"
    )
    assert report.lines[1].startswith("rule parity: not checked")
    assert report.lines[2].startswith("wake adapter: none")
    assert report.lines[3].startswith("lessons: not checked")


def test_doctor_passes_a_blanket_allow_when_a_deny_rule_covers_orch_approve(
    worker: tuple[Path, dict[str, str]],
) -> None:
    repo, environ = worker
    settings(repo, {"allow": ["Bash"], "deny": ["Bash(orch approve *)"]})
    report = doctor.run(repo, environ)
    assert not report.failed
    assert report.lines[0].startswith("claude: deny rule 'Bash(orch approve *)'")


@pytest.mark.parametrize(("decision", "refused"), [("allow", True), ("forbidden", False)])
def test_doctor_refuses_codex_rules_that_allow_orch_approve(
    worker: tuple[Path, dict[str, str]], decision: str, refused: bool
) -> None:
    repo, environ = worker
    rules = repo / ".codex" / "rules" / "default.rules"
    rules.parent.mkdir(parents=True)
    rules.write_text('prefix_rule(pattern=["orch"], decision="allow")\n', encoding="utf-8")
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        seen.append(argv)
        return json.dumps({"decision": decision})

    lines, failed = doctor.codex_lines(repo, environ, runner, lambda name, _path: name)
    assert failed is refused
    assert seen[0][-4:] == ["orch", "approve", "--request", "r"]
    assert str(rules) in seen[0]
    assert bool(lines) is refused


@pytest.mark.parametrize(
    ("name", "refused"), [("orch", True), ("orch:*", True), ("orch-wait", False)]
)
def test_doctor_refuses_a_saved_copilot_approval_for_orch(
    worker: tuple[Path, dict[str, str]], name: str, refused: bool
) -> None:
    repo, environ = worker
    saved = Path(environ["HOME"]) / doctor.COPILOT_PERMISSIONS
    saved.parent.mkdir()
    approval = {"kind": "commands", "commandIdentifiers": [name]}
    saved.write_text(
        json.dumps({"locations": {str(repo): {"tool_approvals": [approval]}}}), encoding="utf-8"
    )
    lines, failed = doctor.copilot_lines(repo, environ)
    assert failed is refused and bool(lines) is refused


def test_doctor_reports_herdr_as_the_wake_adapter_inside_a_herdr_pane() -> None:
    environ = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p2", "HERDR_BIN_PATH": sys.executable}
    assert doctor.wake_adapter_line(environ) == "wake adapter: herdr (pane w1:p2)"


def test_doctor_rule_parity_fails_on_an_owner_rule_with_no_kit_entry(tmp_path: Path) -> None:
    ruleset = tmp_path / "ruleset.json"
    owner = [
        {"date": "2026-01-02", "phrase": "keep the gate on"},
        {"date": None, "phrase": "plain"},
    ]
    ruleset.write_text(json.dumps({"rules": [{"id": "a", "owner": owner}]}), encoding="utf-8")
    doc = tmp_path / "rules.md"
    doc.write_text(
        '- Owner rule, 2026-01-02: "keep the gate on".\n- Owner rule. A plain rule.\n'
        'Only the person changes an "Owner rule".\n',
        encoding="utf-8",
    )
    environ = {"ORCH_RULES_DOC": str(doc), "ORCH_RULESET": str(ruleset)}
    assert doctor.rule_parity_lines(environ) == (
        ["rule parity: 2 of 2 owner rules have a kit entry"],
        False,
    )
    doc.write_text(
        doc.read_text(encoding="utf-8") + "- Owner rule, 2026-01-03: new.\n", encoding="utf-8"
    )
    lines, failed = doctor.rule_parity_lines(environ)
    assert failed
    assert lines[0] == f"{doc}:4: owner rule (2026-01-03) has no entry in {ruleset}"
    assert doctor.rule_parity_lines({})[1] is False
