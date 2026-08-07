"""Integrity level: one deterministic rule over declared paths (D9, D11).

Every unit of work gets exactly one of three levels, and the level — not a
judgement, not a prompt — decides the gate set, the model tier and the rework
allowance it earns. The requirements document argues the economics
(`docs/design/factory-loop-requirements.md` §4): a level is the spend gate as
much as the quality gate, so it has to be cheap, total and ungameable.

Three properties this module exists to keep:

- **Deterministic, therefore not gameable** [D9]. The input is the scope globs a
  package already declares and the landing already checks; no model is asked
  what level its own work deserves. It costs zero tokens.
- **Total, and single-valued**. Every path this repo can hold resolves, and no
  path resolves twice. That is structural rather than incidental: the L2 rule
  names the L3 patterns as exclusions instead of relying on match order, so the
  ordering of :data:`_RULES` is presentation, not semantics, and
  ``tests/test_integrity.py`` asserts exactly-one-match over every tracked file.
- **Reads the change, not only where it lives** [D11]. A path rule alone
  over-classifies: a typo fix in ``cli.py`` is not a consumer-surface change. A
  small diff that touches no public signature downgrades L3 to L2 and records
  why, which keeps the ungameable property while spending the L3 budget on the
  changes that actually reach a consumer.

**The L3 set is not invented here.** It is the five surfaces the implementation
plan §9 freezes for semver — CLI commands and flags, ``basicly.toml`` plus its
overlay, the catalog source schemas, the generated-file/manifest contract, and
the owned ledger format — mapped onto the paths that declare each one. Where a
surface has a declaration and an implementation, the *declaration* is what sits
at L3: ``schema.py`` and ``.basicly/core/schemas/`` carry the catalog source
contract, while ``loader.py``, which parses against it, is ordinary engine code.

This module imports nothing from ``basicly``. It is a rule over strings, so it
stays testable without a repo, a tracker or a config file, and it sits at the
bottom of the import stack where every consumer can reach it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

# Cheapest first. Ordering is load-bearing: :func:`assign` takes the highest
# level any declared path resolves to, because a package that touches one
# consumer surface is a consumer change whatever else it touches.
LEVELS = ("L1", "L2", "L3")

# How small a diff has to be before an L3 path stops buying the L3 budget.
#
# A seed, and it is honest to call it one: D11 fixes the *mechanism* (small diff,
# no public signature change) and names no number, and nothing in this repo has
# measured where the line belongs. It is the caller's argument, not a constant
# baked into the rule, so a repo that learns a better number passes it rather
# than editing the engine.
DEFAULT_DOWNGRADE_MAX_LINES = 20


@dataclass(frozen=True)
class _Rule:
    """One named clause of the path rule.

    A ``!``-prefixed glob is an exclusion, and exclusions are what make the rule
    set single-valued without depending on the order clauses are tried in: the L2
    clause covers ``src/basicly`` *except* the paths an L3 clause already claims,
    so at most one clause can ever match. Any exclusion wins over any inclusion,
    which is deliberately stricter — and easier to reason about — than
    gitignore's last-match-wins.
    """

    name: str
    level: str
    globs: tuple[str, ...]

    def claims(self, path: str) -> bool:
        """Whether this clause claims *path*: a glob matches and no exclusion does.

        "No path is claimed twice" is a property of the rule *set*, so a checker of
        it has to be able to ask each clause on its own rather than infer the answer
        from whichever clause resolution happened to return first;
        :func:`claiming_rules` is that reader.
        """
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


# The five frozen consumer surfaces, one clause each, named for the surface
# rather than for the module so a verdict reads as "this is the CLI surface".
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

# Prose and tests. Markdown is L1 wherever it lives, which is why the engine
# clause below excludes it too — a document under `src/` is still a document.
_DOCS_AND_TESTS = _Rule(
    "docs-and-tests",
    "L1",
    ("docs/**", "tests/**", "site/**", "changelog.d/**", "**/*.md"),
)

# Engine code with no consumer surface on it: everything the engine ships, minus
# the five surfaces above and minus prose.
_ENGINE = _Rule(
    "engine-internal",
    "L2",
    ("src/basicly/**", ".scripts/**", *(f"!{glob}" for glob in _CONSUMER_GLOBS), "!**/*.md"),
)

_RULES: tuple[_Rule, ...] = (*_CONSUMER_SURFACES, _DOCS_AND_TESTS, _ENGINE)

# What a path no clause claims resolves to — CI config, packaging, catalog
# content, tracker data. L2 rather than L1 or L3 on purpose: L1 would fast-gate a
# path the rule has never been taught, and L3 would demand a human ship for every
# unrecognised file, which is the same as having no levels at all. The middle is
# the only choice that fails in neither direction, and the rule name says the
# clause was absent rather than pretending a decision was made.
_FALLBACK = _Rule("unclassified", "L2", ())

_BY_NAME: dict[str, _Rule] = {rule.name: rule for rule in (*_RULES, _FALLBACK)}


@dataclass(frozen=True)
class Selection:
    """What a level buys: the gate set, the model tier, the rework allowance.

    One record, read by the caller, so no consumer re-derives the mapping from
    the level string (§4 of the requirements document is the table this encodes).
    """

    # First entry is the `basicly verify` mode; any further entry is a gate the
    # level adds on top of it.
    gates: tuple[str, ...]
    model_tier: str
    rework_allowance: int
    # Whether the ship step may be delegated, or has to be a human.
    ship: str


_SELECTIONS: dict[str, Selection] = {
    "L1": Selection(gates=("fast",), model_tier="medium", rework_allowance=1, ship="delegable"),
    "L2": Selection(gates=("full",), model_tier="high", rework_allowance=2, ship="delegable"),
    # The table says "high/maximum" for L3. A rule that has to be single-valued
    # cannot hold both, and the surface that reaches consumers is where the
    # cheaper half of that pair is the false economy.
    "L3": Selection(
        gates=("full", "validate-as-consumer", "evidence-binding"),
        model_tier="maximum",
        rework_allowance=2,
        ship="human",
    ),
}


def selection_for(level: str) -> Selection:
    """The gate set, model tier and rework allowance *level* selects.

    Raises:
        ValueError: *level* is outside :data:`LEVELS`.
    """
    try:
        return _SELECTIONS[level]
    except KeyError:
        raise ValueError(
            f"unknown integrity level {level!r}; expected one of {list(LEVELS)}"
        ) from None


@dataclass(frozen=True)
class Assignment:
    """One unit of work's level, the clause that decided it, and why."""

    level: str
    # The clause name that decided it, or ``"downgrade"`` when D11 moved it.
    rule: str
    reason: str
    selection: Selection


def _normalize(entry: str) -> str:
    """A scope entry as a repo-relative posix string.

    Windows separators and a leading ``./`` are spelling, not meaning; a leading
    ``/`` is dropped so an absolute-looking declaration still matches the rules
    rather than silently falling through to :data:`_FALLBACK`.
    """
    text = entry.strip().replace("\\", "/").lstrip("/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _covers(entry: str, pattern: str) -> bool:
    """Whether *entry* and *pattern* can name the same file.

    Symmetric on purpose. A scope entry is itself a glob, so the two directions
    answer different questions: ``entry.full_match(pattern)`` asks whether the
    entry lies inside the clause, and ``pattern.full_match(entry)`` asks whether
    a wildcard entry such as ``src/basicly/*.py`` *covers* the clause's file.
    Only the second one classifies ``src/basicly/*.py`` as the CLI surface, which
    it is.

    The limit, stated rather than discovered later: two patterns that cross
    without either containing the other (``src/**/c*.py`` against ``**/cli.py``)
    are not detected. Declared scopes in this repo are file lists, and the
    unmatched case falls to :data:`_FALLBACK` rather than to L1, so the miss costs
    a level of caution and never a skipped gate.
    """
    left, right = PurePosixPath(entry), PurePosixPath(pattern)
    return left.full_match(pattern) or right.full_match(entry)


def claiming_rules(path: str) -> tuple[str, ...]:
    """The clauses that claim *path*, by name.

    Exactly one, or none when the fallback takes it — but the check that the rule
    set is single-valued has to be able to see *every* claim rather than whichever
    one resolution returned first, and that is why this is the primitive and
    :func:`_rule_for_path` is written on top of it.
    """
    return tuple(rule.name for rule in _RULES if rule.claims(path))


def _rule_for_path(path: str) -> _Rule:
    """The single clause that claims *path*, or :data:`_FALLBACK` when none does.

    Total by construction: every string resolves, because the fallback is a clause
    rather than a ``None``.
    """
    claimed = claiming_rules(path)
    return _BY_NAME[claimed[0]] if claimed else _FALLBACK


def assign(
    scope: Iterable[str],
    *,
    patch: str | None = None,
    downgrade_max_lines: int = DEFAULT_DOWNGRADE_MAX_LINES,
) -> Assignment:
    """The integrity level for a unit of work declaring *scope*.

    The level is the highest any declared path resolves to. When *patch* is given
    — the unified diff of the change as ``git diff`` prints it — a level of L3
    that turns out to be a small, signature-preserving change is downgraded to L2
    with the reason recorded (D11); without a patch there is no change to read
    and the path rule stands alone.

    An empty scope is not an error and does not escalate: a hand-filed bead
    routinely declares none, so it resolves through :data:`_FALLBACK` with a
    reason that says the scope was absent rather than checked.
    """
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


# A changed line that declares something a consumer can call. Underscored names
# are excluded because they are not the surface; indentation is allowed because a
# method on a public class is as public as a module-level function.
_PUBLIC_SIGNATURE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?!_)\w+")
_FILE_HEADER = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+)$")


def _diff_facts(patch: str) -> tuple[int, bool]:
    """Changed-line count and whether *patch* touches a public signature.

    Non-Python files count as a signature change on any edit, and that is the
    conservative half of the rule rather than an approximation: a JSON schema, a
    template or ``basicly.toml`` *is* the contract, so there is no equivalent of
    a private line in one and no small edit to one that a consumer cannot see.

    A changed line before any ``+++`` header belongs to a file this reader cannot
    name, and is treated the same way. The downgrade is the only thing built on
    this answer, so an unreadable patch has to hold the level rather than release
    it — the failure of a parser must not be spendable as a discount.
    """
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
