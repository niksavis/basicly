from __future__ import annotations

import json
import sys
import threading
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import checkout, run_record, runner, task_context


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    root = tmp_path / "consumer"
    root.mkdir()
    home = tmp_path / "host"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    checkout.run(["git", "init", "-b", "main"], cwd=root)
    (root / "app.txt").write_text("initial\n")
    checkout.run(["git", "add", "app.txt"], cwd=root)
    checkout.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        cwd=root,
    )
    return root, home


def _spec(family="codex"):
    native = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == family)
    return replace(
        native, command=(sys.executable, "exec", "{prompt}"), model="pinned-model", tier=None
    )


def _record_host(monkeypatch, *, returncode=0, observed=True, effect=None):
    seeds = []

    def run(spec, _prompt, cwd, **kwargs):
        seed = kwargs.get("seed")
        seeds.append(seed)
        if effect is not None:
            effect(cwd)
        session = seed.session_id if seed is not None else str(uuid.uuid4())
        return runner.RunResult(
            spec.name, (), True, returncode=returncode, session_id=session if observed else None
        )

    monkeypatch.setattr(runner, "run", run)
    return seeds


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_build_and_repair_keep_their_explicit_native_context(consumer, monkeypatch, family):
    root, _ = consumer
    seeds = _record_host(monkeypatch)
    first = task_context.run_task(root, "task", _spec(family), "build", root)
    second = task_context.run_task(root, "task", _spec(family), "repair the failed test", root)
    assert not seeds[0].exists and seeds[1].exists
    assert first.session_id == second.session_id == seeds[1].session_id
    assert checkout.run(["git", "status", "--porcelain"], cwd=root).stdout == ""


@pytest.mark.parametrize(
    "change",
    [
        "configuration",
        "permissions",
        "revision",
        "dirty",
        "new-file",
        "environment",
        "model",
        "task",
        "worktree",
    ],
)
def test_changed_context_refreshes_instead_of_resuming(consumer, monkeypatch, tmp_path, change):
    root, _ = consumer
    spec = _spec()
    seeds = _record_host(monkeypatch)
    task_context.run_task(root, "task", spec, "build", root)
    issue, cwd = "task", root
    if change == "configuration":
        (root / "basicly.toml").write_text("[runner]\ndefault='codex'\n")
    elif change == "permissions":
        spec = replace(spec, sandbox="read-only", approval="never")
    elif change == "revision":
        checkout.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "next",
            ],
            cwd=root,
        )
    elif change == "dirty":
        (root / "app.txt").write_text("external change\n")
    elif change == "new-file":
        (root / "new.txt").write_text("external change\n")
    elif change == "environment":
        monkeypatch.setenv("BASICLY_CONTEXT_TEST", "changed")
    elif change == "model":
        spec = replace(spec, model="different-model")
    elif change == "task":
        issue = "other-task"
    else:
        cwd = tmp_path / "other-worktree"
        checkout.run(["git", "worktree", "add", "-b", "other", str(cwd)], cwd=root)
    task_context.run_task(root, issue, spec, "repair", cwd)
    assert len(seeds) == 2 and not seeds[1].exists
    assert seeds[0].session_id != seeds[1].session_id


def test_a_crashed_worker_with_observed_session_can_resume(consumer, monkeypatch):
    root, _ = consumer
    seeds = _record_host(
        monkeypatch,
        returncode=143,
        effect=lambda cwd: (cwd / "checkpoint.txt").write_text("in progress"),
    )
    task_context.run_task(root, "task", _spec(), "build", root)
    task_context.run_task(root, "task", _spec(), "continue", root)
    assert seeds[1].exists and seeds[1].session_id == seeds[0].session_id


def test_a_worker_without_native_session_evidence_cannot_seed_a_resume(consumer, monkeypatch):
    root, _ = consumer
    seeds = _record_host(monkeypatch, observed=False)
    for _ in range(2):
        task_context.run_task(root, "task", _spec(), "build", root)
    assert all(not seed.exists for seed in seeds)


def test_corrupt_cached_identity_cannot_choose_another_session(consumer, monkeypatch):
    root, _ = consumer
    seeds = _record_host(monkeypatch)
    task_context.run_task(root, "task", _spec(), "build", root)
    path = next((root / task_context.CONTEXTS).glob("*/session.json"))
    stored = json.loads(path.read_text())
    stored["identity"]["task"] = "other-task"
    path.write_text(json.dumps(stored))
    task_context.run_task(root, "task", _spec(), "repair", root)
    assert not seeds[1].exists


def test_unpinned_model_stays_fresh(consumer, monkeypatch):
    root, _ = consumer
    seeds = _record_host(monkeypatch)
    spec = replace(_spec(), model=None, tier=None)
    for _ in range(2):
        task_context.run_task(root, "task", spec, "build", root)
    assert seeds == [None, None]


@pytest.mark.parametrize("change", ["none", "model", "task"])
def test_concurrent_resume_of_same_context_is_refused(consumer, monkeypatch, change):
    root, _ = consumer
    entered, release = threading.Event(), threading.Event()
    completed = []

    def run(*_a, **_k):
        entered.set()
        assert release.wait(5)
        return runner.RunResult("codex", (), True, returncode=0)

    monkeypatch.setattr(runner, "run", run)

    def work():
        completed.append(task_context.run_task(root, "task", _spec(), "build", root))

    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(5)
        competing = replace(_spec(), model="other-model") if change == "model" else _spec()
        task = "other-task" if change == "task" else "task"
        with pytest.raises(RuntimeError, match="already running"):
            task_context.run_task(root, task, competing, "repair", root)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(completed) == 1 and completed[0].returncode == 0


def test_claude_usage_bookkeeping_keeps_context_but_mcp_changes_refresh_it(consumer, monkeypatch):
    root, home = consumer
    host = home / ".claude.json"
    host.write_text(json.dumps({"numStartups": 1, "mcpServers": {}}))
    seeds = _record_host(monkeypatch)
    task_context.run_task(root, "task", _spec("claude"), "build", root)
    host.write_text(json.dumps({"numStartups": 2, "mcpServers": {}}))
    task_context.run_task(root, "task", _spec("claude"), "repair", root)
    assert seeds[1].exists
    host.write_text(
        json.dumps({
            "numStartups": 2,
            "mcpServers": {"new": {"type": "http", "url": "https://example.invalid/mcp"}},
        })
    )
    task_context.run_task(root, "task", _spec("claude"), "repair", root)
    assert not seeds[2].exists


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_native_observed_session_is_captured_from_real_process(tmp_path, family):
    session = str(uuid.uuid4())
    event = (
        {"type": "thread.started", "thread_id": session}
        if family == "codex"
        else {"type": "system", "subtype": "init", "session_id": session}
    )
    usage = runner.CODEX_JSONL if family == "codex" else runner.CLAUDE_STREAM_JSON
    spec = runner.RunnerSpec(
        family,
        command=(sys.executable, "-c", f"print({json.dumps(event)!r})", "{prompt}"),
        usage_format=usage,
    )
    result = runner.run(spec, "inspect", tmp_path, capture_usage=True)
    assert result.returncode == 0 and result.session_id == session


@pytest.mark.parametrize("value", ["--last", "latest", "", "--resume=other", None])
def test_invalid_native_session_cannot_become_a_command_option(value):
    with pytest.raises(ValueError, match="explicit UUID"):
        runner.SessionSeed(value, exists=True)


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_conflicting_native_ids_are_refused(family):
    sessions = [str(uuid.uuid4()), str(uuid.uuid4())]
    events = [
        {"type": "thread.started", "thread_id": session}
        if family == "codex"
        else {"type": "system", "subtype": "init", "session_id": session}
        for session in sessions
    ]
    with pytest.raises(ValueError, match="conflicting"):
        runner.observed_session(_spec(family), "\n".join(map(json.dumps, events)))


@pytest.mark.parametrize("flag", ["--last", "--resume=unexpected", "--continue"])
def test_preconfigured_continuation_cannot_override_task_binding(consumer, monkeypatch, flag):
    root, _ = consumer
    seeds = _record_host(monkeypatch)
    spec = _spec()
    with pytest.raises(ValueError, match="must not contain"):
        task_context.run_task(
            root, "task", replace(spec, command=(*spec.command, flag)), "build", root
        )
    assert seeds == []


def test_native_host_cannot_substitute_a_different_resumed_context(consumer, monkeypatch):
    root, _ = consumer
    _record_host(monkeypatch)
    task_context.run_task(root, "task", _spec(), "build", root)
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: runner.RunResult(
            "codex", (), True, returncode=0, session_id=str(uuid.uuid4())
        ),
    )
    result = task_context.run_task(root, "task", _spec(), "repair", root)
    assert result.returncode == 1 and result.context_action == "session-mismatch"
    assert result.session_id is None
    assert list((root / task_context.CONTEXTS).glob("*/session.json")) == []


def test_claude_resumed_cost_records_only_the_new_dispatch_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "adapter_version", lambda *_a: "test")
    session = str(uuid.uuid4())
    spec = runner.RunnerSpec("claude", usage_format=runner.CLAUDE_JSON)
    for action, cost in [("fresh", 1.0), ("resumed", 1.4), ("resumed", 1.9)]:
        result = runner.RunResult(
            "claude",
            (),
            True,
            returncode=0,
            session_id=session,
            context_action=action,
            stdout=json.dumps({
                "type": "result",
                "total_cost_usd": cost,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }),
        )
        runner.record_dispatch(tmp_path, "task", spec, result)
    history = run_record.load_run_records(tmp_path)["task"]
    assert [item["session_cost"] for item in history] == [1.0, 1.4, 1.9]
    assert [item["cost"] for item in history] == pytest.approx([1.0, 0.4, 0.5])
    assert run_record.cost_rollup(history).cost == pytest.approx(1.9)


def test_claude_resume_without_previous_cost_evidence_stays_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "adapter_version", lambda *_a: "test")
    result = runner.RunResult(
        "claude",
        (),
        True,
        returncode=0,
        session_id=str(uuid.uuid4()),
        context_action="resumed",
        stdout=json.dumps({
            "type": "result",
            "total_cost_usd": 1.4,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }),
    )
    runner.record_dispatch(
        tmp_path, "task", runner.RunnerSpec("claude", usage_format=runner.CLAUDE_JSON), result
    )
    entry = run_record.load_run_records(tmp_path)["task"][0]
    assert entry["cost"] is None and entry["session_cost"] == 1.4


def test_interrupted_real_worker_resumes_its_checkpoint_in_same_context(consumer):
    root, _ = consumer
    program = """import json, pathlib, sys, time, uuid
arguments = sys.argv[1:]
resumed = "resume" in arguments
session = arguments[arguments.index("resume") + 1] if resumed else str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
checkpoint = pathlib.Path("checkpoint.txt")
if not resumed:
    checkpoint.write_text("unfinished task")
    time.sleep(60)
else:
    assert checkpoint.read_text() == "unfinished task"
    item = {"type": "agent_message", "text": "checkpoint resumed"}
    print(json.dumps({"type": "item.completed", "item": item}), flush=True)
"""
    spec = runner.RunnerSpec(
        "codex",
        command=(sys.executable, "-c", program, "exec", "{prompt}", "{model}"),
        model="pinned",
        usage_format=runner.CODEX_JSONL,
        resume_style=runner.CODEX_RESUME,
    )
    first = task_context.run_task(root, "task", spec, "build", root, timeout=1)
    assert first.timed_out and first.session_id is not None
    second = task_context.run_task(root, "task", spec, "continue", root, timeout=5)
    assert second.returncode == 0 and second.context_action == "resumed"
    assert second.session_id == first.session_id
    assert runner.result_text(spec, second.stdout) == "checkpoint resumed"


def test_codex_resume_preserves_readonly_and_no_approval_flags():
    native = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "codex")
    spec = replace(native, model="pinned", sandbox="read-only", approval="never")
    session = str(uuid.uuid4())
    argv = runner.format_command(spec, "repair", seed=runner.SessionSeed(session, True))
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("-a") + 1] == "never"
    position = argv.index("exec")
    assert argv[position + 1 : position + 3] == ["resume", session]
    assert "--last" not in argv


def test_explicit_settings_file_change_refreshes_context(consumer, monkeypatch):
    root, _ = consumer
    settings = root / "host-settings.json"
    settings.write_text('{"permissions":{"deny":["Write"]}}')
    seeds = _record_host(monkeypatch)
    spec = _spec("claude")
    spec = replace(spec, command=(*spec.command, "--settings", str(settings)))
    task_context.run_task(root, "task", spec, "build", root)
    settings.write_text('{"permissions":{"deny":["Write","Bash"]}}')
    task_context.run_task(root, "task", spec, "repair", root)
    assert not seeds[1].exists


def test_configuration_beneath_protected_environment_path_is_refused(consumer, monkeypatch):
    root, _ = consumer
    seeds = _record_host(monkeypatch)
    spec = _spec()
    spec = replace(
        spec, command=(*spec.command, "--settings", str(root / ".env.local" / "settings.json"))
    )
    with pytest.raises(ValueError, match="protected session configuration"):
        task_context.run_task(root, "task", spec, "build", root)
    assert seeds == []
