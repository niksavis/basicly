from __future__ import annotations

import json

import yaml

from basicly import __version__
from basicly.scaffolds import CONSUMER_CI_WORKFLOW, DIST_SOURCE, VSCODE_TASKS_JSON

_SCAFFOLDS = {"tasks.json": VSCODE_TASKS_JSON, "basicly-gates.yml": CONSUMER_CI_WORKFLOW}


def test_no_scaffold_resolves_the_engine_from_a_branch() -> None:

    for name, body in _SCAFFOLDS.items():
        assert "@main" not in body, f"{name} resolves the engine from a branch"


def test_every_scaffolded_uvx_line_pins_the_scaffolding_version() -> None:
    assert DIST_SOURCE.endswith(f"@v{__version__}")
    for name, body in _SCAFFOLDS.items():
        uvx_lines = [line for line in body.splitlines() if "uvx --from" in line]
        assert uvx_lines, f"{name} has no uvx line to pin"
        for line in uvx_lines:
            assert DIST_SOURCE in line, f"{name} carries an unpinned uvx line: {line.strip()}"


def test_the_vscode_scaffold_parses_as_the_jsonc_vscode_reads() -> None:

    body = "\n".join(
        line for line in VSCODE_TASKS_JSON.splitlines() if not line.strip().startswith("//")
    )
    parsed = json.loads(body)
    assert parsed["version"] == "2.0.0"
    assert parsed["tasks"], "the scaffold would install an empty task list"


def test_every_scaffolded_task_is_a_single_command() -> None:

    body = "\n".join(
        line for line in VSCODE_TASKS_JSON.splitlines() if not line.strip().startswith("//")
    )
    for task in json.loads(body)["tasks"]:
        assert "&&" not in task["command"], f"{task['label']} chains with && "


def test_every_scaffolded_task_is_labelled_and_described() -> None:
    body = "\n".join(
        line for line in VSCODE_TASKS_JSON.splitlines() if not line.strip().startswith("//")
    )
    for task in json.loads(body)["tasks"]:
        assert task.get("label", "").startswith("basicly: ")
        assert task.get("detail")


def test_the_ci_scaffold_parses_as_yaml_and_declares_its_triggers() -> None:

    parsed = yaml.safe_load(CONSUMER_CI_WORKFLOW)
    assert parsed["name"] == "basicly-gates"
    assert "on" in parsed, "bare `on` parsed as a boolean — the workflow would not trigger"
    assert parsed["on"]["push"]["branches"] == ["main"]


def test_the_ci_scaffold_keeps_tracker_only_pushes_out_of_ci() -> None:

    parsed = yaml.safe_load(CONSUMER_CI_WORKFLOW)
    assert ".basicly/ledger/**" in parsed["on"]["push"]["paths-ignore"]
