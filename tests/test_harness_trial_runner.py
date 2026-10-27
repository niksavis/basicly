from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import runner


def load_trial_runner() -> Any:
    path = Path(__file__).resolve().parents[1] / ".scripts/harness_trial_runner.py"
    spec = importlib.util.spec_from_file_location("harness_trial_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as paths:
        paths.syspath_prepend(str(path.parent))
        spec.loader.exec_module(module)
    return module


def case() -> dict[str, Any]:
    return {
        "id": "synthetic-control",
        "files": {"value.txt": "before"},
        "prompt": "fixture",
        "check": [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text() == 'after'",
        ],
    }


def identity() -> dict[str, str]:
    return {
        "id": "test",
        "family": "fixture",
        "model": "fixture",
        "effort": "none",
        "arm": "control",
    }


def test_harness_trial_runner_grades_real_fixture_changes(tmp_path: Path) -> None:
    spec = runner.RunnerSpec(
        "fixture",
        command=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('value.txt').write_text('after')",
            runner.PROMPT_PLACEHOLDER,
        ),
    )
    result = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    assert result["outcome"] == "passed"
    assert result["tokens"] is None
    assert result["cost"] is None
    assert (tmp_path / "one/trial.json").is_file()


@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize(
    "usage_format,event_kind,key",
    [
        (runner.CODEX_JSONL, "thread.started", "thread_id"),
        (runner.CLAUDE_JSON, "result", "session_id"),
        (runner.CLAUDE_STREAM_JSON, "result", "session_id"),
        (runner.CLAUDE_STREAM_JSON, "system", "session_id"),
    ],
)
def test_harness_trial_runner_records_unambiguous_native_session(
    tmp_path: Path, conflicting: bool, usage_format: str, event_kind: str, key: str
) -> None:
    session = "00000000-0000-0000-0000-000000000001"
    events = [{"type": event_kind, "subtype": "init", key: session}]
    if conflicting:
        events.append({
            "type": event_kind,
            "subtype": "init",
            key: "00000000-0000-0000-0000-000000000002",
        })
    script = (
        "import json; from pathlib import Path; Path('value.txt').write_text('after'); "
        f"[print(json.dumps(event)) for event in {events!r}]"
    )
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=usage_format,
    )
    report = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    if conflicting:
        assert report["outcome"] == "infrastructure_error"
        assert "conflicting native session" in report["error"]
    else:
        assert report["outcome"] == "passed"
        assert report["session_id"] == session


@pytest.mark.parametrize("session", ["", 42, "not-a-session-id"])
def test_harness_trial_runner_rejects_malformed_native_session(
    tmp_path: Path, session: Any
) -> None:
    script = (
        "import json; from pathlib import Path; Path('value.txt').write_text('after'); "
        f"print(json.dumps({{'type': 'thread.started', 'thread_id': {session!r}}}))"
    )
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=runner.CODEX_JSONL,
    )
    report = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    assert report["outcome"] == "infrastructure_error"
    assert "invalid native session identifier" in report["error"]
    assert report["acceptance"]["outcome"] == "passed"


def test_harness_trial_runner_rejects_successful_agent_with_wrong_output(tmp_path: Path) -> None:
    spec = runner.RunnerSpec(
        "fixture", command=(sys.executable, "-c", "pass", runner.PROMPT_PLACEHOLDER)
    )
    result = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    assert result["outcome"] == "failed"


def test_harness_trial_runner_launch_failure_is_not_a_task_pass(tmp_path: Path) -> None:
    spec = runner.RunnerSpec(
        "fixture", command=(str(tmp_path / "missing-program"), runner.PROMPT_PLACEHOLDER)
    )
    result = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    assert result["outcome"] == "infrastructure_error"
    assert not (tmp_path / "one/grading.json").exists()


@pytest.mark.parametrize("timed_out,returncode", [(True, None), (False, 1)])
def test_harness_trial_runner_retains_acceptance_after_unsuccessful_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timed_out: bool, returncode: int | None
) -> None:
    module = load_trial_runner()

    def finish(_spec: Any, _prompt: str, workspace: Path, **_kwargs: Any) -> runner.RunResult:
        (workspace / "value.txt").write_text("after")
        return runner.RunResult(
            runner="fixture",
            command=(),
            executed=True,
            returncode=returncode,
            timed_out=timed_out,
            duration_s=1.0,
        )

    monkeypatch.setattr(module.runner, "run", finish)
    result = module.execute_trial(
        runner.RunnerSpec("fixture"), case(), tmp_path / "one", identity()
    )
    assert result["outcome"] == ("timed_out" if timed_out else "infrastructure_error")
    assert result["acceptance"]["outcome"] == "passed"
    assert json.loads((tmp_path / "one/grading.json").read_text())["outcome"] == "passed"


def test_harness_trial_runner_detects_preservation_failure_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_trial_runner()

    def finish(_spec: Any, _prompt: str, workspace: Path, **_kwargs: Any) -> runner.RunResult:
        (workspace / "value.txt").write_text("after")
        (workspace / "NOTICE.txt").write_text("changed")
        return runner.RunResult(
            runner="fixture",
            command=(),
            executed=True,
            timed_out=True,
            duration_s=1.0,
        )

    monkeypatch.setattr(module.runner, "run", finish)
    selected = {
        **case(),
        "files": {"value.txt": "before", "NOTICE.txt": "preserve"},
        "preserve": ["NOTICE.txt"],
    }
    result = module.execute_trial(
        runner.RunnerSpec("fixture"), selected, tmp_path / "one", identity()
    )
    assert result["outcome"] == "timed_out"
    assert result["acceptance"]["outcome"] == "failed"
    assert result["acceptance"]["preserved_files_unchanged"] is False


def test_harness_trial_runner_setup_failure_prevents_agent_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_trial_runner()

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("agent dispatched after setup failure")

    monkeypatch.setattr(module.runner, "run", unexpected)
    selected = {**case(), "setup": [["{python}", "-c", "raise SystemExit(1)"]]}
    result = module.execute_trial(
        runner.RunnerSpec("fixture"), selected, tmp_path / "one", identity()
    )
    assert result["outcome"] == "infrastructure_error"
    assert "setup failed" in result["error"]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/absolute", ".env", ".git/config"])
def test_harness_trial_runner_refuses_unsafe_fixture_paths(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="unsafe fixture"):
        load_trial_runner().write_fixture(tmp_path / "workspace", {name: "unwanted"})
    assert not (tmp_path / "workspace").exists()


def test_harness_trial_runner_does_not_reuse_a_workspace(tmp_path: Path) -> None:
    module = load_trial_runner()
    module.write_fixture(tmp_path / "workspace", {"value.txt": "preserve"})
    with pytest.raises(FileExistsError):
        module.write_fixture(tmp_path / "workspace", {"value.txt": "replace"})
    assert (tmp_path / "workspace/value.txt").read_text() == "preserve"


def test_harness_trial_runner_reports_broken_grader_as_infrastructure(tmp_path: Path) -> None:
    result = load_trial_runner().grade(tmp_path, [sys.executable, "-c", "raise SystemExit(2)"], 10)
    assert result["outcome"] == "infrastructure_error"


def test_harness_trial_runner_grades_source_despite_stale_bytecode(tmp_path: Path) -> None:
    source = tmp_path / "subject.py"
    source.write_text("value = 1\n")
    original = source.stat()
    py_compile.compile(str(source), invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    source.write_text("value = 2\n")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    report = load_trial_runner().grade(
        tmp_path, ["{python}", "-c", "import subject; assert subject.value == 2"], 10
    )
    assert report["outcome"] == "passed"


def test_harness_trial_runner_preserves_interrupted_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise KeyboardInterrupt

    module = load_trial_runner()
    monkeypatch.setattr(module.runner, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        module.execute_trial(runner.RunnerSpec("fixture"), case(), tmp_path / "one", identity())
    result = json.loads((tmp_path / "one/trial.json").read_text())
    assert result["outcome"] == "interrupted"
    assert result["duration_s"] >= 0


def test_harness_trial_runner_schedule_keeps_paired_arms_adjacent() -> None:
    module = load_trial_runner()
    schedule = module.schedule(["small", "repair"], ["before", "after"], 3, seed=17)
    assert schedule == module.schedule(["small", "repair"], ["before", "after"], 3, seed=17)
    assert len(schedule) == 12
    assert len(set(schedule)) == 12
    for first, second in zip(schedule[::2], schedule[1::2], strict=True):
        assert first[:2] == second[:2]
        assert {first[2], second[2]} == {"before", "after"}


def test_harness_trial_runner_schedule_refuses_zero_repetitions() -> None:
    with pytest.raises(ValueError, match="positive"):
        load_trial_runner().schedule(["small"], ["before", "after"], 0, seed=17)


@pytest.mark.parametrize(
    "case_id", ["small-bug", "cross-file", "docs-only", "repair", "parallel-independent", "overlap"]
)
def test_harness_trial_fixtures_install_before_agent_dispatch(tmp_path: Path, case_id: str) -> None:
    source = Path(__file__).resolve().parent / "fixtures/harness-trials.json"
    selected = next(row for row in json.loads(source.read_text())["cases"] if row["id"] == case_id)
    module = load_trial_runner()
    workspace = tmp_path / "workspace"
    module.write_fixture(workspace, selected["files"])
    report: dict[str, Any] = {}
    assert module.prepare_case(workspace, selected, report, 120), report


@pytest.mark.parametrize(
    "case_id", ["small-bug", "cross-file", "docs-only", "repair", "parallel-independent", "overlap"]
)
def test_harness_trial_fixtures_distinguish_defect_from_correct_solution(
    tmp_path: Path, case_id: str
) -> None:
    source = Path(__file__).resolve().parent / "fixtures/harness-trials.json"
    selected = next(row for row in json.loads(source.read_text())["cases"] if row["id"] == case_id)
    module = load_trial_runner()
    workspace = tmp_path / "workspace"
    module.write_fixture(workspace, selected["files"])
    assert module.grade(workspace, selected["check"], 10)["outcome"] == "failed"
    solutions = {
        "small-bug": {
            "limits.py": (
                "def clamp(value, low, high):\n    if low > high:\n"
                "        raise ValueError('bounds')\n    return max(low, min(high, value))\n"
            ),
        },
        "cross-file": {
            "ports.py": (
                "def parse_port(text):\n    value = int(text)\n    if not 1 <= value <= 65535:\n"
                "        raise ValueError('port must be 1 through 65535')\n    return value\n"
            ),
            "port_cli.py": (
                "import sys\nfrom ports import parse_port\ntry:\n"
                "    print(parse_port(sys.argv[1]))\n"
                "except ValueError as error:\n    print(str(error), file=sys.stderr)\n"
                "    raise SystemExit(2)\n"
            ),
        },
        "docs-only": {"README.md": "# Tool\n\nDefault timeout: 30 seconds. Set --timeout.\n"},
        "repair": {"slug.py": "def slug(text):\n    return '-'.join(text.lower().split())\n"},
        "parallel-independent": {
            "trial_squares.py": "def square(value):\n    return value * value\n",
            "words.py": (
                "def clean_words(values):\n"
                "    return [value.strip() for value in values if value.strip()]\n"
            ),
        },
        "overlap": {
            "selection.py": (
                "def select_items(rows, limit):\n    if limit < 0:\n"
                "        raise ValueError('negative limit')\n"
                "    return [row for row in rows if row['active']][:limit]\n"
            ),
        },
    }
    for name, content in solutions[case_id].items():
        (workspace / name).write_text(content)
    assert module.grade(workspace, selected["check"], 10)["outcome"] == "passed"


def test_harness_catalog_fixture_accepts_local_source_projection(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parent / "fixtures/harness-trials.json"
    selected = next(
        row for row in json.loads(source.read_text())["cases"] if row["id"] == "catalog-projection"
    )
    module = load_trial_runner()
    workspace = tmp_path / "workspace"
    module.write_fixture(workspace, selected["files"])
    for command in selected["setup"]:
        result = module.grade(workspace, command, 120)
        assert result["outcome"] == "passed", result
    assert module.grade(workspace, selected["check"], 30)["outcome"] == "failed"
    fragment = workspace / ".basicly-local/fragments/trial-safety.fragment.yaml"
    fragment.parent.mkdir(parents=True, exist_ok=True)
    fragment.write_text(
        "schema_version: 1\nid: trial-safety\ndescription: Preserve consumer trial inputs.\n"
        "category: testing\npriority: high\nphase: build\napplies_to: [all]\n"
        "status: active\ntitle: Trial Safety\nbody: |\n  - Preserve trial-owned input files.\n"
    )
    built = module.grade(workspace, ["{python}", "-m", "basicly.cli", "build"], 120)
    assert built["outcome"] == "passed", built
    result = module.grade(workspace, selected["check"], 30)
    assert result["outcome"] == "passed", result


@pytest.mark.parametrize("damage", [False, True])
def test_harness_trial_preserves_required_files(tmp_path: Path, damage: bool) -> None:
    selected = case()
    selected["files"]["managed/keep.txt"] = "original"
    selected["preserve"] = ["managed"]
    program = "from pathlib import Path; Path('value.txt').write_text('after')"
    if damage:
        program += "; Path('managed/keep.txt').write_text('changed')"
    spec = runner.RunnerSpec(
        "fixture", command=(sys.executable, "-c", program, runner.PROMPT_PLACEHOLDER)
    )
    result = load_trial_runner().execute_trial(spec, selected, tmp_path / "one", identity())
    assert result["outcome"] == ("failed" if damage else "passed")
