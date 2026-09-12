from __future__ import annotations

import getpass
import re

_GENERIC_RULE = "generic-secret-assignment"

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

_PLACEHOLDER = re.compile(
    r"(?i)example|changeme|placeholder|redacted|dummy|sample|your[-_ ]"
    r"|<[^>]+>|x{4,}|\.\.\.|test[-_]?(?:value|secret|token|key|password)"
)


def _placeholder(rule: str) -> str:
    return f"<redacted:{rule}>"


def redact_secrets(text: str) -> str:

    if not text:
        return text
    for rule, pattern in _RULES:

        def _sub(match: re.Match[str], rule: str = rule) -> str:
            if rule == _GENERIC_RULE and _PLACEHOLDER.search(match.group(0)):
                return match.group(0)
            return _placeholder(rule)

        text = pattern.sub(_sub, text)
    return text


_PATH_TAIL = r"[^\s\"'`,;)\]}]*"

MACHINE_PATH_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("posix-home-path", re.compile(rf"/(?:home|Users)/[A-Za-z0-9._-]+{_PATH_TAIL}")),
    ("windows-unc-path", re.compile(rf"\\\\\?\\[A-Za-z]:\\{_PATH_TAIL}")),
    ("windows-drive-path", re.compile(rf"[A-Za-z]:\\{_PATH_TAIL}")),
)


def redact_machine_paths(text: str) -> str:

    if not text:
        return text
    for rule, pattern in MACHINE_PATH_RULES:
        text = pattern.sub(_placeholder(rule), text)
    return text


IDENTITY_RULE = "machine-username"

MIN_IDENTITY_LENGTH = 4


def machine_identity() -> str:
    try:
        name = getpass.getuser()
    except KeyError, OSError:
        return ""
    return name if len(name) >= MIN_IDENTITY_LENGTH else ""


def redact_machine_identity(text: str) -> str:
    name = machine_identity()
    if not text or not name:
        return text
    return re.sub(rf"\b{re.escape(name)}\b", _placeholder(IDENTITY_RULE), text)


_ENV_ASSIGNMENT = r"[A-Z][A-Z0-9_]*=\S*"

_DUMP_RUN = 8

_CREDENTIAL_NAME = r"[A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASSWD)"

_CREDENTIAL_VALUE = r"""(?:'[^']{4,}'|"[^"]{4,}"|\S{4,})"""

ENVIRONMENT_DUMP_RULE = "environment-dump"
ENV_CREDENTIAL_RULE = "env-credential-assignment"

_DUMP_PATTERN = re.compile(rf"(?:{_ENV_ASSIGNMENT}\s+){{{_DUMP_RUN - 1},}}{_ENV_ASSIGNMENT}")
_CREDENTIAL_PATTERN = re.compile(rf"(?<![A-Za-z0-9_])({_CREDENTIAL_NAME}=)({_CREDENTIAL_VALUE})")


def _credential_assignment(match: re.Match[str]) -> str:
    if _PLACEHOLDER.search(match.group(2)):
        return match.group(0)
    return f"{match.group(1)}{_placeholder(ENV_CREDENTIAL_RULE)}"


def redact_environment(text: str) -> str:

    if not text:
        return text
    text = _DUMP_PATTERN.sub(_placeholder(ENVIRONMENT_DUMP_RULE), text)
    return _CREDENTIAL_PATTERN.sub(_credential_assignment, text)


def redact_committed(text: str) -> str:

    return redact_machine_identity(redact_machine_paths(redact_secrets(redact_environment(text))))
