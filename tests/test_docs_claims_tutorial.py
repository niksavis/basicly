"""The tutorial's version currency, split off `test_docs_claims.py` (basicly-c7nvs2).

Its own aspect file under the `test_<module>_<aspect>.py` form `check_test_naming.py`
sanctions, because four tests took the parent 3721 -> 4387 tokens against a 4000-token cap
and the alternative was a permanent waiver on a file that grows with every claim added.

What binds here is *one* claim family: every version `docs/tutorial/` names has to be the
release a consumer can install. The parent keeps the generated-block machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

# The sibling owns loading the script (it is not a package) and this needs the same module
# object, so the loader stays in one place rather than being copied for a second import.
from tests.test_docs_claims import REPO, claims

if TYPE_CHECKING:
    import pytest

TUTORIAL_MD = "docs/tutorial/first-loop.md"


def _run(root: Path, mode: str) -> int:
    """Invoke the script's entry point against *root*."""
    return claims.main([mode, "--root", str(root)])


def test_the_released_version_comes_from_the_newest_changelog_heading() -> None:
    """The positive control for the reader: it must find a version at all.

    Keyed on the newest ``## vX.Y.Z - date`` rather than on ``__version__`` because a pin
    a consumer copies has to name a release that exists, which is what the changelog
    records and the version constant does not.
    """
    version = claims._released_version(REPO)
    assert version.count(".") == 2
    assert f"## v{version} - " in (REPO / "CHANGELOG.md").read_text(encoding="utf-8")


def test_a_tutorial_install_pin_behind_the_release_fails_naming_the_line(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lag `basicly-c7nvs2` measured: the page pinned @v0.8.0 against a released v0.11.0.

    Nothing detected it, because the tutorial is exempt from ``release.PIN_FILES`` and the
    exemption came with no gate.
    """
    path = work_repo / TUTORIAL_MD
    current = claims._released_version(work_repo)
    text = path.read_text(encoding="utf-8")
    assert f"@v{current}" in text
    path.write_text(text.replace(f"@v{current}", "@v0.0.1"), encoding="utf-8")
    # Derived, not a snapshot: the number reported has to be the line the pin is on, and a
    # literal would break on any paragraph added above it instead.
    first = next(
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "@v0.0.1" in line
    )

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[tutorial-versions]" in err
    assert f"{TUTORIAL_MD}:{first}: install pin @v0.0.1 is not the released v{current}" in err


def test_a_tutorial_transcript_quoting_an_older_engine_fails_naming_the_line(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The half a mechanical pin bump would have left behind.

    The page's transcripts quote the engine version they were recorded against, so a pin
    bumped without a re-recording leaves the install line and the outputs disagreeing —
    which is the reason the tutorial was exempted from the pin set in the first place.
    """
    path = work_repo / TUTORIAL_MD
    current = claims._released_version(work_repo)
    text = path.read_text(encoding="utf-8")
    older = text.replace(f"engine: basicly {current}", "engine: basicly 0.0.1")
    path.write_text(older, encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert f"transcript quotes basicly 0.0.1, not the released {current}" in err


def test_a_changelog_with_no_release_heading_is_a_loud_failure(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed: an unreadable changelog must not report every pin current."""
    path = work_repo / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("\n## v", "\n## release v"), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    assert "no `## vX.Y.Z - YYYY-MM-DD` release heading found" in capsys.readouterr().err
