from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from basicly import redact as engine_redact

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


JSDOC = "  /** Server-attested source. */ token" + ': "github_webhook";\n'
REAL = "password" + ' = "' + "Qx7vR2mPz9" + '"\n'


def _entry(stderr: str, reason: str) -> str:
    start = stderr.index("[[allow]]")
    block = stderr[start:].split("\n\n", 1)[0].splitlines()[:5]
    assert block[-1] == 'reason = ""'
    return "\n".join([*block[:-1], f"reason = {reason!r}"]) + "\n"


def _install_hook(repo: Path) -> None:
    _git(repo, "config", "core.hooksPath", ".git/hooks")
    hook = repo / ".git" / "hooks" / "pre-commit"
    script = SCRIPT.as_posix()
    hook.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "{script}"\n', newline="")
    hook.chmod(0o755)


def _commit(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_proof_a_printed_allow_entry_admits_the_jsdoc_line_and_nothing_else(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _install_hook(repo)
    (repo / "types.ts").write_text(JSDOC, encoding="utf-8")
    _git(repo, "add", "types.ts")

    first = _commit(repo, "first")
    entry = _entry(first.stderr, "a generated type name, not a credential")
    (repo / scan.ALLOW_FILE).write_text(entry, encoding="utf-8")
    _git(repo, "add", scan.ALLOW_FILE)
    second = _commit(repo, "second")
    (repo / "app.py").write_text(REAL, encoding="utf-8")
    _git(repo, "add", "app.py")
    third = _commit(repo, "third")

    digest = scan.line_hash(JSDOC.rstrip("\n"))
    assert first.returncode == 1
    assert f"types.ts:1: generic-secret-assignment sha256:{digest}" in first.stderr
    assert "A person must read each generic-secret-assignment line above first" in first.stderr
    assert second.returncode == 0, second.stderr
    assert third.returncode == 1
    assert "app.py:1: generic-secret-assignment" in third.stderr


def _allow(repo: Path, path: str, line: str, **fields: str) -> None:
    values = {
        "rule": "generic-secret-assignment",
        "path": path,
        "line_sha256": scan.line_hash(line),
        "reason": "read by a person",
        **fields,
    }
    body = "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items())
    (repo / scan.ALLOW_FILE).write_text("[[allow]]\n" + body, encoding="utf-8")
    _git(repo, "add", scan.ALLOW_FILE)


def _stage(repo: Path, path: str, text: str) -> None:
    (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", path)


def test_an_entry_never_admits_a_key_format_rule_or_another_line(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    mixed = "token" + ': "github_webhook" ' + GITHUB
    _stage(repo, "types.ts", f"{JSDOC}{mixed}\n{REAL}")
    _allow(repo, "types.ts", mixed)

    result = _run_hook(repo)

    assert result.returncode == 1
    assert "types.ts:2: github-token" in result.stderr
    assert "types.ts:3: generic-secret-assignment" in result.stderr
    assert "types.ts:1: generic-secret-assignment" in result.stderr


def test_a_changed_line_makes_the_entry_stale_and_is_refused_again(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "types.ts", JSDOC)
    _allow(repo, "types.ts", JSDOC.rstrip("\n"))
    assert _run_hook(repo).returncode == 0
    _git(repo, "commit", "-q", "-m", "seed", "--no-verify")

    changed = JSDOC.replace("github_webhook", "github_webhooks")
    _stage(repo, "types.ts", changed)
    result = _run_hook(repo)

    assert result.returncode == 1
    assert "allow-entry-stale" in result.stderr
    assert f"sha256:{scan.line_hash(changed.rstrip(chr(10)))}" in result.stderr


def test_an_unrelated_commit_passes_while_the_allowed_line_stays(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "types.ts", JSDOC)
    _allow(repo, "types.ts", JSDOC.rstrip("\n"))
    _git(repo, "commit", "-q", "-m", "seed", "--no-verify")

    _stage(repo, "app.py", "total = 1\n")

    assert _run_hook(repo).returncode == 0


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"rule": "github-token"}, "a key-format rule is never allowed"),
        ({"reason": "  "}, "reason must say why"),
        ({"line_sha256": "abc"}, "line_sha256 must be the 64-character hash"),
    ],
)
def test_an_invalid_entry_fails_the_scan_by_name(
    tmp_path: Path, fields: dict[str, str], named: str
) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "types.ts", JSDOC)
    _allow(repo, "types.ts", JSDOC.rstrip("\n"), **fields)

    result = _run_hook(repo)

    assert result.returncode == 1
    assert "allow-entry-invalid" in result.stderr
    assert named in result.stderr


def test_an_unstaged_allow_file_admits_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "types.ts", JSDOC)
    _allow(repo, "types.ts", JSDOC.rstrip("\n"))
    _git(repo, "rm", "-q", "--cached", scan.ALLOW_FILE)

    result = _run_hook(repo)

    assert result.returncode == 1
    assert "types.ts:1: generic-secret-assignment" in result.stderr


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KIT_REDACT = _load(REPO_ROOT / ".basicly/core/kit/tracker/redact.py", "secret_scan_kit_redact")


def _table(rules: tuple) -> list[tuple[str, str, int]]:
    return [(name, pattern.pattern, pattern.flags) for name, pattern in rules]


def test_the_hook_and_both_redactors_hold_one_secret_rule_table() -> None:
    assert _table(scan._RULES) == _table(engine_redact._RULES)
    assert _table(scan._RULES) == _table(KIT_REDACT.SECRET_RULES)
    assert scan._PLACEHOLDER.pattern == engine_redact._PLACEHOLDER.pattern
    assert scan._PLACEHOLDER.pattern == KIT_REDACT.PLACEHOLDER.pattern
    assert scan._GENERIC_RULE == engine_redact._GENERIC_RULE == KIT_REDACT.GENERIC_RULE


def test_intended_difference_the_hook_reads_placeholders_on_the_whole_line() -> None:
    line = "token" + ' = "' + "abcdefgh12" + '"  # example'
    marker = "<redacted:generic-secret-assignment>"

    assert scan.rule_hit(line) is None
    assert marker in engine_redact.redact_secrets(line)
    assert marker in KIT_REDACT.text(line)


def test_intended_difference_only_the_hook_honours_the_pragma() -> None:
    line = f"{GITHUB}  # {scan.ALLOWLIST_PRAGMA}"

    assert scan.rule_hit(line) is None
    assert GITHUB not in engine_redact.redact_secrets(line)
    assert GITHUB not in KIT_REDACT.text(line)
