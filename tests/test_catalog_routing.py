"""Tests for the Tier-2 routing eval (basicly-m4zv.2).

The gate's own failure mode is passing vacuously: a ranking over a corpus a
prompt shares no words with still has a first place, and an assertion written
against position alone reports coverage it never had. Several tests below exist
only to hold that line — each pairs a case the gate must fail with one it must
pass, so "the check fires" and "the check discriminates" are separate claims.
"""

from __future__ import annotations

from basicly import catalog_routing as routing

# A corpus with no shared vocabulary between entries, so a ranking is decided by
# the prompt rather than by which description happened to be wordiest.
CORPUS = {
    "tool-jq": "Parse and filter json in shell pipelines.",
    "tool-yq": "Query and edit yaml configuration manifests.",
    "tool-tree": "Visualize directory layout as a hierarchy.",
}


def test_ranking_is_total_and_breaks_ties_on_the_slug() -> None:
    """A prompt matching nothing still yields a stable, reproducible order."""
    ranked = routing.Ranker(CORPUS).rank("zzzz nothing matches")
    assert [entry.slug for entry in ranked] == sorted(CORPUS)
    assert [entry.rank for entry in ranked] == [1, 2, 3]
    assert {entry.score for entry in ranked} == {0.0}


def test_a_positive_prompt_ranks_its_owner_first() -> None:
    """The base case: a prompt about an entry reaches it, and counts toward rank-1."""
    report = routing.evaluate(CORPUS, [routing.PositiveCase("tool-yq", "edit this yaml")], [])
    assert report.failures == ()
    assert report.rank1_hits == 1
    assert report.rank1_rate == 1.0


def test_a_positive_prompt_outside_top_k_fails_and_names_what_beat_it() -> None:
    """A miss must name the winners, so the fix is a description edit, not a hunt."""
    report = routing.evaluate(
        CORPUS,
        [routing.PositiveCase("tool-tree", "filter the json in this directory", top_k=1)],
        [],
    )
    assert len(report.failures) == 1
    assert "tool-jq" in report.failures[0]
    assert report.rank1_hits == 0


def test_a_positive_prompt_matching_nothing_fails_instead_of_passing_on_a_tie_break() -> None:
    """The anti-vacuity rule, stated as a pair.

    ``tool-jq`` sorts first in this corpus, so a prompt sharing no vocabulary
    with anything hands it rank 1 and an assertion written on position alone
    would call that a pass — having measured nothing at all.
    """
    vacuous = routing.evaluate(CORPUS, [routing.PositiveCase("tool-jq", "zzzz qqqq wwww")], [])
    assert len(vacuous.failures) == 1
    assert "scores 0" in vacuous.failures[0]
    assert vacuous.rank1_hits == 0

    real = routing.evaluate(CORPUS, [routing.PositiveCase("tool-jq", "filter json")], [])
    assert real.failures == ()


def test_a_negative_passes_when_its_declared_owner_outranks_the_entry() -> None:
    """The pairwise assertion holds when routing puts the prompt where it belongs."""
    report = routing.evaluate(
        CORPUS, [], [routing.NegativeCase("tool-yq", "filter this json", "tool-jq")]
    )
    assert report.failures == ()


def test_a_negative_fails_when_the_entry_is_not_outranked_by_its_owner() -> None:
    """An entry stealing another's prompt is the trigger bug this tier exists to find."""
    report = routing.evaluate(
        CORPUS, [], [routing.NegativeCase("tool-jq", "filter this json", "tool-yq")]
    )
    assert len(report.failures) == 1
    assert "tool-yq" in report.failures[0]


def test_a_negative_whose_owner_matches_nothing_fails_rather_than_passing_vacuously() -> None:
    """The reason the pairwise form was chosen over "must not rank first".

    ``tool-tree`` does not rank first for this prompt, so the weak form passes —
    but the prompt reaches no entry at all, so the pass is about nothing. The
    owner scoring zero is what makes that detectable.
    """
    report = routing.evaluate(
        CORPUS, [], [routing.NegativeCase("tool-tree", "zzzz qqqq", "tool-jq")]
    )
    assert len(report.failures) == 1
    assert "proves nothing" in report.failures[0]


def test_near_duplicate_descriptions_fail_and_merely_similar_ones_warn() -> None:
    """Both collision lines, and that they are different lines."""
    duplicated = routing.evaluate(
        {"a": "Parse and filter json in shell pipelines.", **CORPUS}, [], []
    )
    assert any("a and tool-jq" in failure for failure in duplicated.failures)

    # Four entries, not two: with a two-document corpus every shared term has
    # ``idf == 0``, so a pair can only ever be scored on the words it does *not*
    # share and the similarity collapses to zero.
    similar = routing.evaluate(
        {
            "a": "Parse json in shell pipelines.",
            "b": "Parse json in shell pipelines with a filter.",
            "c": "Visualize directory layout as a hierarchy.",
            "d": "Download files over http with retries.",
        },
        [],
        [],
    )
    assert similar.failures == ()
    assert len(similar.collision_warnings) == 1
    assert "warning at" in similar.collision_warnings[0]


def test_a_distinct_catalog_produces_no_collision_finding() -> None:
    """The collision check stays silent on a catalog whose entries are distinct."""
    report = routing.evaluate(CORPUS, [], [])
    assert report.failures == ()
    assert report.collision_warnings == ()


def test_an_empty_corpus_reports_a_zero_rank1_rate_not_a_perfect_one() -> None:
    """A floor must not be clearable by having no evidence at all."""
    assert routing.evaluate({}, [], []).rank1_rate == 0.0


def test_a_rate_below_the_declared_floor_is_a_violation() -> None:
    """The CI metric binds: under the floor fails, over it passes."""
    assert routing.floor_violations(0.80, 0.85, 0.85) != []
    assert routing.floor_violations(0.90, 0.85, 0.85) == []


def test_a_floor_below_its_high_water_mark_is_refused() -> None:
    """The ratchet: a floor may be raised, never lowered.

    The measured rate clears the lowered floor here, so without this rule the
    regression would land green — which is the exact failure the ceiling exists
    to prevent, performed in a way that reads like maintenance.
    """
    violations = routing.floor_violations(0.86, 0.70, 0.85)
    assert len(violations) == 1
    assert "never lowered" in violations[0]
    assert routing.floor_violations(0.90, 0.90, 0.85) == []


def test_an_undeclared_floor_is_a_violation_that_reports_the_measured_rate() -> None:
    """A gate with no threshold cannot fail, so the absence is itself the finding."""
    violations = routing.floor_violations(0.92, None, None)
    assert len(violations) == 1
    assert "92.0%" in violations[0]
