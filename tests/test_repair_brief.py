"""The brief a failing gate leaves for the run that repairs it.

Moved out of `test_loop_repair.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14), along the boundary the module states: what a failed gate *recorded*
and what the next run is *told* about it. Nothing here advances a phase, so nothing here
drives `loop.advance` — the three acceptance criteria that are properties of the engine
(the repair runs in the worktree the lane already has, the prompt reaches the dispatch,
the per-gate allowances stop at a lane ceiling) stay with the loop that owns them.

The one check that runs here really runs and really fails, per the fixture rule
basicly-m4zv.6: a dependency's output must be observed, never composed.
"""

from __future__ import annotations

import json
from pathlib import Path

from basicly import repair_brief, rubrics, verify

# A check that always fails and prints something recognisable, so the brief's command
# and output are read off a real run rather than invented.
FAILING_CHECK = json.dumps([
    "python",
    "-c",
    "import sys; sys.stdout.write('E   assert 1 == 2\\n'); sys.exit(1)",
])


def _worktree(tmp_path: Path, *, checks: str = "") -> Path:
    """A real on-disk worktree; the brief is a file in it, so a fake path proves nothing."""
    path = tmp_path / "wt"
    path.mkdir()
    if checks:
        (path / "basicly.toml").write_text(checks, encoding="utf-8")
    return path


def _brief(**overrides) -> repair_brief.RepairBrief:
    fields = {
        "issue_id": "i",
        "gate": verify.DEFAULT_GATE,
        "reason": "verify fast failed: pytest",
        "findings": ("pytest",),
        "evidence": (repair_brief.GateEvidence("pytest", "pytest -q", "E   assert 1 == 2"),),
    }
    fields.update(overrides)
    return repair_brief.RepairBrief(**fields)


# --- the evidence a gate hands over --------------------------------------------


def test_verify_evidence_pairs_the_gate_findings_with_the_command_and_output(
    tmp_path: Path,
) -> None:
    """The evidence is read off the gate's own config and a captured re-run."""
    checks = f'[[verify.checks]]\nname = "pytest"\ncommand = {FAILING_CHECK}\nmodes = ["fast"]\n'
    cwd = _worktree(tmp_path, checks=checks)
    report = verify.VerifyReport("fast", (verify.CheckResult("pytest", "fail", 1),))

    evidence = repair_brief.verify_evidence(report, cwd, "fast")

    assert [e.check for e in evidence] == ["pytest"]
    assert evidence[0].command.startswith("python -c")
    assert "E   assert 1 == 2" in evidence[0].output


def test_a_green_report_yields_no_evidence(tmp_path: Path) -> None:
    """The re-run is paid for only where a gate has already failed."""
    report = verify.VerifyReport("fast", (verify.CheckResult("pytest", "pass", 0),))

    assert repair_brief.verify_evidence(report, _worktree(tmp_path), "fast") == ()


def test_a_failure_with_no_readable_config_still_names_the_check(tmp_path: Path) -> None:
    """A brief that quietly omitted a finding would under-report what the repair must fix.

    The command and the output are both unavailable here — there is no `basicly.toml` in
    the tree — and the check is reported anyway, with the empty fields the caller's
    prompt then leaves out.
    """
    report = verify.VerifyReport("fast", (verify.CheckResult("pytest", "fail", 1),))

    evidence = repair_brief.verify_evidence(report, _worktree(tmp_path), "fast")

    assert [(e.check, e.command, e.output) for e in evidence] == [("pytest", "", "")]


def test_a_long_gate_output_keeps_its_tail_under_the_prompt_bound() -> None:
    """A failing suite can print megabytes; a prompt that does not fit is no repair."""
    clipped = repair_brief.clip_output("x" * 50_000 + "the assertion")

    assert clipped.endswith("the assertion")
    assert len(clipped) < repair_brief.MAX_REPAIR_OUTPUT_CHARS + 100


def test_output_that_already_fits_is_not_marked_as_cut() -> None:
    """The control for the clip: a marker on untruncated output would be a false claim."""
    assert repair_brief.clip_output("  short  ") == "short"


# --- the round trip through the worktree ---------------------------------------


def test_a_brief_survives_the_worktree_and_is_consumed_on_read(tmp_path: Path) -> None:
    """Consumed on presence, so one failed round cannot brief two dispatches.

    A brief describes one failed round; leaving it would brief a second run about a
    gate the first may already have fixed.
    """
    cwd = _worktree(tmp_path)
    brief = _brief()

    assert repair_brief.write_repair_brief(cwd, brief)
    assert repair_brief.take_repair_brief(cwd) == brief
    assert repair_brief.take_repair_brief(cwd) is None


def test_a_brief_is_never_written_into_a_tree_that_is_gone(tmp_path: Path) -> None:
    """A gate-failure path must not acquire a second way to fall over.

    False rather than a raise or a stray directory: the next dispatch is the un-briefed
    one it was before, which is what the caller was doing anyway.
    """
    missing = tmp_path / "gone"

    assert not repair_brief.write_repair_brief(missing, _brief())
    assert not missing.exists()


def test_a_brief_that_cannot_be_parsed_is_dropped_rather_than_raised(tmp_path: Path) -> None:
    """A garbled brief costs one un-briefed dispatch, never a crash in the build phase."""
    cwd = tmp_path / "wt"
    (cwd / repair_brief.REPAIR_BRIEF_FILE).parent.mkdir(parents=True)
    (cwd / repair_brief.REPAIR_BRIEF_FILE).write_text("{not json", encoding="utf-8")

    assert repair_brief.take_repair_brief(cwd) is None
    assert not (cwd / repair_brief.REPAIR_BRIEF_FILE).exists()  # consumed, so it cannot re-fire


def test_a_tree_that_never_failed_a_gate_has_no_brief(tmp_path: Path) -> None:
    """Absence is the common case on every green dispatch, and is not an error."""
    assert repair_brief.take_repair_brief(_worktree(tmp_path)) is None


def test_the_brief_lives_where_the_usage_dir_self_ignores() -> None:
    """Bound to the tree it describes and never in a commit (basicly-o774).

    `.basicly/usage/` is the sentinel's convention and self-ignores; a brief written
    anywhere else would be staged by the very repair run it is briefing.
    """
    assert repair_brief.REPAIR_BRIEF_FILE.parent == Path(".basicly/usage")


# --- what the next run is told -------------------------------------------------


def test_the_prompt_refuses_the_two_moves_that_turn_a_repair_into_a_build() -> None:
    """The work exists and is committed; one named gate rejected it.

    A build prompt tells the agent to read the requirement and implement it, which is
    the wrong instruction twice over — so re-planning and starting somewhere else are
    both refused explicitly rather than merely left unmentioned.
    """
    prompt = repair_brief.repair_prompt(_brief())

    assert "do not re-plan the work" in prompt
    assert "do not start a new branch or worktree" in prompt
    assert "Read AGENTS.md" not in prompt


def test_the_prompt_carries_the_gate_its_command_and_its_output() -> None:
    """The three things the fixed build text carried none of."""
    prompt = repair_brief.repair_prompt(_brief())

    assert f"Gate: {verify.DEFAULT_GATE}" in prompt
    assert "verify fast failed: pytest" in prompt
    assert "- pytest" in prompt
    assert "pytest -q" in prompt
    assert "E   assert 1 == 2" in prompt


def test_an_evidence_entry_with_nothing_to_say_is_left_out_of_the_prompt() -> None:
    """A check the gate gave no command and no output for adds a heading and no fact.

    Reachable against this repo's own config: a failing check that runs in another
    mode resolves to neither, and the check's name is in the findings already.
    """
    brief = repair_brief.RepairBrief(
        issue_id="i",
        gate=verify.DEFAULT_GATE,
        reason="verify fast failed: typos",
        findings=("typos",),
        evidence=(
            repair_brief.GateEvidence("typos"),
            repair_brief.GateEvidence("ruff", command="ruff check"),
        ),
    )

    prompt = repair_brief.repair_prompt(brief)

    assert "Check typos" not in prompt
    assert "- typos" in prompt  # still reported, as a finding
    assert "Check ruff — command: ruff check" in prompt


def test_a_collision_is_not_one_of_the_gates_a_repair_run_can_act_on() -> None:
    """The two that judge the lane's own diff, and the landing status that is a red verify.

    The merge gate is deliberately absent: a conflict is not a defect in the work, and
    `supervise._bounce_lane` briefs its owner from the conflicting paths instead.
    """
    assert repair_brief.REPAIR_GATES == (verify.DEFAULT_GATE, rubrics.RUBRIC_GATE)
    assert "merge" not in repair_brief.REPAIR_GATES
    assert repair_brief.LANDING_VERIFY_FAILED == "verify-failed"
