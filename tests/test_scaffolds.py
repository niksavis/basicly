"""The file bodies `basicly install` writes into a consumer repo.

These are literal templates, never parsed by the engine — so the only thing that can
go wrong is that one of them stops being valid in the format its *consumer* parses it
as, and nothing here would notice. A malformed `tasks.json` breaks a consumer's editor
and a malformed workflow breaks their CI, in both cases at install time in someone
else's repository. That is what these assertions exist to catch.
"""

from __future__ import annotations

import json

import yaml

from basicly.scaffolds import CONSUMER_CI_WORKFLOW, VSCODE_TASKS_JSON


def test_the_vscode_scaffold_parses_as_the_jsonc_vscode_reads() -> None:
    """It ships with `//` comments, which is JSONC — valid to VS Code, not to `json`.

    Asserted by stripping the comment lines and parsing the remainder, because the
    thing that must hold is that everything *other* than the comments is well-formed
    JSON. A trailing comma or an unclosed brace survives a substring check and breaks
    the consumer's editor silently.
    """
    body = "\n".join(
        line for line in VSCODE_TASKS_JSON.splitlines() if not line.strip().startswith("//")
    )
    parsed = json.loads(body)
    assert parsed["version"] == "2.0.0"
    assert parsed["tasks"], "the scaffold would install an empty task list"


def test_every_scaffolded_task_is_a_single_command() -> None:
    """No `&&` chaining: the scaffold's own contract is that it works in PowerShell 5.

    `cmd` and PowerShell 5 do not accept `&&`, so a chained command is a task that is
    broken on exactly the platforms this project claims to support.
    """
    body = "\n".join(
        line for line in VSCODE_TASKS_JSON.splitlines() if not line.strip().startswith("//")
    )
    for task in json.loads(body)["tasks"]:
        assert "&&" not in task["command"], f"{task['label']} chains with && "


def test_every_scaffolded_task_is_labelled_and_described() -> None:
    """A task with no label is unrunnable from the palette; one with no detail is a guess."""
    body = "\n".join(
        line for line in VSCODE_TASKS_JSON.splitlines() if not line.strip().startswith("//")
    )
    for task in json.loads(body)["tasks"]:
        assert task.get("label", "").startswith("basicly: ")
        assert task.get("detail")


def test_the_ci_scaffold_parses_as_yaml_and_declares_its_triggers() -> None:
    """`on` is quoted in the source because bare `on` is YAML 1.1's boolean True.

    That is the classic workflow footgun, and it is why the assertion reads the key
    back rather than trusting the file looks right: if the quoting were ever dropped,
    the parsed mapping would carry a `True` key and GitHub would run nothing.
    """
    parsed = yaml.safe_load(CONSUMER_CI_WORKFLOW)
    assert parsed["name"] == "basicly-gates"
    assert "on" in parsed, "bare `on` parsed as a boolean — the workflow would not trigger"
    assert parsed["on"]["push"]["branches"] == ["main"]


def test_the_ci_scaffold_keeps_tracker_only_pushes_out_of_ci() -> None:
    """The harness commits `.beads/**` separately from the work it describes.

    Without this path filter every tracker commit spends a CI run, which is the cost
    the comment above the constant claims it avoids — so it is worth binding.
    """
    parsed = yaml.safe_load(CONSUMER_CI_WORKFLOW)
    assert ".beads/**" in parsed["on"]["push"]["paths-ignore"]
