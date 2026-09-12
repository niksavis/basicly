from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "secret-scan.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("secret_scan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load_hook()

AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB = "ghp_" + "B" * 36
GITLAB = "glpat-" + "C" * 24
SLACK = "xoxb-" + "111111111-abcdefghijkl"
TEAMS = "https://t." + "webhook.office.com/webhookb2/" + "d" * 20
TELEGRAM = "123456789" + ":" + "E" * 35
GOOGLE = "AIza" + "k" * 35
OPENAI = "sk-" + "l" * 40
STRIPE = "sk_live_" + "f" * 24
NPM = "npm_" + "g" * 36
JWT = "eyJ" + "h" * 12 + ".eyJ" + "i" * 12 + ".sig" + "j" * 12
PK = "-----BEGIN RSA PRIVATE " + "KEY-----"
GENERIC = "api_key" + ' = "' + "s3cr3tValue123" + '"'


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (AWS, "aws-access-key-id"),
        (GITHUB, "github-token"),
        (GITLAB, "gitlab-token"),
        (SLACK, "slack-token"),
        (TEAMS, "teams-webhook"),
        (TELEGRAM, "telegram-bot-token"),
        (GOOGLE, "google-api-key"),
        (OPENAI, "openai-key"),
        (STRIPE, "stripe-key"),
        (NPM, "npm-token"),
        (JWT, "jwt"),
        (PK, "private-key"),
        (GENERIC, "generic-secret-assignment"),
    ],
)
def test_rule_hit_flags_each_pattern(text: str, rule: str) -> None:
    assert scan.rule_hit(text) == rule


def test_rule_hit_passes_clean_placeholder_and_allowlisted() -> None:
    assert scan.rule_hit("total = sum(orders)") is None
    assert scan.rule_hit("api_key" + ' = "changeme-please"') is None
    assert scan.rule_hit("token" + ' = "' + "your-token-here" + '"') is None
    assert scan.rule_hit(f"{AWS}  # {scan.ALLOWLIST_PRAGMA}") is None


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _run_hook(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_hook_passes_on_clean_staged_content(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("total = sum(orders)\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    assert _run_hook(repo).returncode == 0


def test_hook_blocks_staged_secret_with_location(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "app.py").write_text(f'total = 1\nkey = "{AWS}"\n', encoding="utf-8")
    _git(repo, "add", "app.py")
    result = _run_hook(repo)
    assert result.returncode == 1
    assert "app.py:2: aws-access-key-id" in result.stderr


def test_hook_scans_only_added_lines(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    app = repo / "app.py"
    app.write_text(f'v = "{GITHUB}"\nvalue = 1\n', encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "seed", "--no-verify")
    app.write_text(f'v = "{GITHUB}"\nvalue = 2\n', encoding="utf-8")
    _git(repo, "add", "app.py")
    assert _run_hook(repo).returncode == 0


def test_hook_scans_added_line_that_looks_like_a_diff_header(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(f'++ leaked = "{AWS}"\n', encoding="utf-8")
    _git(repo, "add", "notes.txt")
    result = _run_hook(repo)
    assert result.returncode == 1
    assert "notes.txt:1: aws-access-key-id" in result.stderr


def test_hook_allowlist_pragma_lets_it_through(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "app.py").write_text(f'key = "{AWS}"  # {scan.ALLOWLIST_PRAGMA}\n', encoding="utf-8")
    _git(repo, "add", "app.py")
    assert _run_hook(repo).returncode == 0
