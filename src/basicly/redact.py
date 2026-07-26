"""Redact high-signal secrets from surfaced text (basicly-3p2i).

Captured runner stdout/stderr is surfaced to the user and read by the loop, so a
credential an agent echoes would leak there. :func:`redact_secrets` replaces each
high-signal secret shape with a labeled placeholder, leaving secret-free text
untouched — a fail-safe guardrail (over-redaction never leaks).

The pattern set is the sibling of the ``secret-scan`` pre-commit hook
(``.basicly/core/hooks/secret-scan.py``, basicly-yzyd): the same high-signal
shapes, kept in step by convention. The hook is a standalone stdlib script copied
to consumers, so it cannot import this module — the shapes are mirrored, not
shared. Keep both edited together when a rule changes.
"""

from __future__ import annotations

import re

# Name of the noisier rule that also honors the placeholder allowlist below.
_GENERIC_RULE = "generic-secret-assignment"

# (rule name, pattern) — mirrors secret-scan.py `_RULES`. High-signal credential
# shapes first; the generic secret-named assignment last (placeholders filtered).
# The generic rule is the cross-vendor backstop (it catches any `<secret-word> =
# "..."`, so a provider with no distinctive token shape — e.g. Azure/Teams — is
# still covered); the named rules add precise, no-false-positive hits.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}")),
    (
        "teams-webhook",
        re.compile(r"https://[A-Za-z0-9.-]+\.webhook\.office\.com/[A-Za-z0-9@/._-]{10,}"),
    ),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        _GENERIC_RULE,
        re.compile(
            r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key"
            r"|private[_-]?key|client[_-]?secret|bearer|credential|webhook"
            r"|connection[_-]?string)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
)

# Substrings that mark a generic-rule match as a placeholder, not a real secret.
_PLACEHOLDER = re.compile(
    r"(?i)example|changeme|placeholder|redacted|dummy|sample|your[-_ ]"
    r"|<[^>]+>|x{4,}|\.\.\.|test[-_]?(?:value|secret|token|key|password)"
)


def _placeholder(rule: str) -> str:
    return f"<redacted:{rule}>"


def redact_secrets(text: str) -> str:
    """Return *text* with every high-signal secret replaced by ``<redacted:<rule>>``.

    Secret-free text is returned unchanged. The generic secret-named-assignment
    rule skips obvious placeholders (``changeme``, ``<...>`` …) so common
    non-secret assignments are left intact; the specific-token rules always
    redact (their shapes are distinctive enough to be secrets).
    """
    if not text:
        return text
    for rule, pattern in _RULES:

        def _sub(match: re.Match[str], rule: str = rule) -> str:
            if rule == _GENERIC_RULE and _PLACEHOLDER.search(match.group(0)):
                return match.group(0)
            return _placeholder(rule)

        text = pattern.sub(_sub, text)
    return text


# --- Machine-specific paths (basicly-vkh0.5) --------------------------------

# Shapes that identify a filesystem location on one particular machine. A path
# like this is published when it reaches a committed artifact, and it is a wrong
# answer everywhere else: the repo's hard constraint is that no machine-specific
# path, username, or hostname is ever committed.
#
# These are matched against *parsed* strings — a JSON value after decoding, not
# the escaped source text — so one literal backslash is one backslash here.
# Mirrored by the ``tracker-path-scan`` pre-commit hook, which parses each record
# for exactly that reason; ``test_tracker_path_scan`` asserts the two sets stay
# equal, so this is a checked mirror rather than a convention.
# Where a path stops. Path characters are almost unrestricted, so the tail runs
# to whitespace or the punctuation that ends a path in prose. Redacting the whole
# path, not just its user-identifying head, is deliberate: the tail is the
# directory layout, which is machine-specific in its own right.
_PATH_TAIL = r"[^\s\"'`,;)\]}]*"

MACHINE_PATH_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # A POSIX or macOS home directory: the segment after it is a username.
    ("posix-home-path", re.compile(rf"/(?:home|Users)/[A-Za-z0-9._-]+{_PATH_TAIL}")),
    # A Windows UNC/extended-length prefix, checked before the bare drive rule so
    # the longer form wins the label and the report names the shape found.
    ("windows-unc-path", re.compile(rf"\\\\\?\\[A-Za-z]:\\{_PATH_TAIL}")),
    # A Windows drive-rooted path. Deliberately not narrowed to `\Users\`: the
    # leak this rule was written for was `\\?\C:\Development\basicly`, which
    # carries a working-directory layout and no username at all.
    ("windows-drive-path", re.compile(rf"[A-Za-z]:\\{_PATH_TAIL}")),
)


def redact_machine_paths(text: str) -> str:
    """Return *text* with every machine-specific path replaced by a placeholder.

    Fail-safe in the same direction as :func:`redact_secrets`: over-redaction
    costs a little detail, under-redaction publishes someone's home directory. A
    reader who needs the original has the local tracker database, which is
    git-ignored and keeps full fidelity — only the committed export is redacted.
    """
    if not text:
        return text
    for rule, pattern in MACHINE_PATH_RULES:
        text = pattern.sub(_placeholder(rule), text)
    return text
