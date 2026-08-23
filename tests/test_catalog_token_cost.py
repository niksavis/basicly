"""Tests for the declared always-on token cost gate."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from basicly import catalog_token_cost as ctc
from basicly.catalog_lint import lint_catalog, skill_warnings
from basicly.config import load_project_paths
from basicly.loader import load_fragments_from_roots, load_targets
from basicly.planner import plan_outputs

REPO = Path(__file__).parent.parent
SCHEMAS = (
    "skill.schema.json",
    "fragment.schema.json",
    "agent.schema.json",
    "block.schema.json",
)
SKILL = (
    "schema_version: 1\nname: s\ninvocation: model\n"
    "description: A description long enough to price.\ninstructions: |\n  body\n"
)
FRAGMENT = (
    "schema_version: 1\nid: f\ndescription: d\ncategory: project\n"
    "applies_to: [all]\nbody: |\n  - the authored line every turn pays for\n"
)
SCOPED_FRAGMENT = (
    "schema_version: 1\nid: g\ndescription: d\ncategory: project\n"
    "applies_to: [all]\nscope:\n  paths: ['src/**']\n"
    "body: |\n  - a scoped line codex inlines and claude does not\n"
)


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    """A tmp catalog with the repo's real targets and templates, so renders are real."""
    core = tmp_path / ".basicly/core"
    (core / "schemas").mkdir(parents=True)
    for name in SCHEMAS:
        shutil.copy(REPO / ".basicly/core/schemas" / name, core / "schemas" / name)
    shutil.copytree(REPO / ".basicly/core/targets", core / "targets")
    shutil.copytree(REPO / ".basicly/core/templates", core / "templates")
    (core / "skills/s").mkdir(parents=True)
    (core / "skills/s/skill.yaml").write_text(SKILL, encoding="utf-8")
    (core / "fragments/project").mkdir(parents=True)
    (core / "fragments/project/f.fragment.yaml").write_text(FRAGMENT, encoding="utf-8")
    return tmp_path


def _fragment(catalog: Path) -> Path:
    return catalog / ".basicly/core/fragments/project/f.fragment.yaml"


def _declare(path: Path, block: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")


def _measured(catalog: Path, path: Path) -> dict[str, int]:
    return ctc.measured_costs(catalog)[path]


def test_every_source_is_measured(catalog: Path) -> None:
    """Both source families report a per-surface cost, keyed by source path."""
    costs = ctc.measured_costs(catalog)
    assert costs[_fragment(catalog)].keys() == {"claude", "codex", "copilot"}
    skill = catalog / ".basicly/core/skills/s/skill.yaml"
    assert costs[skill].keys() == {ctc.LISTING_SURFACE}
    assert costs[skill][ctc.LISTING_SURFACE] > 0


def test_absent_declaration_warns_inside_the_window(catalog: Path) -> None:
    """AC1: in the warning window an undeclared source is named and does not fail."""
    violations, warnings = ctc.problems(catalog, version="0.9.0")
    assert violations == []
    assert any("fragments/project/f.fragment.yaml" in line for line in warnings)
    assert all("no `token_cost:` declared" in line for line in warnings)


def test_absent_declaration_fails_once_the_window_closes(catalog: Path) -> None:
    """AC2: past the window the same absence fails and names the source."""
    violations, warnings = ctc.problems(catalog, version=ctc.REQUIRED_FROM_VERSION)
    assert warnings == []
    assert any("fragments/project/f.fragment.yaml" in line for line in violations)


def test_window_is_keyed_on_the_version_not_the_clock() -> None:
    """The window closes at a release, so the verdict never depends on when CI ran."""
    assert ctc.window_open("0.9.0")
    assert ctc.window_open("0.10.99")
    assert not ctc.window_open(ctc.REQUIRED_FROM_VERSION)
    assert not ctc.window_open("1.0.0")
    assert ctc.window_open("0.10.0rc1")


def test_declaration_beyond_tolerance_is_reported(catalog: Path) -> None:
    """AC3: a declared figure that no longer matches the projection fails."""
    path = _fragment(catalog)
    measured = _measured(catalog, path)
    _declare(path, "token_cost:\n  claude: 9000\n  codex: 9000\n  copilot: 9000\n")
    violations, warnings = ctc.problems(catalog, version="0.9.0")
    assert not [w for w in warnings if "f.fragment.yaml" in w]
    assert any(f"update it to {measured['claude']}" in line for line in violations)


def test_declaration_inside_tolerance_is_accepted(catalog: Path) -> None:
    """A reworded sentence must not fail a declaration; only a real edit does."""
    path = _fragment(catalog)
    measured = _measured(catalog, path)
    drifted = {name: count + ctc.tolerance(count) for name, count in measured.items()}
    _declare(path, "token_cost:\n" + "".join(f"  {k}: {v}\n" for k, v in drifted.items()))
    violations, warnings = ctc.problems(catalog, version="0.9.0")
    assert violations == []
    assert not [w for w in warnings if "f.fragment.yaml" in w]


def test_scoped_fragment_costs_agents_md_and_nothing_else(catalog: Path) -> None:
    """AC4: a source projecting to nothing for a target declares that zero explicitly."""
    scoped = catalog / ".basicly/core/fragments/project/g.fragment.yaml"
    scoped.write_text(SCOPED_FRAGMENT, encoding="utf-8")
    measured = _measured(catalog, scoped)
    assert measured["codex"] > 0, "AGENTS.md inlines a scoped fragment"
    assert measured["claude"] == 0
    assert measured["copilot"] == 0


def test_declaring_one_number_for_a_split_cost_is_refused(catalog: Path) -> None:
    """AC4: the declaration must name every surface, not collapse them into one."""
    path = _fragment(catalog)
    _declare(path, "token_cost:\n  codex: 20\n")
    violations, _ = ctc.problems(catalog, version="0.9.0")
    assert any("names surfaces ['codex'] but this source projects to" in v for v in violations)


def test_a_malformed_declaration_is_reported(catalog: Path) -> None:
    """A non-mapping or negative declaration fails rather than being normalised away."""
    path = _fragment(catalog)
    _declare(path, "token_cost: 40\n")
    violations, _ = ctc.problems(catalog, version="0.9.0")
    assert any("must be a mapping of surface to token count" in v for v in violations)

    path.write_text(FRAGMENT + "token_cost:\n  claude: -1\n", encoding="utf-8")
    violations, _ = ctc.problems(catalog, version="0.9.0")
    assert any("must be non-negative integers (claude)" in v for v in violations)


def test_a_wrong_declaration_fails_inside_the_window(catalog: Path) -> None:
    """The window forgives silence, never a number nobody checked."""
    _declare(_fragment(catalog), "token_cost:\n  claude: 1\n  codex: 1\n  copilot: 1\n")
    violations, _ = ctc.problems(catalog, version="0.9.0")
    assert violations, "a rotted declaration must fail even while absence is a warning"


def test_declaration_does_not_change_the_projection(catalog: Path) -> None:
    """AC5: the field is authoring metadata, so the rendered bytes are identical."""

    def rendered() -> dict[str, str]:
        paths = load_project_paths(catalog)
        targets = load_targets(catalog / paths.targets_dir)
        fragments = load_fragments_from_roots(
            [(catalog / paths.core_fragments_dir, "core")], {t.name for t in targets}
        )
        return {
            item.output_path.name: ctc._render(catalog / paths.templates_dir, item)
            for item in plan_outputs(fragments, targets, catalog)
        }

    before = rendered()
    path = _fragment(catalog)
    _declare(
        path,
        "token_cost:\n" + "".join(f"  {k}: {v}\n" for k, v in _measured(catalog, path).items()),
    )
    assert rendered() == before


def test_no_targets_means_nothing_to_measure(tmp_path: Path) -> None:
    """A catalog that projects nowhere has no always-on cost to rule on."""
    (tmp_path / ".basicly/core/fragments/project").mkdir(parents=True)
    (tmp_path / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        FRAGMENT, encoding="utf-8"
    )
    assert ctc.fragment_costs(tmp_path) == {}


def test_catalog_lint_collects_the_rule(catalog: Path) -> None:
    """AC6: the finding reaches the named `catalog lint` check, on the right channel."""
    assert any("no `token_cost:` declared" in w for w in skill_warnings(catalog))
    _declare(_fragment(catalog), "token_cost:\n  claude: 9000\n  codex: 9000\n  copilot: 9000\n")
    assert any("`token_cost.claude` declares 9000" in v for v in lint_catalog(catalog))


def test_this_repo_declares_no_wrong_cost() -> None:
    """The shipped catalog carries no rotted declaration, so the gate is green here."""
    assert ctc.violations(REPO) == []
