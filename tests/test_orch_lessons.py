from __future__ import annotations

import importlib
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".basicly" / "core" / "kit" / "orch"))

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
    lesson_id = log.Writer(root, clean, "orchestrator", lambda: T0).add(
        "w", "r", "e", "rec-1", "local"
    )
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
            "--scope",
            "local",
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
            "--scope",
            "local",
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
            "--scope",
            "local",
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
            "--scope",
            "local",
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
        "--scope",
        "basicly",
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
        "--scope",
        "basicly",
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
    writer.add("w", "r", "e", "x", "local")
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
    log.Writer(root, clean, "o", lambda: T0).add("w", "old", "e", "x", "local")
    log.Writer(root, clean, "o", lambda: T0 + timedelta(days=10)).add("w", "new", "e", "x", "local")
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
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    monkeypatch.setattr(
        doctor, "guard_self_test", lambda: (False, "orch approve guard: self-test FAILED")
    )
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
    assert report.lines[0] == "orch approve guard: self-test FAILED"
    assert report.lines[1] == (
        f"{path}: allow rule 'Bash(orch:*)' lets a session run orch approve; add the deny rule "
        "'Bash(orch approve *)', which wins over every allow rule"
    )
    assert report.lines[2].startswith("rule parity: not checked")
    assert report.lines[3].startswith("relay entries: not checked")
    assert report.lines[4].startswith("wake adapter: none")
    assert report.lines[5].startswith("lessons: not checked")


def test_a_passing_guard_self_test_needs_no_deny_rule(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    environ = {"HOME": str(home), "PATH": str(home), "ORCH_LESSONS_ROOT": str(home)}
    settings(tmp_path, {"allow": ["Bash(orch:*)"]})

    report = doctor.run(tmp_path, environ)

    assert report.lines[0].startswith("orch approve guard: self-test refused all 5 probes")
    assert not any("add the deny rule" in line for line in report.lines)


def test_the_guard_self_test_passes_on_the_shipped_kit() -> None:
    passed, line = doctor.guard_self_test()

    assert passed, line


def test_doctor_passes_a_blanket_allow_when_a_deny_rule_covers_orch_approve(
    worker: tuple[Path, dict[str, str]],
) -> None:
    repo, environ = worker
    settings(repo, {"allow": ["Bash"], "deny": ["Bash(orch approve *)"]})
    report = doctor.run(repo, environ)
    assert not report.failed
    assert report.lines[1].startswith("claude: deny rule 'Bash(orch approve *)'")


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


def _add(repo: Path, scope: str | None) -> int:
    return run(
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
        *(["--scope", scope] if scope is not None else []),
    )


def test_scope_missing_refuses_by_name_and_appends_nothing(repo: Path) -> None:
    with pytest.raises(log.RefusedError, match=r"--scope local or --scope basicly"):
        _add(repo, None)
    assert events(repo) == []


def test_scope_unknown_refuses_by_name_and_appends_nothing(repo: Path) -> None:
    with pytest.raises(log.RefusedError, match=r"--scope local or --scope basicly"):
        _add(repo, "global")
    assert events(repo) == []


@pytest.mark.parametrize("scope", ["local", "basicly"])
def test_scope_recorded_on_the_add_event(repo: Path, scope: str) -> None:
    assert _add(repo, scope) == 0
    (only,) = events(repo)
    assert only["scope"] == scope
    (lesson,) = log.lessons(repo / ".orchestrator" / "lessons")
    assert lesson.scope == scope


def test_export_skips_local_scope_lessons(repo: Path) -> None:
    target = repo / "agent.yaml"
    target.write_text("name: orchestrator\n", encoding="utf-8")
    for scope, rule in (("local", "Local rule."), ("basicly", "Shared rule.")):
        run(
            repo,
            "lesson",
            "add",
            "--what",
            "w",
            "--requirement",
            rule,
            "--enforcement",
            "t",
            "--record",
            "x",
            "--scope",
            scope,
        )
    out = repo / "lessons.patch"
    assert run(repo, "lessons", "export", "--target", str(target), "--out", str(out)) == 0
    patch = out.read_text(encoding="utf-8")
    assert "Shared rule." in patch
    assert "Local rule." not in patch


def test_every_kit_caller_of_orch_lesson_add_passes_a_scope() -> None:
    core = REPO / ".basicly" / "core"
    missing = [
        f"{path.relative_to(REPO).as_posix()}:{number}"
        for path in sorted(core.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".yaml", ".md", ".json", ".toml"}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "orch lesson add" in line and "--scope" not in line
    ]
    assert missing == [], f"orch lesson add without --scope: {missing}"


def _unscoped(repo: Path, rule: str) -> str:
    root = repo / ".orchestrator" / "lessons"
    log.append(
        root,
        {
            "event": "add",
            "id": rule[:8].lower().replace(" ", "-"),
            "time": T0.isoformat(),
            "member": "o",
            "what": "w",
            "requirement": rule,
            "enforcement": "t",
            "record": "x",
        },
    )
    return rule[:8].lower().replace(" ", "-")


def test_export_leaves_out_and_names_a_lesson_without_scope(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = repo / "agent.yaml"
    target.write_text("name: orchestrator\n", encoding="utf-8")
    lesson = _unscoped(repo, "Unscoped rule.")
    out = repo / "lessons.patch"
    assert run(repo, "lessons", "export", "--target", str(target), "--out", str(out)) == 0
    assert "Unscoped rule." not in out.read_text(encoding="utf-8")
    assert lesson in capsys.readouterr().err


def test_scope_event_sets_the_scope_of_an_old_lesson(repo: Path) -> None:
    lesson = _unscoped(repo, "Later shared.")
    assert run(repo, "lesson", "scope", lesson, "basicly") == 0
    (only,) = log.lessons(repo / ".orchestrator" / "lessons")
    assert only.scope == "basicly"
    assert log.unscoped(repo / ".orchestrator" / "lessons") == []


@pytest.mark.parametrize(
    ("lesson", "value", "match"),
    [("nope", "local", "no lesson"), (None, "global", "local or basicly")],
)
def test_scope_event_refuses_by_name_and_appends_nothing(
    repo: Path, lesson: str | None, value: str, match: str
) -> None:
    known = _unscoped(repo, "Some rule.")
    before = events(repo)
    with pytest.raises(log.RefusedError, match=match):
        run(repo, "lesson", "scope", lesson or known, value)
    assert events(repo) == before
