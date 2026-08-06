"""Regression tests for the release changelog generator's insertion seam (basicly-pui7)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from basicly import release


def _load_module():
    """Load the generate-release-changelog script module from its path."""
    script_path = Path(__file__).resolve().parents[1] / ".scripts" / "generate_release_changelog.py"
    spec = importlib.util.spec_from_file_location("generate_release_changelog", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CHANGELOG_WITH_UNRELEASED = (
    "# Changelog\n"
    "\n"
    "All notable changes to this project are documented here.\n"
    "\n"
    "## [Unreleased]\n"
    "\n"
    "## v0.5.1 - 2026-07-20\n"
    "\n"
    "Delta: v0.5.0..v0.5.1\n"
    "\n"
    "### Fixed\n"
    "\n"
    "- an earlier fix (abc123)\n"
)


_CURATED_NOTE = "- **A curated highlight** somebody wrote by hand (`basicly-abcd`)."

_CHANGELOG_WITH_CURATED_UNRELEASED = _CHANGELOG_WITH_UNRELEASED.replace(
    "## [Unreleased]\n\n",
    f"## [Unreleased]\n\n### Added\n\n{_CURATED_NOTE}\n\n",
)


def _heading_order(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


def _section_body(text: str, heading: str) -> list[str]:
    """Return the lines of the named section, exclusive of its own heading."""
    lines = text.splitlines()
    start = lines.index(heading)
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return [line for line in body if line.strip()]


def test_new_section_lands_after_unreleased_not_above_it() -> None:
    """The new dated section is inserted below [Unreleased], which stays pinned on top."""
    m = _load_module()
    section = m._build_section("v0.6.0", "2026-07-22", "v0.5.1", ["add a thing (def456)"])
    out = m._upsert_section(_CHANGELOG_WITH_UNRELEASED, "v0.6.0", section)

    assert _heading_order(out) == [
        "## [Unreleased]",
        "## v0.6.0 - 2026-07-22",
        "## v0.5.1 - 2026-07-20",
    ]


def test_no_consecutive_blank_lines_at_the_seam() -> None:
    """The generated changelog has no double blanks (markdownlint MD012)."""
    m = _load_module()
    section = m._build_section("v0.6.0", "2026-07-22", "v0.5.1", ["add a thing (def456)"])
    out = m._upsert_section(_CHANGELOG_WITH_UNRELEASED, "v0.6.0", section)

    assert "\n\n\n" not in out


def test_pre_existing_double_blank_is_collapsed() -> None:
    """A double blank already present in the source is normalized away, not preserved."""
    m = _load_module()
    dirty = _CHANGELOG_WITH_UNRELEASED.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n\n")
    assert "\n\n\n" in dirty  # the source really does carry a double blank
    section = m._build_section("v0.6.0", "2026-07-22", "v0.5.1", ["x (def456)"])
    out = m._upsert_section(dirty, "v0.6.0", section)

    assert "\n\n\n" not in out


def test_fallback_inserts_after_intro_when_no_unreleased_section() -> None:
    """With no [Unreleased] heading, the new section goes above the newest release."""
    m = _load_module()
    no_unreleased = _CHANGELOG_WITH_UNRELEASED.replace("## [Unreleased]\n\n", "")
    section = m._build_section("v0.6.0", "2026-07-22", "v0.5.1", ["y (def456)"])
    out = m._upsert_section(no_unreleased, "v0.6.0", section)

    assert _heading_order(out) == ["## v0.6.0 - 2026-07-22", "## v0.5.1 - 2026-07-20"]
    assert "\n\n\n" not in out


def test_rerunning_same_tag_replaces_in_place_keeping_order() -> None:
    """Re-running for an existing tag replaces its section without duplicating or reordering."""
    m = _load_module()
    first = m._upsert_section(
        _CHANGELOG_WITH_UNRELEASED,
        "v0.6.0",
        m._build_section("v0.6.0", "2026-07-22", "v0.5.1", ["first (def456)"]),
    )
    second = m._upsert_section(
        first,
        "v0.6.0",
        m._build_section("v0.6.0", "2026-07-22", "v0.5.1", ["second (999999)"]),
    )

    assert _heading_order(second) == [
        "## [Unreleased]",
        "## v0.6.0 - 2026-07-22",
        "## v0.5.1 - 2026-07-20",
    ]
    assert "second (999999)" in second
    assert "first (def456)" not in second
    assert "\n\n\n" not in second


def test_curated_unreleased_body_is_promoted_into_the_dated_section() -> None:
    """The notes a human wrote land in the tagged section, not stranded under Unreleased.

    The release commit and the annotated tag are one step and the release workflow
    reads CHANGELOG.md from the tagged commit, so a curated body left behind is
    never published (basicly-m3od.1).
    """
    m = _load_module()
    out = m.upsert_release_section(
        _CHANGELOG_WITH_CURATED_UNRELEASED, "v0.6.0", "2026-07-22", "v0.5.1", ["raw (def456)"]
    )

    assert _section_body(out, "## v0.6.0 - 2026-07-22") == [
        "Delta: v0.5.1..v0.6.0",
        "### Added",
        _CURATED_NOTE,
    ]
    assert "raw (def456)" not in out
    assert "### Changes" not in out


def test_promotion_empties_unreleased_and_keeps_it_pinned_on_top() -> None:
    """Unreleased survives as an empty heading, ready for the next cycle."""
    m = _load_module()
    out = m.upsert_release_section(
        _CHANGELOG_WITH_CURATED_UNRELEASED, "v0.6.0", "2026-07-22", "v0.5.1", ["raw (def456)"]
    )

    assert _heading_order(out) == [
        "## [Unreleased]",
        "## v0.6.0 - 2026-07-22",
        "## v0.5.1 - 2026-07-20",
    ]
    assert _section_body(out, "## [Unreleased]") == []
    assert "\n\n\n" not in out


def test_empty_unreleased_still_generates_the_commit_delta_skeleton() -> None:
    """With nothing curated, the commit-subject list remains the traceability fallback."""
    m = _load_module()
    out = m.upsert_release_section(
        _CHANGELOG_WITH_UNRELEASED, "v0.6.0", "2026-07-22", "v0.5.1", ["raw (def456)"]
    )

    assert _section_body(out, "## v0.6.0 - 2026-07-22") == [
        "Delta: v0.5.1..v0.6.0",
        "### Changes",
        "- raw (def456)",
    ]


def test_rerun_does_not_replace_a_curated_section_with_a_commit_dump() -> None:
    """A retried release keeps the promoted notes; only a generated section is refreshed."""
    m = _load_module()
    promoted = m.upsert_release_section(
        _CHANGELOG_WITH_CURATED_UNRELEASED, "v0.6.0", "2026-07-22", "v0.5.1", ["raw (def456)"]
    )
    again = m.upsert_release_section(
        promoted, "v0.6.0", "2026-07-22", "v0.5.1", ["second (999999)"]
    )

    assert _section_body(again, "## v0.6.0 - 2026-07-22") == [
        "Delta: v0.5.1..v0.6.0",
        "### Added",
        _CURATED_NOTE,
    ]
    assert "second (999999)" not in again
    assert _heading_order(again) == _heading_order(promoted)


def test_the_fragment_assembler_and_the_generator_agree_on_the_unreleased_heading() -> None:
    """One heading string, two modules: a drift here loses every lane's fragment.

    ``release._assemble_fragments`` folds the fragments into this heading's body and
    this generator promotes that body into the dated section. If the two spellings
    ever diverge the release refuses (no heading to fold into) or, worse, assembles
    into a body nothing promotes — so pin them to each other rather than to a literal.
    """
    assert release.UNRELEASED_HEADING == _load_module().UNRELEASED_HEADING
