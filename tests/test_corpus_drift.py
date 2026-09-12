from __future__ import annotations

from basicly import corpus_drift

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
    findings = corpus_drift.epic_findings("basicly-u2hl", STALE_EPIC, CLOSED_CHILDREN)
    assert [finding.bullet[:20] for finding in findings] == [
        "decompose emits no d",
        "supervised rework di",
        "`park` (Hold) exists",
    ]
    assert findings[0].closed_children == ("basicly-u2hl.3", "basicly-u2hl.4")


def test_a_bullet_naming_a_child_is_accounted_for() -> None:
    findings = corpus_drift.epic_findings("basicly-u2hl", CORRECTED_EPIC, CLOSED_CHILDREN)
    assert len(findings) == 1
    assert findings[0].bullet.startswith("decompose emits")
    assert findings[0].accounted_children == ("basicly-u2hl.3", "basicly-u2hl.4")


def test_an_unverified_mark_accounts_for_a_bullet_nobody_re_established() -> None:
    marked = "## Context\n\n- UNVERIFIED 2026-08-13: retrospective does not exist in the engine\n"
    assert corpus_drift.epic_findings("epic", marked, CLOSED_CHILDREN) == ()


def test_nothing_is_flagged_until_a_child_closes() -> None:
    open_children = {"epic.1": "open", "epic.2": "in_progress"}
    assert corpus_drift.epic_findings("epic", STALE_EPIC, open_children) == ()
    assert corpus_drift.epic_findings("epic", STALE_EPIC, {}) == ()


def test_a_child_id_is_matched_whole_not_by_substring() -> None:
    description = "## Context\n\n- module length is now gated (basicly-u2hl.52)\n"
    children = {"basicly-u2hl.5": "closed", "basicly-u2hl.52": "open"}
    findings = corpus_drift.epic_findings("basicly-u2hl", description, children)
    assert findings == ()
    other = "## Context\n\n- module length is now gated (basicly-u2hl.512)\n"
    assert len(corpus_drift.epic_findings("basicly-u2hl", other, children)) == 1


def test_only_context_bullets_outside_a_fence_are_claims() -> None:
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
    annotated = corpus_drift.annotate(CORRECTED_EPIC, CLOSED_CHILDREN)
    assert "- [UNVERIFIED — 2 of this epic's children have closed" in annotated
    assert "] decompose emits no dependency graph" in annotated
    assert "- SHIPPED 2026-08-08 (basicly-u2hl.4): repair now runs" in annotated
    assert corpus_drift.annotate(CORRECTED_EPIC, {}) == CORRECTED_EPIC


def test_children_are_read_from_both_of_brs_dependency_spellings() -> None:
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
