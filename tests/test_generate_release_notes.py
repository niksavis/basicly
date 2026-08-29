"""The release page is the summary of a changelog section, never the section (basicly-xsdvp6).

Two readers must agree on what a summary is: `.scripts/generate_release_notes.py`, which
builds the page after the tag, and `release._summary_missing`, which refuses a cut with none
before the tag. Both are exercised here on the same bodies, and the page is rendered over the
repository's own v0.10.0 section - the one the owner called a wall of text.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from basicly import release
from tests.test_release import repo

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".scripts" / "generate_release_notes.py"
REPO_URL = "https://github.com/niksavis/basicly"
# The imported fixture is what lets this file run a real release; ruff reads the export.
__all__ = ["repo"]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


notes = _load(SCRIPT, "generate_release_notes")

SECTION = """# Changelog

## [Unreleased]

## v1.2.0 - 2026-09-01

Delta: v1.1.0..v1.2.0

One paragraph a human wrote about what this release means.

- A themed highlight, still above the first category heading.

### Added

- **A new thing.** Detail (basicly-aaa).
- **BREAKING: an old flag is gone.** Pass the new one
  instead (basicly-bbb).

### Fixed

- **A fix nobody needs on the page.** Detail (basicly-ccc).
- **BREAKING: a default changed.** Detail (basicly-ddd).

## v1.1.0 - 2026-08-01

Older.
"""


def _body() -> tuple[str, list[str]]:
    return notes.section(SECTION.splitlines(), "v1.2.0")


def test_the_section_is_cut_at_the_next_release_heading_with_its_date() -> None:
    date, body = _body()
    assert date == "2026-09-01"
    assert body[-1] == "" and "Older." not in body and "- **A fix nobody needs" in "\n".join(body)


def test_an_undated_or_absent_heading_is_refused_by_name() -> None:
    for text, tag in (("## v1.2.0\n\nbody\n", "v1.2.0"), (SECTION, "v9.9.9")):
        try:
            notes.section(text.splitlines(), tag)
        except ValueError as exc:
            assert tag in str(exc)
        else:
            raise AssertionError(f"{tag} was accepted")


def test_the_summary_is_the_prose_above_the_first_category_without_the_delta_line() -> None:
    _, body = _body()
    prose = notes.summary(body)
    assert prose[0].startswith("One paragraph") and prose[-1].startswith("- A themed highlight")
    assert not any(line.startswith("Delta:") for line in prose)


def test_counts_and_breaking_entries_come_from_every_category() -> None:
    _, body = _body()
    assert notes.counts(body) == {"Added": 2, "Fixed": 2}
    broken = notes.breaking(body)
    assert [entry[0][:30] for entry in broken] == [
        "- **BREAKING: an old flag is g",
        "- **BREAKING: a default change",
    ]
    assert broken[0][1].startswith("  instead")


def test_the_page_carries_summary_counts_link_breaking_and_install_and_nothing_else() -> None:
    date, body = _body()
    page = notes.render("v1.2.0", date, body, REPO_URL)
    assert page.startswith("## v1.2.0 - 2026-09-01\n\nOne paragraph")
    assert "4 entries - 2 added, 2 fixed. Full detail in [CHANGELOG.md]" in page
    assert f"{REPO_URL}/blob/v1.2.0/CHANGELOG.md#v120---2026-09-01" in page
    assert page.count("BREAKING") == 2 and "A fix nobody needs" not in page
    assert "A new thing" not in page
    assert page.rstrip().endswith(f"uvx --from git+{REPO_URL}@v1.2.0 basicly install\n```")


def test_the_repositorys_own_v0_10_0_section_renders_under_the_cap_with_every_breaking_entry() -> (
    None
):
    """The regression the record was filed on: 2,326 lines in, a page a human reads out."""
    lines = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    date, body = notes.section(lines, "v0.10.0")
    page = notes.render("v0.10.0", date, body, REPO_URL)
    fixed = body.index("### Fixed")
    first_fix = next(line for line in body[fixed:] if line.startswith("- **"))

    assert len("\n".join(body)) > 20_000 and len(page) < 6_000
    assert page.count("- **BREAKING") == len(notes.breaking(body)) == 2
    assert first_fix not in page
    assert notes.NO_SUMMARY in page


def test_the_refusal_and_the_page_agree_on_what_a_summary_is() -> None:
    """One rule, two readers: a body the page would call empty is a body the cut refuses."""
    cases = {
        "none": ["", "### Added", "", "- x"],
        "delta only": ["", "Delta: v1..v2", "", "### Added"],
        "prose": ["", "A sentence.", "", "### Added"],
        "no categories": ["", "Just prose."],
    }
    for name, body in cases.items():
        lines = ["# Changelog", "", release.UNRELEASED_HEADING, *body, "", "## v0.1.0 - 2026-01-01"]
        refused = release._summary_missing(lines) is not None
        assert refused == (not notes.summary(body)), name


def test_a_cut_with_no_summary_under_unreleased_is_refused_before_anything_is_written(
    repo: Path,
) -> None:
    (repo / release.CHANGELOG_FILE).write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- an entry with no summary above it\n",
        encoding="utf-8",
    )
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused
    assert any("carries no summary" in reason for reason in result.refusals)
    assert not result.tagged


def test_the_script_runs_as_the_workflow_runs_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "CHANGELOG.md").write_text(SECTION, encoding="utf-8")

    code = notes.main(["--tag", "v1.2.0", "--repo-url", REPO_URL + "/", "--root", str(tmp_path)])

    assert code == 0 and capsys.readouterr().out.startswith("## v1.2.0 - 2026-09-01")
    assert notes.main(["--tag", "v0.0.1", "--repo-url", REPO_URL, "--root", str(tmp_path)]) == 1
