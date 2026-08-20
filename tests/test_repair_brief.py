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

import pytest

from basicly import lens_review, repair_brief, rubrics, validate_gate, verify

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


# Two lenses whose findings disagree about severity, so an implementation that ranked
# them would have to put the blocker first and the assertions below would catch it.
_REVIEWS = (
    lens_review.LensFindings("correctness", "off-by-one at parse.py:12 (major)"),
    lens_review.LensFindings("security", "shell injection at run.py:4 (blocker)"),
)


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


# --- a brief the branch has moved past (basicly-59fkfu) -----------------------


def test_a_stale_brief_is_refused_against_a_head_that_moved() -> None:
    """The acceptance criterion, and it was observed rather than imagined.

    On `basicly-gvlpxm` the brief asked for a change that had merged hours earlier, so a
    full metered repair would have found nothing to do.
    """
    reason = repair_brief.stale_against(_brief(branch_head="aaa1111"), "bbb2222")

    assert "aaa1111" in reason
    assert "bbb2222" in reason
    assert "may already be fixed" in reason


def test_a_brief_against_the_current_head_is_dispatched_unchanged() -> None:
    """The third criterion: a defect still open is briefed exactly as before."""
    assert repair_brief.stale_against(_brief(branch_head="aaa1111"), "aaa1111") == ""


@pytest.mark.parametrize(("recorded", "head"), [("", "bbb2222"), ("aaa1111", None), ("", None)])
def test_a_brief_that_cannot_be_judged_stale_is_dispatched(recorded: str, head: str | None) -> None:
    """Cannot-tell dispatches, and each of the two ways it arises is one case here.

    A brief written before the field existed carries no head, and a branch whose ref will not
    resolve answers None. Refusing on the reader's own uncertainty would strand work a red
    gate really does owe, which is the opposite failure and the more expensive one.
    """
    assert repair_brief.stale_against(_brief(branch_head=recorded), head) == ""


def test_the_head_a_brief_was_written_against_survives_the_round_trip(tmp_path: Path) -> None:
    """The field is only useful if it comes back, so the JSON shape carries it."""
    cwd = _worktree(tmp_path)
    brief = _brief(branch_head="aaa1111")

    assert repair_brief.write_repair_brief(cwd, brief)
    assert repair_brief.take_repair_brief(cwd) == brief


def test_a_brief_written_before_the_field_existed_reads_as_cannot_tell(tmp_path: Path) -> None:
    """The tolerant read the sentinel takes: an older brief is dispatched, not refused."""
    cwd = _worktree(tmp_path)
    (cwd / repair_brief.REPAIR_BRIEF_FILE).parent.mkdir(parents=True, exist_ok=True)
    (cwd / repair_brief.REPAIR_BRIEF_FILE).write_text(
        json.dumps({"issue_id": "i", "gate": verify.DEFAULT_GATE, "reason": "old"}),
        encoding="utf-8",
    )
    taken = repair_brief.take_repair_brief(cwd)

    assert taken is not None
    assert taken.branch_head == ""
    assert repair_brief.stale_against(taken, "bbb2222") == ""


def test_a_repair_that_committed_nothing_is_named_rather_than_charged() -> None:
    """The second criterion: a repair that commits nothing is named, not charged.

    It leaves the branch where it found it, so the next advance takes the same brief again -
    the wedge rather than the waste.
    """
    reason = repair_brief.no_commit_reason(_brief(), "worktree 'w'")

    assert "committed nothing" in reason
    assert "worktree 'w'" in reason
    assert "same round again" in reason


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
    """The three that judge the lane's own work, and the landing status that is a red verify.

    The merge gate is deliberately absent: a conflict is not a defect in the work, and
    `supervise._bounce_lane` briefs its owner from the conflicting paths instead. The
    consumer validation is present: it judged this diff, so its failure is a defect a
    repair run can act on (basicly-w88t).
    """
    assert repair_brief.REPAIR_GATES == (
        verify.DEFAULT_GATE,
        rubrics.RUBRIC_GATE,
        validate_gate.VALIDATE_GATE,
    )
    assert "merge" not in repair_brief.REPAIR_GATES
    assert repair_brief.LANDING_VERIFY_FAILED == "verify-failed"


# --- the reviews a judged gate hands over (basicly-w88t) ------------------------


def test_each_lens_gets_its_own_section_and_nothing_ranks_one_against_the_other() -> None:
    """§6.4: lens output is reported per lens and never merged into one ranked list.

    Asserted positionally rather than by membership: each lens's text has to fall
    between its own heading and whatever follows, which a merged or severity-ordered
    list would fail — the blocker is recorded second here and must stay second.
    """
    prompt = repair_brief.repair_prompt(_brief(reviews=_REVIEWS))
    correctness = prompt.index("Lens: correctness")
    security = prompt.index("Lens: security")

    assert correctness < prompt.index("off-by-one at parse.py:12 (major)") < security
    assert security < prompt.index("shell injection at run.py:4 (blocker)")
    assert "neither merged nor ranked against each other" in prompt


def test_the_reviews_are_handed_over_as_advice_rather_than_as_a_gate() -> None:
    """§6.5: the validator owns the gate, so a finding never becomes a precondition.

    The named gate is what the repair has to satisfy; a run that read these as gates
    would either widen the repair or refuse to finish while one stayed unaddressed.
    """
    prompt = repair_brief.repair_prompt(_brief(reviews=_REVIEWS))

    assert "They are advisory" in prompt
    assert "no finding here is a gate of its own or a precondition" in prompt


def test_a_lens_that_recorded_nothing_is_named_rather_than_left_out() -> None:
    """A lens missing from the brief reads as a lens that was never asked."""
    reviews = (_REVIEWS[0], lens_review.LensFindings("security"))

    prompt = repair_brief.repair_prompt(_brief(reviews=reviews))

    assert f"Lens: security\n{repair_brief.NO_REVIEW}" in prompt


def test_a_brief_with_no_reviews_is_the_prompt_it_always_was() -> None:
    """A repair after a red verify is briefed exactly as it was before basicly-w88t."""
    prompt = repair_brief.repair_prompt(_brief())

    assert "one section per lens" not in prompt
    assert "Lens:" not in prompt


def test_the_reviews_survive_the_worktree_the_same_way_the_evidence_does(
    tmp_path: Path,
) -> None:
    """The brief is a file between two processes, so the lens split has to survive JSON."""
    cwd = _worktree(tmp_path)
    brief = _brief(gate=validate_gate.VALIDATE_GATE, reviews=_REVIEWS)

    assert repair_brief.write_repair_brief(cwd, brief)

    assert repair_brief.take_repair_brief(cwd) == brief


def test_a_review_entry_with_no_lens_name_is_dropped_rather_than_merged(
    tmp_path: Path,
) -> None:
    """The name is the whole of what keeps two lenses apart, so a nameless one is not one.

    The tolerant direction the module takes everywhere else: a garbled entry costs its
    own findings, never the dispatch.
    """
    cwd = _worktree(tmp_path)
    (cwd / repair_brief.REPAIR_BRIEF_FILE).parent.mkdir(parents=True)
    (cwd / repair_brief.REPAIR_BRIEF_FILE).write_text(
        json.dumps({
            "issue_id": "i",
            "gate": validate_gate.VALIDATE_GATE,
            "reviews": [{"lens": " ", "findings": "orphaned"}, {"lens": "security", "f": 1}],
        }),
        encoding="utf-8",
    )

    brief = repair_brief.take_repair_brief(cwd)

    assert brief is not None
    assert brief.reviews == (lens_review.LensFindings("security", ""),)
