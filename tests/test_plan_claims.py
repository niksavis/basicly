"""What `docs/plan/implementation-plan.md` claims about this tree (basicly-uhiq.1).

The boundary is the plan's *content* against the docs-claims *gate*, which
`test_docs_claims` owns: nothing here runs the generator or the checker, and everything
here recounts the tree by hand and holds the committed document to the answer. Split out
of `test_docs_claims` by basicly-5p49, which needed to correct the check-count recount and
found that file frozen at its module-size baseline with no room to correct anything.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from tests.doc_blocks import block_body, cells

REPO = Path(__file__).resolve().parents[1]
IMPLEMENTATION_PLAN = "docs/plan/implementation-plan.md"


def _declared_checks() -> list[dict[str, Any]]:
    """Every `[[verify.checks]]` entry, from `basicly.toml` and its `basicly.d` fragments.

    Parsed here rather than taken from :func:`basicly.config.load_verify_config`, so the
    document is held against the files a reviewer opens rather than against the same
    loader the renderer used. The fragments are half the population: `basicly.d/README.md`
    is where a lane is told to wire its check, and reading the anchor alone asserted that
    no lane may (basicly-5p49).
    """
    sources = [REPO / "basicly.toml", *sorted((REPO / "basicly.d").glob("*.toml"))]
    return [
        check
        for source in sources
        for check in tomllib
        .loads(source.read_text(encoding="utf-8"))
        .get("verify", {})
        .get("checks", [])
    ]


def test_plan_current_state_matches_the_tree_it_claims_to_measure() -> None:
    """Every row of the plan's generated block is the real measurement.

    Read out of the committed document rather than out of the renderer: the claim is
    that the *plan* carries the number, not that the function agrees with itself.
    """
    rows = block_body(
        (REPO / IMPLEMENTATION_PLAN).read_text(encoding="utf-8"), "plan-current-state"
    )
    stated = {
        cells(row)[0]: cells(row)[1] for row in rows if row.startswith("| ") and "---" not in row
    }
    stated.pop("Measure", None)

    modules = len(sorted((REPO / "src" / "basicly").glob("*.py")))
    test_files = sorted((REPO / "tests").glob("test_*.py"))
    assert stated["Engine modules (`src/basicly/*.py`)"] == str(modules)
    assert stated["Test files"] == str(len(test_files))

    checks = _declared_checks()
    assert stated["`[[verify.checks]]` declared"] == str(len(checks))
    for mode in ("fast", "full", "staged"):
        expected = sum(1 for check in checks if mode in (check.get("modes") or []))
        assert stated[f"…of which run in `--mode {mode}`"] == str(expected)


def test_the_plan_states_no_verify_check_count_outside_the_generated_block() -> None:
    """A hand-written check count is wrong for at least one mode, so there may be none.

    The plan stated one fixed number for `verify --mode full` and a different one for
    what the config declares. Both were wrong, and no single sentence could have been
    right: the count is per-mode. That is the stale claim basicly-uhiq.1 removed.
    """
    text = (REPO / IMPLEMENTATION_PLAN).read_text(encoding="utf-8")
    body = (
        text.split("<!-- docs-claims:begin plan-current-state -->")[0]
        + text.split("<!-- docs-claims:end plan-current-state -->")[1]
    )

    offenders = re.findall(r"\b(?:an? )?(\w+)-check `?verify", body)
    assert offenders == [], f"hand-written verify check count outside the block: {offenders}"


def test_the_plan_indexes_every_document_that_survives_under_docs() -> None:
    """The plan is the index that makes "delete a fulfilled document" enforceable.

    Owner rule 2026-08-02: a document not listed here should not exist. So an
    unlisted document is either a plan defect or a deletion nobody performed, and
    both need a human — hence a test rather than prose.
    """
    plan = (REPO / IMPLEMENTATION_PLAN).read_text(encoding="utf-8")
    on_disk = {
        path.relative_to(REPO / "docs").as_posix()
        for path in (REPO / "docs").rglob("*.md")
        if path.name != "implementation-plan.md"
    }
    unlisted = sorted(name for name in on_disk if name.rsplit("/", 1)[-1] not in plan)

    assert unlisted == [], (
        f"documents under docs/ that the plan does not name — index them or delete them: {unlisted}"
    )
