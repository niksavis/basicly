from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_ENTRY = Path("node_modules/markdownlint-cli2/markdownlint-cli2-bin.mjs")


def provision_markdownlint_double(repo: Path) -> None:
    entry = repo / CLI_ENTRY
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("process.exit(0);\n", encoding="utf-8")


def copy_locked_markdownlint_runtime(repo: Path) -> None:
    source = REPO_ROOT / "node_modules"
    locked = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    pending = [source / "markdownlint-cli2"]
    copied: set[Path] = set()
    while pending:
        package = pending.pop()
        if package in copied:
            continue
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        relative = package.relative_to(source)
        assert (
            metadata["version"] == locked[(Path("node_modules") / relative).as_posix()]["version"]
        )
        shutil.copytree(package, repo / "node_modules" / relative)
        copied.add(package)
        dependencies = metadata.get("dependencies", {})
        optional = metadata.get("optionalDependencies", {})
        for name in dependencies.keys() | optional.keys():
            candidates = [
                parent / "node_modules" / name
                for parent in (package, *package.parents)
                if parent == source.parent or source in parent.parents
            ]
            found = next((candidate for candidate in candidates if candidate.is_dir()), None)
            if found is None:
                if name not in optional:
                    raise FileNotFoundError(f"{package.name} needs missing runtime package {name}")
            else:
                pending.append(found)
