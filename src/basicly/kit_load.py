from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import checkout
from .catalog import iter_catalog_files

KIT_DIR = "kit"

_KIT_PATH_PARTS = 3

_LOAD_EVERY_MODULE = """
import importlib.util
import sys
from pathlib import Path

kit = Path(sys.argv[1])
sys.path.insert(0, str(kit))
for index, path in enumerate(sorted(kit.glob("*.py"))):
    spec = importlib.util.spec_from_file_location(f"basicly_kit_load_{index}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
"""


@dataclass(frozen=True)
class UnloadableKit:
    name: str
    kept: tuple[str, ...]
    error: str


def _kept_kit_modules(kept: Iterable[str]) -> dict[str, list[str]]:

    by_kit: dict[str, list[str]] = {}
    for rel_path in kept:
        parts = rel_path.split("/")
        if len(parts) == _KIT_PATH_PARTS and parts[0] == KIT_DIR and rel_path.endswith(".py"):
            by_kit.setdefault(parts[1], []).append(rel_path)
    return by_kit


def _load_error(kit_dir: Path) -> str | None:

    done = checkout.run(
        [sys.executable, "-I", "-B", "-c", _LOAD_EVERY_MODULE, str(kit_dir)], check=False
    )
    if done.returncode == 0:
        return None
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    return lines[-1] if lines else f"exit {done.returncode}"


def unloadable_kits(bundled: Path, core: Path, kept: Iterable[str]) -> list[UnloadableKit]:

    found: list[UnloadableKit] = []
    for name, kept_modules in sorted(_kept_kit_modules(kept).items()):
        with tempfile.TemporaryDirectory() as scratch:
            staged = Path(scratch) / name
            staged.mkdir()
            bundled_kit = bundled / KIT_DIR / name
            if bundled_kit.is_dir():
                for source in iter_catalog_files(bundled_kit):
                    target = staged / source.relative_to(bundled_kit)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            for rel_path in kept_modules:
                shutil.copy2(core / rel_path, staged / Path(rel_path).name)
            error = _load_error(staged)
        if error is not None:
            found.append(UnloadableKit(name, tuple(kept_modules), error))
    return found


def refusal(core_label: str, unloadable: Iterable[UnloadableKit]) -> str:

    lines = [
        "basicly install: refusing to write anything: the managed core at "
        f"{core_label} would mix kit modules that cannot load together.",
    ]
    for kit in unloadable:
        lines.append(f"  {KIT_DIR}/{kit.name}: {kit.error}")
        lines.extend(f"    kept: {rel_path}" for rel_path in kit.kept)
    lines.append(
        "Re-run with --force to replace each kept file with this release's version, "
        f"and move a file of unknown origin out of {core_label} (hand-edits belong in the overlay)."
    )
    return "\n".join(lines)
