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
