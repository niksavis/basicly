"""Tripwires tying docs/architecture/architecture.md to the code it documents.

The CLI section's tables must cover exactly the registered subcommands, and the
fragment section's schema table must match ``schema.py`` — so doc drift fails CI
instead of accumulating (basicly-kd8).

The document has no section numbers, so each anchor is the heading text; renaming
a heading must move the constant here in the same change, or ``_section`` raises
rather than silently asserting nothing.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
from pathlib import Path

import pytest

from basicly import cli, schema

ARCHITECTURE_MD = Path(__file__).parent.parent / "docs" / "architecture" / "architecture.md"
CLI_SECTION = "## 22. The CLI surface"
FRAGMENT_SECTION = "## 13. The fragment model"


def _section(text: str, heading: str) -> str:
    """The body of one ``## `` section, up to the next ``## `` heading."""
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
    """A new subcommand must gain a CLI-section row before it ships."""
    section = _section(architecture, CLI_SECTION)
    spans = re.findall(r"`([^`]+)`", section)
    documented_words = {word for span in spans for word in re.findall(r"[a-z][a-z-]+", span)}

    missing = sorted(registered_commands - documented_words)
    assert not missing, f"registered commands absent from the architecture CLI section: {missing}"


def test_section_8_documents_only_registered_commands(
    architecture: str, registered_commands: set[str]
) -> None:
    """A removed or renamed subcommand must leave the CLI section's tables."""
    section = _section(architecture, CLI_SECTION)
    table_rows = [line for line in section.splitlines() if line.startswith("|")]
    documented = {
        match.group(1) for row in table_rows for match in re.finditer(r"`basicly ([a-z-]+)", row)
    }

    stale = sorted(documented - registered_commands)
    assert not stale, f"the architecture CLI section documents unregistered commands: {stale}"


def test_section_5_categories_match_schema(architecture: str) -> None:
    """The category row of the fragment field table equals schema.CATEGORIES."""
    section = _section(architecture, FRAGMENT_SECTION)
    category_row = next(line for line in section.splitlines() if line.startswith("| `category`"))
    values_cell = category_row.split("|")[3]
    documented = set(re.findall(r"`([a-z-]+)`", values_cell))

    assert documented == schema.CATEGORIES, (
        f"the architecture fragment table diverges from schema.CATEGORIES: "
        f"doc-only {sorted(documented - schema.CATEGORIES)}, "
        f"code-only {sorted(schema.CATEGORIES - documented)}"
    )


def test_section_5_field_rows_exist_on_fragment(architecture: str) -> None:
    """Every field the fragment table names is a real Fragment field.

    Doc -> code only: the dataclass also carries internal fields (``body``,
    ``source_path``, ``title``) that deliberately stay out of the authoring
    table, so the reverse direction is not enforced.
    """
    section = _section(architecture, FRAGMENT_SECTION)
    field_names = {
        match.group(1)
        for line in section.splitlines()
        if line.startswith("| `")
        for match in [re.match(r"\| `([a-z_.]+)`", line)]
        if match
    }
    fragment_fields = {f.name for f in dataclasses.fields(schema.Fragment)}

    unknown = sorted(name for name in field_names if name.replace(".", "_") not in fragment_fields)
    assert not unknown, f"the architecture fragment table names unknown Fragment fields: {unknown}"
