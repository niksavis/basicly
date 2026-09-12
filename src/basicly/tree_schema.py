from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Table:
    keys: frozenset[str] = frozenset()
    tables: dict[str, Table] = field(default_factory=dict)
    arrays: dict[str, Table] = field(default_factory=dict)
    open_keys: bool = False


_ENGINE_SOURCE = Path("src") / "basicly" / "config.py"

ORDERING_RULE = (
    "This tree ships its own src/basicly/config.py but its CONFIG_SCHEMA could not be "
    "read statically, so the name was checked against the running engine's schema "
    "instead. If the name is one this tree adds, land the schema change first and the "
    "basicly.toml declaration in a following commit."
)


class _UnreadableSchemaError(Exception):
    pass


_TREE_SCHEMA_CACHE: dict[Path, tuple[int, int, dict[str, Table] | None]] = {}


def ships_engine_source(repo_root: Path) -> bool:
    return (repo_root / _ENGINE_SOURCE).is_file()


def read(repo_root: Path) -> dict[str, Table] | None:

    path = repo_root / _ENGINE_SOURCE
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _TREE_SCHEMA_CACHE.get(path)
    if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    try:
        schema = _parse_schema(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    _TREE_SCHEMA_CACHE[path] = (stat.st_mtime_ns, stat.st_size, schema)
    return schema


def _parse_schema(source: str) -> dict[str, Table] | None:

    try:
        module = ast.parse(source)
    except SyntaxError, ValueError:
        return None
    names: dict[str, object] = {}
    for statement in module.body:
        target, value = _assigned(statement)
        if target is None or value is None:
            continue
        try:
            names[target] = _evaluate(value, names)
        except _UnreadableSchemaError, TypeError:
            if target == "CONFIG_SCHEMA":
                return None
    try:
        schema = _table_map(names.get("CONFIG_SCHEMA"))
    except _UnreadableSchemaError:
        return None
    return schema or None


def _assigned(statement: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id, statement.value
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        if isinstance(target, ast.Name):
            return target.id, statement.value
    return None, None


def _evaluate(node: ast.expr, names: Mapping[str, object]) -> object:

    match node:
        case ast.Constant(value=bool() | str() as value):
            return value
        case ast.Name(id=name) if name in names:
            return names[name]
        case ast.Set(elts=elts) | ast.List(elts=elts) | ast.Tuple(elts=elts):
            return [_evaluate(element, names) for element in elts]
        case ast.Dict(keys=keys, values=values) if all(k is not None for k in keys):
            return {
                _evaluate(k, names): _evaluate(v, names)
                for k, v in zip(keys, values, strict=True)
                if k is not None
            }
        case ast.Call(func=ast.Name(id="frozenset"), args=args, keywords=[]) if len(args) <= 1:
            return frozenset(_sequence(_evaluate(args[0], names)) if args else ())
        case ast.Call(func=ast.Name(id="Table"), args=[], keywords=keywords):
            return _table(dict(_declared(keywords, names)))
    raise _UnreadableSchemaError(ast.dump(node)[:120])


def _declared(
    keywords: list[ast.keyword], names: Mapping[str, object]
) -> Iterator[tuple[str, object]]:
    for keyword in keywords:
        if keyword.arg is None:
            raise _UnreadableSchemaError("Table(**expansion)")
        yield keyword.arg, _evaluate(keyword.value, names)


_TABLE_FIELDS: dict[str, object] = {
    "keys": frozenset(),
    "tables": {},
    "arrays": {},
    "open_keys": False,
}


def _table(declared: dict[str, object]) -> Table:

    if unmodelled := set(declared) - set(_TABLE_FIELDS):
        raise _UnreadableSchemaError(f"Table({', '.join(sorted(unmodelled))}=...)")
    fields = _TABLE_FIELDS | declared
    keys, open_keys = fields["keys"], fields["open_keys"]
    if not isinstance(keys, frozenset) or not isinstance(open_keys, bool):
        raise _UnreadableSchemaError("Table(keys=|open_keys=)")
    named = frozenset(key for key in keys if isinstance(key, str))
    if len(named) != len(keys):
        raise _UnreadableSchemaError("Table(keys=) holds a non-string")
    return Table(
        keys=named,
        tables=_table_map(fields["tables"]),
        arrays=_table_map(fields["arrays"]),
        open_keys=open_keys,
    )


def _table_map(value: object) -> dict[str, Table]:
    if not isinstance(value, dict):
        raise _UnreadableSchemaError(f"expected a table mapping, got {type(value).__name__}")
    mapped = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, Table)}
    if len(mapped) != len(value):
        raise _UnreadableSchemaError("a table mapping holds a non-Table")
    return mapped


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _UnreadableSchemaError(f"expected a sequence literal, got {type(value).__name__}")
    return value
