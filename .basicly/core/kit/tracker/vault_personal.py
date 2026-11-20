from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

LEDGER_FIELD = "ledger"
MIN_IDENTITY_LENGTH = 4
PUBLIC_MAIL_DOMAINS = frozenset({
    "example.invalid",
    "example.com",
    "users.noreply.github.com",
    "github.com",
})
EMAIL_MARKER = "<redacted:email>"
EMAIL = re.compile(
    r"(?<![\\A-Za-z0-9._%+-])(?:<redacted:[a-z][a-z0-9-]*>)?[A-Za-z0-9._%+-]*"
    r"[A-Za-z0-9_%+-]@(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![A-Za-z0-9-])"
)


def _private(found: re.Match[str]) -> bool:
    return found.group("domain").lower() not in PUBLIC_MAIL_DOMAINS


def redact_emails(text: str) -> str:
    return EMAIL.sub(lambda found: EMAIL_MARKER if _private(found) else found.group(0), text)


def count_emails(text: str) -> int:
    return sum(1 for found in EMAIL.finditer(text) if _private(found))


def email_scan(lines: Sequence[Mapping[str, object]]) -> dict[str, int]:
    text = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
    return {"other email": count_emails(text)}


Scan = Callable[[Sequence[Mapping[str, object]]], dict[str, int]]


def _counts(found: dict[str, int]) -> str:
    return ", ".join(f"{label} {count}" for label, count in sorted(found.items()) if count)


def in_import(scan: Scan | None, lines: Sequence[Mapping[str, object]]) -> str | None:
    if scan is None:
        return (
            "an import needs a personal-data scan; "
            "pass scan=vault_personal.email_scan or a stricter one"
        )
    counts = _counts(scan(lines))
    if not counts:
        return None
    return (
        f"the import still carries personal data ({counts}); nothing was published. "
        "Add --redact-name for each remaining form and preview again"
    )


def in_vault_copy(
    scan: Scan | None, held: Sequence[Mapping[str, Any]], found: Sequence[Any]
) -> str | None:
    if scan is None:
        return None
    wanted = {event.id for event in found}
    copy = [
        line
        for line in held
        if isinstance(line.get(LEDGER_FIELD), dict) and line[LEDGER_FIELD].get("id") in wanted
    ]
    counts = _counts(scan(copy))
    if not counts:
        return None
    return (
        f"the vault already carries this source, but its copy holds personal data ({counts}); "
        "the vault copy must be rewritten before the vault is made public"
    )
