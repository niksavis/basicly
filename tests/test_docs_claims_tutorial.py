from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_docs_claims import REPO, claims

if TYPE_CHECKING:
    import pytest

TUTORIAL_MD = "docs/tutorial/first-loop.md"


def _run(root: Path, mode: str) -> int:
    return claims.main([mode, "--root", str(root)])


def test_the_released_version_comes_from_the_newest_changelog_heading() -> None:

    version = claims._released_version(REPO)
    assert version.count(".") == 2
    assert f"## v{version} - " in (REPO / "CHANGELOG.md").read_text(encoding="utf-8")


def test_a_tutorial_install_pin_behind_the_release_fails_naming_the_line(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    path = work_repo / TUTORIAL_MD
    current = claims._released_version(work_repo)
    text = path.read_text(encoding="utf-8")
    assert f"@v{current}" in text
    path.write_text(text.replace(f"@v{current}", "@v0.0.1"), encoding="utf-8")
    first = next(
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "@v0.0.1" in line
    )

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[tutorial-versions]" in err
    assert f"{TUTORIAL_MD}:{first}: install pin @v0.0.1 is not the released v{current}" in err


def test_a_tutorial_claim_naming_an_older_version_fails_naming_the_line(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    path = work_repo / TUTORIAL_MD
    current = claims._released_version(work_repo)
    text = path.read_text(encoding="utf-8")
    older = text.replace(
        f"basicly {current} in a real terminal", "basicly 0.0.1 in a real terminal"
    )
    assert older != text, "the control: the claim names the released version to begin with"
    path.write_text(older, encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert f"transcript quotes basicly 0.0.1, not the released {current}" in err


def test_the_quoted_output_names_no_version_for_a_tool_to_rewrite(work_repo: Path) -> None:

    text = (work_repo / TUTORIAL_MD).read_text(encoding="utf-8")

    assert "engine: basicly ..." in text, (
        "the page elides the version in what it presents as printed, so a release pins "
        "the command a reader types and never rewrites a line quoted as output"
    )
    assert "catalog: installed by basicly ... at ..." in text


def test_a_changelog_with_no_release_heading_is_a_loud_failure(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = work_repo / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("\n## v", "\n## release v"), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    assert "no `## vX.Y.Z - YYYY-MM-DD` release heading found" in capsys.readouterr().err
