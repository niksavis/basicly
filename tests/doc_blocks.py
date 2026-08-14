"""Read a `docs-claims` generated block out of a committed markdown document.

The boundary is reading the artifact against judging it: this knows the marker pair and
table row `.scripts/docs_claims.py` writes and nothing about which claims are true, which
is why `test_docs_claims` and `test_plan_claims` can share it (basicly-5p49).
"""

from __future__ import annotations


def block_body(text: str, name: str) -> list[str]:
    """The lines strictly between a marker pair, stripped of their indentation."""
    lines = text.splitlines()
    begin = next(i for i, line in enumerate(lines) if f"docs-claims:begin {name}" in line)
    end = next(i for i, line in enumerate(lines) if f"docs-claims:end {name}" in line)
    return [line.strip() for line in lines[begin + 1 : end] if line.strip()]


def cells(row: str) -> list[str]:
    """The content cells of a markdown table row."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]
