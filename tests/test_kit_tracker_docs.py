from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
PACKAGE = REPO_ROOT / "packages" / "basicly-tracker"
DOCUMENTS = (
    KIT_DIR / "GUIDANCE.md",
    KIT_DIR / "INSTRUCTION.md",
    KIT_DIR / "REFERENCE.md",
    KIT_DIR / "SPEC.md",
    PACKAGE / "README.md",
)
ENTRY = "python3 .basicly/kit/tracker/cli.py "
FIRST = frozenset({"<id>", "<parent-id>", "acme-a1b2"})
SECOND = frozenset({"<the-id-it-waits-on>", "acme-c3d4"})
REFUSING = frozenset({"dor"})
REFUSED_ON_A_FRESH_LEDGER = {"resolve": "no unresolved conflict"}
LEFT_BEHIND = frozenset({".basicly", ".git", ".gitattributes", ".gitignore", ".agents", ".claude"})
LEFT_BEHIND |= {"issues.jsonl", "tracker-board.html"}


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer = _load(REPO_ROOT / "packages" / "kit_installer.py", "kit_installer_docs")
cli = _load(KIT_DIR / "cli.py", "tracker_cli_docs")


def _commands(document: Path) -> list[str]:
    text = re.sub(r"\\\n\s*", " ", document.read_text(encoding="utf-8"))
    found = []
    for line in text.splitlines():
        stripped = line.strip().removeprefix("$ ")
        if stripped.startswith(ENTRY):
            found.append(stripped[len(ENTRY) :])
    return found


DOCUMENTED = [
    pytest.param(command, id=f"{document.name}:{command[:40]}")
    for document in DOCUMENTS
    for command in _commands(document)
]


def _consumer(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # nosec B603 B607
    kit = installer.Kit(
        command="basicly-tracker",
        name="tracker",
        directory=KIT_DIR,
        module="basicly_tracker_docs_under_test",
        configure_file="install_hook.py",
        configure_args=("--ledger", ".basicly/ledger"),
    )
    sys.modules.pop("basicly_kit_configure_tracker", None)
    assert installer.run(kit, ["init", "--into", str(tmp_path)]) == 0
    (tmp_path / "issues.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def _run(repo: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(repo / ".basicly" / "kit" / "tracker" / "cli.py"), *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _record(repo: Path) -> str:
    made = _run(repo, ["create", ".basicly/ledger", "--prefix", "acme", "--title", "seed"])
    return json.loads(made.stdout)["record"]


def test_the_documents_name_commands_at_all() -> None:
    assert len(DOCUMENTED) >= 30


@pytest.mark.parametrize("command", DOCUMENTED)
def test_every_documented_command_runs_on_a_fresh_install(command: str, tmp_path: Path) -> None:
    repo = _consumer(tmp_path)
    first, second = _record(repo), _record(repo)
    names = {**dict.fromkeys(FIRST, first), **dict.fromkeys(SECOND, second), "<p>": "acme"}
    argv = [names.get(token, token) for token in shlex.split(command, comments=True)]

    done = _run(repo, argv)

    report = json.loads(done.stdout)
    expected = REFUSED_ON_A_FRESH_LEDGER.get(argv[0])
    if expected is not None:
        assert expected in str(report.get("refused")), report
        return
    assert not isinstance(report.get("refused"), str), report
    assert done.returncode == 0 or argv[0] in REFUSING, done.stdout
    assert {path.name for path in repo.iterdir()} <= LEFT_BEHIND


def test_the_reference_names_every_command_the_parser_defines() -> None:
    parser = cli.arguments.parser()
    commands = next(
        action.choices for action in parser._actions if isinstance(action.choices, dict)
    )
    headings = set(
        re.findall(r"^### (\S+)$", (KIT_DIR / "REFERENCE.md").read_text("utf-8"), re.MULTILINE)
    )

    assert headings == set(commands)
