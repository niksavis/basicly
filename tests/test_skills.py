from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from basicly import cli
from basicly.schema import ValidationError
from basicly.skill_source import discover_skills
from basicly.skills import (
    DEFAULT_SKILL_ROOTS,
    GENERATED_MARKER,
    SKILLS_SOURCE_DIR,
    UNMANAGED_REASON_PREFIX,
    SkillDefinition,
    check_synced_skills,
    render_skill_md,
    resolve_skill_roots,
    root_requires_description,
    sync_skills,
)


def _write_skill(
    repo_root: Path, slug: str, name: str, description: str, technologies: str | None = None
) -> None:
    path = repo_root / SKILLS_SOURCE_DIR / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "# yaml-language-server: $schema=../../schemas/skill.schema.json",
            "schema_version: 1",
            f"name: {name}",
            "invocation: model",
            f"description: {description}",
            *([f"technologies: {technologies}"] if technologies else []),
            "instructions: |",
            f"  # {name}",
            "",
            "  ## When To Use",
            "  - Example.",
        ])
        + "\n",
        encoding="utf-8",
    )


def test_render_skill_md_frontmatter_marker_and_body() -> None:
    skill = SkillDefinition("s", "s", "model", "A skill.", "# Body\n\ntext\n", Path("skill.yaml"))
    out = render_skill_md(skill)

    assert (
        out == f"---\nname: s\ndescription: A skill.\n---\n{GENERATED_MARKER}\n\n# Body\n\ntext\n"
    )
    assert (
        out.replace(GENERATED_MARKER + "\n", "", 1)
        == "---\nname: s\ndescription: A skill.\n---\n\n# Body\n\ntext\n"
    )


def test_sync_and_check_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tool-ripgrep", "tool-ripgrep", "Use ripgrep for fast code search.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])

    result, _pruned = sync_skills(tmp_path, roots)

    assert len(result.written) == 1
    target = roots[0] / "tool-ripgrep" / "SKILL.md"
    assert GENERATED_MARKER in target.read_text(encoding="utf-8")
    assert len(check_synced_skills(tmp_path, roots)) == 0

    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_synced_skills(tmp_path, roots) == [(target, "content mismatch")]


def test_sync_skills_filters_and_prunes_by_selection(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    _write_skill(tmp_path, "tool-git", "tool-git", "Git usage.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    excluded = roots[0] / "tool-uv" / "SKILL.md"

    sync_skills(tmp_path, roots)
    assert excluded.is_file()

    selection = frozenset({"zsh"})
    assert check_synced_skills(tmp_path, roots, selection=selection) == [
        (excluded, "excluded by technology selection")
    ]
    result, pruned = sync_skills(tmp_path, roots, selection=selection)
    assert pruned == [excluded]
    assert not excluded.parent.exists()
    assert (roots[0] / "tool-git" / "SKILL.md").is_file()
    assert check_synced_skills(tmp_path, roots, selection=selection) == []

    result, pruned = sync_skills(tmp_path, roots, selection=frozenset({"python"}))
    assert pruned == [] and excluded in result.written


def _write_resource(repo_root: Path, slug: str, rel: str, content: bytes) -> Path:
    path = repo_root / SKILLS_SOURCE_DIR / slug / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _frontmatter(text: str) -> dict:
    body = text.split("---\n", 2)[1]
    return yaml.safe_load(body)


def test_sync_projects_full_skill_directory(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    _write_resource(tmp_path, "pdf", "references/REF.md", b"# Reference\n")
    _write_resource(tmp_path, "pdf", "scripts/extract.sh", b"#!/bin/sh\necho hi\n")
    _write_resource(tmp_path, "pdf", "assets/logo.bin", b"\x00\x01\x02")
    _write_resource(tmp_path, "pdf", "NOTES.txt", b"extra top-level file\n")
    _write_resource(tmp_path, "pdf", "extra/nested/deep.dat", b"deep\n")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills", ".agents/skills"])

    sync_skills(tmp_path, roots)

    for root in roots:
        skill_dir = root / "pdf"
        assert GENERATED_MARKER in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert (skill_dir / "references/REF.md").read_bytes() == b"# Reference\n"
        assert (skill_dir / "scripts/extract.sh").read_bytes() == b"#!/bin/sh\necho hi\n"
        assert (skill_dir / "assets/logo.bin").read_bytes() == b"\x00\x01\x02"
        assert (skill_dir / "NOTES.txt").read_bytes() == b"extra top-level file\n"
        assert (skill_dir / "extra/nested/deep.dat").read_bytes() == b"deep\n"
        assert GENERATED_MARKER not in (skill_dir / "references/REF.md").read_text(encoding="utf-8")
    assert check_synced_skills(tmp_path, roots) == []


def test_optional_frontmatter_round_trips(tmp_path: Path) -> None:
    path = tmp_path / SKILLS_SOURCE_DIR / "pdf" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "schema_version: 1",
            "name: pdf",
            "invocation: model",
            "description: Work with PDFs.",
            "license: Apache-2.0",
            "compatibility: Requires Python 3.14+ and uv",
            "allowed-tools: Bash(git:*) Read",
            "metadata:",
            "  author: example-org",
            '  version: "1.0"',
            "instructions: |",
            "  # pdf",
            "  Body.",
        ])
        + "\n",
        encoding="utf-8",
    )
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])

    sync_skills(tmp_path, roots)

    front = _frontmatter((roots[0] / "pdf" / "SKILL.md").read_text(encoding="utf-8"))
    assert front["name"] == "pdf"
    assert front["description"] == "Work with PDFs."
    assert front["license"] == "Apache-2.0"
    assert front["compatibility"] == "Requires Python 3.14+ and uv"
    assert front["allowed-tools"] == "Bash(git:*) Read"
    assert front["metadata"] == {"author": "example-org", "version": "1.0"}


def test_minimal_frontmatter_is_unchanged() -> None:
    skill = SkillDefinition("s", "s", "model", "A skill.", "# Body\n\ntext\n", Path("skill.yaml"))
    out = render_skill_md(skill)
    assert out.startswith("---\nname: s\ndescription: A skill.\n---\n")


def test_deselect_prunes_whole_skill_directory(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    _write_resource(tmp_path, "tool-uv", "references/REF.md", b"ref\n")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    skill_dir = roots[0] / "tool-uv"

    sync_skills(tmp_path, roots)
    assert (skill_dir / "references/REF.md").is_file()

    selection = frozenset({"zsh"})
    _result, pruned = sync_skills(tmp_path, roots, selection=selection)
    assert (skill_dir / "SKILL.md") in pruned and (skill_dir / "references/REF.md") in pruned
    assert not skill_dir.exists()
    assert check_synced_skills(tmp_path, roots, selection=selection) == []


def test_check_detects_resource_drift_and_orphans(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    _write_resource(tmp_path, "pdf", "references/REF.md", b"# Reference\n")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    skill_dir = roots[0] / "pdf"

    sync_skills(tmp_path, roots)
    assert check_synced_skills(tmp_path, roots) == []

    ref = skill_dir / "references/REF.md"
    ref.write_bytes(b"# Reference tampered\n")
    orphan = skill_dir / "references/STALE.md"
    orphan.write_bytes(b"left over\n")
    mismatches = dict(check_synced_skills(tmp_path, roots))
    assert mismatches[ref] == "content mismatch"
    assert mismatches[orphan] == "unexpected (not in source)"

    _result, pruned = sync_skills(tmp_path, roots)
    assert orphan in pruned
    assert ref.read_bytes() == b"# Reference\n"
    assert check_synced_skills(tmp_path, roots) == []


def test_check_reports_a_hand_authored_skill_with_no_source(tmp_path: Path) -> None:

    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    sync_skills(tmp_path, roots)
    assert check_synced_skills(tmp_path, roots) == []

    hand_authored = roots[0] / "release-process" / "SKILL.md"
    hand_authored.parent.mkdir(parents=True)
    hand_authored.write_text("---\nname: release-process\n---\n\nbody\n", encoding="utf-8")
    bundled = hand_authored.parent / "references" / "NOTES.md"
    bundled.parent.mkdir()
    bundled.write_text("notes\n", encoding="utf-8")

    expected = (
        f"{UNMANAGED_REASON_PREFIX}no skill source at "
        f"{(SKILLS_SOURCE_DIR / 'release-process' / 'skill.yaml').as_posix()})"
    )
    assert dict(check_synced_skills(tmp_path, roots)) == {
        hand_authored: expected,
        bundled: expected,
    }


def test_build_reports_but_never_deletes_an_unmanaged_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    hand_authored = roots[0] / "release-process" / "SKILL.md"
    hand_authored.parent.mkdir(parents=True)
    hand_authored.write_text("---\nname: release-process\n---\n\nbody\n", encoding="utf-8")

    _result, pruned = sync_skills(tmp_path, roots)

    assert pruned == []
    assert hand_authored.is_file()
    assert len(check_synced_skills(tmp_path, roots)) == 1


def test_check_reports_a_loose_file_at_a_projected_root(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    sync_skills(tmp_path, roots)

    readme = roots[0] / "README.md"
    readme.write_text("# Skills Folder\n", encoding="utf-8")

    assert check_synced_skills(tmp_path, roots) == [
        (readme, f"{UNMANAGED_REASON_PREFIX}a projected skills root holds skill dirs only)")
    ]


def test_a_deselected_skill_is_not_reported_as_unmanaged(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"])
    sync_skills(tmp_path, roots)
    excluded = roots[0] / "tool-uv" / "SKILL.md"

    assert check_synced_skills(tmp_path, roots, selection=frozenset({"zsh"})) == [
        (excluded, "excluded by technology selection")
    ]


def _definition(invocation: str, description: str) -> SkillDefinition:
    return SkillDefinition(
        "s", "s", invocation, description, "# Body\n\ntext\n", Path("skill.yaml")
    )


def test_a_user_invoked_skill_projects_no_description_line() -> None:
    rendered = render_skill_md(_definition("user", ""))

    frontmatter = rendered.split("---")[1]
    assert "description:" not in frontmatter
    assert "name: s" in frontmatter


def test_a_model_invoked_skill_still_projects_its_description() -> None:
    rendered = render_skill_md(_definition("model", "Do a thing."))

    assert "description: Do a thing." in rendered.split("---")[1]


def test_a_user_invoked_skill_never_advertises_the_generated_marker() -> None:

    rendered = render_skill_md(_definition("user", ""))

    body = rendered.split("---\n")[2]
    assert body.splitlines()[0] == "# Body", body
    assert GENERATED_MARKER not in body
    assert GENERATED_MARKER in rendered


def test_a_model_invoked_skill_keeps_the_marker_as_its_first_body_line() -> None:

    rendered = render_skill_md(_definition("model", "Do a thing."))

    assert rendered.split("---\n")[2].splitlines()[0] == GENERATED_MARKER
    assert f"# {GENERATED_MARKER}" not in rendered


@pytest.mark.parametrize(
    ("root", "requires"),
    [
        (Path(".claude/skills"), False),
        (Path("/repo/.claude/skills"), False),
        (Path("C:/repo/.claude/skills"), False),
        (Path(".agents/skills"), True),
        (Path("/repo/.agents/skills"), True),
        (Path(".github/skills"), True),
        (Path("vendor/custom-root"), True),
    ],
)
def test_root_description_requirement_is_data_not_host_dependent(
    root: Path, requires: bool
) -> None:

    assert root_requires_description(root) is requires


def test_a_user_invoked_skill_gets_a_description_where_the_loader_demands_one() -> None:

    rendered = render_skill_md(_definition("user", ""), require_description=True)

    frontmatter = rendered.split("---")[1]
    assert "description: User-invoked skill s." in frontmatter
    assert "s" in frontmatter


def test_a_description_bearing_render_keeps_the_marker_out_of_the_advertised_slot() -> None:

    rendered = render_skill_md(_definition("user", ""), require_description=True)

    assert f"# {GENERATED_MARKER}" not in rendered.split("---")[1]
    assert rendered.split("---\n")[2].splitlines()[0] == GENERATED_MARKER


def test_build_and_check_agree_per_root(tmp_path: Path) -> None:

    _write_skill(tmp_path, "handrun", "handrun", "")
    path = tmp_path / SKILLS_SOURCE_DIR / "handrun" / "skill.yaml"
    path.write_text(
        "schema_version: 1\nname: handrun\ninvocation: user\ninstructions: |\n  # x\n",
        encoding="utf-8",
    )
    roots = [tmp_path / ".claude/skills", tmp_path / ".agents/skills"]

    sync_skills(tmp_path, roots)

    assert check_synced_skills(tmp_path, roots) == []
    tolerant = (roots[0] / "handrun" / "SKILL.md").read_text(encoding="utf-8")
    demanding = (roots[1] / "handrun" / "SKILL.md").read_text(encoding="utf-8")
    assert "description:" not in tolerant.split("---")[1]
    assert "description:" in demanding.split("---")[1]


def test_the_claude_fence_reaches_only_the_root_that_understands_it(tmp_path: Path) -> None:

    _write_skill(tmp_path, "fenced", "fenced", "Do a thing.")
    path = tmp_path / SKILLS_SOURCE_DIR / "fenced" / "skill.yaml"
    path.write_text(
        "schema_version: 1\nname: fenced\ninvocation: model\n"
        "description: Do a thing.\n"
        'claude:\n  paths: ["**/*.py"]\n'
        "instructions: |\n  # x\n",
        encoding="utf-8",
    )
    roots = [tmp_path / ".claude/skills", tmp_path / ".agents/skills"]

    sync_skills(tmp_path, roots)

    fenced = (roots[0] / "fenced" / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    portable = (roots[1] / "fenced" / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert "paths:" in fenced
    assert "**/*.py" in fenced
    assert "paths:" not in portable, "a Claude-only key must not reach the open-standard root"
    assert "description:" in portable, "the portable fields still project"
    assert check_synced_skills(tmp_path, roots) == []


def test_the_claude_fence_may_not_shadow_a_rendered_key(tmp_path: Path) -> None:
    _write_skill(tmp_path, "shadow", "shadow", "Do a thing.")
    path = tmp_path / SKILLS_SOURCE_DIR / "shadow" / "skill.yaml"
    path.write_text(
        "schema_version: 1\nname: shadow\ninvocation: model\n"
        "description: Do a thing.\n"
        "claude:\n  description: something else\n"
        "instructions: |\n  # x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="may not shadow"):
        discover_skills(tmp_path)


def test_no_root_flag_resolves_every_default_root(tmp_path: Path) -> None:

    assert resolve_skill_roots(tmp_path, roots=None) == [
        tmp_path / root for root in DEFAULT_SKILL_ROOTS
    ]


def test_an_explicit_root_narrows_to_that_root_alone(tmp_path: Path) -> None:
    assert resolve_skill_roots(tmp_path, roots=[".agents/skills"]) == [tmp_path / ".agents/skills"]


def test_a_bare_build_writes_the_open_standard_root(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tool-git", "tool-git", "Git usage.")

    sync_skills(tmp_path, resolve_skill_roots(tmp_path, roots=None))

    for root in DEFAULT_SKILL_ROOTS:
        assert (tmp_path / root / "tool-git" / "SKILL.md").is_file()


def test_the_retired_flag_still_parses_and_says_it_does_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    args = cli._build_parser().parse_args(["skills-check", "--all-default-roots"])

    roots = cli._resolve_skill_output_roots(args, tmp_path)

    assert roots == [tmp_path / root for root in DEFAULT_SKILL_ROOTS]
    assert "deprecated" in capsys.readouterr().err
