from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_module():
    script_path = REPO / ".scripts" / "docs_claims.py"
    spec = importlib.util.spec_from_file_location("docs_claims", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load_module()


def _run(root: Path, mode: str) -> int:
    return claims.main([mode, "--root", str(root)])


def test_the_consumer_surfaces_name_only_commands_the_cli_ships() -> None:
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
    path = work_repo / surface
    path.write_text(path.read_text(encoding="utf-8") + claim, encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[consumer-commands]" in err
    assert "basicly deploy" in err


def test_a_missing_subcommand_of_a_real_group_is_caught(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = work_repo / "README.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n`basicly loop teleport`\n", "utf-8")

    assert _run(work_repo, "--check") == 1
    assert "basicly loop teleport" in capsys.readouterr().err


def test_prose_naming_the_tool_is_not_read_as_a_command_claim(work_repo: Path) -> None:

    path = work_repo / "README.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nbasicly deploys nothing.\n", "utf-8")

    assert _run(work_repo, "--check") == 0


@pytest.mark.parametrize(
    ("label", "markdown", "flagged"),
    [
        ("prose in a text fence", "```text\nwhen basicly finishes the run\n```\n", False),
        ("a diagram fence", "```mermaid\ngraph TD; basicly deploy\n```\n", False),
        ("plain prose", "basicly deploys nothing\n", False),
        ("a shell fence", "```sh\nbasicly deploy\n```\n", True),
        ("a bare fence", "```\nbasicly deploy\n```\n", True),
        ("a tilde fence", "~~~sh\nbasicly deploy\n~~~\n", True),
        ("inline code", "run `basicly deploy` now\n", True),
        ("a doubled space", "`basicly  deploy`\n", True),
    ],
)
def test_only_shell_formatted_spans_are_read_as_claims(
    work_repo: Path, label: str, markdown: str, flagged: bool
) -> None:

    path = work_repo / "README.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + markdown, encoding="utf-8")

    assert (_run(work_repo, "--check") == 1) is flagged, label


@pytest.mark.parametrize(
    ("invocation", "flagged"),
    [
        ("basicly install teleport", True),
        ("basicly verify fast", True),
        ("basicly brief basicly-a4q3", False),
        ("basicly loop supervise", False),
        ("basicly check", False),
    ],
)
def test_a_trailing_word_is_judged_against_what_the_command_accepts(
    work_repo: Path, invocation: str, flagged: bool
) -> None:

    path = work_repo / "README.md"
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n```sh\n{invocation}\n```\n", encoding="utf-8"
    )

    assert (_run(work_repo, "--check") == 1) is flagged, invocation
