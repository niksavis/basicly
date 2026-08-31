"""Tests for the config-driven verify runner (onb.2)."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

import pytest
import yaml

from basicly import cli, dropin, policy, tracker, tracker_paths, usage, verify
from basicly.config import (
    VerifyCheck,
    VerifyConfig,
    load_verify_config,
    load_worktree_config,
)
from tests import flipped_tracker


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


def test_run_check_maps_returncode_to_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero exit is pass; non-zero is fail; the command runs as configured."""
    seen: list[list[str]] = []

    def fake_run(command, **_kw):
        seen.append(command)
        return _Proc(0 if command == ["ok"] else 1)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    assert verify.run_check(_check("ok", ("full",)), tmp_path, "full").status == "pass"
    assert verify.run_check(_check("bad", ("full",)), tmp_path, "full").status == "fail"
    assert seen == [["ok"], ["bad"]]


def test_run_check_drops_a_base_virtual_env_for_a_worktree_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check run in a worktree loses the base checkout's VIRTUAL_ENV; the base keeps it.

    Regression (basicly-uq3pki): `uv` ignores the mismatched value and warns, so every
    `uv run` check printed a `does not match the project environment path` line.
    """
    base = tmp_path / "base"
    (base / ".venv").mkdir(parents=True)
    lane = tmp_path / "base.worktrees" / "lane"
    lane.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(base / ".venv"))
    seen: list[dict[str, str]] = []

    def fake_run(_command, **kw):
        seen.append(kw["env"])
        return _Proc(0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    verify.run_check(_check("ok", ("full",)), lane, "full")
    verify.run_check(_check("ok", ("full",)), base, "full")

    assert "VIRTUAL_ENV" not in seen[0]
    assert seen[1]["VIRTUAL_ENV"] == str(base / ".venv")


def test_run_check_fails_cleanly_on_missing_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command not on PATH is a failed check with a one-line message.

    Regression (basicly-zrj.13.2): the FileNotFoundError used to escape as a
    traceback from the loop verify gate on consumers without the tool.
    """
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))  # deterministic: nothing resolvable
    check = VerifyCheck(name="ghost", command=("ghost-tool",), modes=frozenset({"full"}))

    result = verify.run_check(check, tmp_path, "full")

    assert result.status == "fail"
    assert result.returncode == 127
    assert "command not found: ghost-tool" in result.detail
    assert "\n" not in result.detail  # readable one-liner, not a traceback


def test_run_check_fails_cleanly_on_unrunnable_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PATH candidate that exists but cannot be executed also fails cleanly.

    On WSL, Windows mounts on PATH surface a missing tool as PermissionError
    rather than FileNotFoundError; both must yield a one-line failure.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "ghost-tool"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o644)  # present but not executable
    monkeypatch.setenv("PATH", str(bin_dir))
    check = VerifyCheck(name="ghost", command=("ghost-tool",), modes=frozenset({"full"}))

    result = verify.run_check(check, tmp_path, "full")

    assert result.status == "fail"
    assert result.returncode in (126, 127)  # PermissionError vs FileNotFoundError by OS
    # Linux surfaces PermissionError ("cannot run"); Windows treats the
    # non-executable as not found. Either way the detail names the tool.
    assert "ghost-tool" in result.detail
    assert "\n" not in result.detail


def test_run_check_staged_fails_when_git_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed git call fails the check instead of silently skipping it."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: None)
    result = verify.run_check(_check("ruff", ("staged",), ".py"), tmp_path, "staged")
    assert result.status == "fail"
    assert "git diff" in (result.detail or "")


def test_staged_files_returns_none_outside_a_repo(tmp_path: Path) -> None:
    """staged_files distinguishes git failure (None) from nothing staged ([])."""
    assert verify.staged_files(tmp_path, ".py") is None


def test_run_check_staged_skips_when_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged check with no matching staged files is skipped, not run."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: [])
    ran = False

    def fake_run(_command, **_kw):
        nonlocal ran
        ran = True
        return _Proc(0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    result = verify.run_check(_check("ruff", ("staged",), ".py"), tmp_path, "staged")
    assert result.status == "skip"
    assert ran is False


def test_run_check_staged_appends_matching_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In staged mode the matching staged files are appended to the command."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: ["a.py", "b.py"])
    captured: list[str] = []

    def fake_run(command, **_kw):
        captured.extend(command)
        return _Proc(0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    verify.run_check(_check("ruff", ("staged",), ".py"), tmp_path, "staged")
    assert captured == ["ruff", "a.py", "b.py"]


# --- The engine witnesses the checks it runs (basicly-3yi3) -------------------
#
# Nothing else can. A check exists only as a `[[verify.checks]]` entry, so the
# `tool-usage` hook — which counts what an agent *typed* at a shell — never sees
# one, and the exercised-or-unproven release gate was refusing a tag over checks
# it had just watched pass.


def test_run_check_records_a_passing_check_as_an_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass is the evidence the release gate reads, keyed by the check's name."""
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: _Proc(0))

    verify.run_check(_check("vulture", ("full",)), tmp_path, "full")
    verify.run_check(_check("vulture", ("full",)), tmp_path, "full")

    recorded = usage.load_verify_checks(tmp_path)
    assert recorded is not None
    assert recorded["vulture"]["count"] == 2


def test_the_recorded_ledger_never_dirties_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release it unblocks also refuses a dirty tree, so the record must self-ignore.

    A gate that has to be run before tagging, and whose record then blocks the tag it
    was run for, is unsatisfiable in a different way — the shape this bead removed.
    """
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: _Proc(0))

    verify.run_check(_check("vulture", ("full",)), tmp_path, "full")

    assert (tmp_path / usage.VERIFY_CHECKS_FILE).exists()  # not vacuous: something was written
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert status.stdout.strip() == ""


def test_run_check_records_nothing_for_a_failure_or_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a pass proves the capability works.

    A `fail` covers the two states in which it demonstrably did not run at all —
    command not found (127) and not executable (126) — and a skip ran nothing.
    """
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: _Proc(1))
    verify.run_check(_check("failing", ("full",)), tmp_path, "full")

    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: [])
    verify.run_check(_check("skipped", ("staged",), ".py"), tmp_path, "staged")

    assert usage.load_verify_checks(tmp_path) is None


def test_a_fix_run_is_not_an_execution_of_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixer is not the gate: `ruff format` passing says nothing about `--check`."""
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: _Proc(0))
    check = VerifyCheck(
        name="ruff-format",
        command=("ruff", "format", "--check"),
        modes=frozenset({"full"}),
        fix_command=("ruff", "format"),
    )

    verify.run_fix(check, tmp_path, "full")

    assert usage.load_verify_checks(tmp_path) is None


def test_recording_can_never_fail_the_check_it_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telemetry never becomes a verdict — an unwritable ledger path is swallowed."""
    blocked = tmp_path / usage.VERIFY_CHECKS_FILE.parent
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_k: _Proc(0))

    assert verify.run_check(_check("vulture", ("full",)), tmp_path, "full").status == "pass"


def test_run_verify_filters_by_mode_and_aggregates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only mode-matching checks run; the report reflects each verdict."""
    monkeypatch.setattr(
        verify.subprocess, "run", lambda command, **_kw: _Proc(0 if command == ["a"] else 1)
    )
    config = VerifyConfig((_check("a", ("full",)), _check("b", ("full",)), _check("c", ("fast",))))

    report = verify.run_verify(tmp_path, "full", config)
    assert [(r.name, r.status) for r in report.results] == [("a", "pass"), ("b", "fail")]
    assert report.passed is False
    assert report.failures == ("b",)


def test_run_fix_skips_a_check_without_a_fix_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only checks that declare a mechanical repair are ever fixed."""
    monkeypatch.setattr(
        verify.subprocess, "run", lambda *_a, **_k: pytest.fail("no fixer is configured")
    )
    assert verify.run_fix(_check("ruff", ("full",)), tmp_path, "full").status == "skip"


def test_apply_fixes_runs_the_fix_commands_not_the_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_fixes invokes each declared fix_command and nothing else."""
    seen: list[list[str]] = []
    monkeypatch.setattr(
        verify.subprocess, "run", lambda command, **_kw: (seen.append(command), _Proc(0))[1]
    )
    config = VerifyConfig((
        _check("pytest", ("full",)),
        VerifyCheck(
            name="ruff-format",
            command=("ruff", "format", "--check"),
            modes=frozenset({"full"}),
            fix_command=("ruff", "format"),
        ),
    ))

    report = verify.apply_fixes(tmp_path, "full", config)

    assert seen == [["ruff", "format"]]
    assert [(r.name, r.status) for r in report.results] == [
        ("pytest", "skip"),
        ("ruff-format", "pass"),
    ]


def test_apply_fixes_scopes_to_staged_files_in_staged_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In staged mode a fix touches the staged files only, like the check does."""
    monkeypatch.setattr(verify, "staged_files", lambda _root, _suffix: ["a.py"])
    captured: list[str] = []
    monkeypatch.setattr(
        verify.subprocess, "run", lambda command, **_kw: (captured.extend(command), _Proc(0))[1]
    )
    check = VerifyCheck(
        name="ruff-format",
        command=("ruff", "format", "--check"),
        modes=frozenset({"staged"}),
        staged_suffix=".py",
        fix_command=("ruff", "format"),
    )

    verify.run_fix(check, tmp_path, "staged")

    assert captured == ["ruff", "format", "a.py"]


def test_report_gate_without_a_tracker(tmp_path: Path) -> None:
    """A repository with no ledger degrades gracefully and says why.

    Driven by handing it a directory that holds no tracker rather than by stubbing the
    seam, so the message a consumer reads is the store's own rather than one this test
    composed.
    """
    report = verify.VerifyReport(mode="full", results=())

    ok, message = verify.report_gate(tmp_path, "basicly-x", report)

    assert ok is False
    assert "tracker kit is not installed" in message


def test_report_gate_builds_expected_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passing report records a pass gate with the aggregate note."""
    captured: dict[str, list[str]] = {}

    def fake_write(_root, args):
        captured["cmd"] = args

    monkeypatch.setattr(verify.tracker, "write", fake_write)
    report = verify.VerifyReport(mode="full", results=(verify.CheckResult("ruff", "pass", 0),))

    ok, _message = verify.report_gate(tmp_path, "basicly-x", report, gate="verify")
    cmd = captured["cmd"]
    assert ok is True
    assert cmd[:2] == ["gate", "report"]
    assert "--status" in cmd and cmd[cmd.index("--status") + 1] == "pass"
    assert cmd[-1] == "basicly-x"


def test_report_gate_stamps_the_runner_as_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known runner is recorded as the gate's audit-trail actor."""
    captured: dict[str, list[str]] = {}

    def fake_write(_root, args):
        captured["cmd"] = args

    monkeypatch.setattr(verify.tracker, "write", fake_write)
    report = verify.VerifyReport(mode="full", results=(verify.CheckResult("ruff", "pass", 0),))

    verify.report_gate(tmp_path, "basicly-x", report, actor="claude")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--actor") + 1] == "claude"
    assert cmd[-1] == "basicly-x"


def test_report_gate_omits_the_actor_without_a_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No runner known: no --actor flag is added."""
    captured: dict[str, list[str]] = {}

    def fake_write(_root, args):
        captured["cmd"] = args

    monkeypatch.setattr(verify.tracker, "write", fake_write)
    report = verify.VerifyReport(mode="full", results=())

    verify.report_gate(tmp_path, "basicly-x", report)
    assert "--actor" not in captured["cmd"]


# --- the flip: the gate a leaf's walk records (basicly-wpc8.1) ----------------


def test_report_gate_records_the_gate_in_the_owned_ledger_with_br_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate surface of the criterion: br off PATH, a spawn fatal, the row readable."""
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "seam-1")
    flipped_tracker.refuse_spawn(monkeypatch)
    report = verify.VerifyReport(mode="full", results=(verify.CheckResult("ruff", "pass", 0),))

    ok, message = verify.report_gate(repo, "seam-1", report, gate="verify")

    kit = tracker.kit(repo)
    row = next(e for e in flipped_tracker.ledger_events(repo) if e.kind == kit.KIND_GATE)
    assert ok is True
    assert "recorded gate verify=pass" in message
    assert row.payload[kit.GATE_NAME_KEY] == "verify"
    assert row.payload[kit.GATE_PASSED_KEY] is True


def test_report_gate_carries_a_refusing_store_s_own_reason_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that cannot take the write reports NOT recorded, with the cause.

    The flipped rung with no kit installed: there is nowhere for the row to land and no
    br to fall back to, which is the state a write surface with no owned equivalent
    reaches. It must read as a gate that was not recorded — never as a pass, and never as
    a row that half-landed in one store.
    """
    (tmp_path / "basicly.toml").write_text(
        f'[tracker]\nmode = "{tracker.MODE_OWNED}"\n', encoding="utf-8"
    )
    flipped_tracker.refuse_spawn(monkeypatch)

    ok, message = verify.report_gate(tmp_path, "seam-1", verify.VerifyReport("full", ()))

    assert ok is False
    assert "NOT recorded" in message


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # nosec B603 B607


@pytest.fixture
def linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real git repo plus a linked worktree of it, as ``(repo, worktree)``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init", "--no-verify")
    linked = tmp_path / "repo.worktrees" / "wt"
    _git(repo, "worktree", "add", "-b", "harness/wt", str(linked))
    return repo, linked


def test_linked_worktree_guard_states(linked_worktree: tuple[Path, Path], tmp_path: Path) -> None:
    """The guard trips only in a linked worktree — not in the main checkout or outside git."""
    repo, linked = linked_worktree
    assert verify.linked_worktree_guard(repo) is None
    reason = verify.linked_worktree_guard(linked)
    assert reason is not None and "linked worktree" in reason
    outside = tmp_path / "plain"
    outside.mkdir()
    assert verify.linked_worktree_guard(outside) is None


def test_linked_worktree_guard_writes_nothing_to_the_tracker(
    linked_worktree: tuple[Path, Path],
) -> None:
    """An abort gate halts and reports; it must not record anything on the way out.

    Exercised inside a read-only section rather than by inspecting the source, so a
    tracker write added to the guard later fails here. The guard runs immediately
    before the record it protects (``cli`` calls it at both gate-recording sites),
    which is exactly where a write would be invisible.
    """
    _, linked = linked_worktree
    with tracker.read_only("the linked-worktree abort gate"):
        assert verify.linked_worktree_guard(linked) is not None


def test_the_linked_worktree_gate_is_classified_as_the_check_that_trips(
    linked_worktree: tuple[Path, Path],
) -> None:
    """The name the taxonomy keys on has to be the name of a check that trips.

    Pinning both halves in one test is the point: a classified name no check
    implements, or a check nothing classifies, would each read as coverage.
    """
    _, linked = linked_worktree
    assert verify.linked_worktree_guard(linked) is not None
    assert policy.gate_type(policy.LINKED_WORKTREE_GATE) == policy.ABORT


def test_linked_worktree_guard_allows_redirected_tracker(
    linked_worktree: tuple[Path, Path],
) -> None:
    """A worktree whose ledger redirects to base shares the tracker — recording is safe."""
    repo, linked = linked_worktree
    ledger = linked / tracker_paths.LEDGER_DIR_NAME
    ledger.mkdir(parents=True)
    (ledger / tracker_paths.REDIRECT_NAME).write_text(f"{repo}\n", encoding="utf-8")
    assert verify.linked_worktree_guard(linked) is None

    # A redirect elsewhere (or dangling) keeps the refusal.
    (ledger / tracker_paths.REDIRECT_NAME).write_text(str(linked / "elsewhere"), encoding="utf-8")
    assert verify.linked_worktree_guard(linked) is not None


def test_cli_verify_refuses_to_record_gate_from_linked_worktree(
    linked_worktree: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`verify --issue` from a linked worktree fails fast instead of losing the gate."""
    _repo, linked = linked_worktree
    monkeypatch.chdir(linked)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: pytest.fail("checks must not run"))
    assert cli.main(["verify", "--mode", "full", "--issue", "basicly-x"]) == 1
    err = capsys.readouterr().err
    assert "refusing to record gate" in err and "base checkout" in err


def test_cli_verify_returns_nonzero_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI exits 1 when a check fails and 0 when all pass."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text(
        '[[verify.checks]]\nname = "x"\ncommand = ["x"]\nmodes = ["full"]\n', encoding="utf-8"
    )

    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("x", "fail", 1),)),
    )
    assert cli.main(["verify", "--mode", "full"]) == 1

    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("x", "pass", 0),)),
    )
    assert cli.main(["verify", "--mode", "full"]) == 0


def test_cli_verify_fix_repairs_before_checking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--fix applies the declared repair first; a plain run stays a pure verdict.

    The plain run is what CI executes, so unformatted input from outside the
    harness must still fail there (basicly-kjc5.43).
    """
    monkeypatch.chdir(tmp_path)
    python = Path(sys.executable).as_posix()
    (tmp_path / "basicly.toml").write_text(
        "[[verify.checks]]\n"
        'name = "fmt"\n'
        f"command = ['{python}', '-c', "
        """'import pathlib, sys; sys.exit(0 if pathlib.Path("formatted").exists() else 1)']\n"""
        f"fix_command = ['{python}', '-c', "
        """'import pathlib; pathlib.Path("formatted").write_text("x")']\n"""
        'modes = ["full"]\n',
        encoding="utf-8",
    )

    assert cli.main(["verify", "--mode", "full"]) == 1
    assert not (tmp_path / "formatted").exists()

    assert cli.main(["verify", "--mode", "full", "--fix"]) == 0
    assert (tmp_path / "formatted").exists()
    assert "[fix] applied fmt" in capsys.readouterr().out


def test_cli_verify_fix_reports_a_broken_fixer_without_hiding_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fixer that cannot run is named on stderr and the check still decides."""
    monkeypatch.chdir(tmp_path)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    (tmp_path / "basicly.toml").write_text(
        '[[verify.checks]]\nname = "fmt"\ncommand = ["ghost-tool"]\n'
        'fix_command = ["ghost-tool", "--write"]\nmodes = ["full"]\n',
        encoding="utf-8",
    )

    assert cli.main(["verify", "--mode", "full", "--fix"]) == 1
    captured = capsys.readouterr()
    assert "[fix] fmt failed" in captured.err
    assert "command not found: ghost-tool" in captured.err
    assert "[verify] FAIL: fmt" in captured.err


# --- Telling an unreliable gate from a merit failure (basicly-55yh) ----------


def _flaky_run(fail_first: set[str]) -> object:
    """A subprocess.run stand-in where each named command fails once, then passes."""

    def fake_run(command, **_kw):
        name = command[0]
        if name in fail_first:
            fail_first.discard(name)
            return _Proc(1)
        return _Proc(0)

    return fake_run


def test_rerun_failures_reruns_only_the_checks_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A green check must not be paid for twice — only the failures re-run."""
    seen: list[list[str]] = []

    def fake_run(command, **_kw):
        seen.append(command)
        return _Proc(1 if command == ["bad"] else 0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    config = VerifyConfig((_check("ok", ("full",)), _check("bad", ("full",))))
    report = verify.run_verify(tmp_path, "full", config)
    seen.clear()

    rerun = verify.rerun_failures(report, tmp_path, "full", config)
    assert seen == [["bad"]]
    assert rerun.passed is False


def test_rerun_failures_passes_when_the_failure_does_not_reproduce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: a check that fails once and then passes is not a merit failure."""
    monkeypatch.setattr(verify.subprocess, "run", _flaky_run({"pytest"}))
    config = VerifyConfig((_check("pytest", ("full",)),))
    report = verify.run_verify(tmp_path, "full", config)
    assert report.passed is False

    assert verify.rerun_failures(report, tmp_path, "full", config).passed is True


def test_rerun_failures_returns_a_green_report_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passing run pays nothing: no command runs at all."""
    seen: list[list[str]] = []

    def fake_run(command, **_kw):
        seen.append(command)
        return _Proc(0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    config = VerifyConfig((_check("ok", ("full",)),))
    report = verify.run_verify(tmp_path, "full", config)
    seen.clear()

    assert verify.rerun_failures(report, tmp_path, "full", config) is report
    assert seen == []


def test_rerun_failures_keeps_the_verdict_when_no_check_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No new evidence means the original failure stands — never a vacuous pass.

    An empty VerifyReport reads as passing, so re-running nothing must return the
    failed report rather than an empty one, or a real failure would be forgiven.
    """
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_kw: _Proc(1))
    config = VerifyConfig((_check("gone", ("full",)),))
    report = verify.run_verify(tmp_path, "full", config)

    rerun = verify.rerun_failures(report, tmp_path, "full", VerifyConfig(()))
    assert rerun is report
    assert rerun.passed is False


def test_a_streamed_run_captures_no_output_so_nothing_is_forgiven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-safe direction: absent evidence must never excuse a failure.

    A normal gate streams to the terminal and carries no text, so
    ``dependency_defect`` has nothing to match and the original verdict stands
    (basicly-kjc5.56).
    """
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_kw: _Proc(1, _LOCK_TIMEOUT))
    config = VerifyConfig((_check("pytest", ("full",)),))

    report = verify.run_verify(tmp_path, "full", config)

    assert report.results[0].output == ""
    assert verify.dependency_defect(report) is None


def test_capture_collects_output_for_the_signature_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both streams, because a dependency may report on either."""
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_kw: _Proc(1, "out-", "err"))

    result = verify.run_check(_check("pytest", ("full",)), tmp_path, "full", capture=True)

    assert result.output == "out-err"


def test_dependency_defect_names_the_check_and_why_forgiving_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forgiven failure must say which dependency and on what grounds."""
    monkeypatch.setattr(verify.subprocess, "run", lambda *_a, **_kw: _Proc(1, _LOCK_TIMEOUT))
    config = VerifyConfig((_check("pytest", ("full",)),))
    report = verify.run_verify(tmp_path, "full", config)
    captured = verify.rerun_failures(report, tmp_path, "full", config, capture=True)

    reason = verify.dependency_defect(captured)

    assert reason is not None
    assert reason.startswith("pytest: ")
    assert "one lock" in reason


def test_dependency_defect_ignores_the_signature_on_a_passing_check() -> None:
    """Only a failure can be forgiven; matching a green check would be meaningless."""
    passing = verify.VerifyReport(
        "full", (verify.CheckResult("pytest", "pass", 0, output=_LOCK_TIMEOUT),)
    )

    assert verify.dependency_defect(passing) is None


def test_dependency_defect_needs_the_error_class_on_the_same_line() -> None:
    """The defect phrase alone is not proof a dependency produced it.

    A test fixture quoting the phrase must not be able to forgive its own failure, so
    the register also requires the store's own error class on that line.
    """
    bare = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pytest", "fail", 1, output="E  assert 'cannot be before created_at' in text\n"
            ),
        ),
    )

    assert verify.dependency_defect(bare) is None


def test_dependency_defect_forgives_a_br_write_lock_timeout() -> None:
    """A contended tracker lock is not evidence against the lane's diff.

    The ledger fails a write outright when it cannot take the lock before the timeout,
    and a landing that hits it spends a rework attempt against a cap of 2 for a defect
    that does not exist (basicly-m4zv.14, R8).
    """
    contended = verify.VerifyReport(
        "full", (verify.CheckResult("pytest", "fail", 1, output=_LOCK_TIMEOUT),)
    )

    reason = verify.dependency_defect(contended)

    assert reason is not None
    assert reason.startswith("pytest: ")
    assert "one lock" in reason


def test_dependency_defect_needs_the_error_class_on_the_lock_line_too() -> None:
    """A test asserting *about* the lock message must not forgive its own failure."""
    bare = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pytest", "fail", 1, output="E  assert 'another writer holds' in message\n"
            ),
        ),
    )

    assert verify.dependency_defect(bare) is None


def test_dependency_defect_refuses_when_only_some_failures_are_explained() -> None:
    """A run mixing a dependency defect with a real failure is a real failure."""
    mixed = verify.VerifyReport(
        "full",
        (
            verify.CheckResult("pytest", "fail", 1, output=_LOCK_TIMEOUT),
            verify.CheckResult("ruff", "fail", 1, output="E  F401 unused import\n"),
        ),
    )

    assert verify.dependency_defect(mixed) is None


# The ledger's own answer to a held lock, taken from `events.LedgerLock.__enter__`
# rather than composed (basicly-m4zv.14, R8): an invented fixture is what made the
# clock recogniser dead code through two "fixes" (basicly-aswc).
_LOCK_TIMEOUT = (
    "E           basicly_tracker_kit_events.LockUnavailableError: another writer holds "
    "/repo/.basicly/ledger/.events.lock after 5.0s\n"
)


# --- The projection gates must run locally, not only in CI (basicly-m4zv.11) ---

_REPO_ROOT = Path(__file__).parent.parent
_COMMANDS_FRAGMENT = (
    _REPO_ROOT / ".basicly-local" / "fragments" / "user" / "commands" / "commands.fragment.yaml"
)


def _projection_check_subcommands() -> set[str]:
    """Every projection check subcommand the CLI ships, read off its handler registry.

    Derived, not listed. A hand-written set is the defect this pair of tests exists
    to catch: the CLI shipped ``permissions-build``/``permissions-check`` while the
    list here named only the other four pairs, so a permissions edit was gated
    nowhere (basicly-tcmy.23). Every projection pair is ``<thing>-build`` /
    ``<thing>-check``, plus the unprefixed ``build`` / ``check`` for the fragment
    projection itself.
    """
    return {name for name in cli._handlers() if name == "check" or name.endswith("-check")}


def _basicly_subcommand(command: tuple[str, ...]) -> str | None:
    """The ``basicly`` subcommand *command* invokes, past a launcher prefix.

    The projection checks run through ``uv run`` (basicly-yru8eu), so the CLI is no
    longer argv[0] and matching on it would silently find no projection gate at all —
    which is the pass-by-empty-set shape the test below exists to refuse.
    """
    argv = list(command)
    if argv[:2] == ["uv", "run"]:
        argv = argv[2:]
    return argv[1] if len(argv) > 1 and argv[0] == "basicly" else None


def test_this_repos_fast_mode_runs_every_projection_gate() -> None:
    """A stale projection must fail before the change can reach the remote.

    ``protect-generated-commit`` compares the *staged* generated blob against the
    manifest, so it catches a hand-edited output but is blind to a *stale* one:
    editing a fragment does not stage the generated file at all, so its bytes still
    match. Before this, the projection gates ran only in CI — so a fragment edit
    with no rebuild passed every local hook and pushed stale output, which is
    precisely the posture the README, the site and the repo About claim we do not
    have.

    Asserts the wiring rather than the detection: that ``basicly check`` reports
    drift is covered elsewhere, and what went missing here was nobody *calling* it.
    ``fast`` specifically, not merely ``full`` — the published claim says commit
    time, and ``fast`` is the pre-commit mode.

    Matches on the subcommand alone, since a check may carry flags
    (``skills-check --all-default-roots``).
    """
    config = load_verify_config(_REPO_ROOT)
    fast = {
        sub
        for check in config.checks
        if "fast" in check.modes and (sub := _basicly_subcommand(check.command))
    }

    missing = _projection_check_subcommands() - fast
    assert not missing, f"projection gates absent from this repo's fast mode: {sorted(missing)}"


def test_the_always_on_commands_fragment_lists_every_projection_gate() -> None:
    """The always-on instruction text must name every projection pair the CLI ships.

    The gate above makes the drift fail; this one makes an agent able to *fix* it
    without reading the CLI's ``--help``. Reads the authored fragment rather than a
    projected ``AGENTS.md``/``CLAUDE.md``, which is where the list is written; that
    the projections match their source is ``basicly check``'s job.
    """
    body = yaml.safe_load(_COMMANDS_FRAGMENT.read_text(encoding="utf-8"))["body"]
    listed = set(re.findall(r"^uv run basicly ([a-z-]+)", body, flags=re.MULTILINE))

    missing = _projection_check_subcommands() - listed
    assert not missing, f"projection gates absent from the commands fragment: {sorted(missing)}"


def test_no_repo_declared_argv_invokes_a_bare_basicly() -> None:
    """A bare ``basicly`` reads the catalog of whichever checkout's venv is on PATH.

    ``catalog.bundled_catalog_root()`` walks up from its own ``__file__``, so the *binary*
    carries the catalog while the projected files are read from the command's cwd. During a
    landing those are two different trees: the engine runs from the base checkout and the
    tree under check is a lane worktree. basicly-yru8eu's own hook projection was therefore
    compared against base's catalog and its new file reported as ``not in the catalog (stale
    managed hook file)`` — a green tree failing the merge gate. ``uv run`` resolves the
    project from cwd, putting both halves in one tree.

    Both populations, because the write side does the same thing with a worse outcome: a
    regenerate command would project base's catalog over the lane's own edit and commit it.
    """
    checks = {
        f"[[verify.checks]] {check.name}": check.command
        for check in load_verify_config(_REPO_ROOT).checks
    }
    regenerate = {
        f"[worktree.regenerate_commands] {path}": command
        for path, command in load_worktree_config(_REPO_ROOT).regenerate_commands.items()
    }

    bare = sorted(
        label for label, command in (checks | regenerate).items() if command[:1] == ("basicly",)
    )
    assert not bare, f"argv invoking the base checkout's basicly, not the tree's: {bare}"


# --- A repo script runs on the project interpreter, not a bare one (basicly-tcmy.32) ---

_UV_RUN_PYTHON = ("uv", "run", "python")
_BARE_INTERPRETER = re.compile(r"^(python|python3(\.\d+)?|py)(\.exe)?$", flags=re.IGNORECASE)


def _interpreter_offences(check: VerifyCheck) -> list[str]:
    """Every argv of *check* that reaches an interpreter without going through uv."""
    offences = []
    for label, argv in (("command", check.command), ("fix_command", check.fix_command)):
        if not argv:
            continue
        names_the_interpreter = _BARE_INTERPRETER.match(argv[0]) is not None
        runs_a_script = any(arg.endswith(".py") for arg in argv)
        if not (names_the_interpreter or runs_a_script):
            continue
        if tuple(argv[:3]) == _UV_RUN_PYTHON:
            continue
        offences.append(f"{check.name}.{label} = {list(argv)}")
    return offences


def test_no_verify_check_invokes_a_bare_python_interpreter() -> None:
    """A check that runs a repository ``.py`` file must run it under ``uv run python``.

    The bare-binary convention the other checks follow holds for *console scripts* —
    ``ruff``, ``pyright``, ``bandit``, ``pytest``, ``basicly`` — which the venv installs
    into its ``bin``/``Scripts`` directory. It does not hold for the *interpreter*: on
    windows-latest a bare ``python`` resolves to a system interpreter that has neither
    ``yaml`` nor ``basicly`` importable, so ``docs-claims`` died at import time and
    failed the Windows quality-gates job alone while passing on ubuntu and macos
    (basicly-tcmy.32).

    Reads the invocation form out of this repo's own config rather than running the
    command, so the assertion is made on every platform instead of only on the runner
    that would break — the fourth platform-only defect to reach main is what put this
    rule in a test at all. ``_interpreter_offences`` is exercised against a known-bad
    check below, so a green result here cannot be an empty sweep.
    """
    offences = [
        offence
        for check in load_verify_config(_REPO_ROOT).checks
        for offence in _interpreter_offences(check)
    ]

    assert not offences, (
        "verify checks invoke a bare interpreter; use uv run python so the project "
        f"dependencies resolve on Windows too: {offences}"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("python", ".scripts/docs_claims.py", "--check"),
        ("python3", ".scripts/docs_claims.py"),
        ("python3.14", ".scripts/docs_claims.py"),
        ("py", "-3", ".scripts/docs_claims.py"),
        ("uv", "run", ".scripts/docs_claims.py"),
        (".scripts/docs_claims.py", "--check"),
    ],
)
def test_the_interpreter_rule_flags_a_bare_python_check(argv: tuple[str, ...]) -> None:
    """The control for the sweep above: each known-bad form is actually reported."""
    bad = VerifyCheck(name="future-check", command=argv, modes=frozenset({"fast"}))

    assert _interpreter_offences(bad) == [f"future-check.command = {list(argv)}"]


def test_the_interpreter_rule_accepts_uv_run_python_and_console_scripts() -> None:
    """And it stays silent on the two forms the repo does want."""
    script = VerifyCheck(
        name="docs-claims",
        command=("uv", "run", "python", ".scripts/docs_claims.py", "--check"),
        modes=frozenset({"fast"}),
        fix_command=("uv", "run", "python", ".scripts/docs_claims.py", "--fix"),
    )
    console = VerifyCheck(name="ruff", command=("ruff", "check"), modes=frozenset({"fast"}))

    assert _interpreter_offences(script) == []
    assert _interpreter_offences(console) == []


# --- Nothing merges wired to nothing (basicly-uexy) ---


def _load_wired_or_deleted():
    """Load the wired-or-deleted script from its path (``.scripts`` is not a package)."""
    script_path = _REPO_ROOT / ".scripts" / "wired_or_deleted.py"
    spec = importlib.util.spec_from_file_location("wired_or_deleted", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wired = _load_wired_or_deleted()


def _declared(name: str) -> VerifyCheck:
    """The check this repo declares under *name*, or a failure naming what is missing."""
    checks = {check.name: check for check in load_verify_config(_REPO_ROOT).checks}
    assert name in checks, f"basicly.toml declares no [[verify.checks]] entry '{name}'"
    return checks[name]


def test_this_repo_runs_vulture_as_a_declared_verify_check() -> None:
    """The dead-code tool must be *called*, which is the defect it was filed against.

    ``vulture`` sat in the dev dependency group and was invoked by no check and no
    script, so the tool that finds instruments nobody connected was one. Asserted on
    the declared argv rather than by running it: what went missing was the call, and a
    tool that reports findings is vulture's own problem.

    ``tests`` must stay out of its paths. That exclusion is what makes "read only by a
    test" a finding, so a well-meaning edit adding ``tests`` would quietly delete half
    the rule while leaving the check green.
    """
    check = _declared("vulture")

    assert check.command[0] == "vulture"
    assert "src" in check.command
    assert "tests" not in check.command


def test_this_repo_runs_the_wired_or_deleted_gate_as_a_declared_verify_check() -> None:
    """And the gate for the three surfaces vulture cannot see is wired too."""
    check = _declared("wired-or-deleted")

    assert check.command[-1].endswith("wired_or_deleted.py")
    assert "fast" in check.modes and "full" in check.modes


def test_the_gate_fails_when_no_vulture_check_is_declared(tmp_path: Path) -> None:
    """Removing the vulture check must break the gate, not silence it.

    The gate reads the declared command instead of restating it, so this is the same
    assertion as the one above made from the other side: a tree whose ``basicly.toml``
    has no vulture entry cannot pass, which is why the policing run below can trust
    that the argv it re-runs is the one that ships.
    """
    (tmp_path / "basicly.toml").write_text('[[verify.checks]]\nname = "ruff"\n', encoding="utf-8")

    with pytest.raises(wired.WiringError, match=r"declares no .* named 'vulture'"):
        wired.declared_vulture_command(tmp_path)


def test_an_unreferenced_command_is_reported_by_name() -> None:
    """A command no invocation names is the ``permissions-check`` defect exactly."""
    findings = wired.command_findings([("polish", "boots")], "nothing invokes it here")

    assert [finding.key for finding in findings] == ["command:polish boots"]
    assert "basicly polish boots" in findings[0].detail


def test_a_command_is_wired_by_a_shell_line_or_an_argv_array() -> None:
    """Both spellings an invocation is written in count as wiring."""
    shell = wired.command_findings([("polish", "boots")], "run `basicly polish boots --all`")
    argv = wired.command_findings([("polish", "boots")], '["basicly", "polish", "boots"]')

    assert shell == [] and argv == []


def test_a_command_is_not_wired_by_prose_that_omits_the_console_script() -> None:
    """The invocation form is required, so prose reusing the words is not credit.

    ``permissions-check`` was documented in full and gated nowhere, so a rule that a
    mention anywhere satisfies would credit the defect it exists to catch.
    """
    findings = wired.command_findings([("catalog", "list")], "the catalog list of skills")

    assert [finding.key for finding in findings] == ["command:catalog list"]


def test_a_longer_command_does_not_wire_its_prefix() -> None:
    """``merge`` is not satisfied by the ``merge-queue`` documented beside it."""
    findings = wired.command_findings([("worktree", "merge")], "basicly worktree merge-queue")

    assert [finding.key for finding in findings] == ["command:worktree merge"]


def test_the_command_sweep_reads_a_non_empty_command_set() -> None:
    """A sweep over nothing passes forever; the import contract failed that way."""
    paths = wired.command_paths()

    assert ("worktree", "create") in paths
    assert ("check",) in paths


def _plant_tree(root: Path) -> None:
    """A miniature ``src/basicly`` where exactly two declarations are unwired."""
    package = root / "src" / "basicly"
    package.mkdir(parents=True)
    (package / "widget.py").write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\nclass Widget:\n    wired: bool\n    orphan: bool\n",
        encoding="utf-8",
    )
    (package / "config.py").write_text(
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Config:\n    inert: bool\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from basicly.widget import Widget\n\n\n"
        "def use(widget: Widget) -> bool:\n    return widget.wired\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_widget.py").write_text(
        "from basicly.widget import Widget\n\n\n"
        "def test_orphan() -> None:\n    assert not Widget(True, False).orphan\n",
        encoding="utf-8",
    )


def test_a_field_read_only_by_its_own_module_or_a_test_is_reported_by_name(
    tmp_path: Path,
) -> None:
    """The control for the field rule, and for the ``tests/`` half of the rule.

    ``orphan`` is read, but only by a test, which is the shape a green tick hides: the
    suite proves the field can be constructed and nothing proves anyone wants it. A
    ``config.py`` declaration is reported as a config key rather than a record field,
    because an inert ``basicly.toml`` key and an unread record field are different
    conversations with the person fixing them.
    """
    _plant_tree(tmp_path)

    findings = wired.field_findings(tmp_path, wired.build_index(tmp_path))

    assert sorted(finding.key for finding in findings) == [
        "config-key:basicly.config.Config.inert",
        "record-field:basicly.widget.Widget.orphan",
    ]
    reported = {finding.key: finding for finding in findings}
    assert reported["record-field:basicly.widget.Widget.orphan"].location.endswith("widget.py:7")
    assert "config key 'Config.inert'" in reported["config-key:basicly.config.Config.inert"].detail


def test_a_field_a_second_module_reads_is_wired(tmp_path: Path) -> None:
    """The other direction: ``Widget.wired`` is read by ``consumer`` and stays silent."""
    _plant_tree(tmp_path)

    findings = wired.field_findings(tmp_path, wired.build_index(tmp_path))

    assert "record-field:basicly.widget.Widget.wired" not in {f.key for f in findings}


def test_a_private_record_is_internal_by_design_and_not_reported(tmp_path: Path) -> None:
    """An underscored record says "module-internal", so the rule does not apply."""
    _plant_tree(tmp_path)
    (tmp_path / "src" / "basicly" / "internal.py").write_text(
        "from dataclasses import dataclass\n\n\n@dataclass\nclass _Scratch:\n    only_here: bool\n",
        encoding="utf-8",
    )

    findings = wired.field_findings(tmp_path, wired.build_index(tmp_path))

    assert "record-field:basicly.internal._Scratch.only_here" not in {f.key for f in findings}


def test_a_vulture_suppression_that_stopped_reproducing_is_reported() -> None:
    """The baseline must shrink with the deletions, not outlive them.

    Vulture suppresses by bare name and never re-checks its own ignore list, so a name
    ``basicly-tcmy.21`` deletes would leave an exemption behind that silences the next
    unused name spelled the same way. That is the fail-open shape this phase exists to
    remove, one indirection out.
    """
    findings = wired.suppression_findings(["deleted_since", "still_unused"], {"still_unused"})

    assert [finding.key for finding in findings] == ["vulture-suppression:deleted_since"]
    assert "no finding any more" in findings[0].detail


def test_a_glob_vulture_suppression_is_refused() -> None:
    """One wildcard silences a surface nobody enumerated, and cannot be policed."""
    findings = wired.suppression_findings(["baseline_*"], {"baseline_runs"})

    assert [finding.key for finding in findings] == ["vulture-suppression:baseline_*"]
    assert "glob" in findings[0].detail


def test_the_baseline_holds_only_findings_that_still_reproduce() -> None:
    """Every exemption is a debt that must still be real, checked both ways.

    A stale entry is as much a defect as a new finding: it is an exemption earning
    nothing, and the gate reports it so ``basicly-tcmy.21`` cannot delete a symbol and
    leave its suppression behind. Runs the real collection over this repo, which is the
    only place the baseline means anything.
    """
    new, stale = wired.unexpected(wired.collect(_REPO_ROOT))

    assert [finding.detail for finding in new] == []
    assert stale == []


def _tiny_repo(root: Path) -> None:
    """A minimal tree with one public record whose field nothing outside its module reads."""
    module = root / "src" / "basicly" / "sample.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\nclass Sample:\n    folded: int\n",
        encoding="utf-8",
    )


def test_a_nested_worktree_does_not_retire_every_record_field_finding(tmp_path: Path) -> None:
    """A second copy of a module inside the root must not count as its own consumer.

    Regression for basicly-jr0l.70. A parallel agent spawn puts a linked git worktree at
    `.claude/worktrees/agent-<id>/`, so `src/basicly` is indexed twice under two site
    labels and every field looks referenced from outside itself. Measured on the real
    repo with two agent worktrees live: 48 modules but 401 sites, and **all 44**
    record-field baseline entries reported stale at once.

    The reason this is a P0 rather than noise is the advice: the gate says "remove the
    entry", which during a parallel run empties the baseline and blinds the surface.
    """
    _tiny_repo(tmp_path)
    before = wired.field_findings(tmp_path, wired.build_index(tmp_path))
    assert [f.key for f in before] == ["record-field:basicly.sample.Sample.folded"]

    # The duplicate a worktree-isolated agent leaves inside the root.
    nested = tmp_path / ".claude" / "worktrees" / "agent-abc" / "src" / "basicly"
    nested.mkdir(parents=True)
    (nested / "sample.py").write_text(
        (tmp_path / "src" / "basicly" / "sample.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    after = wired.field_findings(tmp_path, wired.build_index(tmp_path))
    assert [f.key for f in after] == [f.key for f in before], (
        "a nested checkout must not be a referrer site"
    )


def test_the_kit_is_not_a_referrer_because_it_cannot_import_basicly(tmp_path: Path) -> None:
    """The kit sharing a field's English word is vocabulary, not a consumer.

    Regression for basicly-jr0l.70. `.basicly/core/kit/**` ships standalone with zero
    `basicly` imports, enforced by the `kit-boundary` check, so it structurally cannot
    read a `basicly` record field. Counted as a referrer it silently retires genuine
    suppressions - it retired `supervise.DispatchBundle.folded` and
    `worktree.RemovalVerdict.holds` because `migrate.py` and `events.py` use the ordinary
    words `folded` and `holds`.
    """
    _tiny_repo(tmp_path)
    kit = tmp_path / ".basicly" / "core" / "kit" / "tracker"
    kit.mkdir(parents=True)
    (kit / "events.py").write_text(
        '"""Fold the log."""\n\n\ndef fold(events: list[int]) -> int:\n'
        "    folded = sum(events)\n    return folded\n",
        encoding="utf-8",
    )

    findings = wired.field_findings(tmp_path, wired.build_index(tmp_path))

    assert [f.key for f in findings] == ["record-field:basicly.sample.Sample.folded"], (
        "kit vocabulary must not mask a record-field finding"
    )


# --- The security scan covers every harness directory, not the first two (basicly-5gn2) ---

_HARNESS_PYTHON_ROOTS = (".scripts", ".basicly/core")

_UNSAFE_MODULE = (
    "import subprocess\n\n\ndef spawn(command):\n    return subprocess.run(command, shell=True)\n"
)


def _bandit_targets() -> tuple[str, ...]:
    """The paths this repo's declared bandit check recurses into."""
    for check in load_verify_config(_REPO_ROOT).checks:
        if check.name == "bandit":
            return tuple(check.command[check.command.index("-r") + 1 :])
    raise AssertionError("this repo declares no bandit check")


def _unscanned_directories(targets: tuple[str, ...], paths: list[str]) -> list[str]:
    """Every directory holding one of *paths* that no bandit target recurses into."""
    scanned = [PurePosixPath(target) for target in targets]
    unscanned = {
        str(directory)
        for directory in (PurePosixPath(path).parent for path in paths)
        if not any(directory == target or target in directory.parents for target in scanned)
    }
    return sorted(unscanned)


def _tracked_harness_python() -> list[str]:
    """Every tracked ``.py`` file under the roots the harness executes from."""
    listing = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, roots are literals
        ["git", "ls-files", "--", *_HARNESS_PYTHON_ROOTS],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in listing.stdout.splitlines() if line.endswith(".py")]


def test_bandit_scans_every_harness_python_directory() -> None:
    """No directory of harness Python may sit outside the security scan.

    The targets were written when ``.scripts`` and ``.basicly/core/hooks`` were the
    whole set. ``.basicly/core/kit`` arrived later (basicly-wbsz.1) and inherited no
    coverage, and nothing failed — the scan cannot notice a directory it was never
    pointed at, which is the one failure shape a green security gate hides
    (basicly-5gn2). Sweeping the tracked tree makes the *next* such directory fail
    here instead.

    Tracked files rather than a directory walk: an untracked scratch file is not
    something the repo ships, and would otherwise fail a gate about what does.
    """
    tracked = _tracked_harness_python()
    assert tracked, f"the sweep found no harness Python under {list(_HARNESS_PYTHON_ROOTS)}"

    unscanned = _unscanned_directories(_bandit_targets(), tracked)

    assert not unscanned, (
        "harness Python outside the bandit check's targets; add each directory to the "
        f"bandit [[verify.checks]] entry in basicly.toml: {unscanned}"
    )


def test_the_coverage_sweep_reports_a_directory_no_target_covers() -> None:
    """The control for the sweep above, in the exact shape this bead was filed for."""
    unscanned = _unscanned_directories(
        (".scripts", ".basicly/core/hooks"),
        [".scripts/docs_claims.py", ".basicly/core/kit/tier/tier_resolver.py"],
    )

    assert unscanned == [".basicly/core/kit/tier"]


def test_bandit_fails_on_an_unsafe_construct_in_the_kit(tmp_path: Path) -> None:
    """Being named as a target has to make an unsafe kit module *fail* the check.

    Coverage in the argv is necessary and not sufficient — a scan is silent when its
    config skips the rule — so this runs the declared command verbatim against a tree
    shaped like the repo's, with the unsafe module in the kit. The same command minus
    the kit target is run over the same tree as the discriminator: it passes, which is
    the silent green this bead removes.

    Asserts on the filename and the rule id rather than a rendered path, so the
    separator bandit prints does not decide the verdict.

    The kit directory is built independently of *targets* rather than as a side
    effect of iterating them: were the kit target dropped from the config, deriving
    it from *targets* would leave the module unwritten and this test would die of
    ``FileNotFoundError`` during setup instead of reporting the silent green it
    exists to name.
    """
    unsafe_module = tmp_path / ".basicly/core/kit/unsafe_probe.py"
    shutil.copy(_REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    targets = _bandit_targets()
    for target in targets:
        (tmp_path / target).mkdir(parents=True, exist_ok=True)
    unsafe_module.parent.mkdir(parents=True, exist_ok=True)
    unsafe_module.write_text(_UNSAFE_MODULE, encoding="utf-8")

    def scan(paths: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603 - argv read from committed config, no shell
            ["bandit", "-q", "-c", "pyproject.toml", "-r", *paths],
            cwd=tmp_path,
            check=False,  # a non-zero exit is the assertion, not an error
            capture_output=True,
            text=True,
        )

    scanned = scan(targets)
    without_the_kit = scan(tuple(path for path in targets if path != ".basicly/core/kit"))

    assert scanned.returncode != 0, f"the unsafe kit module passed the scan: {scanned.stdout}"
    assert "unsafe_probe.py" in scanned.stdout
    assert "B602" in scanned.stdout
    assert without_the_kit.returncode == 0, (
        "the discriminator failed for another reason than the kit, so a passing scan "
        f"above would prove nothing: {without_the_kit.stdout}"
    )


# --- A landing must verify the lane, not the base checkout (basicly-ihg3) -----
#
# lint-imports is the only configured check that resolves its target by *import*
# rather than by path: pyright and vulture read files under cwd, so they follow the
# worktree, while import-linter imports the `basicly` package the active venv points
# at. Spelled bare, it therefore analysed the base checkout during every landing —
# `basicly loop advance` runs verify as a subprocess and PATH carried the base
# `.venv/bin`. A lane adding a new module failed with `module basicly.<new> does not
# exist`, a finding that is not about the lane's tree and that no amount of bounded
# rework could act on: basicly-u2hl.2 escalated at rework 2/2 on it.


def _import_resolving_checks() -> tuple[VerifyCheck, ...]:
    """The declared checks that find their target by importing it, not by reading a path."""
    by_name = {check.name: check for check in load_verify_config(_REPO_ROOT).checks}
    return tuple(by_name[name] for name in ("lint-imports",) if name in by_name)


def test_an_import_resolving_check_runs_under_the_project_environment() -> None:
    """Every import-resolving check is spelled `uv run`, so it analyses this checkout."""
    checks = _import_resolving_checks()
    assert checks, "this repo declares no import-resolving check"
    for check in checks:
        assert tuple(check.command[:2]) == ("uv", "run"), (
            f"the {check.name} check is spelled {check.command}, which resolves from PATH; "
            "under a landing that is the base checkout's venv, so it would verify a package "
            "the lane does not have"
        )


def test_a_path_reading_check_needs_no_uv_run_prefix() -> None:
    """The discriminator: a path-reading check is not required to carry the prefix.

    Without this, the assertion above would pass just as well if every check in the
    file happened to start with `uv run` for unrelated reasons, and would stop being
    evidence about import resolution specifically.
    """
    by_name = {check.name: check for check in load_verify_config(_REPO_ROOT).checks}
    assert by_name["vulture"].command[0] == "vulture"
    assert "src" in by_name["vulture"].command, (
        "vulture is being cited as a path-reading check, so it must name the paths it reads"
    )


# --- The check set is assembled from the drop-in fragments too (basicly-ef7t) -----------

_FRAGMENT_CONFIG = """\
[[verify.checks]]
name = "declared-in-the-config"
command = ["true"]
modes = ["fast", "full"]
"""


def _fragment_repo(root: Path, **fragments: str) -> Path:
    """A repo declaring one check in basicly.toml and *fragments* keyed by filename stem."""
    (root / "basicly.toml").write_text(_FRAGMENT_CONFIG, encoding="utf-8")
    (root / "basicly.d").mkdir(exist_ok=True)
    for stem, body in fragments.items():
        (root / "basicly.d" / f"{stem}.toml").write_text(body, encoding="utf-8")
    return root


def _declared_check(name: str) -> str:
    return f'[[verify.checks]]\nname = "{name}"\ncommand = ["true"]\nmodes = ["fast"]\n'


def test_a_fragment_check_is_appended_to_the_configs_own_in_filename_order(tmp_path: Path) -> None:
    """A lane's own file contributes a check, and the order does not depend on the reader.

    Filename order rather than directory order, so two machines assemble the same set —
    and after the config's own entries, so a fragment cannot reorder the gates a repo
    already declared.
    """
    repo = _fragment_repo(
        tmp_path,
        **{
            "basicly-zzzz": _declared_check("last-lane"),
            "basicly-aaaa": _declared_check("first-lane"),
        },
    )

    assert [check.name for check in load_verify_config(repo).checks] == [
        "declared-in-the-config",
        "first-lane",
        "last-lane",
    ]


def test_a_fragment_is_appended_where_the_machine_overlay_still_replaces(tmp_path: Path) -> None:
    """The one asymmetry: a lane adds, a machine overrides.

    A fragment is one lane's addition, so appending is the only reading under which two
    lanes both keep their gate. basicly.local.toml is the machine saying *instead*, which
    is what it has always meant, and this pins that the fragment change did not quietly
    turn it into an addition as well.
    """
    repo = _fragment_repo(tmp_path, **{"basicly-lane": _declared_check("lane-gate")})
    (repo / "basicly.local.toml").write_text(_declared_check("only-on-this-machine"), "utf-8")

    assert [check.name for check in load_verify_config(repo).checks] == ["only-on-this-machine"]


def test_an_unknown_key_in_a_fragment_is_refused_and_names_the_fragment(tmp_path: Path) -> None:
    """A fragment goes through the same schema as basicly.toml, and fails the same way.

    Without this the fragment directory would be the one place in the config layering where
    a typo is silently ignored — the hole basicly-1piy closed for the two files.
    """
    repo = _fragment_repo(
        tmp_path, **{"basicly-lane": '[[verify.checks]]\nname = "x"\ncomand = ["true"]\n'}
    )

    with pytest.raises(ValueError, match=r"basicly.d/basicly-lane.toml: unknown key 'comand'"):
        load_verify_config(repo)


def test_a_fragment_that_is_not_toml_refuses_instead_of_being_skipped(tmp_path: Path) -> None:
    """An unreadable fragment must not degrade to "this lane declared nothing"."""
    repo = _fragment_repo(tmp_path, **{"basicly-lane": "[[verify.checks]\nname = 'x'\n"})

    with pytest.raises(dropin.FragmentError, match=r"basicly.d/basicly-lane.toml"):
        load_verify_config(repo)


def test_this_repo_declares_its_whole_check_set_between_the_config_and_the_fragments() -> None:
    """The assembled set is exactly what the two sources declare, nothing lost in between.

    The second acceptance criterion: assembling from fragments must run the same checks as
    the single array did. Compared against the raw TOML rather than a frozen list of names,
    so adding a check keeps the assertion true and *removing* one from the reader's answer
    still fails it.
    """
    declared = tomllib.loads((_REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    names = [check["name"] for check in declared["verify"]["checks"]]
    for document in dropin.documents(_REPO_ROOT).values():
        names += [check["name"] for check in (document.get("verify") or {}).get("checks", [])]

    assert [check.name for check in load_verify_config(_REPO_ROOT).checks] == names


# What `.scripts/ratchet.py` `report` writes for a closed record owing a note, verbatim:
# subject line, then the indented remedy naming the exact file to create.
_RELEASE_NOTES = (
    "release-notes: basicly-fi1i7z: closed with a `## Scope` naming a shipped path and "
    "no release note\n"
    "release-notes:   write `changelog.d/basicly-fi1i7z.<category>.md`, or declare it "
    "invisible to a consumer\n"
)


def test_the_remedy_a_ratchet_gate_printed_is_carried() -> None:
    """A lane close reported only the check's name twice on 2026-08-21 (basicly-fi1i7z)."""
    remedy = verify.check_remedy(_RELEASE_NOTES, "release-notes")
    assert remedy is not None
    assert "changelog.d/basicly-fi1i7z.<category>.md" in remedy
    # The label is stripped: the caller already prints the check's name.
    assert not remedy.startswith("release-notes:")


def test_a_check_that_printed_no_labelled_line_yields_no_remedy() -> None:
    """Absence is None rather than an empty string, so a caller can fall back."""
    assert verify.check_remedy(_RELEASE_NOTES, "module-size") is None
