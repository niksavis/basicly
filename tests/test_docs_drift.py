"""Tripwires tying docs/architecture.md to the code it documents.

Section 8's CLI tables must cover exactly the registered subcommands, and
section 5's schema table must match ``schema.py`` — so doc drift fails CI
instead of accumulating (basicly-kd8).
"""

from __future__ import annotations

import argparse
import dataclasses
import re
from pathlib import Path

import pytest

from basicly import cli, schema

ARCHITECTURE_MD = Path(__file__).parent.parent / "docs" / "architecture.md"


def _section(text: str, heading: str) -> str:
    """The body of one ``## N)`` section, up to the next ``## `` heading."""
    start = text.index(heading)
    end = text.find("\n## ", start)
    return text[start:end] if end != -1 else text[start:]


@pytest.fixture(scope="module")
def architecture() -> str:
    """The architecture doc text, read once for the module."""
    return ARCHITECTURE_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def registered_commands() -> set[str]:
    """Every top-level subcommand the CLI parser registers."""
    parser = cli._build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def test_section_8_lists_every_registered_command(
    architecture: str, registered_commands: set[str]
) -> None:
    """A new subcommand must gain a section 8 row before it ships."""
    section = _section(architecture, "## 8) CLI surface")
    spans = re.findall(r"`([^`]+)`", section)
    documented_words = {word for span in spans for word in re.findall(r"[a-z][a-z-]+", span)}

    missing = sorted(registered_commands - documented_words)
    assert not missing, f"registered commands absent from architecture.md section 8: {missing}"


def test_section_8_documents_only_registered_commands(
    architecture: str, registered_commands: set[str]
) -> None:
    """A removed or renamed subcommand must leave the section 8 tables."""
    section = _section(architecture, "## 8) CLI surface")
    table_rows = [line for line in section.splitlines() if line.startswith("|")]
    documented = {
        match.group(1) for row in table_rows for match in re.finditer(r"`basicly ([a-z-]+)", row)
    }

    stale = sorted(documented - registered_commands)
    assert not stale, f"architecture.md section 8 documents unregistered commands: {stale}"


def test_section_5_categories_match_schema(architecture: str) -> None:
    """The category row of the section 5 field table equals schema.CATEGORIES."""
    section = _section(architecture, "## 5) Fragment model")
    category_row = next(line for line in section.splitlines() if line.startswith("| `category`"))
    values_cell = category_row.split("|")[3]
    documented = set(re.findall(r"`([a-z-]+)`", values_cell))

    assert documented == schema.CATEGORIES, (
        f"architecture.md section 5 category vocabulary diverges from schema.CATEGORIES: "
        f"doc-only {sorted(documented - schema.CATEGORIES)}, "
        f"code-only {sorted(schema.CATEGORIES - documented)}"
    )


def test_section_5_field_rows_exist_on_fragment(architecture: str) -> None:
    """Every field the section 5 table names is a real Fragment field.

    Doc -> code only: the dataclass also carries internal fields (``body``,
    ``source_path``, ``title``) that deliberately stay out of the authoring
    table, so the reverse direction is not enforced.
    """
    section = _section(architecture, "## 5) Fragment model")
    field_names = {
        match.group(1)
        for line in section.splitlines()
        if line.startswith("| `")
        for match in [re.match(r"\| `([a-z_.]+)`", line)]
        if match
    }
    fragment_fields = {f.name for f in dataclasses.fields(schema.Fragment)}

    unknown = sorted(name for name in field_names if name.replace(".", "_") not in fragment_fields)
    assert not unknown, f"architecture.md section 5 names unknown Fragment fields: {unknown}"
