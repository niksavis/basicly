from __future__ import annotations

import json
from pathlib import Path

from basicly import read_cost

_RECORD_FIELDS = ("issue_id", "agent", "tier", "status", "turns", "tokens", "cost_usd")

_MEASURED_ERROR = {
    "prose": 0.016,
    "beads-json": -0.107,
    "run-record-json": -0.164,
    "run-record-md-headings": -0.289,
    "run-record-tsv": -0.395,
}


def _write(repo: Path, rel: str, chars: int) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * chars, encoding="utf-8")


def _run_records(count: int) -> list[dict[str, object]]:
    return [
        {
            "issue_id": f"basicly-u2hl.{index}",
            "agent": "claude",
            "tier": "high",
            "status": "landed",
            "turns": 40 + index,
            "tokens": 8_000_000 + index * 13_337,
            "cost_usd": round(12.5 + index * 0.37, 2),
        }
        for index in range(count)
    ]


def _calibrated(estimate: int, payload_format: str) -> float:
    return estimate / (1 + _MEASURED_ERROR[payload_format])


def test_a_format_switch_is_not_rankable_through_the_estimator() -> None:

    records = _run_records(20)
    as_json = "\n".join(json.dumps(record) for record in records)
    as_markdown = "\n".join(
        f"## {record['issue_id']}\n"
        + "\n".join(f"- {field}: {record[field]}" for field in _RECORD_FIELDS)
        for record in records
    )

    estimated_json = read_cost._text_tokens(as_json)
    estimated_markdown = read_cost._text_tokens(as_markdown)
    resolution = 1 - (1 + _MEASURED_ERROR["run-record-md-headings"]) / (
        1 + _MEASURED_ERROR["run-record-json"]
    )
    assert abs(1 - estimated_markdown / estimated_json) < resolution

    real_json = _calibrated(estimated_json, "run-record-json")
    real_markdown = _calibrated(estimated_markdown, "run-record-md-headings")
    assert real_markdown / real_json > 1.10


def test_a_format_saving_read_off_the_estimator_is_not_the_real_saving() -> None:

    records = _run_records(20)
    as_json = "\n".join(json.dumps(record) for record in records)
    as_tsv = "\n".join(
        "\t".join(str(record[field]) for field in _RECORD_FIELDS) for record in records
    )

    estimated_json = read_cost._text_tokens(as_json)
    estimated_tsv = read_cost._text_tokens(as_tsv)
    reported_saving = 1 - estimated_tsv / estimated_json

    real_saving = 1 - _calibrated(estimated_tsv, "run-record-tsv") / _calibrated(
        estimated_json, "run-record-json"
    )
    assert reported_saving > real_saving > 0
    assert reported_saving - real_saving > 0.10


def test_instruction_overhead_tokenizes_agents_md(tmp_path: Path) -> None:
    assert read_cost.instruction_overhead(tmp_path) == 0
    _write(tmp_path, "AGENTS.md", 8_000)
    assert read_cost.instruction_overhead(tmp_path) == 2_000


def test_scope_read_cost_sums_matching_files_once(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", 400)
    _write(tmp_path, "src/b.py", 200)
    _write(tmp_path, "docs/c.md", 999)
    cost = read_cost.scope_read_cost(tmp_path, ("src/*.py", "src/a.py"))
    assert cost == (400 + 200) // 4


def test_scope_read_cost_recursive_glob_and_greenfield(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/deep/mod.py", 800)
    assert read_cost.scope_read_cost(tmp_path, ("src/**/*.py",)) == 200
    assert read_cost.scope_read_cost(tmp_path, ("brand/new/file.py",)) == 0


def test_a_large_file_is_sized_at_what_a_lane_reads_out_of_it(tmp_path: Path) -> None:

    _write(tmp_path, "src/big.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 10)

    cost = read_cost.scope_read_cost(tmp_path, ("src/big.py",))
    assert cost == read_cost.SCOPE_FILE_READ_CAP
    assert cost < read_cost._text_tokens("x" * (read_cost.SCOPE_FILE_READ_CAP * 4 * 10))


def test_a_file_under_the_read_cap_still_costs_all_of_itself(tmp_path: Path) -> None:

    _write(tmp_path, "src/small.py", 800)
    assert read_cost.scope_read_cost(tmp_path, ("src/small.py",)) == 200


def test_the_read_cap_applies_per_file_so_a_wider_scope_still_costs_more(
    tmp_path: Path,
) -> None:

    for name in ("a", "b", "c"):
        _write(tmp_path, f"src/{name}.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 3)

    assert read_cost.scope_read_cost(tmp_path, ("src/a.py",)) == read_cost.SCOPE_FILE_READ_CAP
    assert read_cost.scope_read_cost(tmp_path, ("src/*.py",)) == 3 * read_cost.SCOPE_FILE_READ_CAP


def test_scope_read_cost_keeps_dot_directory_scopes(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert read_cost.scope_read_cost(tmp_path, (".claude/rules/*.md",)) == 100
    assert read_cost.scope_read_cost(tmp_path, ("./.claude/rules/*.md",)) == 100
    _write(tmp_path, "src/a.py", 40)
    assert read_cost.scope_read_cost(tmp_path, ("./src/a.py",)) == 10


def test_scope_read_cost_excludes_dependency_and_cache_trees(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", 40)
    _write(tmp_path, ".venv/lib/dep.py", 4000)
    _write(tmp_path, "node_modules/pkg/index.py", 4000)
    _write(tmp_path, "src/__pycache__/a.cpython-314.pyc", 4000)
    _write(tmp_path, ".git/hooks/thing.py", 4000)
    assert read_cost.scope_read_cost(tmp_path, ("**/*.py",)) == 10


def test_scope_read_cost_keeps_project_authored_dot_directories(tmp_path: Path) -> None:
    _write(tmp_path, ".basicly/core/skills/s/skill.yaml", 400)
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert read_cost.scope_read_cost(tmp_path, (".basicly/**",)) == 100
    assert read_cost.scope_read_cost(tmp_path, (".claude/**",)) == 100


def test_scope_read_cost_reads_a_file_named_like_an_excluded_dir(tmp_path: Path) -> None:
    _write(tmp_path, "src/venv", 40)
    assert read_cost.scope_read_cost(tmp_path, ("src/**",)) == 10


def test_scope_read_cost_skips_unglobbable_patterns(tmp_path: Path) -> None:
    _write(tmp_path, "etc/conf.py", 40)
    assert read_cost.scope_read_cost(tmp_path, ("/etc/conf.py",)) == 10
    assert read_cost.scope_read_cost(tmp_path, ("c:/nowhere/*.py",)) == 0
