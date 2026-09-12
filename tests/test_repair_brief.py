from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import lens_review, repair_brief, rubrics, validate_gate, verify

FAILING_CHECK = json.dumps([
    "python",
    "-c",
    "import sys; sys.stdout.write('E   assert 1 == 2\\n'); sys.exit(1)",
])


def _worktree(tmp_path: Path, *, checks: str = "") -> Path:
    path = tmp_path / "wt"
    path.mkdir()
    if checks:
        (path / "basicly.toml").write_text(checks, encoding="utf-8")
    return path


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


def test_verify_evidence_pairs_the_gate_findings_with_the_command_and_output(
    tmp_path: Path,
) -> None:
    checks = f'[[verify.checks]]\nname = "pytest"\ncommand = {FAILING_CHECK}\nmodes = ["fast"]\n'
    cwd = _worktree(tmp_path, checks=checks)
    report = verify.VerifyReport("fast", (verify.CheckResult("pytest", "fail", 1),))

    evidence = repair_brief.verify_evidence(report, cwd, "fast")

    assert [e.check for e in evidence] == ["pytest"]
    assert evidence[0].command.startswith("python -c")
    assert "E   assert 1 == 2" in evidence[0].output


def test_a_green_report_yields_no_evidence(tmp_path: Path) -> None:
    report = verify.VerifyReport("fast", (verify.CheckResult("pytest", "pass", 0),))

    assert repair_brief.verify_evidence(report, _worktree(tmp_path), "fast") == ()


def test_a_failure_with_no_readable_config_still_names_the_check(tmp_path: Path) -> None:

    report = verify.VerifyReport("fast", (verify.CheckResult("pytest", "fail", 1),))

    evidence = repair_brief.verify_evidence(report, _worktree(tmp_path), "fast")

    assert [(e.check, e.command, e.output) for e in evidence] == [("pytest", "", "")]


def test_a_long_gate_output_keeps_its_tail_under_the_prompt_bound() -> None:
    clipped = repair_brief.clip_output("x" * 50_000 + "the assertion")

    assert clipped.endswith("the assertion")
    assert len(clipped) < repair_brief.MAX_REPAIR_OUTPUT_CHARS + 100


def test_output_that_already_fits_is_not_marked_as_cut() -> None:
    assert repair_brief.clip_output("  short  ") == "short"


def test_a_brief_survives_the_worktree_and_is_consumed_on_read(tmp_path: Path) -> None:

    cwd = _worktree(tmp_path)
    brief = _brief()

    assert repair_brief.write_repair_brief(cwd, brief)
    assert repair_brief.take_repair_brief(cwd) == brief
    assert repair_brief.take_repair_brief(cwd) is None


def test_a_stale_brief_is_refused_against_a_head_that_moved() -> None:

    reason = repair_brief.stale_against(_brief(branch_head="aaa1111"), "bbb2222")

    assert "aaa1111" in reason
    assert "bbb2222" in reason
    assert "may already be fixed" in reason
    assert "discarding it" in reason


def test_a_brief_against_the_current_head_is_dispatched_unchanged() -> None:
    assert repair_brief.stale_against(_brief(branch_head="aaa1111"), "aaa1111") == ""


@pytest.mark.parametrize(("recorded", "head"), [("", "bbb2222"), ("aaa1111", None), ("", None)])
def test_a_brief_that_cannot_be_judged_stale_is_dispatched(recorded: str, head: str | None) -> None:

    assert repair_brief.stale_against(_brief(branch_head=recorded), head) == ""


def test_the_head_a_brief_was_written_against_survives_the_round_trip(tmp_path: Path) -> None:
    cwd = _worktree(tmp_path)
    brief = _brief(branch_head="aaa1111")

    assert repair_brief.write_repair_brief(cwd, brief)
    assert repair_brief.take_repair_brief(cwd) == brief


def test_a_brief_written_before_the_field_existed_reads_as_cannot_tell(tmp_path: Path) -> None:
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

    reason = repair_brief.no_commit_reason(_brief(), "worktree 'w'")

    assert "committed nothing" in reason
    assert "worktree 'w'" in reason
    assert "same round again" in reason


def test_a_brief_is_never_written_into_a_tree_that_is_gone(tmp_path: Path) -> None:

    missing = tmp_path / "gone"

    assert not repair_brief.write_repair_brief(missing, _brief())
    assert not missing.exists()


def test_a_brief_that_cannot_be_parsed_is_dropped_rather_than_raised(tmp_path: Path) -> None:
    cwd = tmp_path / "wt"
    (cwd / repair_brief.REPAIR_BRIEF_FILE).parent.mkdir(parents=True)
    (cwd / repair_brief.REPAIR_BRIEF_FILE).write_text("{not json", encoding="utf-8")

    assert repair_brief.take_repair_brief(cwd) is None
    assert not (cwd / repair_brief.REPAIR_BRIEF_FILE).exists()


def test_a_tree_that_never_failed_a_gate_has_no_brief(tmp_path: Path) -> None:
    assert repair_brief.take_repair_brief(_worktree(tmp_path)) is None


def test_the_brief_lives_where_the_usage_dir_self_ignores() -> None:

    assert repair_brief.REPAIR_BRIEF_FILE.parent == Path(".basicly/usage")


def test_the_prompt_refuses_the_two_moves_that_turn_a_repair_into_a_build() -> None:

    prompt = repair_brief.repair_prompt(_brief())

    assert "do not re-plan the work" in prompt
    assert "do not start a new branch or worktree" in prompt
    assert "Read AGENTS.md" not in prompt


def test_the_prompt_carries_the_gate_its_command_and_its_output() -> None:
    prompt = repair_brief.repair_prompt(_brief())

    assert f"Gate: {verify.DEFAULT_GATE}" in prompt
    assert "verify fast failed: pytest" in prompt
    assert "- pytest" in prompt
    assert "pytest -q" in prompt
    assert "E   assert 1 == 2" in prompt


def test_an_evidence_entry_with_nothing_to_say_is_left_out_of_the_prompt() -> None:

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
    assert "- typos" in prompt
    assert "Check ruff — command: ruff check" in prompt


def test_a_collision_is_not_one_of_the_gates_a_repair_run_can_act_on() -> None:

    assert repair_brief.REPAIR_GATES == (
        verify.DEFAULT_GATE,
        rubrics.RUBRIC_GATE,
        validate_gate.VALIDATE_GATE,
    )
    assert "merge" not in repair_brief.REPAIR_GATES
    assert repair_brief.LANDING_VERIFY_FAILED == "verify-failed"


def test_each_lens_gets_its_own_section_and_nothing_ranks_one_against_the_other() -> None:

    prompt = repair_brief.repair_prompt(_brief(reviews=_REVIEWS))
    correctness = prompt.index("Lens: correctness")
    security = prompt.index("Lens: security")

    assert correctness < prompt.index("off-by-one at parse.py:12 (major)") < security
    assert security < prompt.index("shell injection at run.py:4 (blocker)")
    assert "neither merged nor ranked against each other" in prompt


def test_the_reviews_are_handed_over_as_advice_rather_than_as_a_gate() -> None:

    prompt = repair_brief.repair_prompt(_brief(reviews=_REVIEWS))

    assert "They are advisory" in prompt
    assert "no finding here is a gate of its own or a precondition" in prompt


def test_a_lens_that_recorded_nothing_is_named_rather_than_left_out() -> None:
    reviews = (_REVIEWS[0], lens_review.LensFindings("security"))

    prompt = repair_brief.repair_prompt(_brief(reviews=reviews))

    assert f"Lens: security\n{repair_brief.NO_REVIEW}" in prompt


def test_a_brief_with_no_reviews_is_the_prompt_it_always_was() -> None:
    prompt = repair_brief.repair_prompt(_brief())

    assert "one section per lens" not in prompt
    assert "Lens:" not in prompt


def test_the_reviews_survive_the_worktree_the_same_way_the_evidence_does(
    tmp_path: Path,
) -> None:
    cwd = _worktree(tmp_path)
    brief = _brief(gate=validate_gate.VALIDATE_GATE, reviews=_REVIEWS)

    assert repair_brief.write_repair_brief(cwd, brief)

    assert repair_brief.take_repair_brief(cwd) == brief


def test_a_review_entry_with_no_lens_name_is_dropped_rather_than_merged(
    tmp_path: Path,
) -> None:

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
