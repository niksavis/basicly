from __future__ import annotations

from pathlib import Path

from basicly import listing_budget
from basicly.context_window import ADAPTER_WINDOWS, CODEX_FALLBACK_WINDOW
from basicly.skill_source import discover_skills


def _skill(root: Path, slug: str, description: str, extra: str = "") -> None:
    path = root / ".basicly/core/skills" / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"schema_version: 1\nname: {slug}\ninvocation: model\n"
        f'description: "{description}"\n{extra}'
        "instructions: |\n  # x\n\n  text\n",
        encoding="utf-8",
    )


def _hosts(root: Path) -> dict[str, listing_budget._Host]:
    return {host.name: host for host in listing_budget.hosts(root)}


def test_each_host_takes_its_window_from_the_one_place_the_runner_reads(tmp_path: Path) -> None:
    hosts = _hosts(tmp_path)

    assert hosts["Claude Code"].window == ADAPTER_WINDOWS["claude"].tokens
    assert hosts["Codex"].window == CODEX_FALLBACK_WINDOW
    assert hosts["Codex"].fraction == 0.02


def test_a_listing_over_both_budgets_names_both_hosts(tmp_path: Path) -> None:
    for index in range(60):
        _skill(tmp_path, f"filler-{index}", "x" * 400)

    warnings = listing_budget.listing_budget_warnings(tmp_path)

    assert [warning.split(" skill listing")[0] for warning in warnings] == ["Claude Code", "Codex"]
    assert "20000-character budget" in warnings[0]
    assert "5168-character budget" in warnings[1]


def test_codex_counts_the_skill_path_and_claude_code_does_not(tmp_path: Path) -> None:
    _skill(tmp_path, "pdf-tools", "Reads PDFs. Use when a task reads a PDF.")
    entries = discover_skills(tmp_path)
    hosts = _hosts(tmp_path)

    claude = listing_budget.listing_characters(entries, hosts["Claude Code"])
    codex = listing_budget.listing_characters(entries, hosts["Codex"])

    assert codex - claude == len(".agents/skills/pdf-tools/SKILL.md\n")


def test_a_skill_only_a_person_starts_leaves_the_claude_listing_only(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "deploy",
        "Deploys. Use when a person deploys.",
        "claude:\n  disable-model-invocation: true\n",
    )
    entries = discover_skills(tmp_path)
    hosts = _hosts(tmp_path)

    assert listing_budget.listing_characters(entries, hosts["Claude Code"]) == 0
    assert listing_budget.listing_characters(entries, hosts["Codex"]) > 0
