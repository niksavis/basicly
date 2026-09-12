from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

LEVELS = ("L1", "L2", "L3")

CLASSIFICATION_MARKER = "[harness-classification]"

DEFAULT_DOWNGRADE_MAX_LINES = 20


@dataclass(frozen=True)
class _Rule:
    name: str
    level: str
    globs: tuple[str, ...]

    def claims(self, path: str) -> bool:

        entry = _normalize(path)
        if not entry:
            return False
        matched = False
        for glob in self.globs:
            if glob.startswith("!"):
                if _covers(entry, glob[1:]):
                    return False
            elif _covers(entry, glob):
                matched = True
        return matched


_CONSUMER_SURFACES: tuple[_Rule, ...] = (
    _Rule("cli-surface", "L3", ("src/basicly/cli.py",)),
    _Rule(
        "config-surface",
        "L3",
        ("src/basicly/config.py", "basicly.toml", "basicly.local.toml"),
    ),
    _Rule(
        "catalog-source-schemas",
        "L3",
        ("src/basicly/schema.py", ".basicly/core/schemas/**"),
    ),
    _Rule(
        "generated-file-contract",
        "L3",
        (
            "src/basicly/projection.py",
            "src/basicly/renderers/**",
            ".basicly/core/templates/**",
        ),
    ),
    _Rule("ledger-format", "L3", ("src/basicly/run_record.py",)),
)

_CONSUMER_GLOBS: tuple[str, ...] = tuple(g for rule in _CONSUMER_SURFACES for g in rule.globs)

_DOCS_AND_TESTS = _Rule(
    "docs-and-tests",
    "L1",
    ("docs/**", "tests/**", "site/**", "changelog.d/**", "**/*.md"),
)

_ENGINE = _Rule(
    "engine-internal",
    "L2",
    ("src/basicly/**", ".scripts/**", *(f"!{glob}" for glob in _CONSUMER_GLOBS), "!**/*.md"),
)

_RULES: tuple[_Rule, ...] = (*_CONSUMER_SURFACES, _DOCS_AND_TESTS, _ENGINE)

_FALLBACK = _Rule("unclassified", "L2", ())

_BY_NAME: dict[str, _Rule] = {rule.name: rule for rule in (*_RULES, _FALLBACK)}


@dataclass(frozen=True)
class Selection:
    gates: tuple[str, ...]
    model_tier: str
    rework_allowance: int
    ship: str


VALIDATE_GATE = "validate-as-consumer"

_SELECTIONS: dict[str, Selection] = {
    "L1": Selection(gates=("fast",), model_tier="medium", rework_allowance=1, ship="delegable"),
    "L2": Selection(gates=("full",), model_tier="high", rework_allowance=2, ship="delegable"),
    "L3": Selection(
        gates=("full", VALIDATE_GATE, "evidence-binding"),
        model_tier="maximum",
        rework_allowance=2,
        ship="human",
    ),
}


def selection_for(level: str) -> Selection:

    try:
        return _SELECTIONS[level]
    except KeyError:
        raise ValueError(
            f"unknown integrity level {level!r}; expected one of {list(LEVELS)}"
        ) from None


@dataclass(frozen=True)
class Assignment:
    level: str
    rule: str
    reason: str
    selection: Selection


def _normalize(entry: str) -> str:

    text = entry.strip().replace("\\", "/").lstrip("/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _covers(entry: str, pattern: str) -> bool:

    left, right = PurePosixPath(entry), PurePosixPath(pattern)
    return left.full_match(pattern) or right.full_match(entry)


def claiming_rules(path: str) -> tuple[str, ...]:

    return tuple(rule.name for rule in _RULES if rule.claims(path))


def _rule_for_path(path: str) -> _Rule:

    claimed = claiming_rules(path)
    return _BY_NAME[claimed[0]] if claimed else _FALLBACK


def assign(
    scope: Iterable[str],
    *,
    patch: str | None = None,
    downgrade_max_lines: int = DEFAULT_DOWNGRADE_MAX_LINES,
) -> Assignment:

    entries = [normalized for raw in scope if (normalized := _normalize(str(raw)))]
    if not entries:
        return Assignment(
            level=_FALLBACK.level,
            rule=_FALLBACK.name,
            reason="no scope declared, so no path was classified",
            selection=selection_for(_FALLBACK.level),
        )

    decided, rule = max(
        ((entry, _rule_for_path(entry)) for entry in entries),
        key=lambda pair: LEVELS.index(pair[1].level),
    )
    base = Assignment(
        level=rule.level,
        rule=rule.name,
        reason=f"{decided!r} is {rule.name}",
        selection=selection_for(rule.level),
    )
    if base.level != "L3" or patch is None:
        return base

    changed_lines, signature_changed = _diff_facts(patch)
    if changed_lines >= downgrade_max_lines or signature_changed:
        return base
    return Assignment(
        level="L2",
        rule="downgrade",
        reason=(
            f"downgraded from L3 ({base.reason}): {changed_lines} changed lines is under the "
            f"{downgrade_max_lines}-line threshold and no public signature changed"
        ),
        selection=selection_for("L2"),
    )


_PUBLIC_SIGNATURE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?!_)\w+")
_FILE_HEADER = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+)$")


def _diff_facts(patch: str) -> tuple[int, bool]:

    changed = 0
    signature_changed = False
    python_file = False
    for line in patch.splitlines():
        if line.startswith("+++"):
            if header := _FILE_HEADER.match(line):
                python_file = header.group("path").endswith(".py")
            continue
        if line.startswith(("---", "@@")) or not line.startswith(("+", "-")):
            continue
        changed += 1
        if not python_file or _PUBLIC_SIGNATURE.match(line[1:]):
            signature_changed = True
    return changed, signature_changed
