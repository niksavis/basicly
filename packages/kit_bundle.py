from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipapp
from pathlib import Path

SKIPPED_NAMES = frozenset({"__pycache__"})

INTERPRETER = "/usr/bin/env python3"

MAIN = """import importlib.util
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGE = "@PACKAGE@"
DIGEST = "@DIGEST@"


def _cache_root():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    return Path(base) if base else Path.home() / ".cache"


def _extract(archive, staging):
    prefix = PACKAGE + "/"
    for member in archive.namelist():
        if not member.startswith(prefix) or member.endswith("/"):
            continue
        relative = Path(member)
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit("the archive names an unsafe path: " + member)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read(member))


def _unpacked():
    target = _cache_root() / "basicly-kits" / (PACKAGE + "-" + DIGEST)
    entry = target / PACKAGE / "__init__.py"
    if entry.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=str(target.parent)))
    with zipfile.ZipFile(os.path.dirname(os.path.abspath(__file__))) as archive:
        _extract(archive, staging)
    try:
        os.replace(str(staging), str(target))
    except OSError:
        shutil.rmtree(str(staging), ignore_errors=True)
        if not entry.is_file():
            raise
    return target


def main():
    entry = _unpacked() / PACKAGE / "__init__.py"
    spec = importlib.util.spec_from_file_location(PACKAGE, str(entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module.main(sys.argv[1:])


sys.exit(main())
"""


def package_files(package_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(package_dir.rglob("*"))
        if path.is_file()
        and not any(part in SKIPPED_NAMES for part in path.relative_to(package_dir).parts)
    ]


def digest(package_dir: Path, files: list[Path]) -> str:
    hashed = hashlib.sha256()
    for path in files:
        hashed.update(path.relative_to(package_dir).as_posix().encode("utf-8") + b"\0")
        hashed.update(path.read_bytes() + b"\0")
    return hashed.hexdigest()[:16]


def bundle(package_dir: Path, out: Path) -> Path:

    files = package_files(package_dir)
    if not files:
        raise SystemExit(f"the package is missing from {package_dir}")
    package = package_dir.name
    with tempfile.TemporaryDirectory() as staging:
        stage = Path(staging)
        for path in files:
            destination = stage / package / path.relative_to(package_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        text = MAIN.replace("@PACKAGE@", package).replace("@DIGEST@", digest(package_dir, files))
        (stage / "__main__.py").write_text(text, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        zipapp.create_archive(stage, out, interpreter=INTERPRETER)
    return out
