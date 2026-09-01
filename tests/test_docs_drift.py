"""Tripwires tying two documents to the code they document.

``docs/reference/cli.md``'s tables must cover exactly the registered subcommands, and
``docs/architecture/architecture.md``'s fragment table must match ``schema.py`` — so doc
drift fails CI instead of accumulating (basicly-kd8).

The two are read differently, and that is the CLI reference being its own document
(basicly-mfavrh): it is gated whole, so nothing keys on a heading in it. The
architecture document is sliced by heading text, and renaming a heading must move the
constant here in the same change, or ``_section`` raises rather than silently asserting
nothing.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
from pathlib import Path

import pytest

from basicly import agents, cli, config, schema, tracker_paths

ARCHITECTURE_MD = Path(__file__).parent.parent / "docs" / "architecture" / "architecture.md"
CLI_MD = Path(__file__).parent.parent / "docs" / "reference" / "cli.md"
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
def cli_reference() -> str:
    """The CLI reference text, whole: every table in it tabulates commands."""
    return CLI_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def registered_commands() -> set[str]:
    """Every top-level subcommand the CLI parser registers."""
    parser = cli._build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def test_the_cli_reference_lists_every_registered_command(
    cli_reference: str, registered_commands: set[str]
) -> None:
    """A new subcommand must gain a reference row before it ships."""
    spans = re.findall(r"`([^`]+)`", cli_reference)
    documented_words = {word for span in spans for word in re.findall(r"[a-z][a-z-]+", span)}

    missing = sorted(registered_commands - documented_words)
    assert not missing, f"registered commands absent from the CLI reference: {missing}"


def test_the_cli_reference_documents_only_registered_commands(
    cli_reference: str, registered_commands: set[str]
) -> None:
    """A removed or renamed subcommand must leave the CLI reference's tables."""
    table_rows = [line for line in cli_reference.splitlines() if line.startswith("|")]
    documented = {
        match.group(1) for row in table_rows for match in re.finditer(r"`basicly ([a-z-]+)", row)
    }

    stale = sorted(documented - registered_commands)
    assert not stale, f"the CLI reference documents unregistered commands: {stale}"


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


def _subsection(text: str, heading: str) -> str:
    """The body of one ``### `` subsection, up to the next ``### `` or ``## `` heading."""
    start = text.index(heading)
    rest = text[start + len(heading) :]
    ends = [offset for offset in (rest.find("\n### "), rest.find("\n## ")) if offset != -1]
    return heading + (rest[: min(ends)] if ends else rest)


def test_section_36_2_check_counts_match_the_verify_configuration(architecture: str) -> None:
    """The per-mode table and the total are re-derived, never read (basicly-byvpvx).

    They were 22/26/27 against a configuration holding 35/39/40: every number in the
    table was wrong, because a check lands in a `basicly.d` fragment and nothing here
    was tied to the assembled result.
    """
    section = _subsection(architecture, "### 36.2 The verify pipeline")
    verify = config.load_verify_config(Path(__file__).parent.parent)
    documented = {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"^\| (fast|full|staged) \| (\d+) \|", section, re.MULTILINE)
    }
    total = re.search(r"declares (\d+) checks in total", section)
    assert total is not None, "the verify-pipeline subsection states no check total"

    assert documented == {mode: len(verify.for_mode(mode)) for mode in config.VERIFY_MODES}
    assert int(total.group(1)) == len(verify.checks)


def test_section_36_1_names_the_mode_each_hook_stage_runs(architecture: str) -> None:
    """Layer 3 runs pre-push's mode, not pre-commit's — the two differ by four checks."""
    hooks = Path(__file__).parent.parent / ".basicly" / "core" / "hooks"
    modes: dict[str, str] = {}
    for stage in ("pre-commit", "pre-push"):
        source = (hooks / f"{stage}.py").read_text(encoding="utf-8")
        found = re.search(r"run_checks\(\w+, \"(\w+)\"", source)
        assert found is not None, f"{stage}.py makes no run_checks call to read a mode from"
        modes[stage] = found.group(1)
    section = _subsection(architecture, "### 36.1 The four layers")

    assert f"`{modes['pre-push']}` mode as layer 2's **pre-push**" in section
    assert f"**pre-commit** stage runs the narrower `{modes['pre-commit']}`" in section


def test_section_30_counts_the_agent_sources_and_the_shared_blocks(architecture: str) -> None:
    """The diagram said four shared blocks against a catalog holding five."""
    core = Path(__file__).parent.parent / agents.CORE_AGENTS_DIR
    sources = len(list(core.glob(f"*/{agents.AGENT_SOURCE_FILE}")))
    blocks = len(list((core / agents.BLOCKS_DIR_NAME).glob(agents.BLOCK_SOURCE_GLOB)))
    section = _section(architecture, "## 30. Roles at dispatch")

    assert f"{sources} agent.yaml sources" in section
    assert f"plus {blocks} shared blocks" in section


def test_the_board_serve_row_names_every_serve_flag(cli_reference: str) -> None:
    """A flag absent from the row is a surface a reader cannot know about.

    The row claimed `board serve` answered GET alone while `do_POST` has run an action
    route since it landed; `--bind` and `--no-actions` were undocumented with it.
    """
    parser = cli._build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    board_sub = next(
        a for a in top.choices["board"]._actions if isinstance(a, argparse._SubParsersAction)
    )
    flags = {
        option
        for action in board_sub.choices["serve"]._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }
    row = next(
        line for line in cli_reference.splitlines() if line.startswith("| `basicly board serve")
    )

    missing = sorted(flag for flag in flags if flag not in row)
    assert not missing, f"the board serve row names no {missing}"


def test_section_27_1_names_the_live_redirect_file_and_its_resolver(architecture: str) -> None:
    """It said the redirect went with the external binary; both symbols are live."""
    section = _subsection(architecture, "### 27.1 The worktree")

    module = tracker_paths.__name__.split(".")[-1]

    assert f"`{tracker_paths.REDIRECT_NAME}` file" in section
    assert f"`{module}.{tracker_paths.tracker_root.__name__}`" in section


# docs/architecture/architecture.md §14 and §16 point at a gated block instead of typing an
# inventory. The pointer is the block's name, then the file holding it — read as a pattern
# rather than as two literals, so a third pointer is covered by the test landing with it.
_BLOCK_POINTER = re.compile(r"generated `([a-z-]+)` block in\s+`([^`]+)`")


def test_a_generated_block_the_document_points_at_exists(architecture: str) -> None:
    """A pointer at a block that no longer exists is the stale count in a new shape.

    Two hand-typed inventories were replaced by a pointer at the gated block that renders
    them (basicly-728lzf). That trades a number nothing read for a path nothing read, unless
    the path is checked: a renamed or deleted block would leave those two sections sending a
    reader to a table that is not there, and reading as correct.
    """
    pointers = _BLOCK_POINTER.findall(architecture)
    assert pointers, "the architecture document names no generated block to point at"

    for name, path in pointers:
        text = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
        assert f"<!-- docs-claims:begin {name} -->" in text, (
            f"the document points at a `{name}` block in {path}, which holds no such marker"
        )
