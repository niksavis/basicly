from __future__ import annotations

from pathlib import Path

from basicly import catalog_routing as routing
from basicly.catalog_lint import lint_catalog
from basicly.routing_evals import RoutingOutcome, routing_outcome
from tests.test_catalog_lint import _INSTRUCTIONS, REPO, _catalog, _skill_source

_EVAL_POSITIVES = (
    '    - prompt: "filter the records in this json response"\n'
    '    - prompt: "pull one field out of the json body"\n'
    '    - prompt: "reshape this json array in a pipeline"\n'
)


def _routing_catalog(tmp_path: Path) -> Path:
    root = _catalog(tmp_path)
    (root / ".basicly/core/schemas/evals.schema.json").write_text(
        (REPO / ".basicly/core/schemas/evals.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for slug, description in (
        ("tool-jq", "Parse and filter json in shell pipelines."),
        ("tool-tree", "Visualize directory layout as a hierarchy."),
    ):
        _skill_source(
            root,
            slug,
            f"schema_version: 1\nname: {slug}\ninvocation: model\n"
            f"description: {description}\n{_INSTRUCTIONS}",
        )
    return root


def _evals(root: Path, slug: str, body: str) -> None:
    (root / ".basicly/core/skills" / slug / "evals.yaml").write_text(body, encoding="utf-8")


def test_a_catalog_with_no_eval_cases_does_not_demand_a_floor(tmp_path: Path) -> None:

    assert lint_catalog(_routing_catalog(tmp_path)) == []


def test_a_routing_eval_that_holds_passes_the_lint(tmp_path: Path) -> None:
    root = _routing_catalog(tmp_path)
    _evals(
        root,
        "tool-jq",
        "schema_version: 1\ntrigger:\n  positive:\n" + _EVAL_POSITIVES + "  negative:\n"
        '    - prompt: "show me the directory hierarchy"\n      owner: tool-tree\n'
        '    - prompt: "visualize the layout of this directory"\n      owner: tool-tree\n',
    )
    (root / "basicly.toml").write_text("[catalog]\nrank1_floor = 0.5\n", encoding="utf-8")

    assert lint_catalog(root) == []


def test_a_positive_prompt_that_misroutes_fails_the_lint(tmp_path: Path) -> None:
    root = _routing_catalog(tmp_path)
    _evals(
        root,
        "tool-tree",
        "schema_version: 1\ntrigger:\n  positive:\n"
        '    - prompt: "filter the json records"\n      top_k: 1\n'
        '    - prompt: "show me the directory hierarchy"\n'
        '    - prompt: "visualize this layout"\n'
        "  negative:\n"
        '    - prompt: "parse this json in a shell pipeline"\n      owner: tool-jq\n'
        '    - prompt: "filter the json body"\n      owner: tool-jq\n',
    )
    (root / "basicly.toml").write_text("[catalog]\nrank1_floor = 0.1\n", encoding="utf-8")

    violations = lint_catalog(root)

    assert any("tool-tree" in v and "filter the json records" in v for v in violations), violations


def test_an_eval_case_naming_itself_as_a_negative_owner_is_rejected(tmp_path: Path) -> None:
    root = _routing_catalog(tmp_path)
    _evals(
        root,
        "tool-jq",
        "schema_version: 1\ntrigger:\n  positive:\n" + _EVAL_POSITIVES + "  negative:\n"
        '    - prompt: "show me the directory hierarchy"\n      owner: tool-jq\n'
        '    - prompt: "visualize the layout of this directory"\n      owner: tool-tree\n',
    )
    (root / "basicly.toml").write_text("[catalog]\nrank1_floor = 0.5\n", encoding="utf-8")

    assert any("its own owner" in v for v in lint_catalog(root))


def test_an_eval_case_on_a_user_invoked_entry_is_rejected(tmp_path: Path) -> None:
    root = _routing_catalog(tmp_path)
    _skill_source(
        root, "tool-bat", f"schema_version: 1\nname: tool-bat\ninvocation: user\n{_INSTRUCTIONS}"
    )
    _evals(
        root,
        "tool-bat",
        "schema_version: 1\ntrigger:\n  positive:\n" + _EVAL_POSITIVES + "  negative:\n"
        '    - prompt: "show me the directory hierarchy"\n      owner: tool-tree\n'
        '    - prompt: "visualize the layout of this directory"\n      owner: tool-tree\n',
    )

    assert any("not a model-invoked entry" in v for v in lint_catalog(root))


def test_an_eval_file_short_of_the_required_counts_is_rejected(tmp_path: Path) -> None:
    root = _routing_catalog(tmp_path)
    _evals(
        root,
        "tool-jq",
        "schema_version: 1\ntrigger:\n  positive:\n"
        '    - prompt: "filter the records in this json response"\n'
        "  negative:\n"
        '    - prompt: "show me the directory hierarchy"\n      owner: tool-tree\n',
    )

    assert any("evals.yaml" in v for v in lint_catalog(root))


def test_the_shipped_catalog_clears_its_own_declared_floor() -> None:

    outcome = routing_outcome(REPO)

    assert outcome.violations == (), outcome.violations
    assert outcome.floor is not None, "this repo must declare [catalog] rank1_floor"
    assert outcome.report.positives > 0
    assert outcome.report.rank1_rate >= outcome.floor


def test_the_declared_floor_leaves_headroom_below_the_measured_rate() -> None:

    outcome = routing_outcome(REPO)

    assert outcome.floor is not None
    assert outcome.report.rank1_rate > outcome.floor


def test_the_report_line_states_the_rate_and_the_floor_it_is_measured_against() -> None:
    report = routing.RoutingReport(failures=(), collision_warnings=(), rank1_hits=80, positives=87)

    outcome = RoutingOutcome(report=report, floor=0.85, violations=(), warnings=())

    assert outcome.summary() == "routing: rank-1 rate 80/87 = 92.0% (floor 85.0%)"


def test_the_report_line_says_so_when_no_floor_is_declared() -> None:
    report = routing.RoutingReport(failures=(), collision_warnings=(), rank1_hits=1, positives=2)

    outcome = RoutingOutcome(report=report, floor=None, violations=(), warnings=())

    assert outcome.summary().endswith("(no floor declared)")


def test_the_missing_floor_advice_states_the_unit_it_wants() -> None:

    advice = "\n".join(routing.floor_violations(0.891, None, None))

    assert "fraction between 0 and 1, not a percentage" in advice
    assert "write 0.87, not 89.1" in advice
