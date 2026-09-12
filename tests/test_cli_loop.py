from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from basicly import cli, decompose, loop, loop_state, supervise, working_set
from basicly.config import (
    CHECKPOINTS,
    LOCAL_CONFIG_FILE,
    RunnerSpec,
    WorktreeConfig,
    load_sizing_config,
)
from basicly.decompose import ChildSpec
from basicly.loop import AdvanceResult, Inputs
from basicly.loop_state import NodeState, RankedNode, WorktreeBinding
from basicly.policy import GateStatus, Grant, SpendStatus
from basicly.runner import HEADLESS

if TYPE_CHECKING:
    import pytest


def _node_state(**overrides: object) -> NodeState:
    defaults: dict[str, object] = {
        "issue_id": "basicly-x",
        "status": "in_progress",
        "issue_type": "task",
        "phase": "build",
        "worktree": WorktreeBinding(name="basicly-x", branch="harness/basicly-x"),
        "gates": GateStatus(False, ("lint",), ("verify",), (), ()),
        "checkpoints": ("classify",),
        "rework": {"verify": 1},
        "has_children": False,
    }
    defaults.update(overrides)
    return NodeState(**defaults)  # type: ignore[arg-type]


def test_loop_advance_maps_flags_to_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_advance(_repo_root, issue_id, *, _config=None, inputs=None):
        captured["issue_id"] = issue_id
        captured["inputs"] = inputs
        return AdvanceResult(issue_id, "intake", "classify", "classified", "recorded task")

    monkeypatch.setattr(loop, "advance", fake_advance)

    assert cli.main(["loop", "advance", "basicly-x", "--work-type", "task", "--mode", "fast"]) == 0
    assert captured["issue_id"] == "basicly-x"
    inputs = captured["inputs"]
    assert isinstance(inputs, Inputs)
    assert inputs.work_type == "task"
    assert inputs.verify_mode == "fast"
    assert inputs.children is None


def test_loop_advance_loads_child_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = (ChildSpec(title="a", acceptance=("x",), scope=("src/a.py",)),)
    captured: dict[str, object] = {}

    monkeypatch.setattr(decompose, "load_plan_file", lambda _path: plan)

    def fake_advance(_repo_root, issue_id, *, _config=None, inputs=None):
        captured["inputs"] = inputs
        return AdvanceResult(issue_id, "classify", "decompose", "decomposed")

    monkeypatch.setattr(loop, "advance", fake_advance)

    assert cli.main(["loop", "advance", "basicly-x", "--children", "plan.toml"]) == 0
    assert captured["inputs"].children == plan  # type: ignore[union-attr]


def test_loop_advance_exits_nonzero_when_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = AdvanceResult(
        "basicly-x", "intake", "intake", "blocked", "needs a work type", needs_input="work_type"
    )
    monkeypatch.setattr(loop, "advance", lambda *_a, **_k: blocked)

    assert cli.main(["loop", "advance", "basicly-x"]) == 1
    out = capsys.readouterr().out
    assert "[blocked]" in out
    assert "needs input: work_type" in out


def _ceremony(monkeypatch: pytest.MonkeyPatch, result: loop.CeremonyResult) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake_ceremony(_repo: object, issue: str, **kwargs: object) -> loop.CeremonyResult:
        seen.update(kwargs, issue=issue)
        return result

    monkeypatch.setattr(loop, "run_ceremony", fake_ceremony)
    return seen


def test_loop_run_prints_each_step_and_the_approvals_between_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ceremony(
        monkeypatch,
        loop.CeremonyResult((
            AdvanceResult("basicly-x", "intake", "intake", "blocked", checkpoint="classify"),
            loop.CheckpointApproval("classify", "delegated under L2 grant"),
            AdvanceResult("basicly-x", "classify", "classify", "blocked", "awaiting the agent"),
        )),
    )

    assert cli.main(["loop", "run", "basicly-x"]) == 1
    out = capsys.readouterr().out
    assert "checkpoint classify: APPROVED (basicly-x) - delegated under L2 grant" in out
    assert out.index("intake -> intake") < out.index("checkpoint classify: APPROVED")
    assert "[blocked]" in out


def test_loop_run_exits_zero_when_the_track_shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _ceremony(
        monkeypatch,
        loop.CeremonyResult((AdvanceResult("basicly-x", "ship", "done", "tore-down"),)),
    )

    assert cli.main(["loop", "run", "basicly-x"]) == 0


def test_loop_run_challenge_reprints_the_whole_command_to_rerun(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "intake", "intake", "blocked", checkpoint="classify"),),
            challenge=("classify", "c0ffee"),
        ),
    )

    exit_code = cli.main(["loop", "run", "basicly-x", "--work-type", "task", "--mode", "fast"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "checkpoint classify: CONFIRMATION REQUIRED (basicly-x)" in err
    assert "basicly loop run basicly-x --work-type task --mode fast --confirm c0ffee" in err
    assert "may run the command themselves" in err


def test_loop_run_prints_why_a_grant_declined_the_challenge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "verify", "verify", "blocked", checkpoint="ship"),),
            challenge=("ship", "c0ffee"),
            challenge_reason=(
                "the active L3 grant covers ship but declined it: "
                "rework escalation on basicly-sib (gate verify: 2/2)"
            ),
        ),
    )

    assert cli.main(["loop", "run", "basicly-x", "--root", "basicly-epic"]) == 1

    err = capsys.readouterr().err
    assert "the active L3 grant covers ship but declined it" in err
    assert "rework escalation on basicly-sib (gate verify: 2/2)" in err
    assert "--confirm c0ffee" in err


def test_loop_run_challenge_without_a_grant_prints_no_reason_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "verify", "verify", "blocked", checkpoint="ship"),),
            challenge=("ship", "c0ffee"),
        ),
    )

    assert cli.main(["loop", "run", "basicly-x"]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "checkpoint ship: CONFIRMATION REQUIRED (basicly-x)"
    assert lines[1].startswith("  The merge to the base branch has ALREADY happened")


def test_loop_run_reports_a_refusal_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "verify", "verify", "blocked", checkpoint="ship"),),
            refused=("ship", "invalid or expired confirm code"),
        ),
    )

    assert cli.main(["loop", "run", "basicly-x", "--confirm", "nope"]) == 1
    assert "checkpoint ship: REFUSED (basicly-x) - invalid or expired" in capsys.readouterr().err


def test_loop_run_passes_the_confirm_code_and_grant_root_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _ceremony(monkeypatch, loop.CeremonyResult())

    cli.main(["loop", "run", "basicly-x", "--confirm", "ship=c0ffee", "--root", "basicly-epic"])
    assert seen["confirms"] == {"ship": "c0ffee"}
    assert seen["grant_root"] == "basicly-epic"


def test_loop_run_bare_confirm_code_is_offered_to_every_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _ceremony(monkeypatch, loop.CeremonyResult())

    cli.main(["loop", "run", "basicly-x", "--confirm", "c0ffee"])
    assert seen["confirms"] == dict.fromkeys(CHECKPOINTS, "c0ffee")


def _loop_subparser(name: str) -> argparse.ArgumentParser:
    parser = cli._build_parser()
    for step in ("loop", name):
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        parser = action.choices[step]
    return parser


def _await_text(path: Path, text: str, *, deadline: float = 30.0) -> str:

    end = time.monotonic() + deadline
    while time.monotonic() < end:
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        if text in content:
            return content
        time.sleep(0.05)
    raise AssertionError(f"{path} never held {text!r} within {deadline}s")


def test_the_detached_run_argv_carries_every_flag_the_launch_was_given() -> None:
    args = argparse.Namespace(
        issue="basicly-hnnmk9",
        work_type="task",
        children="plan.toml",
        mode="quick",
        root="basicly-epic",
        runner="claude",
        autonomy="L3",
        tier="high",
    )

    argv = cli._detach_argv(args, "run")

    assert argv[0] == sys.executable
    assert " ".join(argv[1:]) == (
        "-m basicly.cli loop run basicly-hnnmk9 --work-type task --children plan.toml "
        "--mode quick --root basicly-epic --runner claude --autonomy L3 --tier high"
    )
    assert "--detach" not in argv, "the child must not detach again"


def test_a_detached_run_forwards_the_mode_default_and_no_omitted_flag() -> None:

    args = argparse.Namespace(
        issue="i",
        work_type=None,
        children=None,
        mode="full",
        root=None,
        runner=None,
        autonomy=None,
        tier=None,
    )

    assert cli._detach_argv(args, "run")[-5:] == ["loop", "run", "i", "--mode", "full"]


def test_the_run_forwarding_table_is_every_run_flag_but_detach_and_confirm() -> None:

    declared = {
        action.dest: action.option_strings[0]
        for action in _loop_subparser("run")._actions
        if action.option_strings and action.dest not in {"help", "detach", "confirm"}
    }

    assert declared == cli.RUN_FORWARDED_FLAGS


def test_loop_run_detach_prints_the_pid_and_log_and_drives_no_ceremony(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the launcher drove the ceremony the child was spawned for")

    monkeypatch.setattr(loop, "run_ceremony", never)
    spawned: dict[str, object] = {}

    def fake_spawn(argv: list[str], log: Path, *, cwd: Path) -> int:
        spawned.update(argv=argv, log=log, cwd=cwd)
        return 4242

    monkeypatch.setattr(cli, "_spawn_detached", fake_spawn)

    code = cli.main(["loop", "run", "basicly-x", "--detach", "--tier", "high"])

    out = capsys.readouterr().out
    log, argv = spawned["log"], spawned["argv"]
    assert code == 0
    assert isinstance(log, Path)
    assert isinstance(argv, list)
    assert log.parent == tmp_path / cli.DETACHED_LOGS_DIR
    assert "detached: pid 4242" in out
    assert str(log) in out
    assert "watch:    basicly loop status basicly-x" in out
    assert argv[-7:] == [
        "loop",
        "run",
        "basicly-x",
        "--mode",
        "full",
        "--tier",
        "high",
    ]
    assert spawned["cwd"] == tmp_path


def test_loop_run_refuses_detach_beside_a_confirm_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.chdir(tmp_path)

    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("the refused launch spawned a child anyway")

    monkeypatch.setattr(cli, "_spawn_detached", never)
    monkeypatch.setattr(loop, "run_ceremony", never)

    code = cli.main(["loop", "run", "basicly-x", "--detach", "--confirm", "c0ffee"])

    err = capsys.readouterr().err
    assert code == 1
    assert "run: refused - --confirm cannot be combined with --detach" in err


def test_a_detached_launch_appends_to_its_log_instead_of_truncating_it(tmp_path: Path) -> None:

    log = tmp_path / "detached.log"
    log.write_text("first launch\n", encoding="utf-8")

    cli._spawn_detached([sys.executable, "-c", "print('second launch')"], log, cwd=tmp_path)

    content = _await_text(log, "second launch")
    assert content.splitlines() == ["first launch", "second launch"]


def test_loop_status_prints_reconstructed_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(loop_state, "read_node_state", lambda *_a, **_k: _node_state())
    monkeypatch.setattr(
        loop_state,
        "ready_ranked",
        lambda *_a, **_k: (RankedNode(rank=1, score=50, issue_id="basicly-y", title="t"),),
    )
    monkeypatch.setattr(loop_state, "blocked_ids", lambda *_a, **_k: ("basicly-z",))

    assert cli.main(["loop", "status", "basicly-x"]) == 0
    out = capsys.readouterr().out
    assert "phase:       build" in out
    assert "basicly-x on harness/basicly-x" in out
    assert "advance BLOCKED" in out
    assert "failed:    verify" in out
    assert "checkpoints: classify" in out
    assert "verify=1" in out
    assert "basicly-y" in out
    assert "basicly-z" in out


@dataclass(frozen=True)
class _Preflight:
    dirty: str = ""
    grant: Grant | None = None
    halted: bool = False
    metered: str | None = None
    unmetered: tuple[str, ...] = ()
    lanes: tuple[object, ...] = ()
    children: tuple[tuple[str, str], ...] = (("c.1", "open"),)
    admissions: dict[str, object] = field(default_factory=dict)
    phase: str = "decompose"
    checkpoints: tuple[str, ...] = CHECKPOINTS
    cap: int = 5
    calibration: object | None = None
    append_only: tuple[str, ...] = ()
    scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    generated: dict[str, tuple[str, ...]] = field(default_factory=dict)
    covered: tuple[str, ...] = ("epic", "c.1", "c.2")


def _calibration(**overrides) -> decompose.CalibrationStatus:
    fields = {
        "model": "claude-opus-5",
        "min_samples": 10,
        "samples": {"bug": 7, "task": 2},
        "build_factor_sources": {
            "bug": decompose.BUILD_FACTOR_SEED,
            "task": decompose.BUILD_FACTOR_SEED,
        },
    }
    fields.update(overrides)
    return decompose.CalibrationStatus(**fields)


def _preflight_fixture(monkeypatch: pytest.MonkeyPatch, pinned: _Preflight) -> None:
    dirty, grant, halted, metered, lanes, unmetered = (
        pinned.dirty,
        pinned.grant,
        pinned.halted,
        pinned.metered,
        pinned.lanes,
        pinned.unmetered,
    )
    root = Path()
    monkeypatch.setattr(cli, "_repo_root", lambda: root)
    monkeypatch.setattr(
        cli.worktree,
        "git",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, 0, dirty if "status" in argv else "0", ""
        ),
    )
    monkeypatch.setattr(cli.worktree, "list_sessions", lambda _r: [])
    children = pinned.children
    monkeypatch.setattr(
        cli.supervise,
        "derive_session",
        lambda _r, root, *, lane_label=None: supervise.SessionState(
            root, "open", children, (), lane_label=lane_label
        ),
    )
    monkeypatch.setattr(
        cli.runner, "select_runner", lambda *_a, **_k: RunnerSpec("claude", HEADLESS)
    )
    monkeypatch.setattr(
        cli.policy,
        "spend_status",
        lambda *_a, **_k: SpendStatus(
            grant=grant,
            spent_tokens=0,
            halted=halted,
            unmetered_dispatches=len(unmetered),
            unmetered_labels=unmetered,
        ),
    )
    monkeypatch.setattr(cli.supervise, "metered_without_a_budget", lambda *_a: metered)
    monkeypatch.setattr(cli.supervise, "ready_lanes", lambda *_a, **_k: lanes)
    cap, generated = pinned.cap, pinned.generated
    monkeypatch.setattr(
        cli,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(
            base_branch=None, concurrency=cap, regenerate_commands=generated
        ),
    )
    monkeypatch.setattr(cli.policy, "session_issue_ids", lambda *_a: pinned.covered)
    monkeypatch.setattr(cli.decompose, "unsized_lane_tokens", lambda *_a: (1_000, "measured"))
    calibration = pinned.calibration or _calibration()
    monkeypatch.setattr(cli.decompose, "calibration_status", lambda *_a: calibration)
    monkeypatch.setattr(
        cli.working_set,
        "admit_working_set",
        lambda _r, issue_id, _s: pinned.admissions.get(
            issue_id, working_set.WorkingSetAdmission(issue_id, None, None, refused=False)
        ),
    )
    monkeypatch.setattr(cli.decompose, "append_only_paths", lambda *_a: pinned.append_only)
    scopes = pinned.scopes
    monkeypatch.setattr(
        supervise.merge,
        "declared_scopes",
        lambda _r, beads: {b: scopes[b] for b in beads if b in scopes},
    )
    monkeypatch.setattr(
        cli.loop_state,
        "read_node_state",
        lambda _r, issue_id, *_a: _node_state(
            issue_id=issue_id,
            issue_type="epic",
            phase=pinned.phase,
            worktree=None,
            checkpoints=pinned.checkpoints,
            has_children=bool(children),
        ),
    )


def test_the_lane_selector_is_on_every_command_that_reads_a_session() -> None:

    parser = cli._build_parser()
    for command in ("supervise", "preflight", "session", "stop"):
        reason = ["--reason", "the grant is nearly spent"] if command == "stop" else []
        args = parser.parse_args([
            "loop",
            command,
            "basicly-x",
            "--label",
            "release-v0.7.0",
            *reason,
        ])
        assert args.label == "release-v0.7.0", command
    assert parser.parse_args(["loop", "supervise", "basicly-x"]).label is None


def _preflight_args(**overrides: object) -> argparse.Namespace:

    return argparse.Namespace(**{"issue": "epic", "label": None} | overrides)


def test_preflight_is_ready_when_nothing_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT:   ready" in out


def test_preflight_refuses_an_unrecognised_config_name_before_anything_else(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:

    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    (tmp_path / LOCAL_CONFIG_FILE).write_text("[loop]\nconcurrency = 2\n", encoding="utf-8")

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 1
    assert "config:    INVALID" in out
    assert "'concurrency' is accepted in [worktree]" in out
    assert "VERDICT:   not ready" in out


def _sized(
    issue_id: str,
    total: int,
    *,
    refused: bool,
    violation: str = "",
    scope_tokens: int | None = None,
) -> object:
    estimate = decompose.CostEstimate(
        scope_tokens=total if scope_tokens is None else scope_tokens,
        overhead_tokens=0 if scope_tokens is None else total,
        build_factor=1.0,
    )
    sizing = decompose.DispatchSizing(task_class="task", estimate=estimate, source="dispatch")
    return working_set.WorkingSetAdmission(issue_id, sizing, violation or None, refused=refused)


def test_preflight_blocker_names_the_dispatch_that_could_not_be_metered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=110_000_000),
            halted=True,
            unmetered=("basicly-mcf2uh on claude-sonnet-5",),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    assert "could not be metered: basicly-mcf2uh on claude-sonnet-5" in capsys.readouterr().out


def test_preflight_sizes_each_candidate_when_none_is_dispatchable_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 95_379, refused=True, violation="above")},
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    band = load_sizing_config(Path())
    assert f"band:      {band.working_set_min}..{band.working_set_max}" in out
    assert "c.1" in out and "95379 tok" in out
    assert "REFUSED - too large, split it" in out


def test_preflight_distinguishes_an_admitted_candidate_from_a_refused_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 12_884, refused=False)},
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "12884 tok  in band" in out
    assert "REFUSED" not in out


def test_preflight_separates_an_under_floor_lane_from_one_inside_the_band(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 3_512, refused=False, violation="below")},
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "under the floor - dispatches, but merge it with a sibling" in out
    assert "3512 tok  in band" not in out


def test_preflight_leaves_a_deferred_candidate_out_of_the_band_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "deferred")),
            admissions={
                "c.1": _sized("c.1", 12_884, refused=False),
                "c.2": _sized("c.2", 95_379, refused=True, violation="above"),
            },
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "1 open child(ren)" in out
    assert "12884 tok  in band" in out
    assert "c.2" not in out
    assert "REFUSED" not in out


def test_preflight_flags_a_candidate_whose_scope_matched_no_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 2_693, refused=False, scope_tokens=0)},
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    assert "in band, but its scope matched no file" in capsys.readouterr().out


def test_preflight_names_a_candidate_the_estimator_cannot_size(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "c.1" in out
    assert "no scope the estimator can read" in out


def test_preflight_warns_that_a_pass_will_contend_on_an_undeclared_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open"), ("c.3", "open")),
            append_only=("CHANGELOG.md",),
            scopes={
                "c.1": ("src/basicly/schema.py",),
                "c.2": ("src/basicly/config.py",),
                "c.3": ("src/basicly/usage.py",),
            },
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "contend:   append-only: `CHANGELOG.md`" in out
    assert "3 lane(s) will each append to `CHANGELOG.md` and none declares it: c.1, c.2, c.3" in out
    assert code == 0
    assert "VERDICT:   ready" in out


def test_preflight_says_the_contention_check_is_inert_when_nothing_is_declared(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open")),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    assert "contend:   no append-only path declared" in capsys.readouterr().out


def test_preflight_reports_the_artifacts_a_landing_rebuilds_instead_of_bouncing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open")),
            generated={
                ".basicly/generated-manifest.json": ("basicly", "build"),
                "docs/architecture/status.md": ("docs_claims.py", "--fix"),
            },
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "regen:     generated: a landing conflict confined to these" in out
    assert "spending no rework" in out
    assert "`.basicly/generated-manifest.json` <- `basicly build`" in out
    assert "`docs/architecture/status.md` <- `docs_claims.py --fix`" in out


def test_preflight_says_the_rebuild_check_is_inert_when_nothing_is_declared(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open")),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    assert "regen:     no generated path declared" in capsys.readouterr().out


def test_preflight_refuses_a_dirty_base_before_any_lane_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(
        monkeypatch,
        _Preflight(dirty="M a.py\nM b.py\n", grant=Grant(level="L1", token_budget=10_000)),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 1
    assert "base:      DIRTY - 2 path(s)" in out
    assert "base checkout is dirty" in out


def test_preflight_does_not_count_the_trees_the_landing_sweeps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            dirty=" M .basicly/ledger/events-0001.jsonl\n M .basicly/ledger/events-0002.jsonl\n",
            grant=Grant(level="L1", token_budget=10_000),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "base:      clean" in out
    assert "base checkout is dirty" not in out


def test_preflight_names_a_metered_runner_with_no_budget_and_still_reads_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(monkeypatch, _Preflight(metered="claude"))

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "budget:    MISSING" in out, "the hazard is no longer named"
    assert code == 0, "a metered runner with no budget still refuses the pass"


def test_preflight_forecasts_a_full_fan_out_when_no_lane_is_dispatchable_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "forecast:" in out
    assert "forecast:  1000 tokens forecast" in out
    assert "assumed at the unsizeable-lane bound (measured): c.1" in out
    assert "(1 of 1 open, cap 5)" in out


def test_preflight_says_the_forecast_is_still_on_seeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "spend cal: SEEDS" in out
    assert "claude-opus-5" in out
    assert "bug 7/10" in out and "task 2/10" in out
    assert "factors:   all seeds (never measured)" in out


def test_preflight_names_the_class_whose_spend_stopped_being_a_seed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            calibration=_calibration(samples={"bug": 12, "task": 2}),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "spend cal: measured for bug" in out
    assert "SEEDS" not in out


def test_preflight_says_when_a_build_factor_was_configured_rather_than_seeded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            calibration=_calibration(
                build_factor_sources={
                    "bug": decompose.BUILD_FACTOR_CONFIGURED,
                    "task": decompose.BUILD_FACTOR_SEED,
                }
            ),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "factors:   some configured (never measured)" in out


def test_preflight_says_an_unresolved_model_can_key_no_sample_at_all(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            calibration=_calibration(model=None, samples={"task": 0}),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "spend cal: SEEDS - no model pinned, so no sample can key in" in out
    assert "task 0/10" in out


def test_preflight_refuses_a_seeding_checkpoint_the_live_grant_does_not_delegate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L0", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify",),
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 1
    assert "checkpts:  decompose UNAPPROVED" in out
    assert "basicly policy checkpoint epic decompose --approve" in out
    assert "basicly policy grant epic --level L1" in out
    assert "VERDICT:   not ready" in out


def test_preflight_reports_a_grant_delegated_seeding_checkpoint_as_no_blocker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify",),
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 0
    assert "checkpts:  decompose pending - the live L3 grant delegates it" in out
    assert "checkpts:  ship pending - the live L3 grant delegates it" in out
    assert "UNAPPROVED" not in out
    assert "VERDICT:   ready" in out


def test_preflight_over_a_labelled_cut_reports_it_and_keeps_the_roots_checkpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify",),
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args(label="release-v0.7.0"))

    out = capsys.readouterr().out
    assert code == 0, out
    assert "select:    1 bead(s) carry label 'release-v0.7.0'; 1 still open" in out
    assert "checkpts:  decompose pending" in out
    assert "UNAPPROVED" not in out
    assert "VERDICT:   ready" in out


def test_preflight_refuses_a_lane_selector_no_bead_carries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    def _refuse(*_a: object, **_k: object) -> None:
        raise supervise.LaneSelectionError("no bead outside the pass root carries label 'typo'")

    monkeypatch.setattr(cli.supervise, "derive_session", _refuse)

    code = cli._cmd_loop_preflight(_preflight_args(label="typo"))

    out = capsys.readouterr().out
    assert code == 1
    assert "select:    INVALID - no bead outside the pass root carries label 'typo'" in out
    assert "VERDICT:   not ready - the lane selector names no bead to run" in out


def test_preflight_names_a_selected_lane_the_grant_does_not_cover(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            children=(("c.1", "open"), ("origin.7", "open")),
            covered=("epic", "c.1"),
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args(label="release-v0.7.0"))

    out = capsys.readouterr().out
    assert code == 0, "an uncovered lane still dispatches, so the verdict stands"
    assert "coverage:  1 of 2 selected lane(s) outside the L3 grant's session" in out
    assert "cover each: br dep add epic <id> -t blocks" in out
    assert "uncovered: origin.7" in out


def test_preflight_says_when_every_selected_lane_is_covered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(grant=Grant(level="L3", token_budget=100_000), covered=("epic", "c.1")),
    )

    cli._cmd_loop_preflight(_preflight_args(label="release-v0.7.0"))

    out = capsys.readouterr().out
    assert "coverage:  all 1 selected lane(s) under the L3 grant" in out


def test_preflight_is_ready_when_the_blocking_checkpoint_is_approved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify", "decompose"),
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 0
    assert "UNAPPROVED" not in out
    assert "VERDICT:   ready" in out


def test_preflight_refuses_a_root_with_no_open_child_left_to_provision(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            children=(("c.1", "closed"), ("c.2", "closed")),
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 1
    assert "provision: NONE - 2 child(ren), none open" in out
    assert "VERDICT:   not ready" in out


def test_preflight_refuses_when_every_open_child_is_refused_by_the_band(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            admissions={"c.1": _sized("c.1", 95_379, refused=True, violation="above")},
        ),
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 1
    assert "provision: NONE - every open child is REFUSED by the band" in out


def test_preflight_still_prices_a_childless_root_as_its_own_lane(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch, _Preflight(grant=Grant(level="L3", token_budget=100_000), children=())
    )

    code = cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert code == 0
    assert "provision: NONE" not in out
    assert "if all 1 lanes start" in out


def test_preflight_forecast_never_prices_more_lanes_than_there_are_children(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            children=(("c.1", "open"), ("c.2", "open")),
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "forecast:  2000 tokens forecast" in out
    assert "(2 of 2 open, cap 5)" in out


def test_preflight_prices_a_sized_candidate_at_its_own_estimate_not_the_bound(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            children=(("c.1", "open"), ("c.2", "open")),
            admissions={
                "c.1": _sized("c.1", 9_000, refused=False),
                "c.2": _sized("c.2", 11_000, refused=False),
            },
        ),
    )
    monkeypatch.setattr(
        cli.decompose,
        "dispatch_spend_forecasts",
        lambda _r, sizings, _s: tuple(
            SimpleNamespace(tokens=7_000 + index) for index, _ in enumerate(sizings)
        ),
    )

    cli._cmd_loop_preflight(_preflight_args())

    out = capsys.readouterr().out
    assert "forecast:  14001 tokens forecast" in out
    assert "sized: c.1, c.2" in out
    assert "unsizeable-lane bound" not in out.split("band:")[0]


def _improve_args(dry_run: bool) -> argparse.Namespace:
    return argparse.Namespace(dry_run=dry_run)


def test_loop_improve_runs_the_repos_controller_and_carries_the_dry_run_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / cli.IMPROVEMENT_CONTROLLER
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, cwd=kwargs["cwd"])
        return subprocess.CompletedProcess(command, 3)

    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._cmd_loop_improve(_improve_args(dry_run=True)) == 3
    assert seen["command"] == [sys.executable, str(script), "--dry-run"]
    assert seen["cwd"] == tmp_path


def test_loop_improve_refuses_a_repo_that_declares_no_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)

    assert cli._cmd_loop_improve(_improve_args(dry_run=False)) == 1
    assert cli.IMPROVEMENT_CONTROLLER.as_posix() in capsys.readouterr().err
