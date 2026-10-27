from __future__ import annotations

import argparse
import json
import sys
import tempfile
import tomllib
from pathlib import Path

from basicly import checkout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pythonplatform", choices=("Linux", "Darwin", "Windows"))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    settings = config["tool"]["pyright"]
    failed = False
    for entry in settings["include"]:
        path = root / entry
        if not any(part.startswith(".") and part != "." for part in Path(entry).parts):
            continue
        if not path.is_dir() or not path.resolve().is_relative_to(root):
            print(
                f"pyright-hidden: expected an included directory within the project: {entry}",
                file=sys.stderr,
            )
            return 1
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="basicly-pyright-",
            dir=path,
            delete=False,
            encoding="utf-8",
        ) as stream:
            scoped = Path(stream.name)
            json.dump({"extends": str(root / "pyproject.toml"), "include": ["."]}, stream)
        try:
            command = ["pyright", "--project", str(scoped), "--pythonpath", sys.executable]
            if args.pythonplatform:
                command.extend(["--pythonplatform", args.pythonplatform])
            print(f"pyright-hidden: checking {entry}", flush=True)
            result = checkout.run(command, cwd=root, check=False)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
        finally:
            scoped.unlink()
        failed = failed or result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
