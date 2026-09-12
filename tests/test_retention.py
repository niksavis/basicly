from __future__ import annotations

from pathlib import Path

import pytest

from basicly import retention

REPO = Path(__file__).parent.parent

BASELINE = """\
# Title

Some prose that is not a rule.

## Core Rules

- Minimal diffs; an unrelated refactor hides which change failed.
- Deterministic tests; a bug fix ships a regression test.

## Secure Coding

- Parameterize shell commands and queries; concatenation lets input read as syntax.
- Keep defaults portable; a committed hostname breaks the next clone.
"""


@pytest.fixture
def rules() -> list[retention.Rule]:
    return retention.derive_rules(BASELINE)


def test_only_bullets_under_a_heading_become_rules(rules: list[retention.Rule]) -> None:
    assert [rule.rule_id for rule in rules] == [
        "core-rules.1",
        "core-rules.2",
        "secure-coding.1",
        "secure-coding.2",
    ]


def test_prose_outside_a_section_is_not_a_rule() -> None:
    assert retention.derive_rules("- an orphan bullet before any heading\n") == []


def test_a_bullet_of_pure_stopwords_is_not_a_rule() -> None:
    assert retention.derive_rules("## S\n\n- the it is\n") == []


def test_position_is_the_ordinal_across_the_whole_file(rules: list[retention.Rule]) -> None:
    assert [rule.position for rule in rules] == [0, 1, 2, 3]


def test_verbatim_recall_scores_every_rule(rules: list[retention.Rule]) -> None:
    response = "\n".join(f"- {rule.text}" for rule in rules)

    assert retention.score_response(rules, response).rate == 1.0


def test_unrelated_prose_scores_nothing(rules: list[retention.Rule]) -> None:
    response = "\n".join("The quick brown fox jumps over the lazy dog." for _ in rules)

    assert retention.score_response(rules, response).retained == 0


def test_an_empty_response_scores_nothing(rules: list[retention.Rule]) -> None:
    report = retention.score_response(rules, "")

    assert report.retained == 0
    assert report.rate == 0.0


def test_half_the_rules_recalled_scores_about_half(rules: list[retention.Rule]) -> None:
    response = "\n".join(f"- {rule.text}" for rule in rules[:2])

    assert retention.score_response(rules, response).retained == 2


def test_the_evidence_is_the_line_that_matched(rules: list[retention.Rule]) -> None:
    report = retention.score_response(rules, "- noise here\n- Deterministic tests and regressions.")

    match = next(m for m in report.matches if m.rule.rule_id == "core-rules.2")
    assert "Deterministic tests" in match.evidence


def test_a_rule_split_across_two_response_lines_still_matches(
    rules: list[retention.Rule],
) -> None:
    response = "- Parameterize shell commands and queries\n- so input is not read as syntax"

    match = next(
        m
        for m in retention.score_response(rules, response).matches
        if m.rule.rule_id == "secure-coding.1"
    )
    assert match.retained


def test_the_imperative_clause_is_enough_to_count_as_retained(
    rules: list[retention.Rule],
) -> None:
    response = "- Parameterize shell commands and queries."

    match = next(
        m
        for m in retention.score_response(rules, response).matches
        if m.rule.rule_id == "secure-coding.1"
    )
    assert match.retained, "recalling the instruction without its reason must still count"


def test_a_slash_pair_is_two_words_not_one() -> None:
    assert retention.content_words("safety/security boundaries") >= {"safety", "security"}


def test_a_word_and_its_inflection_share_a_stem() -> None:
    assert retention.stem("refactoring") == retention.stem("refactored")
    assert retention.stem("parameterize") == retention.stem("parameterized")


def test_a_short_word_is_not_stemmed() -> None:
    assert retention.stem("its") == "its"
    assert retention.stem("gates") == "gates"


def test_backticked_code_is_not_treated_as_markdown_noise() -> None:
    assert "rm" in retention.content_words("never run `rm -rf` on a repo")


def test_forgotten_is_ordered_worst_first(rules: list[retention.Rule]) -> None:
    report = retention.score_response(
        rules, "- Deterministic tests; a bug fix ships a regression test."
    )

    scores = [match.score for match in report.forgotten()]
    assert scores == sorted(scores)


def test_by_section_counts_each_section_separately(rules: list[retention.Rule]) -> None:
    response = "\n".join(f"- {rule.text}" for rule in rules[:2])

    assert retention.score_response(rules, response).by_section() == {
        "core-rules": (2, 2),
        "secure-coding": (0, 2),
    }


def test_by_position_splits_into_buckets(rules: list[retention.Rule]) -> None:
    report = retention.score_response(rules, "")

    assert [total for _, _, total in report.by_position(buckets=2)] == [2, 2]


def test_by_position_of_an_empty_report_is_empty() -> None:
    assert retention.Report((), 0).by_position() == []


def test_the_committed_baseline_derives_rules() -> None:
    rules = retention.derive_rules_from(REPO / ".claude/CLAUDE.md")

    assert len(rules) > 30, "the scorer would report a free zero against an empty rule set"


def test_the_scorer_controls_hold_on_the_committed_baseline() -> None:
    rules = retention.derive_rules_from(REPO / ".claude/CLAUDE.md")
    verbatim = retention.score_response(rules, "\n".join(f"- {r.text}" for r in rules))
    noise = "\n".join("The quick brown fox jumps over the lazy dog." for _ in rules)

    assert verbatim.rate == 1.0
    assert retention.score_response(rules, noise).retained == 0


def test_the_verdict_bands_come_from_the_two_measured_populations() -> None:
    assert retention.verdict(0.949, 111, 59) == retention.LOADED
    assert retention.verdict(0.051, 69, 59) == retention.ABSENT


def test_a_score_between_the_populations_is_not_decisive() -> None:
    assert retention.verdict(0.50, 100, 59) == retention.PARTIAL


def test_the_band_edges_are_inclusive_on_the_confident_side() -> None:
    assert retention.verdict(retention.LOADED_FLOOR, 100, 50) == retention.LOADED
    assert retention.verdict(retention.ABSENT_CEILING, 100, 50) == retention.ABSENT


def test_a_short_answer_is_undersampled_rather_than_absent() -> None:
    assert retention.verdict(0.45, 30, 58) == retention.UNDERSAMPLED


def test_undersampled_outranks_a_high_score_too() -> None:
    assert retention.verdict(0.95, 10, 58) == retention.UNDERSAMPLED


def test_the_no_file_arm_is_long_enough_to_be_called_absent() -> None:
    assert retention.verdict(0.051, 69, 59) != retention.UNDERSAMPLED, (
        "the measured control wrote 1.17 lines per rule; clipping it would hide a real absence"
    )


def test_every_verdict_carries_an_explanation() -> None:
    assert set(retention.VERDICTS) == {
        retention.LOADED,
        retention.PARTIAL,
        retention.ABSENT,
        retention.UNDERSAMPLED,
    }


def test_the_report_reads_its_own_verdict(rules: list[retention.Rule]) -> None:
    response = "\n".join(f"- {rule.text}" for rule in rules) + "\n- filler\n" * 4

    assert retention.score_response(rules, response).verdict == retention.LOADED


WRAPPED = """\
## External Facts

- **Your training data has a cutoff and interfaces move.** A flag, field or
  model id may have changed. Never answer from recall: grep the adapter first.
- A short one.
"""


def test_a_bullet_wrapped_over_lines_is_one_rule() -> None:
    derived = retention.derive_rules(WRAPPED)

    assert len(derived) == 2
    assert "grep the adapter first" in derived[0].text


def test_a_wrapped_rule_keeps_the_words_on_its_later_lines() -> None:
    derived = retention.derive_rules(WRAPPED)

    assert {"recall", "adapter", "grep"} <= derived[0].words


def test_a_heading_ends_the_bullet_before_it() -> None:
    derived = retention.derive_rules("## A\n\n- one line\n  continued\n\n## B\n\n- two\n")

    assert [rule.rule_id for rule in derived] == ["a.1", "b.1"]
    assert derived[0].text == "one line continued"


def test_a_blank_line_ends_a_bullet() -> None:
    derived = retention.derive_rules("## A\n\n- one\n\n  a separate paragraph\n")

    assert [rule.text for rule in derived] == ["one"]


def test_a_star_bullet_counts_too() -> None:
    assert len(retention.derive_rules("## A\n\n* starred rule here\n")) == 1
