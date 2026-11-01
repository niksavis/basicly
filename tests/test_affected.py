from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from basicly import affected, cli


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / ".gitignore").write_text(".basicly/usage/\n")
    (tmp_path / "value.txt").write_text("PASS")
    provider = (
        "import json,sys; request=json.load(sys.stdin); "
        "print(json.dumps({**request,'complete':True,'selected':['python'],"
        "'reasons':{'python':'changed project','web':'outside dependency closure'},"
        "'unverified':[]}))"
    )
    body = (
        '[verify.affected]\nchecks = ["python", "web"]\ncommand = '
        + json.dumps([sys.executable, "-c", provider])
        + "\n"
    )
    for name in ["python", "web", "guard"]:
        program = (
            "from pathlib import Path; "
            "p=Path('.basicly/usage/executed'); p.parent.mkdir(parents=True,exist_ok=True); "
            f"p.open('a').write('{name}\\n'); "
            + (
                "raise SystemExit(0 if Path('value.txt').read_text()=='PASS' else 1)"
                if name == "python"
                else ""
            )
        )
        body += (
            "\n[[verify.checks]]\nname = "
            + json.dumps(name)
            + '\nmodes = ["fast", "full"]\ncommand = '
            + json.dumps([sys.executable, "-c", program])
            + "\n"
        )
    (tmp_path / "basicly.toml").write_text(body)
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-qm", "fixture baseline")
    (tmp_path / "value.txt").write_text("FAIL")
    _git(tmp_path, "add", "--", "value.txt")
    (tmp_path / "value.txt").write_text("PASS")
    return tmp_path


def _events(root: Path) -> list[str]:
    return (root / ".basicly/usage/executed").read_text().splitlines()


def test_affected_cli_refuses_staged_failure_hidden_by_worktree_repair(
    consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(consumer)
    original = _git(consumer, "diff", "--cached", "--binary")
    assert cli.main(["verify", "--affected"]) == 1
    assert _events(consumer) == ["python", "guard"]
    assert (consumer / "value.txt").read_text() == "PASS"
    assert _git(consumer, "diff", "--cached", "--binary") == original
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert not report["passed"]
    assert report["selection"]["selected"] == ["python", "guard"]
    assert {c["name"]: c["status"] for c in report["checks"]} == {
        "python": "fail",
        "web": "skip",
        "guard": "pass",
    }
    assert "python: FAIL" in capsys.readouterr().out


def test_ordinary_cli_checks_the_staged_candidate_by_default(
    consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(consumer)
    assert cli.main(["verify"]) == 1
    assert _events(consumer) == ["python", "guard"]
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert report["mode"] == "staged"
    assert report["selection"]["selected"] == ["python", "guard"]


def test_standalone_git_runner_executes_the_same_selected_staged_checks(consumer: Path) -> None:
    source = Path(__file__).resolve().parents[1] / ".basicly/core/hooks"
    program = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0,{str(source)!r}); "
        "from check_runner import run_checks; "
        "raise SystemExit(run_checks(Path.cwd(),'fast',scope_to_diff=True))"
    )
    original = _git(consumer, "diff", "--cached", "--binary")
    with affected.snapshot(consumer) as tree:
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tree,
            env=affected.environment(tree),
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 1, result.stdout + result.stderr
    assert _events(consumer) == ["python", "guard"]
    assert (consumer / "value.txt").read_text() == "PASS"
    assert _git(consumer, "diff", "--cached", "--binary") == original
    report = json.loads((consumer / ".basicly/usage/affected-run.json").read_text())
    assert report["selection"]["selected"] == ["python", "guard"]
    assert [c["disposition"] for c in report["checks"]] == ["executed", "not-selected", "executed"]


def test_snapshot_context_restores_the_callers_environment(consumer: Path) -> None:
    original = dict(os.environ)
    with affected.snapshot(consumer) as tree:
        env = affected.environment(tree)
        assert env["GIT_INDEX_FILE"] != str(consumer / ".git/index")
        assert env["BASICLY_SNAPSHOT_TREE"]
    assert dict(os.environ) == original
    assert affected.environment(consumer) == original


@pytest.mark.parametrize("option", ["--fix", "--issue"])
def test_affected_cli_refuses_to_repair_or_record_partial_evidence(
    consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
) -> None:
    monkeypatch.chdir(consumer)
    argv = ["verify", "--affected", option] + (["fixture-1"] if option == "--issue" else [])
    assert cli.main(argv) == 1
    assert not (consumer / ".basicly/usage/executed").exists()


def _stage_provider(root: Path, code: str) -> None:
    path = root / "basicly.toml"
    text = path.read_text()
    old = json.dumps(tomllib.loads(text)["verify"]["affected"]["command"])
    assert text.count(old) == 1
    path.write_text(text.replace(old, json.dumps([sys.executable, "-c", code])))
    _git(root, "add", "--", "basicly.toml")


@pytest.mark.parametrize("program", ["print('not json')", "raise SystemExit(2)"])
def test_cli_provider_failure_runs_all_commands_and_keeps_staged_failure(
    consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
    program: str,
) -> None:
    monkeypatch.chdir(consumer)
    _stage_provider(consumer, program)
    original = _git(consumer, "diff", "--cached", "--binary")
    assert cli.main(["verify", "--affected"]) == 1
    assert _events(consumer) == ["python", "web", "guard"]
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert report["selection"]["fallback"]
    assert _git(consumer, "diff", "--cached", "--binary") == original
    assert (consumer / "value.txt").read_text() == "PASS"


def test_cli_refuses_provider_repair_of_the_staged_failure(
    consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(consumer)
    _stage_provider(
        consumer,
        "from pathlib import Path; Path('value.txt').write_text('PASS'); print('not json')",
    )
    original = _git(consumer, "diff", "--cached", "--binary")
    assert cli.main(["verify", "--affected"]) == 1
    assert not (consumer / ".basicly/usage/executed").exists()
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert report["selection"]["invalid_snapshot"]
    assert _git(consumer, "diff", "--cached", "--binary") == original
    assert (consumer / "value.txt").read_text() == "PASS"


def test_snapshot_uses_staged_local_configuration_not_unstaged_invalid_copy(
    consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(consumer)
    data = tomllib.loads((consumer / "basicly.toml").read_text())
    command = data["verify"]["affected"]["command"]
    command[-1] = command[-1].replace("'selected':['python']", "'selected':['python','web']")
    local = consumer / "basicly.local.toml"
    local.write_text(
        '[verify.affected]\nchecks=["python","web"]\ncommand=' + json.dumps(command) + "\n"
    )
    _git(consumer, "add", "--", "basicly.local.toml")
    local.write_text("invalid TOML [\n")
    original = _git(consumer, "diff", "--cached", "--binary")
    assert cli.main(["verify", "--affected"]) == 1
    assert _events(consumer) == ["python", "web", "guard"]
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert not report["selection"]["fallback"]
    assert local.read_text() == "invalid TOML [\n"
    assert _git(consumer, "diff", "--cached", "--binary") == original


@pytest.mark.parametrize("duplicate", [False, True])
def test_declared_inputs_select_checks_without_a_provider(
    consumer: Path, monkeypatch: pytest.MonkeyPatch, duplicate: bool
) -> None:
    config = consumer / "basicly.toml"
    text = config.read_text()
    text = text[text.index("[[verify.checks]]") :]
    text = text.replace('name = "python"', 'name = "python"\ninputs = ["value.txt"]')
    text = text.replace('name = "web"', 'name = "web"\ninputs = ["web/**"]')
    if duplicate:
        text = text.replace('name = "web"', 'name = "python"')
    config.write_text(text)
    _git(consumer, "add", "basicly.toml")
    _git(consumer, "commit", "-qm", "input rules", "--only", "basicly.toml")
    monkeypatch.chdir(consumer)
    assert cli.main(["verify"]) == 1
    assert _events(consumer) == (["python", "web", "guard"] if duplicate else ["python", "guard"])
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert report["selection"]["paths"] == ["value.txt"]
    if duplicate:
        assert report["selection"]["selected"] == ["python", "python", "guard"]
        assert "duplicate" in report["selection"]["fallback"]
    else:
        assert report["selection"]["selected"] == ["python", "guard"]
        assert "undeclared" in report["selection"]["reasons"]["guard"]
        assert "inputs" in report["selection"]["reasons"]["web"]


def test_documentation_index_failure_is_selected_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).with_name("test_docs_index.py").read_text()
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_docs_index.py").write_text(source)
    (tmp_path / "docs/architecture").mkdir(parents=True)
    index = tmp_path / "docs/architecture/conventions.md"
    index.write_text("# Documentation\n")
    (tmp_path / "basicly.toml").write_text(
        '[[verify.checks]]\nname="docs-index"\nmodes=["staged"]\n'
        'inputs=["docs/**/*.md"]\ncommand='
        + json.dumps([sys.executable, "-m", "pytest", "tests/test_docs_index.py", "-q"])
        + '\n[[verify.checks]]\nname="unrelated"\nmodes=["staged"]\n'
        'inputs=["library.py"]\ncommand='
        + json.dumps([sys.executable, "-c", "raise SystemExit(1)"])
        + "\n"
    )
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-qm", "baseline")
    (tmp_path / "docs/unlisted.md").write_text("# New evidence\n")
    _git(tmp_path, "add", "docs/unlisted.md")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["verify"]) == 1
    failed = json.loads((tmp_path / ".basicly/usage/verify-run.json").read_text())
    assert failed["selection"]["selected"] == ["docs-index"]
    assert failed["checks"][0]["status"] == "fail"
    assert failed["checks"][1]["status"] == "skip"
    index.write_text("# Documentation\n\nunlisted.md\n")
    _git(tmp_path, "add", "docs/architecture/conventions.md")
    assert cli.main(["verify"]) == 0


def test_no_staged_changes_do_not_run_declared_checks(
    consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = consumer / "basicly.toml"
    text = config.read_text()
    config.write_text(text[text.index("[[verify.checks]]") :])
    _git(consumer, "add", "--all")
    _git(consumer, "commit", "-qm", "clean baseline")
    monkeypatch.chdir(consumer)
    assert cli.main(["verify"]) == 0
    report = json.loads((consumer / ".basicly/usage/verify-run.json").read_text())
    assert report["selection"]["selected"] == []
    assert all(check["status"] == "skip" for check in report["checks"])


def test_full_only_configuration_cannot_pass_default_local_verification(
    consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = consumer / "basicly.toml"
    config.write_text(config.read_text().replace('["fast", "full"]', '["full"]'))
    _git(consumer, "add", "basicly.toml")
    monkeypatch.chdir(consumer)
    assert cli.main(["verify"]) == 1
    assert not (consumer / ".basicly/usage/executed").exists()


@pytest.mark.parametrize("jobs", [1, 2])
def test_parallel_checks_keep_their_snapshot_environment(
    consumer: Path, monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    (consumer / ".venv").mkdir()
    program = (
        "import os; "
        'assert os.environ.get("UV_NO_SYNC") == "1"; '
        'assert os.environ.get("BASICLY_SNAPSHOT_TREE"); '
        'assert os.environ.get("GIT_INDEX_FILE")'
    )
    (consumer / "basicly.toml").write_text(
        f'[verify]\njobs={jobs}\n[[verify.checks]]\nname="context"\n'
        'modes=["staged"]\ncommand=' + json.dumps([sys.executable, "-c", program]) + "\n"
    )
    _git(consumer, "add", "basicly.toml")
    monkeypatch.chdir(consumer)
    assert cli.main(["verify"]) == 0
