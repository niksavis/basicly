from __future__ import annotations


def block_body(text: str, name: str) -> list[str]:
    lines = text.splitlines()
    begin = next(i for i, line in enumerate(lines) if f"docs-claims:begin {name}" in line)
    end = next(i for i, line in enumerate(lines) if f"docs-claims:end {name}" in line)
    return [line.strip() for line in lines[begin + 1 : end] if line.strip()]


def cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]
