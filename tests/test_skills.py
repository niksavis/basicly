"""Tests for skill collection projection helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from basicly.schema import ValidationError
from basicly.skills import (
    GENERATED_MARKER,
    SKILLS_SOURCE_DIR,
    UNMANAGED_REASON_PREFIX,
    SkillDefinition,
    check_synced_skills,
    discover_skills,
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


def test_discover_skills_loads_source(tmp_path: Path) -> None:
    """discover_skills reads skill.yaml sources and parses name/description/instructions."""
    _write_skill(tmp_path, "tool-ripgrep", "tool-ripgrep", "Use ripgrep for fast code search.")

    skills = discover_skills(tmp_path)

    assert [skill.slug for skill in skills] == ["tool-ripgrep"]
    assert skills[0].name == "tool-ripgrep"
    assert skills[0].description == "Use ripgrep for fast code search."
    assert skills[0].instructions.startswith("# tool-ripgrep")


def test_discover_skills_requires_fields(tmp_path: Path) -> None:
    """discover_skills fails when a required field is missing."""
    path = tmp_path / SKILLS_SOURCE_DIR / "tool-ripgrep" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("name: x\ndescription: y\n", encoding="utf-8")  # no instructions

    with pytest.raises(ValidationError):
        discover_skills(tmp_path)


def test_discover_skills_ignores_non_yaml_source(tmp_path: Path) -> None:
    """A stray SKILL.md left in a source dir is not discovered (no double-load)."""
    md = tmp_path / SKILLS_SOURCE_DIR / "legacy" / "SKILL.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("---\nname: legacy\ndescription: d\n---\n\nbody\n", encoding="utf-8")

    assert discover_skills(tmp_path) == []


def test_render_skill_md_frontmatter_marker_and_body() -> None:
    """render_skill_md emits YAML frontmatter, the generated marker, then the body."""
    skill = SkillDefinition("s", "s", "model", "A skill.", "# Body\n\ntext\n", Path("skill.yaml"))
    out = render_skill_md(skill)

    assert (
        out == f"---\nname: s\ndescription: A skill.\n---\n{GENERATED_MARKER}\n\n# Body\n\ntext\n"
    )
    # stripping the marker line yields the plain frontmatter+body (fidelity contract)
    assert (
        out.replace(GENERATED_MARKER + "\n", "", 1)
        == "---\nname: s\ndescription: A skill.\n---\n\n# Body\n\ntext\n"
    )


def test_sync_and_check_skills(tmp_path: Path) -> None:
    """sync_skills renders SKILL.md (with marker) to roots and check validates parity."""
    _write_skill(tmp_path, "tool-ripgrep", "tool-ripgrep", "Use ripgrep for fast code search.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)

    result, _pruned = sync_skills(tmp_path, roots)

    assert len(result.written) == 1
    target = roots[0] / "tool-ripgrep" / "SKILL.md"
    assert GENERATED_MARKER in target.read_text(encoding="utf-8")
    assert len(check_synced_skills(tmp_path, roots)) == 0

    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_synced_skills(tmp_path, roots) == [(target, "content mismatch")]


def test_sync_skills_filters_and_prunes_by_selection(tmp_path: Path) -> None:
    """A tagged skill outside the selection is skipped and its projection pruned."""
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    _write_skill(tmp_path, "tool-git", "tool-git", "Git usage.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
    excluded = roots[0] / "tool-uv" / "SKILL.md"

    # Full projection first (no selection recorded): both skills ship.
    sync_skills(tmp_path, roots)
    assert excluded.is_file()

    # Narrowing to zsh flags the stray projection, then the build prunes it.
    selection = frozenset({"zsh"})
    assert check_synced_skills(tmp_path, roots, selection=selection) == [
        (excluded, "excluded by technology selection")
    ]
    result, pruned = sync_skills(tmp_path, roots, selection=selection)
    assert pruned == [excluded]
    assert not excluded.parent.exists()
    assert (roots[0] / "tool-git" / "SKILL.md").is_file()  # universal always ships
    assert check_synced_skills(tmp_path, roots, selection=selection) == []

    # A matching selection ships the tagged skill like any other.
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
    """The whole skill dir (references/scripts/assets/extra) projects verbatim to every root."""
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    _write_resource(tmp_path, "pdf", "references/REF.md", b"# Reference\n")
    _write_resource(tmp_path, "pdf", "scripts/extract.sh", b"#!/bin/sh\necho hi\n")
    _write_resource(tmp_path, "pdf", "assets/logo.bin", b"\x00\x01\x02")
    _write_resource(tmp_path, "pdf", "NOTES.txt", b"extra top-level file\n")
    _write_resource(tmp_path, "pdf", "extra/nested/deep.dat", b"deep\n")
    roots = resolve_skill_roots(
        tmp_path, roots=[".claude/skills", ".agents/skills"], use_default_roots=False
    )

    sync_skills(tmp_path, roots)

    for root in roots:
        skill_dir = root / "pdf"
        assert GENERATED_MARKER in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert (skill_dir / "references/REF.md").read_bytes() == b"# Reference\n"
        assert (skill_dir / "scripts/extract.sh").read_bytes() == b"#!/bin/sh\necho hi\n"
        assert (skill_dir / "assets/logo.bin").read_bytes() == b"\x00\x01\x02"
        assert (skill_dir / "NOTES.txt").read_bytes() == b"extra top-level file\n"
        assert (skill_dir / "extra/nested/deep.dat").read_bytes() == b"deep\n"
        # Bundled resources are hand-authored; they carry no generated marker.
        assert GENERATED_MARKER not in (skill_dir / "references/REF.md").read_text(encoding="utf-8")
    assert check_synced_skills(tmp_path, roots) == []


def test_optional_frontmatter_round_trips(tmp_path: Path) -> None:
    """license/compatibility/allowed-tools/metadata pass through into SKILL.md frontmatter."""
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
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)

    sync_skills(tmp_path, roots)

    front = _frontmatter((roots[0] / "pdf" / "SKILL.md").read_text(encoding="utf-8"))
    assert front["name"] == "pdf"
    assert front["description"] == "Work with PDFs."
    assert front["license"] == "Apache-2.0"
    assert front["compatibility"] == "Requires Python 3.14+ and uv"
    assert front["allowed-tools"] == "Bash(git:*) Read"
    assert front["metadata"] == {"author": "example-org", "version": "1.0"}


def test_minimal_frontmatter_is_unchanged() -> None:
    """Omitting the optional fields yields the exact pre-spec minimal header."""
    skill = SkillDefinition("s", "s", "model", "A skill.", "# Body\n\ntext\n", Path("skill.yaml"))
    out = render_skill_md(skill)
    assert out.startswith("---\nname: s\ndescription: A skill.\n---\n")


def test_deselect_prunes_whole_skill_directory(tmp_path: Path) -> None:
    """Deselecting a tagged skill prunes its whole projected dir, resources and all."""
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    _write_resource(tmp_path, "tool-uv", "references/REF.md", b"ref\n")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
    skill_dir = roots[0] / "tool-uv"

    sync_skills(tmp_path, roots)
    assert (skill_dir / "references/REF.md").is_file()

    selection = frozenset({"zsh"})
    _result, pruned = sync_skills(tmp_path, roots, selection=selection)
    assert (skill_dir / "SKILL.md") in pruned and (skill_dir / "references/REF.md") in pruned
    assert not skill_dir.exists()
    assert check_synced_skills(tmp_path, roots, selection=selection) == []


def test_check_detects_resource_drift_and_orphans(tmp_path: Path) -> None:
    """A hand-edited resource is flagged stale; an added projected file is flagged orphan."""
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    _write_resource(tmp_path, "pdf", "references/REF.md", b"# Reference\n")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
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

    # A rebuild restores the resource and prunes the orphan.
    _result, pruned = sync_skills(tmp_path, roots)
    assert orphan in pruned
    assert ref.read_bytes() == b"# Reference\n"
    assert check_synced_skills(tmp_path, roots) == []


def test_check_reports_a_hand_authored_skill_with_no_source(tmp_path: Path) -> None:
    """A skill dir the catalog never produced is reported, not silently accepted.

    The per-skill mirror only visits directories a source names, so before
    basicly-tcmy.8 a hand-written SKILL.md dropped into a projected root passed
    every gate — it reached one agent, never the other, and nothing said so.
    """
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
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
    """The gate reports what no source describes; only a human deletes it."""
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
    hand_authored = roots[0] / "release-process" / "SKILL.md"
    hand_authored.parent.mkdir(parents=True)
    hand_authored.write_text("---\nname: release-process\n---\n\nbody\n", encoding="utf-8")

    _result, pruned = sync_skills(tmp_path, roots)

    assert pruned == []
    assert hand_authored.is_file()
    assert len(check_synced_skills(tmp_path, roots)) == 1


def test_check_reports_a_loose_file_at_a_projected_root(tmp_path: Path) -> None:
    """A README sitting beside the skill dirs is reported (it teaches the wrong model)."""
    _write_skill(tmp_path, "pdf", "pdf", "Work with PDFs.")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
    sync_skills(tmp_path, roots)

    readme = roots[0] / "README.md"
    readme.write_text("# Skills Folder\n", encoding="utf-8")

    assert check_synced_skills(tmp_path, roots) == [
        (readme, f"{UNMANAGED_REASON_PREFIX}a projected skills root holds skill dirs only)")
    ]


def test_a_deselected_skill_is_not_reported_as_unmanaged(tmp_path: Path) -> None:
    """A skill excluded by technology keeps its own targeted reason, not the unmanaged one."""
    _write_skill(tmp_path, "tool-uv", "tool-uv", "Python tooling.", technologies="[python]")
    roots = resolve_skill_roots(tmp_path, roots=[".claude/skills"], use_default_roots=False)
    sync_skills(tmp_path, roots)
    excluded = roots[0] / "tool-uv" / "SKILL.md"

    assert check_synced_skills(tmp_path, roots, selection=frozenset({"zsh"})) == [
        (excluded, "excluded by technology selection")
    ]


# --- Invocation axis (basicly-m4zv.1) -----------------------------------------


def _definition(invocation: str, description: str) -> SkillDefinition:
    return SkillDefinition(
        "s", "s", invocation, description, "# Body\n\ntext\n", Path("skill.yaml")
    )


def test_a_user_invoked_skill_projects_no_description_line() -> None:
    """The description is the thing the agent reads to route; an unreachable entry owes none."""
    rendered = render_skill_md(_definition("user", ""))

    frontmatter = rendered.split("---")[1]
    assert "description:" not in frontmatter
    assert "name: s" in frontmatter


def test_a_model_invoked_skill_still_projects_its_description() -> None:
    """The default position is unchanged, so nothing silently loses agent reach."""
    rendered = render_skill_md(_definition("model", "Do a thing."))

    assert "description: Do a thing." in rendered.split("---")[1]


def test_a_user_invoked_skill_never_advertises_the_generated_marker() -> None:
    """The host fills an absent description from the first body line (basicly-m4zv.7).

    With the marker there, the entry advertised the drift notice itself — 91
    characters telling the agent nothing. The entry's own heading must reach that
    slot instead, so the marker moves into the frontmatter as a YAML comment.
    """
    rendered = render_skill_md(_definition("user", ""))

    body = rendered.split("---\n")[2]
    assert body.splitlines()[0] == "# Body", body
    assert GENERATED_MARKER not in body
    # Still detectable as generated, which is what drift detection matches on.
    assert GENERATED_MARKER in rendered


def test_a_model_invoked_skill_keeps_the_marker_as_its_first_body_line() -> None:
    """Its description already occupies the advertised slot, so nothing moves.

    Pinned separately from the byte-for-byte header test because the fix for
    basicly-m4zv.7 branches on the axis, and a later tidy-up that unified the two
    forms would silently change every consumer's model-invoked projection.
    """
    rendered = render_skill_md(_definition("model", "Do a thing."))

    assert rendered.split("---\n")[2].splitlines()[0] == GENERATED_MARKER
    assert f"# {GENERATED_MARKER}" not in rendered


def test_loading_rejects_an_unknown_invocation_value(tmp_path: Path) -> None:
    """A third position on a two-position axis would be unenforceable downstream."""
    path = tmp_path / SKILLS_SOURCE_DIR / "odd" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\nname: odd\ninvocation: occasionally\ninstructions: |\n  # x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="must be one of"):
        discover_skills(tmp_path)


def test_loading_a_user_invoked_skill_needs_no_description(tmp_path: Path) -> None:
    """The loader cannot require a description it is the point of this entry not to have."""
    path = tmp_path / SKILLS_SOURCE_DIR / "handrun" / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\nname: handrun\ninvocation: user\ninstructions: |\n  # x\n",
        encoding="utf-8",
    )

    (skill,) = discover_skills(tmp_path)
    assert skill.invocation == "user"
    assert skill.description == ""


# --- Per-root loader tolerance (basicly-m4zv.10) -------------------------------


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
    """Tolerance keys on the root's own trailing parts, so absolute and relative agree.

    Expressed as a table rather than as a branch on the running platform: the
    thing that varies is the path, and a path is data. An unrecognised root
    requires a description — emitting one costs a few tokens, omitting one can
    cost the whole skill.
    """
    assert root_requires_description(root) is requires


def test_a_user_invoked_skill_gets_a_description_where_the_loader_demands_one() -> None:
    """Codex rejects a description-less file, so stripping it there deletes the skill.

    The source carries none (catalog lint forbids it), so the field is
    synthesized rather than restored: named, so three user-invoked entries do not
    all render the same string, and stating the contract instead of a route.
    """
    rendered = render_skill_md(_definition("user", ""), require_description=True)

    frontmatter = rendered.split("---")[1]
    assert "description: User-invoked skill s." in frontmatter
    assert "s" in frontmatter


def test_a_description_bearing_render_keeps_the_marker_out_of_the_advertised_slot() -> None:
    """With a description present the marker returns to the body, as for a model entry.

    Otherwise the frontmatter would carry both a description and the YAML-comment
    marker — two mechanisms for the same slot, and the m4zv.7 defect waiting to
    resurface if the description were ever dropped again.
    """
    rendered = render_skill_md(_definition("user", ""), require_description=True)

    assert f"# {GENERATED_MARKER}" not in rendered.split("---")[1]
    assert rendered.split("---\n")[2].splitlines()[0] == GENERATED_MARKER


def test_build_and_check_agree_per_root(tmp_path: Path) -> None:
    """The drift check compares bytes, so it must render exactly as the build did.

    Both derive the requirement from the destination root; if they ever diverged,
    every user-invoked skill would report permanent drift on one of the two roots.
    """
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
