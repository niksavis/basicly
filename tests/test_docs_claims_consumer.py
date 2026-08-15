"""Consumer surfaces must not advertise a command the CLI does not ship (a4q3.4).

Split from ``test_docs_claims`` when the module-size ratchet caught that file
crossing the cap, matching the split of the claim itself into
``.scripts/docs_claim_surfaces.py``.

The test that carries the weight here is the *negative* one. These surfaces already
say "basicly requires python 3.14" and carry an ``alt="basicly logo"`` attribute, so
a check that read every "basicly <word>" as an interface claim would report three
commands nobody advertised. Break ``code_spans`` and
``test_prose_naming_the_tool_is_not_read_as_a_command_claim`` is what goes red.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_module():
    """Load the docs-claims script module from its path (it is not a package)."""
    script_path = REPO / ".scripts" / "docs_claims.py"
    spec = importlib.util.spec_from_file_location("docs_claims", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load_module()


def _run(root: Path, mode: str) -> int:
    """Invoke the script's entry point against *root*."""
    return claims.main([mode, "--root", str(root)])


def test_the_consumer_surfaces_name_only_commands_the_cli_ships() -> None:
    """The committed README and site advertise no command that does not exist."""
    for surface in claims.surfaces.CONSUMER_SURFACES:
        assert claims.surfaces.consumer_commands_exist(REPO, surface=surface) == []


@pytest.mark.parametrize(
    ("surface", "claim"),
    [
        ("README.md", "\n`basicly deploy` ships it.\n"),
        ("site/index.html", "\n<code>basicly deploy</code>\n"),
    ],
)
def test_check_fails_when_a_consumer_surface_advertises_a_missing_command(
    work_repo: Path, capsys: pytest.CaptureFixture[str], surface: str, claim: str
) -> None:
    """Both surfaces are covered, and each is proved to fire on its own syntax."""
    path = work_repo / surface
    path.write_text(path.read_text(encoding="utf-8") + claim, encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[consumer-commands]" in err
    assert "basicly deploy" in err


def test_a_missing_subcommand_of_a_real_group_is_caught(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`basicly loop` exists, so only the second token can be the false claim."""
    path = work_repo / "README.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n`basicly loop teleport`\n", "utf-8")

    assert _run(work_repo, "--check") == 1
    assert "basicly loop teleport" in capsys.readouterr().err


def test_prose_naming_the_tool_is_not_read_as_a_command_claim(work_repo: Path) -> None:
    """The discriminator is code formatting, and it has to hold in both directions.

    A check that read every "basicly <word>" as an interface claim would fire on
    the sentences these surfaces already carry — "basicly requires python 3.14",
    "basicly also owns", an `alt="basicly logo"` attribute — and a gate that cries
    wolf on authored prose gets its surface excluded rather than its claim fixed.
    """
    path = work_repo / "README.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nbasicly deploys nothing.\n", "utf-8")

    assert _run(work_repo, "--check") == 0
