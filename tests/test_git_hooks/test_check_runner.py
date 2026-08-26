"""Tests for the config-driven hook check runner (basicly-yp3)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from basicly.config import load_verify_config

# Stand-in for a formatter: with --check it reports unformatted files (exit 1),
# without it rewrites them. Real ruff is not used so the test proves the
# apply-then-check mechanism rather than a tool's behavior.
_FORMATTER = """\
import sys

check = "--check" in sys.argv
unformatted = []
for path in [arg for arg in sys.argv[1:] if arg != "--check"]:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    fixed = text.replace("  =  ", " = ")
    if fixed == text:
        continue
    unformatted.append(path)
    if not check:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(fixed)
sys.exit(1 if check and unformatted else 0)
"""


def _load_check_runner():
    """Load the check_runner module from its hook-script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "check_runner.py"
    )
    spec = importlib.util.spec_from_file_location("check_runner_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(repo: Path, body: str) -> None:
    (repo / "basicly.toml").write_text(body, encoding="utf-8")


def test_no_config_or_checks_passes_with_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A consumer with no basicly.toml (or no checks) is never blocked."""
    module = _load_check_runner()
    assert module.run_checks(tmp_path, "fast") == 0
    assert "nothing to gate" in capsys.readouterr().out

    _write_config(tmp_path, "[worktree]\nconcurrency = 2\n")
    assert module.run_checks(tmp_path, "fast") == 0


def test_mode_routing_selects_only_matching_checks(tmp_path: Path) -> None:
    """Checks run only in their declared modes (fast vs full)."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "fast-only"\ncommand = ["true"]\nmodes = ["fast"]\n'
        '[[verify.checks]]\nname = "full-only"\ncommand = ["true"]\nmodes = ["full"]\n',
    )
    assert [name for name, _ in module.load_checks(tmp_path, "fast")] == ["fast-only"]
    assert [name for name, _ in module.load_checks(tmp_path, "full")] == ["full-only"]


def test_failing_check_fails_the_run(tmp_path: Path) -> None:
    """A non-zero check exits 1."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "bad"\ncommand = ["false"]\nmodes = ["fast"]\n',
    )
    assert module.run_checks(tmp_path, "fast") == 1


def test_passing_checks_pass_the_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """All-green checks exit 0 with a summary line."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "ok"\ncommand = ["true"]\nmodes = ["fast"]\n',
    )
    assert module.run_checks(tmp_path, "fast") == 0
    assert "1/1" in capsys.readouterr().out


def test_missing_tool_is_a_one_line_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command not on PATH fails with a readable message, not a traceback."""
    module = _load_check_runner()
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "ghost"\ncommand = ["ghost-tool"]\nmodes = ["fast"]\n',
    )
    assert module.run_checks(tmp_path, "fast") == 1
    err = capsys.readouterr().err
    assert "command not found: ghost-tool" in err


def test_malformed_check_is_a_loud_error(tmp_path: Path) -> None:
    """A check without a command must not be silently dropped."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "broken"\nmodes = ["fast"]\n',
    )
    with pytest.raises(SystemExit, match="command"):
        module.load_checks(tmp_path, "fast")


def test_malformed_fix_command_is_a_loud_error(tmp_path: Path) -> None:
    """A fix_command that is not a list of strings is rejected, not ignored."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "fmt"\ncommand = ["true"]\n'
        'fix_command = "fmt"\nmodes = ["fast"]\n',
    )
    with pytest.raises(SystemExit, match="fix_command"):
        module.load_fixes(tmp_path, "fast")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # nosec B603 B607


def _staged_text(repo: Path, path: str) -> str:
    proc = subprocess.run(  # nosec B603 B607
        ["git", "show", f":{path}"], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def repo_with_formatter(tmp_path: Path) -> Path:
    """A git repo with a staged unformatted file and a formatter declared as a fix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    formatter = repo / "fmt.py"
    formatter.write_text(_FORMATTER, encoding="utf-8")
    _write_config(
        repo,
        "[[verify.checks]]\n"
        'name = "fmt"\n'
        f'command = ["{Path(sys.executable).as_posix()}", "fmt.py", "--check"]\n'
        f'fix_command = ["{Path(sys.executable).as_posix()}", "fmt.py"]\n'
        'modes = ["fast"]\n'
        'staged_suffix = ".py"\n',
    )
    (repo / "mod.py").write_text("x  =  1\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    return repo


def test_apply_fixes_formats_and_restages_so_the_check_passes(
    repo_with_formatter: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Formatting-only violations are repaired before the gate runs (basicly-kjc5.43).

    The commit carries the formatted bytes, so nothing has to hand-run the
    formatter and re-verify.
    """
    module = _load_check_runner()

    module.apply_fixes(repo_with_formatter, "fast")

    assert (repo_with_formatter / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
    assert _staged_text(repo_with_formatter, "mod.py") == "x = 1\n"
    assert "mod.py" in capsys.readouterr().out
    assert module.run_checks(repo_with_formatter, "fast") == 0


def test_apply_fixes_leaves_a_non_mechanical_failure_to_the_check(
    repo_with_formatter: Path,
) -> None:
    """A check with no fix_command still fails and is reported."""
    module = _load_check_runner()
    _write_config(
        repo_with_formatter,
        '[[verify.checks]]\nname = "lint"\ncommand = ["false"]\nmodes = ["fast"]\n',
    )

    module.apply_fixes(repo_with_formatter, "fast")

    assert (repo_with_formatter / "mod.py").read_text(encoding="utf-8") == "x  =  1\n"
    assert module.run_checks(repo_with_formatter, "fast") == 1


def test_apply_fixes_never_restages_a_partially_staged_file(repo_with_formatter: Path) -> None:
    """A file with unstaged work of its own keeps its staged bytes.

    Re-adding it would sweep changes the author deliberately left out of the
    index into the commit.
    """
    module = _load_check_runner()
    (repo_with_formatter / "mod.py").write_text("x  =  1\nwork_in_progress\n", encoding="utf-8")

    module.apply_fixes(repo_with_formatter, "fast")

    assert _staged_text(repo_with_formatter, "mod.py") == "x  =  1\n"
    assert "work_in_progress" in (repo_with_formatter / "mod.py").read_text(encoding="utf-8")


def test_apply_fixes_skips_a_suffix_with_nothing_staged(repo_with_formatter: Path) -> None:
    """With no staged file of the declared suffix the fixer is not invoked."""
    module = _load_check_runner()
    _git(repo_with_formatter, "reset")
    (repo_with_formatter / "notes.md").write_text("hi\n", encoding="utf-8")
    _git(repo_with_formatter, "add", "notes.md")

    module.apply_fixes(repo_with_formatter, "fast")

    assert (repo_with_formatter / "mod.py").read_text(encoding="utf-8") == "x  =  1\n"


def test_apply_fixes_outside_a_repo_is_a_reported_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable index skips the fix loudly instead of guessing."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "fmt"\ncommand = ["true"]\n'
        'fix_command = ["false"]\nmodes = ["fast"]\n',
    )

    module.apply_fixes(tmp_path, "fast")

    assert "cannot read the git index" in capsys.readouterr().err


# --- The hook reads the drop-in fragments too (basicly-ef7t) ------------------------------


def _write_fragment(repo: Path, stem: str, body: str) -> None:
    (repo / "basicly.d").mkdir(exist_ok=True)
    (repo / "basicly.d" / f"{stem}.toml").write_text(body, encoding="utf-8")


def test_a_check_declared_in_a_fragment_gates_the_commit(tmp_path: Path) -> None:
    """A lane's own gate has to run in the hook, or the fragment split is a way to lose one.

    The engine assembling the fragments is not enough on its own: this runner is standalone
    by contract (a consumer's pre-commit imports nothing from basicly), so it is a second
    reader of the same convention and would otherwise silently gate less than `verify` does.
    """
    module = _load_check_runner()
    _write_config(
        tmp_path, '[[verify.checks]]\nname = "in-config"\ncommand = ["true"]\nmodes = ["fast"]\n'
    )
    _write_fragment(
        tmp_path,
        "basicly-lane",
        '[[verify.checks]]\nname = "in-fragment"\ncommand = ["true"]\nmodes = ["fast"]\n',
    )

    assert [name for name, _ in module.load_checks(tmp_path, "fast")] == [
        "in-config",
        "in-fragment",
    ]


def test_a_malformed_fragment_check_names_the_fragment_not_the_config(tmp_path: Path) -> None:
    """Which file to fix, when the entry that is wrong is not in basicly.toml."""
    module = _load_check_runner()
    _write_config(tmp_path, "[worktree]\nconcurrency = 2\n")
    _write_fragment(
        tmp_path, "basicly-lane", '[[verify.checks]]\nname = "no-command"\nmodes = ["fast"]\n'
    )

    with pytest.raises(
        SystemExit, match=r"basicly-lane.toml: check 'no-command' needs a 'command'"
    ):
        module.load_checks(tmp_path, "fast")


# Scoped commit subset (basicly-j7spdb). `sys.executable` rather than `true`/`false` so a
# check that must be *observed passing* cannot pass by being absent from PATH.
_PASSES = f'["{Path(sys.executable).as_posix()}", "-c", ""]'
_FAILS = f'["{Path(sys.executable).as_posix()}", "-c", "raise SystemExit(1)"]'


@pytest.fixture
def repo_with_a_scoped_gate(tmp_path: Path) -> Path:
    """A git repo with a staged doc and a failing gate that declares only Python inputs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _write_config(
        repo,
        f'[[verify.checks]]\nname = "py-only"\ncommand = {_FAILS}\n'
        'modes = ["fast", "full"]\ninputs = ["src/**/*.py"]\n'
        f'[[verify.checks]]\nname = "unscoped"\ncommand = {_PASSES}\nmodes = ["fast", "full"]\n',
    )
    (repo / "notes.md").write_text("doc\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    return repo


def test_a_gate_no_staged_path_can_reach_is_skipped_by_name(
    repo_with_a_scoped_gate: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A doc-only commit runs the gates its diff reaches and says which it skipped."""
    module = _load_check_runner()

    assert module.run_checks(repo_with_a_scoped_gate, "fast", scope_to_diff=True) == 0

    out = capsys.readouterr().out
    assert "py-only SKIPPED" in out
    assert "1/1" in out and "1 skipped: py-only" in out


def test_the_landing_mode_still_fails_on_a_gate_the_commit_skipped(
    repo_with_a_scoped_gate: Path,
) -> None:
    """No green landing rests on a skip: `full` ignores `inputs` (basicly-j7spdb).

    Same tree and same staged diff as the scoped run above, so the mode is the only
    difference — the property the commit-time subset is affordable on.
    """
    module = _load_check_runner()

    assert module.run_checks(repo_with_a_scoped_gate, "full") == 1
    assert module.run_checks(repo_with_a_scoped_gate, "fast") == 1


def test_a_matching_staged_path_keeps_the_gate(repo_with_a_scoped_gate: Path) -> None:
    """The scoped run is a filter on the diff, not a mode: a matching path still gates."""
    module = _load_check_runner()
    source = repo_with_a_scoped_gate / "src"
    source.mkdir()
    (source / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_with_a_scoped_gate, "add", "src/mod.py")

    assert module.run_checks(repo_with_a_scoped_gate, "fast", scope_to_diff=True) == 1


def test_an_undeterminable_diff_skips_nothing(tmp_path: Path) -> None:
    """Every uncertainty runs the check: not a repo at all, then a repo with an empty index."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        f'[[verify.checks]]\nname = "py-only"\ncommand = {_FAILS}\n'
        'modes = ["fast"]\ninputs = ["src/**/*.py"]\n',
    )
    assert module.scoped_skips(tmp_path, "fast") == {}
    assert module.run_checks(tmp_path, "fast", scope_to_diff=True) == 1

    _git(tmp_path, "init", "-b", "main")
    assert module.scoped_skips(tmp_path, "fast") == {}


def test_malformed_inputs_is_a_loud_error(tmp_path: Path) -> None:
    """`inputs` that is not a list of globs is rejected, not read as "reads everything"."""
    module = _load_check_runner()
    _write_config(
        tmp_path,
        '[[verify.checks]]\nname = "lint"\ncommand = ["ruff"]\nmodes = ["fast"]\ninputs = "src"\n',
    )
    with pytest.raises(SystemExit, match="'inputs' must be a list"):
        module.load_inputs(tmp_path, "fast")


def test_every_declared_input_set_matches_something_in_this_repo() -> None:
    """No check of this repo's own is skipped by every commit there is (basicly-j7spdb).

    The fail-open the `inputs` key introduces is a typo: a glob matching nothing makes the
    gate skip on every diff. Per check, not per glob — `**/*.pyi` legitimately matches
    nothing today and starts mattering the day a stub is added.
    """
    module = _load_check_runner()
    repo_root = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    paths = [PurePosixPath(name) for name in tracked if name]

    for mode in ("fast", "full"):
        for name, globs in module.load_inputs(repo_root, mode).items():
            matched = [g for g in globs if any(path.full_match(g) for path in paths)]
            assert matched, f"{mode}: check {name!r} declares inputs matching no tracked file"


def test_every_declared_input_set_carries_the_lock() -> None:
    """A lock-only commit is the whole shape of a dependency bump, and it skipped ten gates.

    The lock resolves the tool whose verdict the check *is* (basicly-j7spdb).
    """
    module = _load_check_runner()
    repo_root = Path(__file__).resolve().parents[2]

    for name, globs in module.load_inputs(repo_root, "fast").items():
        assert "uv.lock" in globs, f"check {name!r} declares inputs without uv.lock"


def test_the_hook_and_the_engine_assemble_the_same_set_for_this_repo() -> None:
    """Two readers of one convention, pinned to agree on the tree they both really run on.

    The failure this excludes is the split drifting: a fragment the engine reads and the
    hook does not (or the reverse) makes `basicly verify` and the pre-commit gate disagree
    about what this repo's fast mode is, which is exactly the silence a lost gate needs.
    """
    module = _load_check_runner()
    repo_root = Path(__file__).resolve().parents[2]
    engine = [check.name for check in load_verify_config(repo_root).for_mode("fast")]

    assert [name for name, _ in module.load_checks(repo_root, "fast")] == engine
