from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TARGETS = ("AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md")


def _section(target: str, heading: str) -> str:
    text = (ROOT / target).read_text(encoding="utf-8")
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


@pytest.mark.parametrize("target", TARGETS)
def test_an_approved_ci_edit_reuses_approval_after_a_phase_or_agent_change(target: str) -> None:
    rule = _section(target, "Require Explicit Confirmation")
    assert "Reuse it only for the same action and scope" in rule
    assert "Identify that approval" in rule
    assert "A new phase or agent needs no repeat approval" in rule


@pytest.mark.parametrize("target", TARGETS)
def test_a_different_unapproved_ci_edit_still_requires_explicit_approval(target: str) -> None:
    rule = _section(target, "Require Explicit Confirmation")
    assert "These actions need explicit approval" in rule
    assert "only for the same action and scope" in rule
    assert "- edit CI/CD, deployment, infrastructure, ignore or secrets files;" in rule


@pytest.mark.parametrize("target", TARGETS)
def test_safe_read_only_clarification_precedes_a_missing_fact_question(target: str) -> None:
    rule = _section(target, "Decision Protocol")
    assert "Use safe read-only checks to resolve facts first" in rule
    assert "Ask when missing facts, conflicting sources or uncheckable assumptions" in rule
    assert "change the result" in rule


@pytest.mark.parametrize("target", TARGETS)
def test_a_frozen_release_finishes_started_work_and_reports_broader_blockers(target: str) -> None:
    rule = _section(target, "Decision Protocol")
    assert "With frozen scope, finish started work and demonstrated blockers within scope" in rule
    assert "Report broader blockers and ask before expanding" in rule


@pytest.mark.parametrize("target", TARGETS)
def test_a_nonblocking_adjacent_bug_is_recorded_without_implementation(target: str) -> None:
    rule = _section(target, "Decision Protocol")
    assert "Record nonblocking adjacent findings in the tracker without implementing them" in rule
    ownership = _section(target, "Ownership")
    assert (
        "Implement only necessary blockers within scope or separately authorized work" in ownership
    )


@pytest.mark.parametrize("target", TARGETS)
def test_prior_approval_changes_neither_confirmation_categories_nor_gate_protections(
    target: str,
) -> None:
    rule = _section(target, "Require Explicit Confirmation")
    categories = tuple(line for line in rule.splitlines() if line.startswith("- "))
    assert categories == (
        "- force-push, rewrite history, or discard uncommitted work: `git reset --hard`, "
        "`git checkout -- <path>`, `git restore`, `git stash`, `git clean`, `rm -rf`;",
        "- read or write a `.env*` file;",
        "- delete files, branches or data outside the task;",
        "- edit CI/CD, deployment, infrastructure, ignore or secrets files;",
        "- add, remove or upgrade a dependency;",
        "- make a network call outside the task.",
    )
    assert "Tool access does not grant approval" in rule
    assert "Apply this list even when commands run unblocked" in rule
    assert "never defeat a gate" in rule
    assert "Do not skip or weaken a test, a lint or a type check" in rule
    assert "do not bypass a hook (`--no-verify`, `--no-gpg-sign`)" in rule
