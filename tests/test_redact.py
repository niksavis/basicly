"""Tests for secret redaction of surfaced text (basicly-3p2i).

Every fake secret is assembled by concatenation so committing this file never
self-trips the ``secret-scan`` pre-commit hook (which scans staged added lines
for the same shapes this redactor masks).
"""

from __future__ import annotations

import pytest

from basicly import redact

# Constructed so no literal secret lives on a single line of this file.
PK = "-----BEGIN RSA PRIVATE " + "KEY-----"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB = "ghp_" + "a" * 30
GITLAB = "glpat-" + "a" * 24
SLACK = "xoxb-" + "123456789012-abcdefghij"
SLACK_WH = "https://hooks.slack.com/services/" + "T0/B0/" + "c" * 24
TEAMS = "https://t.webhook" + ".office.com/webhookb2/" + "a" * 20
TELEGRAM = "123456789:" + "A" * 35
GOOGLE = "AIza" + "a" * 35
OPENAI = "sk-" + "a" * 40
STRIPE = "sk_live_" + "a" * 24
NPM = "npm_" + "a" * 36
JWT = "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + ".sig" + "c" * 12


@pytest.mark.parametrize(
    ("secret", "rule"),
    [
        (PK, "private-key"),
        (AWS, "aws-access-key-id"),
        (GITHUB, "github-token"),
        (GITLAB, "gitlab-token"),
        (SLACK, "slack-token"),
        (SLACK_WH, "slack-webhook"),
        (TEAMS, "teams-webhook"),
        (TELEGRAM, "telegram-bot-token"),
        (GOOGLE, "google-api-key"),
        (OPENAI, "openai-key"),
        (STRIPE, "stripe-key"),
        (NPM, "npm-token"),
        (JWT, "jwt"),
    ],
)
def test_redact_replaces_each_high_signal_token(secret: str, rule: str) -> None:
    """Each specific-token shape is replaced by its labeled placeholder."""
    out = redact.redact_secrets(f"log line {secret} tail")
    assert secret not in out
    assert f"<redacted:{rule}>" in out
    assert out.startswith("log line ") and out.endswith(" tail")


def test_redact_generic_assignment_but_skips_placeholders() -> None:
    """A secret-named assignment is redacted; an obvious placeholder is left intact."""
    real = redact.redact_secrets("pass" + 'word = "hunter2xyz"')
    assert "hunter2xyz" not in real and "<redacted:generic-secret-assignment>" in real
    placeholder = "tok" + 'en: "changeme-please"'
    assert redact.redact_secrets(placeholder) == placeholder


@pytest.mark.parametrize(
    "assignment",
    [
        "bea" + 'rer = "abcdefgh1234"',
        "web" + 'hook: "https://relay.corp.io/hook/abcd1234"',
        "cred" + 'ential = "s3cr3tblob99"',
        "conn" + 'ection_string = "srv-db-abcd1234xyz"',
    ],
)
def test_redact_generic_covers_broadened_keywords(assignment: str) -> None:
    """The broadened generic rule catches vendor-agnostic secret assignments."""
    out = redact.redact_secrets(assignment)
    assert "<redacted:generic-secret-assignment>" in out


def test_redact_leaves_secret_free_text_unchanged() -> None:
    """Ordinary output (including rubric-style answers) is returned verbatim."""
    text = "q1: yes - added a regression test\nq2: no - not covered\nBuild OK.\n"
    assert redact.redact_secrets(text) == text


def test_redact_empty_string() -> None:
    """Empty output is a no-op."""
    assert redact.redact_secrets("") == ""


def test_redact_multiple_secrets_in_one_blob() -> None:
    """Every hit in a multi-line blob is redacted independently."""
    blob = f"a {AWS} b\nc {OPENAI} d"
    out = redact.redact_secrets(blob)
    assert AWS not in out and OPENAI not in out
    assert out.count("<redacted:") == 2


# --- Environment dumps on the committed path (basicly-vkh0.33) --------------

# The leaked value's real shape: 32 hex characters, no vendor prefix, so every
# credential rule above is blind to it and only the name beside it identifies it.
ENV_TOKEN = "b3f1" + "9c2a" * 7

# The incident body, in miniature: a comment whose backticks ran `env` as command
# substitution and pasted 152 assignments — machine paths, variable names, and one
# live session token — into a write that reached the committed store.
ENV_PAIRS = (
    ("SHELL", "/bin/zsh"),
    ("PWD", "/home/agentuser/dev/basicly"),
    ("LANG", "en_US.UTF-8"),
    ("TERM", "xterm-256color"),
    ("CLAUDE_CODE_MESSAGING_" + "TOKEN", ENV_TOKEN),
    ("EDITOR", "vim"),
    ("PAGER", "less"),
    ("VIRTUAL_ENV", "/home/agentuser/dev/basicly/.venv"),
    ("XDG_RUNTIME_DIR", "/run/user/1000"),
    ("SHLVL", "2"),
)
ENV_DUMP = "reproducing the build: " + " ".join(f"{k}={v}" for k, v in ENV_PAIRS) + " done"


@pytest.fixture(autouse=True)
def _pinned_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the OS username these committed-path tests run under.

    ``redact_committed`` masks the running user's name too, so on a host called ``test``
    that word in a fixture is rewritten and a control about something else fails there.
    """
    monkeypatch.setattr(redact.getpass, "getuser", lambda: "user-in-no-fixture")


@pytest.mark.parametrize(
    "name",
    [
        "CLAUDE_CODE_MESSAGING_" + "TOKEN",
        "APP_" + "SECRET",
        "SERVICE_API_" + "KEY",
        "DB_PASS" + "WORD",
    ],
)
def test_redact_committed_masks_a_secret_named_environment_assignment(name: str) -> None:
    """The value beside a credential-named variable is masked; the name is kept."""
    out = redact.redact_committed(f"the agent echoed {name}={ENV_TOKEN} into the body")
    assert ENV_TOKEN not in out
    assert f"{name}=<redacted:{redact.ENV_CREDENTIAL_RULE}>" in out


def test_redact_committed_collapses_an_environment_dump() -> None:
    """A block of NAME=value assignments is replaced wholesale, token included."""
    out = redact.redact_committed(ENV_DUMP)
    assert ENV_TOKEN not in out
    assert f"<redacted:{redact.ENVIRONMENT_DUMP_RULE}>" in out
    assert out.startswith("reproducing the build: ") and out.endswith(" done")


def test_redact_committed_leaves_prose_and_short_assignment_runs_unchanged() -> None:
    """Positive control: a body carrying no dump and no credential name is verbatim."""
    text = "set TERM=xterm and LANG=C before the run\nq1: yes - added a regression test\n"
    assert redact.redact_committed(text) == text


def test_redact_committed_leaves_a_python_key_constant_unchanged() -> None:
    """A spaced constant is code, not a shell assignment.

    Measured 2026-08-18: 38 module constants of this spelling live in ``src``,
    ``tests`` and ``.basicly/core``, and ledger payloads quote this repo's own code.
    """
    text = "TEXT_" + 'KEY = "text"  # the field name, not a credential'
    assert redact.redact_committed(text) == text


def test_redact_committed_masks_a_credential_shape() -> None:
    """The credential rules run on the committed path too, not only on runner output."""
    out = redact.redact_committed(f"pushed with {GITHUB} from the lane")
    assert GITHUB not in out and "<redacted:github-token>" in out
