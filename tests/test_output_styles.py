from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from basicly import output_styles
from basicly.schema import ValidationError

REPO = Path(__file__).parent.parent

SOURCE = """\
schema_version: 1
name: Tired Engineer
description: Verdict first.
keep_coding_instructions: true
body: |
  ## Shape

  Verdict first.
"""


def _write(repo: Path, slug: str, source: str = SOURCE) -> Path:
    path = repo / output_styles.STYLES_SOURCE_DIR / slug / output_styles.STYLE_SOURCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(tmp_path, "tired-engineer")
    return tmp_path


def _roots(repo: Path) -> list[Path]:
    return output_styles.resolve_style_roots(repo, None)


def _projected(repo: Path) -> Path:
    return repo / ".claude/output-styles/tired-engineer.md"


def test_the_slug_comes_from_the_directory_not_the_name(repo: Path) -> None:
    style = output_styles.discover_styles(repo)[0]

    assert style.slug == "tired-engineer"
    assert style.name == "Tired Engineer"


def test_a_repo_with_no_styles_discovers_none(tmp_path: Path) -> None:
    assert output_styles.discover_styles(tmp_path) == []


def test_the_projection_carries_the_frontmatter_the_host_reads(repo: Path) -> None:
    rendered = output_styles.render_style_md(output_styles.discover_styles(repo)[0])

    assert rendered.startswith("---\n")
    assert "name: Tired Engineer\n" in rendered
    assert "description: Verdict first.\n" in rendered
    assert "keep-coding-instructions: true\n" in rendered


def test_the_generated_marker_sits_inside_the_frontmatter(repo: Path) -> None:
    rendered = output_styles.render_style_md(output_styles.discover_styles(repo)[0])
    head, _, body = rendered.partition("\n---\n")

    assert output_styles.GENERATED_MARKER in head
    assert output_styles.GENERATED_MARKER not in body


def test_the_marker_is_a_yaml_comment_so_it_never_reaches_the_prompt(repo: Path) -> None:
    rendered = output_styles.render_style_md(output_styles.discover_styles(repo)[0])
    front = rendered.split("---\n")[1]

    assert yaml.safe_load(front) == {
        "name": "Tired Engineer",
        "description": "Verdict first.",
        "keep-coding-instructions": True,
    }


def test_keep_coding_instructions_defaults_to_true(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "s",
        "schema_version: 1\nname: S\ndescription: d\nbody: |\n  text\n",
    )

    assert output_styles.discover_styles(tmp_path)[0].keep_coding_instructions is True


def test_false_is_projected_verbatim(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "s",
        "schema_version: 1\nname: S\ndescription: d\n"
        "keep_coding_instructions: false\nbody: |\n  text\n",
    )

    rendered = output_styles.render_style_md(output_styles.discover_styles(tmp_path)[0])

    assert "keep-coding-instructions: false\n" in rendered


def test_a_missing_body_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "s", "schema_version: 1\nname: S\ndescription: d\n")

    with pytest.raises(ValidationError, match="'body' must be a non-empty string"):
        output_styles.discover_styles(tmp_path)


def test_a_non_boolean_keep_flag_is_refused(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "s",
        "schema_version: 1\nname: S\ndescription: d\n"
        "keep_coding_instructions: yes please\nbody: |\n  text\n",
    )

    with pytest.raises(ValidationError, match="must be true or false"):
        output_styles.discover_styles(tmp_path)


def test_a_source_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "s", "- a list\n")

    with pytest.raises(ValidationError, match="must be a mapping"):
        output_styles.discover_styles(tmp_path)


def test_the_build_writes_the_projection(repo: Path) -> None:
    result, pruned = output_styles.sync_styles(repo, _roots(repo))

    assert pruned == []
    assert result.written == [_projected(repo)]
    assert _projected(repo).read_text(encoding="utf-8").startswith("---\n")


def test_the_build_is_idempotent(repo: Path) -> None:
    output_styles.sync_styles(repo, _roots(repo))

    result, _ = output_styles.sync_styles(repo, _roots(repo))

    assert result.written == []
    assert result.unchanged == [_projected(repo)]


def test_the_check_is_clean_after_a_build(repo: Path) -> None:
    output_styles.sync_styles(repo, _roots(repo))

    assert output_styles.check_synced_styles(repo, _roots(repo)) == []


def test_the_check_reports_a_projection_that_was_never_built(repo: Path) -> None:
    mismatches = output_styles.check_synced_styles(repo, _roots(repo))

    assert mismatches == [(_projected(repo), "missing")]


def test_the_check_reports_a_hand_edit(repo: Path) -> None:
    output_styles.sync_styles(repo, _roots(repo))
    target = _projected(repo)
    target.write_text(target.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")

    assert output_styles.check_synced_styles(repo, _roots(repo)) == [(target, "content mismatch")]


def test_the_check_reports_a_projection_with_no_source(repo: Path) -> None:
    output_styles.sync_styles(repo, _roots(repo))
    orphan = repo / ".claude/output-styles/hand-written.md"
    orphan.write_text("---\nname: X\n---\n", encoding="utf-8")

    mismatches = output_styles.check_synced_styles(repo, _roots(repo))

    assert [path for path, _ in mismatches] == [orphan]
    assert mismatches[0][1].startswith(output_styles.UNMANAGED_REASON_PREFIX)


def test_a_non_markdown_file_beside_the_projection_is_ignored(repo: Path) -> None:
    output_styles.sync_styles(repo, _roots(repo))
    (repo / ".claude/output-styles/notes.txt").write_text("scratch", encoding="utf-8")

    assert output_styles.check_synced_styles(repo, _roots(repo)) == []


def test_an_excluded_technology_prunes_the_projection(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "s",
        "schema_version: 1\nname: S\ndescription: d\ntechnologies: [rust]\nbody: |\n  text\n",
    )
    roots = _roots(tmp_path)
    output_styles.sync_styles(tmp_path, roots)
    target = tmp_path / ".claude/output-styles/s.md"
    assert target.exists()

    _, pruned = output_styles.sync_styles(tmp_path, roots, selection=frozenset({"python"}))

    assert pruned == [target]
    assert not target.exists()


def test_the_committed_style_round_trips_through_the_source() -> None:
    styles = output_styles.discover_styles(REPO)

    assert [style.slug for style in styles] == ["tired-engineer"]
    projected = (REPO / ".claude/output-styles/tired-engineer.md").read_text(encoding="utf-8")
    assert output_styles.render_style_md(styles[0]) == projected


def test_the_committed_style_keeps_the_coding_instructions() -> None:
    assert output_styles.discover_styles(REPO)[0].keep_coding_instructions is True
