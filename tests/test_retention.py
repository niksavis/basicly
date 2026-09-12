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
