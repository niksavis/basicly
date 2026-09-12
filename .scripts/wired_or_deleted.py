# module-size-waiver: cost(basicly-kr7t): one BASELINE and one traversal serve all four

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

SKIP_DIRS = frozenset({
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "worktrees",
})

KIT_DIR = ".basicly/core/kit"

TEMPLATE_GLOB = ".basicly/core/templates/**/*"
SCHEMA_GLOB = ".basicly/core/schemas/**/*"

COMMAND_SITE_GLOBS = (
    ".basicly/core/**/*",
    ".basicly-local/**/*",
    ".github/workflows/*",
    ".scripts/*",
)
COMMAND_SITE_FILES = (CONFIG_FILE,)

_IGNORE_OVERRIDE = "__wired_or_deleted_policing_run__"

_VULTURE_FINDING = re.compile(r"^.+?:\d+: unused \w+ '(?P<name>[^']+)'")
_GLOB_CHARS = frozenset("*?[")

BASELINE: frozenset[str] = frozenset({
    "command:catalog list",
    "command:catalog review",
    "command:catalog verify",
    "command:health",
    "command:rubric eval",
    "command:runner list",
    "command:runner run",
    "command:status",
    "command:usage forecast",
    "record-field:basicly.agents.AgentOutputRoot.claude_passthrough",
    "record-field:basicly.agents.AgentDefinition.deprecated_model",
    "record-field:basicly.decompose.CollapsingPath.declarers",
    "record-field:basicly.decompose.CollapsingPath.groups_without",
    "record-field:basicly.decompose.CollapsingPath.neutralized",
    "record-field:basicly.decompose.CostEstimate.overhead_tokens",
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
    "record-field:basicly.supervise.LaneOutcome.provider_refusal",
    "record-field:basicly.worktree.RemovalVerdict.may_remove",
    "record-field:basicly.worktree.RemovalVerdict.holds",
    "record-field:basicly.worktree.RemovalVerdict.indeterminate",
})


class WiringError(RuntimeError):
    pass


@dataclass(frozen=True)
class Finding:
    key: str
    """Stable baseline identity: ``<kind>:<dotted module>.<qualified name>``."""

    location: str
    """Where to go and fix it, as ``path:line`` or a bare path."""

    detail: str
    """The sentence printed to the operator, naming the symbol and the remedy."""


def _iter_files(root: Path, pattern: str) -> Iterator[Path]:
    for path in sorted(root.glob(pattern)):
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(root).parts):
            yield path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_test(root: Path, path: Path) -> bool:
    return path.relative_to(root).parts[0] == TESTS_DIR


def _is_kit(root: Path, path: Path) -> bool:

    return _relative(root, path).startswith(f"{KIT_DIR}/")


def _dotted(root: Path, path: Path) -> str:
    parts = path.relative_to(root / "src").with_suffix("").parts
    return ".".join(parts)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _referenced_names(tree: ast.Module) -> set[str]:

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
    return set(_WORD.findall(text))


_SCHEMA_NAME_KEYS = frozenset({"required", "enum", "const", "$ref"})

_JINJA_CODE = re.compile(r"\{[{%](.*?)[%}]\}", re.DOTALL)


def schema_names(text: str) -> set[str]:

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
    return {name for match in _JINJA_CODE.finditer(text) for name in _tokens(match[1])}


_FIELD_SITE_READERS = {TEMPLATE_GLOB: template_names, SCHEMA_GLOB: schema_names}


@dataclass(frozen=True)
class Index:
    referrers: dict[str, frozenset[str]]
    """name -> the site labels referencing it (a module path, or a site group)."""

    modules: tuple[Path, ...]
    """The ``src/basicly`` modules whose declarations are subject to the rule."""

    def referenced_outside(self, name: str, site: str) -> bool:
        return bool(self.referrers.get(name, frozenset()) - {site})


def build_index(root: Path) -> Index:
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


@dataclass(frozen=True)
class Field:
    record: str
    name: str
    line: int


def declared_fields(tree: ast.Module) -> list[Field]:

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


def command_paths() -> tuple[tuple[str, ...], ...]:

    return tuple(_walk_parser(cli._build_parser(), ()))


def _walk_parser(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...]
) -> Iterator[tuple[str, ...]]:
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

    this_file = Path(__file__).resolve()
    paths = [path for glob in COMMAND_SITE_GLOBS for path in _iter_files(root, glob)]
    paths += [root / name for name in COMMAND_SITE_FILES]
    return "\n".join(
        _read(path) for path in paths if path.is_file() and path.resolve() != this_file
    )


def command_findings(commands: Iterable[tuple[str, ...]], wiring: str) -> list[Finding]:

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


def declared_vulture_command(root: Path) -> tuple[str, ...]:

    config = tomllib.loads(_read(root / CONFIG_FILE))
    for check in config.get("verify", {}).get("checks", []):
        if check.get("name") == VULTURE_CHECK:
            return tuple(check.get("command", ()))
    raise WiringError(
        f"{CONFIG_FILE} declares no [[verify.checks]] entry named '{VULTURE_CHECK}' — "
        "the dead-code gate is unwired"
    )


def configured_ignore_names(root: Path) -> tuple[str, ...]:
    pyproject = tomllib.loads(_read(root / PYPROJECT_FILE))
    names = pyproject.get("tool", {}).get("vulture", {}).get("ignore_names", [])
    return tuple(str(name) for name in names)


def _unfiltered_vulture_names(root: Path, command: tuple[str, ...]) -> set[str]:
    argv = [*command, "--ignore-names", _IGNORE_OVERRIDE]
    completed = subprocess.run(  # nosec B603
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
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


def collect(root: Path) -> list[Finding]:
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
    findings = list(findings)
    new = [finding for finding in findings if finding.key not in BASELINE]
    stale = sorted(BASELINE - {finding.key for finding in findings})
    return new, stale


def main() -> int:
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
