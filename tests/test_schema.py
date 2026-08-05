"""Tests for the shared schema vocabulary and its user-facing error rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly.schema import ValidationError, display_path


def test_a_source_inside_the_repo_root_is_reported_relative(tmp_path: Path) -> None:
    """The absolute form leaks a home directory into anything pasted into an issue."""
    source = tmp_path / ".basicly/core/agents/test-runner/agent.yaml"
    exc = ValidationError("unknown technologies: notatechnology", source, repo_root=tmp_path)

    assert (
        str(exc)
        == ".basicly/core/agents/test-runner/agent.yaml: unknown technologies: notatechnology"
    )


def test_a_source_outside_the_repo_root_keeps_its_absolute_path(tmp_path: Path) -> None:
    """A `..` path would misleadingly read as repo-relative, so the absolute form stays."""
    outside = tmp_path / "elsewhere/.basicly/core/skills/s/skill.yaml"
    exc = ValidationError("invalid YAML: bad", outside, repo_root=tmp_path / "repo")

    assert str(exc) == f"{outside}: invalid YAML: bad"


def test_the_repo_root_defaults_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli._repo_root` is the working directory, so a raise site need not pass one."""
    monkeypatch.chdir(tmp_path)
    exc = ValidationError("missing required field 'name'", tmp_path / ".basicly/core/x/skill.yaml")

    assert str(exc) == ".basicly/core/x/skill.yaml: missing required field 'name'"


def test_a_pathless_error_renders_the_message_alone() -> None:
    """`planner` raises without a source, so there is nothing to make relative."""
    assert str(ValidationError("targets is empty; nothing to project")) == (
        "targets is empty; nothing to project"
    )


def test_the_rendered_separator_is_a_forward_slash_on_every_platform(tmp_path: Path) -> None:
    """Windows would spell the same finding with backslashes and diverge from lint output."""
    rendered = display_path(tmp_path / ".basicly/core/fragments/f.fragment.yaml", tmp_path)

    assert rendered == ".basicly/core/fragments/f.fragment.yaml"
