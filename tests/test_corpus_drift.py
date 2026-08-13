"""Tests for problem-statement drift detection (basicly-b9ef).

The rule under test is deliberately narrow: a bullet is accounted for when it *names* a
child of its own epic or admits it is unverified, never when it resembles one. So these
pin both directions — an unmarked bullet is flagged once a child has closed, and a bullet
that names a child is left alone however stale it looks — and the whole-id test pins the
one substring hazard in the middle (`.5` inside `.52`).

The historical case is a fixture rather than a live tracker read: `basicly-u2hl`'s
problem statement as it stood when two escalations reasoned from its refuted `park`
bullet. Asserting against the live bead would make these tests a report on today's
tracker instead of on the rule.
"""

from __future__ import annotations

from basicly import corpus_drift

# `basicly-u2hl` as of 2026-08-08, before commit `c71940a` corrected it. Four of these
# eight bullets had been superseded by its own closed children and the `park` one was
# refuted outright.
STALE_EPIC = """## Context

Headline measured gaps, each with evidence in the requirements document:

- decompose emits no dependency graph; ordering is derived from scope overlap only
- supervised rework dispatches a fresh agent instead of repairing in place, and the
  prompt carries none of the gate's findings
- `park` (Hold) exists as a word and re-admits the lane - a fail-open on a human
  control point

## Acceptance Criteria

- Given the requirements document when this epic is decomposed then every state is gated
"""

CORRECTED_EPIC = """## Context

Headline measured gaps, each with evidence in the requirements document:

- decompose emits no dependency graph; ordering is derived from scope overlap only
- SHIPPED 2026-08-08 (basicly-u2hl.4): repair now runs in the lane's own worktree
- CORRECTED 2026-08-08 (shipped, basicly-u2hl.3): the `park` claim here was REFUTED
"""

CLOSED_CHILDREN = {
    "basicly-u2hl.3": "closed",
    "basicly-u2hl.4": "closed",
    "basicly-u2hl.54": "open",
}


def test_a_bullet_naming_no_child_is_flagged_once_a_child_has_closed() -> None:
    """The measured defect: a superseded bullet reaching a decider unmarked."""
    findings = corpus_drift.epic_findings("basicly-u2hl", STALE_EPIC, CLOSED_CHILDREN)
    assert [finding.bullet[:20] for finding in findings] == [
        "decompose emits no d",
        "supervised rework di",
        "`park` (Hold) exists",
    ]
    assert findings[0].closed_children == ("basicly-u2hl.3", "basicly-u2hl.4")


def test_a_bullet_naming_a_child_is_accounted_for() -> None:
    """The hand correction's own form clears the gate; the unmarked bullet still does not."""
    findings = corpus_drift.epic_findings("basicly-u2hl", CORRECTED_EPIC, CLOSED_CHILDREN)
    assert len(findings) == 1
    assert findings[0].bullet.startswith("decompose emits")
    assert findings[0].accounted_children == ("basicly-u2hl.3", "basicly-u2hl.4")


def test_an_unverified_mark_accounts_for_a_bullet_nobody_re_established() -> None:
    """The cheap escape hatch is also the safe one: unverified is not a fact."""
    marked = "## Context\n\n- UNVERIFIED 2026-08-13: retrospective does not exist in the engine\n"
    assert corpus_drift.epic_findings("epic", marked, CLOSED_CHILDREN) == ()


def test_nothing_is_flagged_until_a_child_closes() -> None:
    """With nothing closed, no claim can have been superseded by a child."""
    open_children = {"epic.1": "open", "epic.2": "in_progress"}
    assert corpus_drift.epic_findings("epic", STALE_EPIC, open_children) == ()
    assert corpus_drift.epic_findings("epic", STALE_EPIC, {}) == ()


def test_a_child_id_is_matched_whole_not_by_substring() -> None:
    """`.52` must not read as naming `.5` — they are different children."""
    description = "## Context\n\n- module length is now gated (basicly-u2hl.52)\n"
    children = {"basicly-u2hl.5": "closed", "basicly-u2hl.52": "open"}
    findings = corpus_drift.epic_findings("basicly-u2hl", description, children)
    assert findings == ()
    other = "## Context\n\n- module length is now gated (basicly-u2hl.512)\n"
    assert len(corpus_drift.epic_findings("basicly-u2hl", other, children)) == 1


def test_only_context_bullets_outside_a_fence_are_claims() -> None:
    """A quoted claim inside a fence is evidence about a bullet, not a bullet."""
    description = """## Context

- a live claim nobody has marked

```text
- a claim quoted from somewhere else
```

## Acceptance Criteria

- Given an epic when a child closes then the statement is reconciled
"""
    findings = corpus_drift.epic_findings("epic", description, {"epic.1": "closed"})
    assert [finding.bullet for finding in findings] == ["a live claim nobody has marked"]


def test_annotate_marks_the_bullet_in_place_and_leaves_the_rest() -> None:
    """A decider reads top to bottom, so the mark has to be on the claim itself."""
    annotated = corpus_drift.annotate(CORRECTED_EPIC, CLOSED_CHILDREN)
    assert "- [UNVERIFIED — 2 of this epic's children have closed" in annotated
    assert "] decompose emits no dependency graph" in annotated
    assert "- SHIPPED 2026-08-08 (basicly-u2hl.4): repair now runs" in annotated
    assert corpus_drift.annotate(CORRECTED_EPIC, {}) == CORRECTED_EPIC


def test_children_are_read_from_both_of_brs_dependency_spellings() -> None:
    """`br show` spells an edge `id`/`dependency_type`; the export spells it the other way."""
    record = {
        "id": "epic",
        "dependents": [
            {"id": "epic.1", "status": "closed", "dependency_type": "parent-child"},
            {"id": "other", "status": "open", "dependency_type": "blocks"},
        ],
    }
    assert corpus_drift.children_of_record(record) == {"epic.1": "closed"}
    export = [
        {
            "id": "epic.1",
            "status": "closed",
            "dependencies": [
                {"depends_on_id": "epic", "type": "parent-child"},
            ],
        },
        {
            "id": "epic.2",
            "status": "open",
            "dependencies": [
                {"depends_on_id": "epic", "type": "blocks"},
            ],
        },
    ]
    assert corpus_drift.children_by_parent(export) == {"epic": {"epic.1": "closed"}}
