from __future__ import annotations

import argparse

import pytest

from tests.test_kit_vault_view import cli


def test_cli_field_values_keep_json_types_and_explicit_overrides() -> None:
    shape = argparse.Namespace(
        acceptance="The title is readable.", requirements="", description="A public body."
    )
    found = cli.cli_fields.fields(
        "Initial title", ["priority=2", "enabled=true", "title=Final title"], shape
    )
    assert found == {
        "title": "Final title",
        "priority": 2,
        "enabled": True,
        "acceptance_criteria": "The title is readable.",
        "description": "A public body.",
    }


@pytest.mark.parametrize("pair", ["missing-value", "=missing-name"])
def test_cli_field_syntax_refuses_an_incomplete_pair(pair: str) -> None:
    with pytest.raises(ValueError, match="not name=value"):
        cli.cli_fields.fields("title", [pair])
