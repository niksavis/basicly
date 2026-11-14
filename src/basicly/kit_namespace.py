from __future__ import annotations

import builtins
import hashlib
import importlib
import importlib.abc
import importlib.util
import sys
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from types import ModuleType
from typing import Any

PREFIX = "basicly_tracker_"
_lock = threading.RLock()
_contexts: dict[tuple[Path, str], Namespace] = {}


class KitImportError(ImportError):
    pass


def _proxy(original: ModuleType, **overrides: Any) -> ModuleType:
    proxy = ModuleType(original.__name__)
    proxy.__dict__.update(overrides)
    proxy.__dict__["__getattr__"] = lambda name: getattr(original, name)
    return proxy


class _Modules(MutableMapping[str, ModuleType]):
    def __init__(self, context: Namespace) -> None:
        self.context = context

    def __getitem__(self, key: str) -> ModuleType:
        with self.context.lock:
            return sys.modules[self.context.qualify(key)]

    def __setitem__(self, key: str, value: ModuleType) -> None:
        with self.context.lock:
            sys.modules[self.context.qualify(key)] = value

    def __delitem__(self, key: str) -> None:
        with self.context.lock:
            del sys.modules[self.context.qualify(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(sys.modules.copy())

    def __len__(self) -> int:
        return len(sys.modules)


class _Loader(importlib.abc.SourceLoader):
    def __init__(self, context: Namespace, filename: str) -> None:
        self.context = context
        self.filename = filename

    def get_filename(self, fullname: str) -> str:
        if not fullname.startswith(self.context.prefix):
            raise KitImportError("the tracker module has no source namespace")
        return str(self.context.home / self.filename)

    def get_data(self, path: str) -> bytes:
        if Path(path).name != self.filename:
            raise OSError("only captured tracker source can be loaded")
        return self.context.sources[self.filename]

    def exec_module(self, module: ModuleType) -> None:
        context = self.context
        with context.lock:
            if module in context.loaded or module in context.loading:
                return
            context.loading.add(module)
            module.__dict__["__builtins__"] = {
                **vars(builtins),
                "__import__": context.import_module,
            }
            try:
                super().exec_module(module)
            except BaseException:
                sys.modules.pop(module.__name__, None)
                raise
            else:
                context.loaded.add(module)
            finally:
                context.loading.discard(module)


class Namespace:
    def __init__(self, home: Path, sources: dict[str, bytes], digest: str) -> None:
        self.home = home
        self.sources = sources
        self.prefix = PREFIX + digest + "_"
        self.lock = threading.RLock()
        self.loaded: set[ModuleType] = set()
        self.loading: set[ModuleType] = set()
        self.sys = _proxy(sys, modules=_Modules(self))
        self.util = _proxy(
            importlib.util,
            spec_from_file_location=self.spec,
            module_from_spec=self.module_from_spec,
        )
        self.importlib = _proxy(importlib, util=self.util)

    def qualify(self, name: str) -> str:
        if name.startswith(self.prefix) or not name.startswith(PREFIX):
            return name
        return self.prefix + name[len(PREFIX) :]

    def import_module(self, name: str, *args: Any, **kwargs: Any) -> Any:
        imported = builtins.__import__(name, *args, **kwargs)
        level = kwargs.get("level", args[3] if len(args) > 3 else 0)
        fromlist = kwargs.get("fromlist", args[2] if len(args) > 2 else ())
        if level == 0:
            if name == "sys":
                return self.sys
            if name == "importlib":
                return self.importlib
            if name == "importlib.util":
                return self.util if fromlist else self.importlib
        return imported

    def spec(self, name: str, location: Any, **options: Any) -> Any:
        path = Path(location)
        if (
            not name.startswith(PREFIX)
            or path.suffix != ".py"
            or path.parent.resolve() != self.home
            or path.name not in self.sources
            or options
        ):
            raise KitImportError(
                "the tracker kit requested an unknown sibling; run basicly install"
            )
        return importlib.util.spec_from_file_location(
            self.qualify(name), self.home / path.name, loader=_Loader(self, path.name)
        )

    def module_from_spec(self, spec: Any) -> ModuleType:
        if not isinstance(spec.loader, _Loader):
            return importlib.util.module_from_spec(spec)
        if spec.loader.context is not self:
            return spec.loader.context.module_from_spec(spec)
        with self.lock:
            if (module := sys.modules.get(spec.name)) is not None:
                return module
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module

    def load(self, name: str) -> ModuleType:
        if not name.isidentifier() or name + ".py" not in self.sources:
            raise KitImportError(f"the tracker kit has no {name}.py; run basicly install")
        with self.lock:
            spec = self.spec(PREFIX + "kit_" + name, self.home / (name + ".py"))
            return self.module_from_spec(spec)

    def source(self, name: str) -> bytes:
        spec = self.spec(PREFIX + "kit_" + name, self.home / (name + ".py"))
        return spec.loader.get_data(spec.loader.get_filename(spec.name))


def _paths(home: Path) -> list[Path]:
    paths = sorted(
        path
        for path in home.iterdir()
        if not path.name.startswith(".env") and path.suffix != ".pyc" and path.name != "__pycache__"
    )
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in paths):
        raise KitImportError("tracker kit modules must be regular files; run basicly install")
    return [path for path in paths if path.is_file()]


def _signature(paths: list[Path]) -> tuple:
    found = []
    for path in paths:
        stat = path.stat()
        found.append((path.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
    return tuple(found)


def snapshot(directory: Path) -> Namespace:
    home = directory.resolve()
    paths = _paths(home)
    before = _signature(paths)
    sources = {path.name: path.read_bytes() for path in paths}
    if before != _signature(_paths(home)):
        raise KitImportError(
            "the tracker kit changed during loading; retry after its update finishes"
        )
    digest = hashlib.sha256(str(home).encode())
    for name, body in sources.items():
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    key = home, digest.hexdigest()
    with _lock:
        if key not in _contexts:
            _contexts[key] = Namespace(home, sources, key[1])
        return _contexts[key]
