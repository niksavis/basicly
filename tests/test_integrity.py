"""Tests for the integrity-level rule (basicly-u2hl.2, D9/D11)."""

from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import integrity
from basicly.schema import MODEL_TIERS

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- The path rule ------------------------------------------------------------
#
# One case per named clause, plus the paths that decide the exclusions. Written as
# data rather than as a loop over the rules, so a rule edited into agreeing with
# itself still has to agree with the surfaces §9 of the implementation plan names.
PATH_LEVELS = (
    # The five frozen consumer surfaces.
    ("src/basicly/cli.py", "L3", "cli-surface"),
    ("src/basicly/config.py", "L3", "config-surface"),
    ("basicly.toml", "L3", "config-surface"),
    ("basicly.local.toml", "L3", "config-surface"),
    ("src/basicly/schema.py", "L3", "catalog-source-schemas"),
    (".basicly/core/schemas/skill.schema.json", "L3", "catalog-source-schemas"),
    ("src/basicly/projection.py", "L3", "generated-file-contract"),
    ("src/basicly/renderers/claude.py", "L3", "generated-file-contract"),
    (".basicly/core/templates/claude/skill.md.j2", "L3", "generated-file-contract"),
    ("src/basicly/run_record.py", "L3", "ledger-format"),
    # Engine code carrying none of them.
    ("src/basicly/loop.py", "L2", "engine-internal"),
    ("src/basicly/loader.py", "L2", "engine-internal"),
    ("src/basicly/integrity.py", "L2", "engine-internal"),
    (".scripts/wired_or_deleted.py", "L2", "engine-internal"),
    # Prose and tests.
    ("docs/design/factory-loop-requirements.md", "L1", "docs-and-tests"),
    ("tests/test_integrity.py", "L1", "docs-and-tests"),
    ("README.md", "L1", "docs-and-tests"),
    ("site/index.html", "L1", "docs-and-tests"),
    # A document under src/ is still a document: the engine clause excludes it.
    ("src/basicly/notes.md", "L1", "docs-and-tests"),
    # Neither engine nor consumer surface nor prose.
    ("pyproject.toml", "L2", "unclassified"),
    (".github/workflows/ci.yml", "L2", "unclassified"),
    (".basicly/core/skills/tool-br.yaml", "L2", "unclassified"),
)


@pytest.mark.parametrize(("path", "level", "rule"), PATH_LEVELS)
def test_path_resolves_to_its_named_clause(path: str, level: str, rule: str) -> None:
    """Each path resolves to the level and the clause the rule set declares."""
    assignment = integrity.assign([path])
    assert assignment.level == level
    assert assignment.rule == rule


@pytest.mark.parametrize(
    "spelling",
    ["src\\basicly\\cli.py", "./src/basicly/cli.py", "/src/basicly/cli.py"],
)
def test_path_spelling_does_not_change_the_level(spelling: str) -> None:
    """A Windows separator, a leading ./ or a leading / is spelling, not meaning."""
    assert integrity.assign([spelling]).level == "L3"


def test_scope_takes_the_highest_level_any_declared_path_holds() -> None:
    """One consumer surface in the scope makes the whole package a consumer change."""
    assignment = integrity.assign(["docs/plan/implementation-plan.md", "src/basicly/cli.py"])
    assert assignment.level == "L3"
    assert assignment.rule == "cli-surface"
    assert "src/basicly/cli.py" in assignment.reason


def test_a_wildcard_scope_that_covers_a_consumer_surface_is_l3() -> None:
    """`src/basicly/*.py` can edit cli.py, so it classifies as the CLI surface."""
    assert integrity.assign(["src/basicly/*.py"]).level == "L3"


def test_a_scope_of_engine_files_stays_l2() -> None:
    """The same wildcard narrowed away from the surfaces does not escalate."""
    assignment = integrity.assign(["src/basicly/loop.py", "tests/test_loop.py"])
    assert assignment.level == "L2"
    assert assignment.rule == "engine-internal"


def test_an_undeclared_scope_resolves_through_the_fallback() -> None:
    """A hand-filed bead declares no scope; that is not an error and not an escalation."""
    assignment = integrity.assign([])
    assert assignment.level == "L2"
    assert assignment.rule == "unclassified"
    assert "no scope declared" in assignment.reason


# --- The D11 downgrade --------------------------------------------------------

_SMALL_PYTHON_PATCH = """\
diff --git a/src/basicly/cli.py b/src/basicly/cli.py
--- a/src/basicly/cli.py
+++ b/src/basicly/cli.py
@@ -10,3 +10,3 @@
-    return _format(value)
+    return _format(value.strip())
"""

_SIGNATURE_PATCH = """\
diff --git a/src/basicly/cli.py b/src/basicly/cli.py
--- a/src/basicly/cli.py
+++ b/src/basicly/cli.py
@@ -10,3 +10,3 @@
-def build(args):
+def build(args, *, strict=False):
"""

_PRIVATE_SIGNATURE_PATCH = """\
diff --git a/src/basicly/cli.py b/src/basicly/cli.py
--- a/src/basicly/cli.py
+++ b/src/basicly/cli.py
@@ -10,3 +10,3 @@
-def _build(args):
+def _build(args, *, strict=False):
"""

_SCHEMA_PATCH = """\
diff --git a/.basicly/core/schemas/skill.schema.json b/.basicly/core/schemas/skill.schema.json
--- a/.basicly/core/schemas/skill.schema.json
+++ b/.basicly/core/schemas/skill.schema.json
@@ -10,3 +10,3 @@
-    "required": ["name"],
+    "required": ["name", "description"],
"""


def test_downgrade_moves_a_small_signature_preserving_l3_change_to_l2() -> None:
    """The path says consumer surface; the change says two edited lines inside it."""
    assignment = integrity.assign(["src/basicly/cli.py"], patch=_SMALL_PYTHON_PATCH)
    assert assignment.level == "L2"
    assert assignment.rule == "downgrade"
    assert assignment.selection == integrity.selection_for("L2")


def test_downgrade_records_its_reason() -> None:
    """The record says what it was, what it became, and on which two facts."""
    assignment = integrity.assign(["src/basicly/cli.py"], patch=_SMALL_PYTHON_PATCH)
    assert "downgraded from L3" in assignment.reason
    assert "2 changed lines" in assignment.reason
    assert "no public signature changed" in assignment.reason


def test_downgrade_is_refused_when_a_public_signature_changed() -> None:
    """A two-line diff is still a contract change when the two lines are a def."""
    assert integrity.assign(["src/basicly/cli.py"], patch=_SIGNATURE_PATCH).level == "L3"


def test_downgrade_ignores_a_private_signature() -> None:
    """An underscored name is not the surface, so editing it does not hold L3."""
    assignment = integrity.assign(["src/basicly/cli.py"], patch=_PRIVATE_SIGNATURE_PATCH)
    assert assignment.level == "L2"


def test_downgrade_is_refused_for_a_non_python_consumer_surface() -> None:
    """A schema file *is* the contract, so no edit to one is a small edit."""
    assert integrity.assign([".basicly/core/schemas/**"], patch=_SCHEMA_PATCH).level == "L3"


def test_downgrade_is_refused_at_or_above_the_configured_line_threshold() -> None:
    """The threshold is the caller's argument, and the same diff falls either side of it."""
    body = "".join(f"+    line_{n} = {n}\n" for n in range(6))
    patch = f"--- a/src/basicly/cli.py\n+++ b/src/basicly/cli.py\n@@ -1,1 +1,6 @@\n{body}"
    scope = ["src/basicly/cli.py"]
    assert integrity.assign(scope, patch=patch, downgrade_max_lines=6).level == "L3"
    assert integrity.assign(scope, patch=patch, downgrade_max_lines=7).level == "L2"


def test_downgrade_is_refused_for_a_diff_over_the_default_threshold() -> None:
    """A signature-free but large edit to a consumer surface keeps the L3 budget."""
    body = "".join(f"+    line_{n} = {n}\n" for n in range(integrity.DEFAULT_DOWNGRADE_MAX_LINES))
    patch = f"--- a/src/basicly/cli.py\n+++ b/src/basicly/cli.py\n@@ -1,1 +1,20 @@\n{body}"
    assert integrity.assign(["src/basicly/cli.py"], patch=patch).level == "L3"


def test_downgrade_needs_a_patch_to_read() -> None:
    """With no change to read, the path rule stands alone."""
    assert integrity.assign(["src/basicly/cli.py"]).level == "L3"


def test_downgrade_never_applies_below_l3() -> None:
    """L1 and L2 are not downgraded: the mechanism exists to stop over-classifying L3."""
    assignment = integrity.assign(["src/basicly/loop.py"], patch=_SMALL_PYTHON_PATCH)
    assert assignment.level == "L2"
    assert assignment.rule == "engine-internal"


# --- What a level selects -----------------------------------------------------


@pytest.mark.parametrize(
    ("level", "gates", "tier", "rework", "ship"),
    [
        ("L1", ("fast",), "medium", 1, "delegable"),
        ("L2", ("full",), "high", 2, "delegable"),
        ("L3", ("full", "validate-as-consumer", "evidence-binding"), "maximum", 2, "human"),
    ],
)
def test_selects_the_gate_set_tier_and_rework_allowance(
    level: str, gates: tuple[str, ...], tier: str, rework: int, ship: str
) -> None:
    """§4's table, encoded once, read by callers rather than re-derived."""
    selection = integrity.selection_for(level)
    assert selection.gates == gates
    assert selection.model_tier == tier
    assert selection.rework_allowance == rework
    assert selection.ship == ship


def test_an_assignment_selects_without_a_second_lookup() -> None:
    """The caller reads one record; nothing maps the letter back to a policy itself."""
    assignment = integrity.assign(["src/basicly/run_record.py"])
    assert assignment.selection is integrity.selection_for(assignment.level)


def test_selects_refuses_a_level_outside_the_ladder() -> None:
    """An unknown level is a loud error, never a silent default to the cheapest gates."""
    with pytest.raises(ValueError, match="unknown integrity level"):
        integrity.selection_for("L4")


def test_selects_a_model_tier_the_engine_can_resolve() -> None:
    """Tripwire: every tier named here must be one `models.model_for` can pin."""
    for level in integrity.LEVELS:
        assert integrity.selection_for(level).model_tier in MODEL_TIERS


# --- Totality -----------------------------------------------------------------


def _tracked_files() -> list[str]:
    """Every file this repo actually holds, from git rather than from a walk."""
    result = subprocess.run(  # nosec B603, B607
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_total_every_tracked_path_resolves_to_exactly_one_level() -> None:
    """Total in both directions: nothing unresolved, nothing resolved twice.

    Run over the real tree rather than a fixture list, because the property that
    matters is about the paths the repo can hold. Exactly-one is asserted against
    the *clauses*, not against match order — the engine clause excludes the
    consumer patterns, so a path claimed twice is a rule-set defect that reordering
    :data:`integrity.RULES` could not hide.
    """
    tracked = _tracked_files()
    assert len(tracked) > 100, "git ls-files returned too little to be the repo"
    claimed_twice = {
        path: claims for path in tracked if len(claims := integrity.claiming_rules(path)) > 1
    }
    assert claimed_twice == {}
    # Total, and not vacuously so: the tree really does exercise all three levels.
    assert {integrity.assign([path]).level for path in tracked} == set(integrity.LEVELS)


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "no-extension",
        "a/very/deep/path/that/nothing/declares.py",
        "src/basicly/renderers/__init__.py",
        ".beads/issues.jsonl",
        "src/basicly/cli.py/not-really-a-file",
    ],
)
def test_total_covers_paths_no_clause_anticipated(path: str) -> None:
    """A path outside every clause resolves to the fallback, never to nothing."""
    assert integrity.assign([path]).level in integrity.LEVELS


def test_total_the_fallback_is_the_middle_level() -> None:
    """An unrecognised path is neither fast-gated nor sent to a human."""
    unrecognised = "vendor/third-party/thing.rb"
    assert integrity.claiming_rules(unrecognised) == ()
    assert integrity.assign([unrecognised]).level == "L2"
    assert integrity.LEVELS.index("L2") == 1


def test_downgrade_is_refused_for_a_patch_with_no_file_header() -> None:
    """A patch this reader cannot attribute to a file holds the level.

    The downgrade is the only consumer of the diff read, so a parse that came up
    empty must not be spendable as a discount on the gates.
    """
    headerless = "@@ -1,2 +1,2 @@\n-    x = 1\n+    x = 2\n"
    assert integrity.assign(["src/basicly/cli.py"], patch=headerless).level == "L3"
