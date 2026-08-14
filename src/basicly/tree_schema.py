"""The ``CONFIG_SCHEMA`` a basicly tree declares, read out of that tree's own source.

One question: which config names does the engine *that tree ships* accept, answered
without importing it (:func:`_parse_schema` says why). :class:`Table` is the shape an
answer takes, and lives here because this module is what builds one out of source no
process has reviewed.

The boundary is *whose* schema against *what a schema does*: :mod:`basicly.config`
declares this engine's own in :class:`Table`, walks a config file against one and
decides what a refusal reads like, while nothing here has ever seen a basicly.toml.
Split out of that module, whose charter is loading this repository's configuration
rather than interpreting a not-yet-merged tree's syntax tree (basicly-2365).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Table:
    """The shape one TOML table accepts: keys, sub-tables, and arrays of tables."""

    keys: frozenset[str] = frozenset()
    tables: dict[str, Table] = field(default_factory=dict)
    arrays: dict[str, Table] = field(default_factory=dict)
    # The key names here are the consumer's to choose — an agent name, a loop
    # phase, a task class — so there is no vocabulary to check them against at
    # this layer. Each such table has its own validator downstream that names what
    # it will accept ([runner] context_windows raises on an unknown agent;
    # policy.evidence_status refuses an unusable phase).
    open_keys: bool = False


# Where a basicly *source checkout* keeps its ``CONFIG_SCHEMA``. A consumer repo has
# no such file, so :func:`read` answers None for it and nothing below ever runs.
_ENGINE_SOURCE = Path("src") / "basicly" / "config.py"

# The ordering rule the refusal names when the tree ships a schema we could not read
# statically, so the caller is told the shape of the failure rather than left with a
# message that reads as a config typo (basicly-69az).
ORDERING_RULE = (
    "This tree ships its own src/basicly/config.py but its CONFIG_SCHEMA could not be "
    "read statically, so the name was checked against the running engine's schema "
    "instead. If the name is one this tree adds, land the schema change first and the "
    "basicly.toml declaration in a following commit."
)


class _UnreadableSchemaError(Exception):
    """A construct in a tree's ``CONFIG_SCHEMA`` this static reader does not model."""


# One entry per tree, invalidated on (mtime, size): a landing rewrites base's
# config.py mid-process, and a cache that missed that would answer with the schema
# from before the merge — the very staleness this whole path exists to remove.
_TREE_SCHEMA_CACHE: dict[Path, tuple[int, int, dict[str, Table] | None]] = {}


def ships_engine_source(repo_root: Path) -> bool:
    """True when *repo_root* is a basicly source checkout rather than a consumer repo."""
    return (repo_root / _ENGINE_SOURCE).is_file()


def read(repo_root: Path) -> dict[str, Table] | None:
    """``CONFIG_SCHEMA`` as *repo_root*'s own source declares it, or None.

    None means either "not a basicly checkout" or "declared in a way this reader
    cannot model"; both fall back to the running engine's schema, and only the
    second is worth saying out loud (:data:`ORDERING_RULE`).
    """
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
    """The ``CONFIG_SCHEMA`` *source* declares, read statically; None if unreadable.

    Static on purpose. The tree whose schema this is has not been merged yet, so
    importing it would run a second copy of the engine inside the process that is
    landing it, and the answer needed here is a set of names — not behaviour.

    Fails closed: any construct :func:`_evaluate` does not model, any missing
    module-level name, and any shape :class:`Table` will not accept all yield None,
    which restores the running engine's schema and the refusal that goes with it.
    """
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
            # Only fatal for the schema itself: the module being read has plenty of
            # module-level assignments (defaults, the scaffold string) that are
            # neither readable this way nor part of the answer.
            if target == "CONFIG_SCHEMA":
                return None
    try:
        schema = _table_map(names.get("CONFIG_SCHEMA"))
    except _UnreadableSchemaError:
        return None
    return schema or None


def _assigned(statement: ast.stmt) -> tuple[str | None, ast.expr | None]:
    """The single module-level name *statement* binds and its value expression."""
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id, statement.value
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        if isinstance(target, ast.Name):
            return target.id, statement.value
    return None, None


def _evaluate(node: ast.expr, names: Mapping[str, object]) -> object:
    """One schema expression's value, or :class:`_UnreadableSchemaError`.

    Models exactly the vocabulary :data:`basicly.config.CONFIG_SCHEMA` is written in:
    string and bool constants, set/list/tuple/dict literals, references to
    module-level names already bound, ``frozenset(...)`` and ``Table(...)``.
    """
    match node:
        case ast.Constant(value=bool() | str() as value):
            return value
        case ast.Name(id=name) if name in names:
            return names[name]
        case ast.Set(elts=elts) | ast.List(elts=elts) | ast.Tuple(elts=elts):
            return [_evaluate(element, names) for element in elts]
        case ast.Dict(keys=keys, values=values) if all(k is not None for k in keys):
            # `k is not None` is re-asserted for the type checker; the guard above
            # has already rejected a `**expansion`, which is what a None key is.
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
    """Each ``Table(...)`` keyword's name and evaluated value; ``**kwargs`` is refused."""
    for keyword in keywords:
        if keyword.arg is None:
            raise _UnreadableSchemaError("Table(**expansion)")
        yield keyword.arg, _evaluate(keyword.value, names)


# The fields a `Table(...)` call may set, with the value an omitted one takes.
_TABLE_FIELDS: dict[str, object] = {
    "keys": frozenset(),
    "tables": {},
    "arrays": {},
    "open_keys": False,
}


def _table(declared: dict[str, object]) -> Table:
    """A :class:`Table` from evaluated keyword values, refusing anything off-shape.

    Every field is re-derived rather than cast: the source is a tree that has not
    been reviewed by this process, so a value that merely *looks* like a schema must
    not reach the walk as one.
    """
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
    """*value* as a ``{name: Table}`` mapping, or :class:`_UnreadableSchemaError`."""
    if not isinstance(value, dict):
        raise _UnreadableSchemaError(f"expected a table mapping, got {type(value).__name__}")
    mapped = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, Table)}
    if len(mapped) != len(value):
        raise _UnreadableSchemaError("a table mapping holds a non-Table")
    return mapped


def _sequence(value: object) -> list[object]:
    """*value* as the list a set/list/tuple literal evaluates to."""
    if not isinstance(value, list):
        raise _UnreadableSchemaError(f"expected a sequence literal, got {type(value).__name__}")
    return value
