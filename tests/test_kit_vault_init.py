from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
PROVIDER_CALLS = re.compile(
    r"""["']gh["']|\bgh\s+(api|repo|auth|pr)\b|api\.github\.com|api\.bitbucket\.org|/rest/api/"""
)


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("tracker_cli_vault_init", "cli.py")
vault_shapes = cli.vault_cli.vault_shapes


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def _empty(tmp_path: Path, name: str = "vault.git") -> Path:
    bare = tmp_path / name
    _git("init", "--bare", "-q", "-b", "main", str(bare), cwd=tmp_path)
    return bare


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _code_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "code"
    repo.mkdir()
    _git("init", "-q", str(repo), cwd=tmp_path)
    return repo


@pytest.fixture(autouse=True)
def _identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "init test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "init@test.invalid")


def test_init_prints_the_name_permissions_and_files_and_pushes_nothing_without_yes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = _empty(tmp_path)

    code, shown = _run(["vault-init", "--vault", str(bare)], capsys)

    assert code == 0
    assert shown["suggested_name"] == "backlog-vault"
    assert any("force-push" in line for line in shown["permissions"])
    assert shown["will_push"] == ["FORMAT.md", "README.md", "reader.py", "vault.toml"]
    assert shown["pushed"] is False
    assert _git("ls-remote", str(bare), cwd=tmp_path) == ""


def test_init_with_yes_pushes_a_genesis_the_shape_check_accepts_and_writes_the_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = _empty(tmp_path)
    repo = _code_repo(tmp_path)
    monkeypatch.chdir(repo)

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)

    assert code == 0
    assert shown["pushed"] is True
    verdicts = vault_shapes.check(bare, "refs/heads/main")
    assert [(v.shape, v.valid) for v in verdicts] == [(vault_shapes.GENESIS, True)]
    config = (repo / ".basicly-tracker.toml").read_text(encoding="utf-8")
    assert 'prefix = "acme"' in config
    assert f'vault = "{bare}"' in config


def test_init_refuses_a_repository_that_already_has_commits_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = _empty(tmp_path)
    assert _run(["vault-init", "--vault", str(bare), "--yes"], capsys)[0] == 0

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes"], capsys)

    assert code == 2
    assert "already has commits" in shown["refused"]
    assert str(shown["vault"]) in shown["refused"]


def test_init_with_no_address_and_no_terminal_names_the_flag(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    code, shown = _run(["vault-init"], capsys)

    assert code == 1
    assert "--vault" in shown["fix"]
    assert shown["suggested_name"] == "backlog-vault"


def test_a_claim_on_a_vault_with_no_genesis_says_so_and_names_init(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = _empty(tmp_path)
    argv = ["claim", "acme-a1b2", "--vault", str(bare), "--state", str(tmp_path / "st")]

    code, shown = _run([*argv, "--session", "s1", "--deadline", "5"], capsys)

    assert code == 8
    assert shown["unsettled"] == "the vault has no genesis commit"
    assert shown["next"].startswith("vault-init --vault")


def test_a_claim_in_a_repository_with_tracker_toml_needs_no_vault_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = _empty(tmp_path)
    repo = _code_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)[0] == 0
    state = str(tmp_path / "st")

    code, shown = _run(["claim", "acme-a1b2", "--state", state, "--session", "s1"], capsys)

    assert code == 0, shown
    assert shown["record"] == "acme-a1b2"


def _provider_hits(paths: list[Path]) -> list[str]:
    return [
        f"{path.name}:{number}"
        for path in paths
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if PROVIDER_CALLS.search(line)
    ]


def test_no_vault_or_tracker_module_calls_gh_or_a_host_api(tmp_path: Path) -> None:
    control = tmp_path / "planted.py"
    control.write_text('runner(["gh", "api", "repos"])\nurl = "https://api.github.com/x"\n')
    modules = [*sorted(KIT.glob("vault_*.py")), KIT / "git_runner.py"]

    assert _provider_hits([control]) == ["planted.py:1", "planted.py:2"]
    assert len(modules) >= 12
    assert _provider_hits(modules) == []
