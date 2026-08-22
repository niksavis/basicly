"""Naming the check that refused, from a gated command's own output (basicly-fi1i7z).

Every fixture here is **observed** rather than composed: the chain text is what
``uv run pre-commit run --all-files`` printed in this worktree on 2026-08-22, and the
``release-notes`` lines are what ``.scripts/ratchet.py`` ``report`` printed when driven
with a real ``_owes`` finding. An invented fixture is how a parser comes to key on text
its producer never emits.

Two properties carry the bug and both need the *mid-chain* shape to bind at all: a
failure followed by later passes, and a failing hook whose own output ends on something
uninformative. A single-hook fixture passes under the defective code.
"""

from __future__ import annotations

from pathlib import Path

from basicly import gate_failure

# The failing hook, then hooks that pass after it. `protect-generated-commit` runs last
# and passed, and was what the salvage commit reported as its rejection reason.
MIXED_CHAIN = """markdownlint.............................................................Failed
- hook id: markdownlint
- exit code: 1

markdownlint-cli2 v0.23.0 (markdownlint v0.41.0)
Linting: 246 file(s)
Summary: 2 error(s)
note.md:1:1 error MD018/no-missing-space-atx No space after hash on atx style heading

identity-guard...........................................................Passed
protect-generated-commit.................................................Passed
"""

# This repo's own hook wraps the whole verify suite, so its block ends on a list of check
# names while the answer sits earlier. The tail heuristic this refuted was the first design.
SUITE_CHAIN = """pre-commit-script........................................................Failed
- hook id: pre-commit-script
- exit code: 1

FAILED: comment-density (1.07s)
checks failed: 28/32 passed in 22.47s (failed: comment-density)
==> ruff
==> mermaid

protect-generated-commit.................................................Passed
"""

# What `ratchet.report` writes for a closed record owing a note, verbatim.
RELEASE_NOTES = (
    "release-notes: basicly-fi1i7z: closed with a `## Scope` naming a shipped path and "
    "no release note\n"
    "release-notes:   write `changelog.d/basicly-fi1i7z.<category>.md`, or declare it "
    "invisible to a consumer\n"
)


def test_the_failing_hook_is_named_and_no_passing_hook_is_quoted() -> None:
    """The salvage defect: the reason came from the last line of the chain, not the failure."""
    summary = gate_failure.summarise(MIXED_CHAIN)
    assert summary is not None
    assert "markdownlint" in summary
    assert "MD018" in summary
    # The whole of the guarantee — a reader must never be sent to audit a check that passed.
    assert "Passed" not in summary
    assert "protect-generated-commit" not in summary


def test_only_the_failing_hook_becomes_a_refusal() -> None:
    """Two hooks passed after the failure; neither is a refusal."""
    assert [r.check for r in gate_failure.refusals(MIXED_CHAIN)] == ["markdownlint"]


def test_the_reason_is_the_stated_verdict_not_the_tail_of_the_block() -> None:
    """A hook that wraps a suite states its verdict before it lists what it ran."""
    (refusal,) = gate_failure.refusals(SUITE_CHAIN)
    assert refusal.check == "pre-commit-script"
    assert "checks failed: 28/32" in refusal.reason
    assert "==> mermaid" not in refusal.reason


def test_a_failure_with_no_hook_chain_is_left_to_its_caller() -> None:
    """`git rev-parse` failing has no check to name, and inventing one is the same defect."""
    assert gate_failure.ran_hooks("fatal: not a git repository") is False
    assert gate_failure.summarise("fatal: not a git repository") is None


def test_an_unidentifiable_refusal_says_so_and_names_where_the_output_went(
    tmp_path: Path,
) -> None:
    """The honest fallback: a chain ran, nothing in it failed identifiably."""
    chain = "identity-guard...........................................................Passed\n"
    summary = gate_failure.summarise(chain, repo_root=tmp_path)
    assert summary is not None
    assert "names no failing check" in summary
    assert gate_failure.OUTPUT_DUMP.as_posix() in summary
    assert (tmp_path / gate_failure.OUTPUT_DUMP).read_text(encoding="utf-8") == chain


def test_the_dump_is_not_named_when_it_could_not_be_written() -> None:
    """A message naming no path is degraded; losing the refusal to an OSError is not."""
    summary = gate_failure.summarise(
        "identity-guard...........................................................Passed\n"
    )
    assert summary is not None
    assert "it was not captured" in summary
    assert gate_failure.OUTPUT_DUMP.as_posix() not in summary


def test_a_reformatting_hook_reports_the_only_line_it_printed() -> None:
    """A formatter fails with this annotation and frequently no output at all."""
    chain = (
        "ruff-format..............................................................Failed\n"
        "- hook id: ruff-format\n"
        "- files were modified by this hook\n"
    )
    (refusal,) = gate_failure.refusals(chain)
    assert refusal.reason == "files were modified by this hook"


def test_the_remedy_a_ratchet_gate_printed_is_carried() -> None:
    """`release-notes` names the exact file to write; the operator was told only its name."""
    remedy = gate_failure.check_remedy(RELEASE_NOTES, "release-notes")
    assert remedy is not None
    assert "changelog.d/basicly-fi1i7z.<category>.md" in remedy
    # The label is stripped: the caller already prints the check's name.
    assert not remedy.startswith("release-notes:")


def test_a_check_that_printed_no_labelled_line_yields_no_remedy() -> None:
    """Absence is None rather than an empty string, so a caller can fall back."""
    assert gate_failure.check_remedy(RELEASE_NOTES, "module-size") is None
