"""The assembled changelog must account for every record its fragments were named for."""

from __future__ import annotations

from pathlib import Path

from basicly import release


def _fragment(repo: Path, name: str, body: str) -> release.ChangelogFragment:
    """Write one fragment under the fragment directory and return its descriptor."""
    directory = repo / release.FRAGMENT_DIR
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")
    return release.ChangelogFragment(path=release.FRAGMENT_DIR / name, category="fixed")


def test_a_fragment_that_cites_its_record_by_filename_alone_gains_the_citation(
    tmp_path: Path,
) -> None:
    """basicly-k8b75o: the filename is deleted by assembly, so the body has to carry the id."""
    silent = _fragment(tmp_path, "fx-1.fixed.md", "- **A fix** nobody cited.\n")
    spoken = _fragment(tmp_path, "fx-2.fixed.md", "- **Another fix** (fx-2).\n")
    fenced = _fragment(tmp_path, "fx-3.fixed.md", "- **A fix with output**\n\n```text\nok\n```\n")

    merged = release._merge_unreleased(tmp_path, [], (silent, spoken, fenced))

    assert "- **A fix** nobody cited. (fx-1)" in merged
    assert "- **Another fix** (fx-2)." in merged
    assert merged[-2:] == ["```", "  (fx-3)"]


def test_the_assembled_changelog_accounts_for_the_records_once_the_files_are_gone(
    tmp_path: Path,
) -> None:
    """The gate reads CHANGELOG.md after assembly; it must find what the filenames said."""
    silent = _fragment(tmp_path, "fx-1.fixed.md", "- **A fix** nobody cited.\n")
    spoken = _fragment(tmp_path, "fx-2.fixed.md", "- **Another fix** (fx-2).\n")
    merged = release._merge_unreleased(tmp_path, [], (silent, spoken))
    (tmp_path / release.CHANGELOG_FILE).write_text("\n".join(merged) + "\n", encoding="utf-8")
    for fragment in (silent, spoken):
        (tmp_path / fragment.path).unlink()

    accounted = release.accounted_records(tmp_path, ["fx-1", "fx-2"])

    assert accounted == {"fx-1", "fx-2"}
