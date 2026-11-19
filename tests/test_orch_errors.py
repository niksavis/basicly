from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

SCRIPTS = Path(__file__).parent.parent / ".scripts"
WORKSPACE = "/work/space"
SINCE = "2026-01-02T00:00:00Z"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


orch_error_classes = _load("orch_error_classes")
orch_errors = _load("orch_errors")


def _use(key: str, tool: str, data: dict[str, Any], stamp: str, uuid: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": stamp,
        "message": {"content": [{"type": "tool_use", "id": key, "name": tool, "input": data}]},
    }


def _result(
    key: str, text: str, stamp: str | None, uuid: str, error: bool = True, **extra: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": "user",
        "uuid": uuid,
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": key, "content": text, "is_error": error}
            ]
        },
        **extra,
    }
    if stamp is not None:
        row["timestamp"] = stamp
    return row


def _write(root: Path, folder: str, name: str, rows: list[dict[str, Any]]) -> Path:
    path = root / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _run(root: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    code = orch_errors.main([
        "--root",
        str(root),
        "--workspace",
        WORKSPACE,
        "--since",
        SINCE,
        "--json",
        *extra,
    ])
    assert code == 0
    return json.loads(capsys.readouterr().out)


def _classes(summary: dict[str, Any], project: str) -> dict[str, int]:
    return {row["class"]: row["count"] for row in summary["projects"][project]["classes"]}


def _commit(key: str, stamp: str, uuid: str, path: str) -> list[dict[str, Any]]:
    return [
        _use(key, "Bash", {"command": f"git -C {path} commit -F msg.txt"}, stamp, uuid + "u"),
        _result(
            key,
            f"Exit code 1\nhook:  commit-msg\nsummary:\n🥊 conventional ({path})\n",
            stamp,
            uuid,
        ),
    ]


def test_reads_session_and_subagent_transcripts_under_the_workspace_slug_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [_use("toolu_a", "Read", {"file_path": "a.txt"}, "2026-01-02T01:00:00Z", "u1")]
    rows.append(_result("toolu_a", "File does not exist.", "2026-01-02T01:00:01Z", "r1"))
    _write(
        tmp_path,
        "-work-space-tools",
        "s1.jsonl",
        [{"type": "agent-name", "agentName": "tools"}, *rows],
    )
    sub = [_use("toolu_b", "Read", {"file_path": "b.txt"}, "2026-01-02T02:00:00Z", "u2")]
    sub.append(_result("toolu_b", "File does not exist.", "2026-01-02T02:00:01Z", "r2"))
    _write(tmp_path, "-work-space-tools", "s1/subagents/agent-x.jsonl", sub)
    other = [_use("toolu_c", "Read", {"file_path": "c.txt"}, "2026-01-02T03:00:00Z", "u3")]
    other.append(_result("toolu_c", "File does not exist.", "2026-01-02T03:00:01Z", "r3"))
    _write(tmp_path, "-elsewhere", "s2.jsonl", other)
    summary = _run(tmp_path, capsys)
    assert list(summary["projects"]) == ["tools"]
    assert summary["projects"]["tools"]["sessions"] == {"tools": 2}


def test_keeps_each_result_by_its_own_timestamp_and_counts_undated_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        _use("toolu_old", "Edit", {"file_path": "a"}, "2026-01-01T23:00:00Z", "u1"),
        _result("toolu_old", "String to replace not found in file.", "2026-01-01T23:00:01Z", "r1"),
        _use("toolu_new", "Edit", {"file_path": "a"}, "2026-01-02T09:00:00Z", "u2"),
        _result("toolu_new", "String to replace not found in file.", "2026-01-02T09:00:01Z", "r2"),
        _use("toolu_none", "Edit", {"file_path": "a"}, "2026-01-02T09:00:00Z", "u3"),
        _result("toolu_none", "String to replace not found in file.", None, "r3"),
    ]
    _write(tmp_path, "-work-space-tools", "s1.jsonl", rows)
    summary = _run(tmp_path, capsys)
    assert summary["errors"] == 1
    assert summary["undated"] == 1
    assert _classes(summary, "tools") == {"Edit|edit-no-match": 1}


def test_dedupe_counts_one_result_per_tool_use_id_across_copies_and_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = _commit("toolu_a", "2026-01-02T01:00:00Z", "r1", "lane")
    copy = _result("toolu_a", "Exit code 1", None, "r1copy")
    _write(tmp_path, "-work-space-tools", "s1.jsonl", [*rows, copy])
    _write(tmp_path, "-work-space-tools", "s2.jsonl", rows)
    summary = _run(tmp_path, capsys)
    assert summary["errors"] == 1
    assert summary["results"] == 1


def test_join_attaches_the_input_and_folds_a_script_throw_into_its_inner_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stamp = "2026-01-02T05:00:00Z"
    rows = _commit("repl_inner", stamp, "r1", "lane")
    rows.append({**_use("repl-error-1", "REPL", {}, stamp, "u2"), "parentUuid": "r1"})
    rows.append(_result("repl-error-1", "Error: commit refused", stamp, "r2"))
    _write(tmp_path, "-work-space-tools", "s1.jsonl", rows)
    summary = _run(tmp_path, capsys)
    assert summary["errors"] == 1
    assert summary["script_throws"] == 1
    row = summary["projects"]["tools"]["classes"][0]
    assert row["class"] == "Bash|git-hook|commit-msg|conventional"
    assert row["sample"]["input"] == "git -C lane commit -F msg.txt"
    assert row["sample"]["script_error"] == ["Error: commit refused"]


def test_join_keeps_a_script_error_that_shares_a_timestamp_with_an_expected_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stamp = "2026-01-02T05:00:00Z"
    rows = [
        _use("repl_rg", "Bash", {"command": "rg -n gone src"}, stamp, "u1"),
        _result("repl_rg", "Exit code 1", stamp, "r1"),
        _use("repl-error-2", "REPL", {}, stamp, "u2"),
        _result("repl-error-2", "TypeError: out.split is not a function", stamp, "r2"),
    ]
    _write(tmp_path, "-work-space-tools", "s1.jsonl", rows)
    summary = _run(tmp_path, capsys)
    assert summary["script_throws"] == 0
    assert summary["errors"] == 1
    assert summary["expected"] == 1
    assert _classes(summary, "tools") == {"Bash|expected-exit|rg": 1, "REPL|js|TypeError": 1}


def test_classes_stay_stable_when_errors_differ_only_in_a_path_or_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fatal = "Exit code 128\nfatal: path 'docs/a.md' exists on disk, but not in 'main'"
    rows = [
        *_commit("toolu_a", "2026-01-02T01:00:00Z", "r1", "first/lane"),
        *_commit("toolu_b", "2026-01-02T02:00:00Z", "r2", "second/other-lane"),
        _use(
            "toolu_c",
            "Bash",
            {"command": "git rev-parse main:docs/a.md"},
            "2026-01-02T03:00:00Z",
            "u3",
        ),
        _result("toolu_c", fatal, "2026-01-02T03:00:01Z", "r3"),
        _use(
            "toolu_d",
            "Bash",
            {"command": "git rev-parse dev:src/b/c.py"},
            "2026-01-02T04:00:00Z",
            "u4",
        ),
        _result(
            "toolu_d",
            "Exit code 128\nfatal: path 'src/b/c.py' does not exist in 'dev'",
            "2026-01-02T04:00:01Z",
            "r4",
        ),
        _use(
            "toolu_p",
            "Bash",
            {"command": "git merge-base --is-ancestor lane main"},
            "2026-01-02T04:30:00Z",
            "u7",
        ),
        _result("toolu_p", "Exit code 1", "2026-01-02T04:30:01Z", "r7"),
        _use("toolu_g", "Bash", {"command": "uv run ruff check"}, "2026-01-02T04:40:00Z", "u8"),
        _result(
            "toolu_g",
            "Exit code 1\nE501 'classifier gave no verdict'",
            "2026-01-02T04:40:01Z",
            "r8",
        ),
        _use("toolu_e", "Bash", {"command": "rg -n missing src"}, "2026-01-02T05:00:00Z", "u5"),
        _result("toolu_e", "Exit code 1", "2026-01-02T05:00:01Z", "r5"),
        _use("toolu_f", "Bash", {"command": "python3 tool.py run"}, "2026-01-02T06:00:00Z", "u6"),
        _result(
            "toolu_f",
            "Exit code 3\nTraceback",
            "2026-01-02T06:00:01Z",
            "r6",
            permissionDecision={"decision": "accept", "reasonType": "classifier"},
        ),
    ]
    _write(tmp_path, "-work-space-tools", "s1.jsonl", rows)
    summary = _run(tmp_path, capsys)
    assert _classes(summary, "tools") == {
        "Bash|git-hook|commit-msg|conventional": 2,
        "Bash|probe|git rev-parse": 2,
        "Bash|probe|git merge-base": 1,
        "Bash|expected-exit|rg": 1,
        "Bash|exit|python3 tool.py run|3": 1,
        "Bash|exit|uv run ruff|1": 1,
    }


def test_report_prints_per_project_count_and_one_sample_per_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (
        "Permission for this action was denied by the Claude Code auto mode classifier."
        " Reason: [Destructive or Unwanted Actions]."
    )
    decision = {"reasonType": "classifier", "classifierCategory": "destructive_or_unwanted_actions"}
    refusal = _result("toolu_r", text, "2026-01-02T06:00:01Z", "r9", permissionDecision=decision)
    script = {"code": 'await tools.SendMessage({to: "peer"});\nawait $`rm -r -f scratch`'}
    _write(
        tmp_path,
        "-work-space-tools",
        "s1.jsonl",
        [_use("toolu_r", "REPL", script, "2026-01-02T06:00:00Z", "u9"), refusal],
    )
    _write(
        tmp_path,
        "-work-space",
        "s2.jsonl",
        _commit("toolu_w", "2026-01-02T07:00:00Z", "r1", "lane"),
    )
    code = orch_errors.main(["--root", str(tmp_path), "--workspace", WORKSPACE, "--since", SINCE])
    out = capsys.readouterr().out
    assert code == 0
    assert "tools: 1 errors in 1 results (tools 1)" in out
    assert "space: 1 errors in 1 results (space 1)" in out
    assert "REPL|classifier|destructive_or_unwanted_actions" in out
    assert "input: await $`rm -r -f scratch`" in out


def test_new_names_classes_unseen_in_the_last_run_and_no_save_keeps_the_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state" / "orch-errors.json"
    projects = tmp_path / "projects"
    _write(
        projects,
        "-work-space-tools",
        "s1.jsonl",
        _commit("toolu_a", "2026-01-02T01:00:00Z", "r1", "lane"),
    )
    first = _run(projects, capsys, "--state", str(state))
    assert first["new_classes"] == 1
    again = _run(projects, capsys, "--state", str(state))
    assert again["new_classes"] == 0
    assert again["projects"]["tools"]["classes"][0]["new_errors"] == 0
    read_first = "File has not been read yet. Read it first before writing to it."
    rows = [
        *_commit("toolu_a", "2026-01-02T01:00:00Z", "r1", "lane"),
        _use("toolu_b", "Edit", {"file_path": "a"}, "2026-01-02T02:00:00Z", "u2"),
        _result("toolu_b", read_first, "2026-01-02T02:00:01Z", "r2"),
    ]
    _write(projects, "-work-space-tools", "s1.jsonl", rows)
    before = state.read_text(encoding="utf-8")
    peek = _run(projects, capsys, "--state", str(state), "--no-save")
    assert peek["new_classes"] == 1
    assert state.read_text(encoding="utf-8") == before


def test_new_refuses_a_state_file_of_another_shape_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state.json"
    state.write_text("[]", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    code = orch_errors.main([
        "--root",
        str(tmp_path / "projects"),
        "--workspace",
        WORKSPACE,
        "--state",
        str(state),
    ])
    assert code == 2
    assert "is not an orch errors state" in capsys.readouterr().err
