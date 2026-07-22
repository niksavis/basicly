"""Regression tests for the release changelog generator's insertion seam (basicly-pui7)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


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


def _heading_order(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


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
