from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

ARCHITECTURE_MD = "docs/architecture/architecture.md"
SKILLS_DIR = ".basicly/core/skills"


class ClaimError(Exception):
    pass


def read_text(path: Path) -> str:
    if not path.exists():
        raise ClaimError(f"{path} does not exist")
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(read_text(path))
    if not isinstance(data, dict):
        raise ClaimError(f"{path}: expected a YAML mapping")
    return data


def subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None
