"""Wired-or-deleted: nothing ships with references only inside its own module.

Every defect the 2026-08-02 evidence pass found had one shape — **an instrument built
and never connected**. ``permissions-check`` shipped wired to no gate; the import
contract forbade modules that could not exist and reported ``1 kept, 0 broken``
forever; ``.scripts/recall_eval.py`` was built, run once and wired to nothing. The
common cause is that nothing asked the question a consumer would ask: *who calls
this?* This gate asks it deterministically.

The rule: a name a consumer is supposed to reach must be referenced from **outside
its own module and outside ``tests/``**. A reference only from the module that
declares it means the module is talking to itself; a reference only from a test
means the test is the sole consumer, which is the same defect wearing a green tick.

Three surfaces, because they are reached differently:

* **commands** — a CLI subcommand is wired by something that *invokes* it: a git
  hook, a ``[[verify.checks]]`` entry, a CI workflow, a repo script, or catalog text
  that instructs an agent to run it. Prose in ``docs/`` is deliberately **not** a
  wiring site: ``permissions-check`` was fully documented in the architecture
  document and gated nowhere, so crediting documentation would credit exactly the
  defect this gate exists to catch. Projected outputs (``AGENTS.md``, ``.claude/``,
  ``.github/copilot-instructions.md``) are excluded for the same reason the rest of
  the repo asserts on authored sources: they are copies of ``.basicly/core/``.
* **config keys** — a field of a ``config.py`` record is wired when production code
  reads it. A key ``basicly.toml`` declares and nothing consumes is inert config.
* **record fields** — a field of any public record elsewhere in ``src/basicly/``.
  Its consumers are production code and the Jinja templates and JSON schemas that
  render or validate it; those two directories are read for the positions that name
  something, never as free text. Catalog prose is not scanned here, because a field
  named ``holds`` or ``folded`` would be masked by any skill that happens to use the
  English word — and for the same reason a schema's ``description`` is not scanned
  either (basicly-r343).

``vulture`` is the fourth surface and runs as its own declared check
(``basicly.toml``), scoped to ``src`` and ``.scripts`` — **excluding ``tests``**, and
that exclusion is the point: it is what turns "read only by a test" into a finding.
It is the dependency this bead was filed against, declared at ``pyproject.toml`` and
called from nowhere until now, so the gate's first finding is the tool that proves the
gate was missing. This script does not duplicate it; it *polices* it. Vulture can only
suppress by bare name, in ``[tool.vulture] ignore_names``, and nothing in vulture
notices when a suppression stops corresponding to a real finding — an unpoliced
suppression list is the same fail-open shape as the import contract. So this gate
re-runs the declared vulture command with the ignore list overridden and fails on any
entry that no longer reproduces, and refuses a glob entry outright since a pattern
cannot be checked against one name.

**The baseline.** ``BASELINE`` holds the findings that already existed when the gate
was written; ``basicly-tcmy.21`` is the deletion half that empties it. It is a list of
exemptions, which is the construct this repo distrusts most, so it is built to bind in
both directions: an entry that stops reproducing fails as a stale suppression, and it
is keyed by module and qualified name rather than by line, so it cannot be satisfied
by an unrelated symbol drifting onto the same line.

Run::

    uv run python .scripts/wired_or_deleted.py
"""

# module-size-waiver: cost(basicly-kr7t): one BASELINE and one traversal serve all four
# surfaces, so the cut the cap wants moves `build_index` out from under the rule that reads
# it. Measured before
# basicly-r343: 6525 tokens of a frozen 6554, so the ratchet refused *any* fix to this file
# — the split is the follow-up that headroom needs, not part of the schema-scan fix.
#
# THIS WAIVER EXPIRES. It is bought on cost, not on cohesion, which is not what a waiver is
# for: §9.3 obliges the first change to a frozen file under 8000 tokens to bring it under the
# cap, and this file was 6554. `basicly-kr7t` does the extraction and carries removing this
# comment as an acceptance criterion, so the waiver cannot outlive the work it stands in for.

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess  # nosec B404
import sys
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly import cli  # noqa: E402  (path set above)

CONFIG_FILE = "basicly.toml"
PYPROJECT_FILE = "pyproject.toml"
VULTURE_CHECK = "vulture"
CONSOLE_SCRIPT = "basicly"

SRC_DIR = "src/basicly"
CONFIG_MODULE = "src/basicly/config.py"
TESTS_DIR = "tests"

# Directories that hold no authored source, so nothing in them is a reference.
#
# `worktrees` is load-bearing rather than tidy (basicly-jr0l.70). A linked git worktree
# placed *inside* the root — `.claude/worktrees/agent-<id>/` is what a parallel agent
# spawn creates — holds a complete second copy of `src/basicly`, and every copy is a
# distinct site label. So each field is "referenced outside its module" by its own
# duplicate and the record-field surface returns nothing at all. Measured 2026-08-06
# with two agent worktrees live: 48 modules indexed but 401 sites, and **all 44** of the
# record-field baseline entries reported as stale suppressions at once.
#
# That failure mode is the dangerous one, not merely wrong: the gate's advice on a stale
# entry is "remove the entry", so a maintainer following it during a parallel run would
# empty the baseline and blind the surface permanently. It also cannot be caught by the
# nested copy's own `tests/` exclusion, because `_is_test` keys on the *first* path part
# and that is `.claude` here, not `tests`.
SKIP_DIRS = frozenset({
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "worktrees",
})

# The kit ships as a standalone deliverable with **zero `basicly` imports**, a boundary
# the `kit-boundary` check enforces — so kit code structurally cannot consume a
# `basicly` record field, and counting it as a referrer can only ever mask a finding.
#
# It masked a real one (basicly-jr0l.70): `migrate.py` and `events.py` use the ordinary
# English words `folded` and `holds`, which retired the genuine suppressions for
# `supervise.DispatchBundle.folded` and `worktree.RemovalVerdict.holds`. The module
# docstring names that hazard for catalog *prose*; the kit slipped through because it
# is Python rather than prose.
KIT_DIR = ".basicly/core/kit"

# Non-Python consumers of a record field: the templates that render one and the
# schemas that validate one. Each is read for the positions that can name a field,
# because reading either as free text makes English prose a reference (basicly-r343):
# five schema files whose descriptions used the word `holds` retired
# `worktree.RemovalVerdict.holds` on 2026-08-13, and the gate's advice on the stale
# entry that produced is "remove the entry" — which deletes a live finding for good,
# since the prose stays. `LandedCost.packages` below is what the fix then unmasked.
TEMPLATE_GLOB = ".basicly/core/templates/**/*"
SCHEMA_GLOB = ".basicly/core/schemas/**/*"

# Where a command may be wired: something that runs it, or catalog text that tells an
# agent to run it. `.basicly-local` is this repo's own authored overlay and counts for
# the same reason `.basicly/core` does.
COMMAND_SITE_GLOBS = (
    ".basicly/core/**/*",
    ".basicly-local/**/*",
    ".github/workflows/*",
    ".scripts/*",
)
COMMAND_SITE_FILES = (CONFIG_FILE,)

# Overrides `[tool.vulture] ignore_names` on the command line so the policing run
# sees the unfiltered findings. Any name that matches nothing would do; this one
# says why it is there if it ever surfaces in output.
_IGNORE_OVERRIDE = "__wired_or_deleted_policing_run__"

_VULTURE_FINDING = re.compile(r"^.+?:\d+: unused \w+ '(?P<name>[^']+)'")
_GLOB_CHARS = frozenset("*?[")

# Findings that predate the gate; how the list binds is the docstring's last section.
BASELINE: frozenset[str] = frozenset({
    # Commands no invocation names (10). The 2026-08-02 evidence pass counted 11.
    "command:catalog list",
    "command:catalog review",
    "command:catalog verify",
    # basicly-zdtx wired the health *module* into the supervisor pass line; this entry is
    # the other finding and still reproduces — nothing *invokes the command*, and the pass
    # line reads `health_report` in process rather than shelling out or becoming a check.
    "command:health",
    "command:rubric eval",
    "command:runner list",
    "command:runner run",
    "command:status",
    "command:usage forecast",
    # Record fields read only by their own module or by a test (29). Was 36 — the seven
    # `health` fields gained a reader in the supervisor pass line (basicly-zdtx). Was 41 —
    # the headline count had not been updated for the six the 2026-08-08 splits retired.
    # Was 43: both
    # `HookSpec` fields gained a reader when `_hook_entry` moved to `precommit_config`.
    # Was 45: `RunRecord.config_overrides` acquired a consumer when `tuning` began
    # reading the ledger — it reads the field's value back out of the record JSON as
    # `entry["config_overrides"]` (basicly-3ifz.1).
    #
    # `CreatedChild.depends_on` went for the *other* reason — the masking hazard this
    # module documents above, where the index matches *bare* names and a name declared
    # anywhere retires a finding it never read. The plan gate gave `ChildSpec` its own
    # `depends_on` field (basicly-u2hl.1), so the record field stopped reproducing
    # without acquiring a reader, and a baseline may only hold live findings.
    #
    # `CostRollup.dispatches` stays for the mirror reason: it still has no consumer, and
    # `tuning` spells its own counter `dispatches_read` so it does not mask this entry.
    # Six entries left with the 2026-08-08 splits: this gate reports a field no reader
    # outside its defining module touches, so moving a record to its own module gives
    # its fields a reader by construction. They stopped reproducing, which is the only
    # reason a baseline entry is ever removed.
    #
    # `LandedCost.packages` is the one entry that is new to the *list* and not new to the
    # *tree*: it has never had a reader outside `run_record`, and the word `packages` in
    # one `description` in `skill.schema.json` was crediting it as wired until basicly-r343
    # stopped the schema scan reading prose. It is parked here so the gate keeps binding,
    # not adjudicated — wiring it or deleting it is still an open call.
    "record-field:basicly.agents.AgentOutputRoot.claude_passthrough",
    "record-field:basicly.agents.AgentDefinition.deprecated_model",
    "record-field:basicly.decompose.CollapsingPath.declarers",
    "record-field:basicly.decompose.CollapsingPath.groups_without",
    "record-field:basicly.decompose.CollapsingPath.neutralized",
    "record-field:basicly.decompose.CostEstimate.overhead_tokens",
    # Read by `PlanVerdict.refused` and `.reason`, its own API. It was credited as wired
    # until vkh0.42.7 by an unrelated `cycles` local in the seam the flip deleted.
    "record-field:basicly.plan_gate.PlanVerdict.cycles",
    "record-field:basicly.loop_state.Ranking.nodes",
    "record-field:basicly.loop_state.Ranking.fallback_sort",
    "record-field:basicly.policy.Grant.unmetered_at_issue",
    "record-field:basicly.policy.SpendMeter.estimated_tokens",
    "record-field:basicly.policy.WaitEvent.waited_s",
    "record-field:basicly.policy.WaitEvent.answered_at",
    "record-field:basicly.release.PinSite.occurrences",
    "record-field:basicly.release.ReleasePlan.current_version",
    "record-field:basicly.release.ReleasePlan.pins",
    "record-field:basicly.release.ReleaseResult.tagged",
    "record-field:basicly.run_record.CostRollup.dispatches",
    "record-field:basicly.run_record.LandedCost.packages",
    "record-field:basicly.runner.Capability.reachable",
    "record-field:basicly.supervise.FoundInfo.affects",
    "record-field:basicly.supervise.DispatchBundle.folded",
    "record-field:basicly.supervise.PassSpendAdmission.unforecast",
    "record-field:basicly.supervise.PassSpendAdmission.assumed",
    "record-field:basicly.supervise.PassSpendAdmission.assumed_source",
    "record-field:basicly.supervise.LaneOutcome.needs_fact",
    "record-field:basicly.supervise.LaneOutcome.transient",
    "record-field:basicly.worktree.RemovalVerdict.may_remove",
    "record-field:basicly.worktree.RemovalVerdict.holds",
    "record-field:basicly.worktree.RemovalVerdict.indeterminate",
})


class WiringError(RuntimeError):
    """The gate could not be evaluated, as distinct from a finding."""


@dataclass(frozen=True)
class Finding:
    """One thing with no reference outside its own module and outside ``tests/``."""

    key: str
    """Stable baseline identity: ``<kind>:<dotted module>.<qualified name>``."""

    location: str
    """Where to go and fix it, as ``path:line`` or a bare path."""

    detail: str
    """The sentence printed to the operator, naming the symbol and the remedy."""


# ------------------------------------------------------------------ reference index


def _iter_files(root: Path, pattern: str) -> Iterator[Path]:
    """Every file under *root* matching *pattern*, skipping non-source directories."""
    for path in sorted(root.glob(pattern)):
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(root).parts):
            yield path


def _relative(root: Path, path: Path) -> str:
    """``src/basicly/health.py`` — posix so a key is identical on every platform."""
    return path.relative_to(root).as_posix()


def _is_test(root: Path, path: Path) -> bool:
    """True for a file under ``tests/``, whose references never count."""
    return path.relative_to(root).parts[0] == TESTS_DIR


def _is_kit(root: Path, path: Path) -> bool:
    """True for a file under :data:`KIT_DIR`, whose references never count either.

    Not an exemption but a consequence of the kit boundary: the kit may not import
    ``basicly`` at all, so a name it shares with a record field is a coincidence of
    vocabulary rather than a consumer.
    """
    return _relative(root, path).startswith(f"{KIT_DIR}/")


def _dotted(root: Path, path: Path) -> str:
    """``src/basicly/health.py`` -> ``basicly.health``."""
    parts = path.relative_to(root / "src").with_suffix("").parts
    return ".".join(parts)


def _read(path: Path) -> str:
    """Text of *path*, tolerant of the odd byte a catalog fixture carries."""
    return path.read_text(encoding="utf-8", errors="replace")


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every name *tree* could be reading something by.

    Deliberately generous: attribute access, keyword argument, bare name, and string
    literal all count. A record field is read as ``record.field`` in code, as
    ``field=`` at a construction site, and as ``payload["field"]`` once it has been
    round-tripped through JSON — and over-counting a reference only ever loses a
    finding, while under-counting invents one.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokens(text: str) -> set[str]:
    """Identifier-shaped words in *text*, for the sites that are not Python."""
    return set(_WORD.findall(text))


# The JSON Schema keywords whose *string values* name something rather than describe
# it: `required` and `enum` list property names and permitted values, `const` is a
# one-member `enum`, and a `$ref` pointer ends in a `$defs` name. Everything else is
# read for its keys only. That is an allowlist rather than a "skip `description`"
# denylist because prose is not the only masking position: `$id` is a URL that would
# donate `change`, `summary` and `block`, and `type` would donate `string` and
# `object`. An unlisted keyword under-counts, which invents a finding the operator
# sees; the alternative loses one silently.
_SCHEMA_NAME_KEYS = frozenset({"required", "enum", "const", "$ref"})

# `{{ expr }}` and `{% stmt %}`. A field reaches a template only through one of these:
# the literal text between them is the rendered document, not a reference to it.
_JINJA_CODE = re.compile(r"\{[{%](.*?)[%}]\}", re.DOTALL)


def schema_names(text: str) -> set[str]:
    """Names a JSON schema refers to: every object key, plus :data:`_SCHEMA_NAME_KEYS`.

    Raises ``json.JSONDecodeError`` for a file the schemas directory should not hold.
    """
    names: set[str] = set()

    def visit(node: object, names_a_field: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                names.update(_tokens(key))
                visit(value, key in _SCHEMA_NAME_KEYS)
        elif isinstance(node, list):
            for item in node:
                visit(item, names_a_field)
        elif names_a_field and isinstance(node, str):
            names.update(_tokens(node))

    visit(json.loads(text), False)
    return names


def template_names(text: str) -> set[str]:
    """Identifiers inside a Jinja template's expressions, never its literal text."""
    return {name for match in _JINJA_CODE.finditer(text) for name in _tokens(match[1])}


_FIELD_SITE_READERS = {TEMPLATE_GLOB: template_names, SCHEMA_GLOB: schema_names}


@dataclass(frozen=True)
class Index:
    """Which non-test sites reference each name, keyed by the site that does."""

    referrers: dict[str, frozenset[str]]
    """name -> the site labels referencing it (a module path, or a site group)."""

    modules: tuple[Path, ...]
    """The ``src/basicly`` modules whose declarations are subject to the rule."""

    def referenced_outside(self, name: str, site: str) -> bool:
        """True when *name* is referenced by some non-test site other than *site*."""
        return bool(self.referrers.get(name, frozenset()) - {site})


def build_index(root: Path) -> Index:
    """Parse every production Python file and read the non-Python consumer sites."""
    referrers: dict[str, set[str]] = {}

    def record(name: str, site: str) -> None:
        referrers.setdefault(name, set()).add(site)

    for path in _iter_files(root, "**/*.py"):
        if _is_test(root, path) or _is_kit(root, path):
            continue
        site = _relative(root, path)
        try:
            tree = ast.parse(_read(path))
        except SyntaxError as exc:  # pragma: no cover - a broken tree fails ruff first
            raise WiringError(f"{site}: {exc}") from exc
        for name in _referenced_names(tree):
            record(name, site)

    for glob, names_in in _FIELD_SITE_READERS.items():
        for path in _iter_files(root, glob):
            try:
                names = names_in(_read(path))
            except ValueError as exc:
                raise WiringError(f"{_relative(root, path)}: {exc}") from exc
            for name in names:
                record(name, glob)

    modules = tuple(_iter_files(root, f"{SRC_DIR}/**/*.py"))
    if not modules:  # pragma: no cover - only reachable outside a checkout
        raise WiringError(f"no modules found under {SRC_DIR}")
    return Index(
        referrers={name: frozenset(sites) for name, sites in referrers.items()},
        modules=modules,
    )


# ----------------------------------------------------------- config keys and fields


@dataclass(frozen=True)
class Field:
    """An annotated class attribute — a config key or a record field."""

    record: str
    name: str
    line: int


def declared_fields(tree: ast.Module) -> list[Field]:
    """Annotated attributes of every public class in *tree*.

    Public classes only: a leading underscore says the record is internal to its
    module, so "no reference from outside the module" is its design rather than a
    defect. Underscored *fields* are skipped for the same reason.
    """
    fields: list[Field] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            target = statement.target
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                fields.append(Field(node.name, target.id, statement.lineno))
    return fields


def field_findings(root: Path, index: Index) -> list[Finding]:
    """Every config key and record field nothing outside its module reads."""
    findings: list[Finding] = []
    for module in index.modules:
        site = _relative(root, module)
        is_config = site == CONFIG_MODULE
        kind = "config-key" if is_config else "record-field"
        noun = "config key" if is_config else "record field"
        dotted = _dotted(root, module)
        for field in declared_fields(ast.parse(_read(module))):
            if index.referenced_outside(field.name, site):
                continue
            findings.append(
                Finding(
                    key=f"{kind}:{dotted}.{field.record}.{field.name}",
                    location=f"{site}:{field.line}",
                    detail=(
                        f"{noun} '{field.record}.{field.name}' is read only inside "
                        f"{dotted} or under {TESTS_DIR}/ — wire a consumer or delete it"
                    ),
                )
            )
    return findings


# ------------------------------------------------------------------------- commands


def command_paths() -> tuple[tuple[str, ...], ...]:
    """Every invocable subcommand path the CLI ships, e.g. ``("worktree", "create")``.

    Read off the parser the CLI actually builds, so a command cannot be omitted by
    hand the way a written list lets it be. A group with subcommands contributes its
    leaves only: ``basicly worktree`` alone is not invocable.
    """
    return tuple(_walk_parser(cli._build_parser(), ()))


def _walk_parser(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...]
) -> Iterator[tuple[str, ...]]:
    """Depth-first walk yielding the leaf command paths under *parser*."""
    action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if action is None:
        if prefix:
            yield prefix
        return
    for name, sub in sorted(action.choices.items()):
        yield from _walk_parser(sub, (*prefix, name))


def command_wiring_text(root: Path) -> str:
    """Every site that could invoke a command or instruct an agent to, concatenated.

    This file is excluded, and the exclusion is load-bearing: ``.scripts`` is a wiring
    site and ``BASELINE`` spells out every unwired command, so without it the gate read
    its own exemption list as the wiring and reported all nine commands as connected.
    """
    this_file = Path(__file__).resolve()
    paths = [path for glob in COMMAND_SITE_GLOBS for path in _iter_files(root, glob)]
    paths += [root / name for name in COMMAND_SITE_FILES]
    return "\n".join(
        _read(path) for path in paths if path.is_file() and path.resolve() != this_file
    )


def command_findings(commands: Iterable[tuple[str, ...]], wiring: str) -> list[Finding]:
    """Every command path that appears at no wiring site.

    Matched in invocation form — the console script's own name followed by the whole
    path — so ``catalog list`` is not credited to prose about a catalog listing, and
    ``catalog verify`` is not credited to an unrelated ``verify``. The separator class
    covers the two shapes an invocation is written in, a shell line and an argv array
    (``["basicly", "skills-check", ...]``). The trailing guard refuses a hyphen as well
    as a word character, so ``merge`` is not satisfied by ``merge-queue``.

    A mention still counts even when it is a comment rather than a call: telling the
    two apart in YAML and Markdown is not tractable, so the rule is deliberately the
    conservative one — it reports a command that no invocation of any kind names.
    """
    findings: list[Finding] = []
    for path in commands:
        separated = r"[\s\"',\]]+".join(re.escape(word) for word in (CONSOLE_SCRIPT, *path))
        if re.search(rf"(?<![\w-]){separated}(?![\w-])", wiring):
            continue
        spelled = " ".join(path)
        findings.append(
            Finding(
                key=f"command:{spelled}",
                location=f"{SRC_DIR}/cli.py",
                detail=(
                    f"command 'basicly {spelled}' is invoked by no hook, verify check, "
                    "workflow, script or catalog instruction — wire it or delete it"
                ),
            )
        )
    return findings


# ------------------------------------------------------------- vulture suppressions


def declared_vulture_command(root: Path) -> tuple[str, ...]:
    """The argv of the ``vulture`` entry in ``[[verify.checks]]``.

    Reading it rather than restating it makes this gate enforce the other half of its
    own requirement: with no declared vulture check there is nothing to read, and the
    gate fails instead of quietly policing a suppression list nothing consults.
    """
    config = tomllib.loads(_read(root / CONFIG_FILE))
    for check in config.get("verify", {}).get("checks", []):
        if check.get("name") == VULTURE_CHECK:
            return tuple(check.get("command", ()))
    raise WiringError(
        f"{CONFIG_FILE} declares no [[verify.checks]] entry named '{VULTURE_CHECK}' — "
        "the dead-code gate is unwired"
    )


def configured_ignore_names(root: Path) -> tuple[str, ...]:
    """``[tool.vulture] ignore_names`` — vulture's only suppression mechanism."""
    pyproject = tomllib.loads(_read(root / PYPROJECT_FILE))
    names = pyproject.get("tool", {}).get("vulture", {}).get("ignore_names", [])
    return tuple(str(name) for name in names)


def _unfiltered_vulture_names(root: Path, command: tuple[str, ...]) -> set[str]:
    """Names the declared vulture command reports with its ignore list overridden."""
    argv = [*command, "--ignore-names", _IGNORE_OVERRIDE]
    # The argv comes from this repo's own basicly.toml, never from user input.
    completed = subprocess.run(  # nosec B603
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    # 0 = nothing found, 3 = findings reported. Anything else is a broken invocation,
    # and reporting it as "no findings" would silently disable the policing run.
    if completed.returncode not in (0, 3):
        raise WiringError(
            f"{' '.join(argv)} exited {completed.returncode}: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    return {
        match["name"]
        for line in completed.stdout.splitlines()
        if (match := _VULTURE_FINDING.match(line))
    }


def suppression_findings(ignored: Iterable[str], reported: set[str]) -> list[Finding]:
    """Every ``ignore_names`` entry that no longer corresponds to a finding.

    A glob is refused rather than resolved: ``ignore_names`` is matched by pattern, so
    one wildcard can silence a whole surface, and no check can tell whether the entry
    is still earning its place.
    """
    findings: list[Finding] = []
    for name in ignored:
        if _GLOB_CHARS & set(name):
            detail = (
                f"vulture suppression '{name}' is a glob — it silences names nobody "
                "enumerated and cannot be policed; list each name instead"
            )
        elif name not in reported:
            detail = (
                f"vulture suppression '{name}' reports no finding any more — delete "
                f"the entry from [tool.vulture] ignore_names in {PYPROJECT_FILE}"
            )
        else:
            continue
        findings.append(
            Finding(key=f"vulture-suppression:{name}", location=PYPROJECT_FILE, detail=detail)
        )
    return findings


# ----------------------------------------------------------------------------- main


def collect(root: Path) -> list[Finding]:
    """Every finding across all four surfaces."""
    index = build_index(root)
    command = declared_vulture_command(root)
    return [
        *command_findings(command_paths(), command_wiring_text(root)),
        *field_findings(root, index),
        *suppression_findings(
            configured_ignore_names(root), _unfiltered_vulture_names(root, command)
        ),
    ]


def unexpected(findings: Iterable[Finding]) -> tuple[list[Finding], list[str]]:
    """Split findings against the baseline, and name the entries that went stale."""
    findings = list(findings)
    new = [finding for finding in findings if finding.key not in BASELINE]
    stale = sorted(BASELINE - {finding.key for finding in findings})
    return new, stale


def main() -> int:
    """Entry point: report anything unwired that the baseline does not already hold."""
    try:
        findings = collect(REPO_ROOT)
    except WiringError as exc:
        print(f"wired-or-deleted: {exc}", file=sys.stderr)
        return 1

    new, stale = unexpected(findings)
    for finding in sorted(new, key=lambda f: f.key):
        print(f"wired-or-deleted: {finding.location}: {finding.detail}", file=sys.stderr)
    for key in stale:
        print(
            f"wired-or-deleted: {key} is in BASELINE but no longer reproduces — "
            f"remove the entry from {_relative(REPO_ROOT, Path(__file__))}",
            file=sys.stderr,
        )
    if new or stale:
        return 1
    print(f"wired-or-deleted: {len(findings)} known finding(s), none new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
