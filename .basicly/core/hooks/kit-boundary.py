from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_runner import project_root

KIT_ROOT = Path(".basicly") / "core" / "kit"

_KIT_DATA_DIRS = frozenset({"core"})

_IMPORT_CALLS = frozenset({"__import__", "import_module", "find_spec"})

_PATH_CALLS = frozenset({"Path", "PurePath", "PurePosixPath", "PureWindowsPath"})
_JOIN_CALLS = frozenset({"join", "joinpath"})

_ENGINE_SOURCE = re.compile(r"(?<![.\w])(?:src[/\\]basicly[/\\]|basicly[/\\][A-Za-z_]\w*\.py)")

_ENGINE_CONFIG = re.compile(r"(?<![\w./\\-])basicly(?:\.local)?\.toml\b")

_DOT_BASICLY = re.compile(r"\.basicly[/\\]([A-Za-z0-9_.-]+)")


class Finding(NamedTuple):
    path: str
    lineno: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: {self.rule}: {self.detail}"


def kit_modules(kit_root: Path) -> list[Path]:
    return sorted(
        path
        for path in kit_root.rglob("*.py")
        if "__pycache__" not in path.parts and path.is_file()
    )


def _root_package(name: str) -> str:
    return name.split(".", 1)[0]


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _path_text(node: ast.expr) -> str | None:

    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _join_parts([node.left, node.right])
    if isinstance(node, ast.Call):
        callee = _callee(node)
        if callee in _PATH_CALLS and node.args:
            return _join_parts(node.args)
        if callee in _JOIN_CALLS:
            base = node.func.value if isinstance(node.func, ast.Attribute) else None
            parts = [base, *node.args] if callee == "joinpath" and base else list(node.args)
            return _join_parts(parts) if parts else None
    return None


def _join_parts(operands: list[ast.expr]) -> str | None:

    parts = [_path_text(operand) for operand in operands]
    if any(part is None for part in parts):
        return None
    return "/".join(part for part in parts if part is not None)


def _statement_strings(tree: ast.Module) -> set[int]:

    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ids.add(id(node.value))
    return ids


def _import_findings(rel: str, tree: ast.Module) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            findings += [
                Finding(rel, node.lineno, "imports-basicly", f"import {alias.name}")
                for alias in node.names
                if _root_package(alias.name) == "basicly"
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.module and _root_package(node.module) == "basicly":
                findings.append(
                    Finding(rel, node.lineno, "imports-basicly", f"from {node.module} import ...")
                )
        elif isinstance(node, ast.Call) and _callee(node) in _IMPORT_CALLS and node.args:
            target = node.args[0]
            if (
                isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and _root_package(target.value) == "basicly"
            ):
                findings.append(
                    Finding(
                        rel,
                        node.lineno,
                        "dynamic-import-basicly",
                        f"{_callee(node)}({target.value!r})",
                    )
                )
    return findings


def _path_rule(text: str) -> str | None:
    if _ENGINE_SOURCE.search(text):
        return "reads-engine-source"
    outside_kit = (match := _DOT_BASICLY.search(text)) and match.group(1) not in _KIT_DATA_DIRS
    if _ENGINE_CONFIG.search(text) or outside_kit:
        return "reads-engine-state"
    return None


def _path_findings(rel: str, tree: ast.Module) -> list[Finding]:
    skip = _statement_strings(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.expr) or id(node) in skip:
            continue
        text = _path_text(node)
        if text and (rule := _path_rule(text)):
            findings.append(Finding(rel, node.lineno, rule, text))
    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[int, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.lineno, finding.rule)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def module_findings(module: Path, rel: str) -> list[Finding]:

    source = module.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(module))
    except SyntaxError as exc:
        return [Finding(rel, exc.lineno or 1, "unparseable", exc.msg)]
    return _import_findings(rel, tree) + _path_findings(rel, tree)


def scan(kit_root: Path, repo_root: Path | None = None) -> list[Finding]:
    base = repo_root or kit_root
    findings: list[Finding] = []
    for module in kit_modules(kit_root):
        try:
            rel = module.relative_to(base).as_posix()
        except ValueError:
            rel = module.as_posix()
        findings += module_findings(module, rel)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate the one-way kit boundary.")
    parser.add_argument(
        "--kit-root",
        type=Path,
        default=None,
        help=f"kit tree to scan (default: {KIT_ROOT.as_posix()} under the repo root)",
    )
    args = parser.parse_args(argv)

    repo_root = project_root()
    kit_root = args.kit_root or repo_root / KIT_ROOT
    if not kit_root.is_dir():
        print(f"kit-boundary: no kit tree at {kit_root}; nothing to gate.")
        return 0

    findings = scan(kit_root, repo_root)
    if not findings:
        return 0

    print(
        "kit-boundary: the kit reaches back into basicly — the dependency direction "
        "is one-way (.basicly/core/kit/tracker/SPEC.md §4).",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(
        "The kit is copied into repositories that have never heard of this harness, "
        "so an engine import or read makes it unusable there.\n"
        "Take the value as an argument instead, or read it from the kit's own "
        "committed data under .basicly/core.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
