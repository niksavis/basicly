from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self


class InputFingerprint(str):
    components: tuple[tuple[str, str], ...]

    def __new__(cls, payload: Mapping[str, object] | str) -> Self:
        if isinstance(payload, str):
            if len(payload) != 64 or any(char not in "0123456789abcdef" for char in payload):
                raise ValueError("Invalid input fingerprint digest")
            copied = super().__new__(cls, payload)
            copied.components = ()
            return copied
        instance = super().__new__(
            cls, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        )
        instance.components = tuple(
            (key, hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())
            for key, value in sorted(payload.items())
        )
        return instance


def implementation_digest() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or any(
            part.startswith(".env") for part in path.relative_to(root).parts
        ):
            raise ValueError("protected or symlinked engine implementation input")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def components(inputs: str) -> tuple[tuple[str, str], ...]:
    return inputs.components if isinstance(inputs, InputFingerprint) else ()


def changed_components(inputs: str, previous: tuple[tuple[str, str], ...]) -> str:
    before = dict(previous)
    changed = [key for key, value in components(inputs) if before.get(key) != value]
    return "changed input components: " + ", ".join(changed) if changed and before else ""


@dataclass(frozen=True)
class CheckReceipt:
    name: str
    command: tuple[str, ...]
    mode: str
    inputs: str
    origin: str
    components: tuple[tuple[str, str], ...] = ()


def supported_command(command: tuple[str, ...], mode: str) -> bool:
    return (
        mode == "full"
        and len(command) == 3
        and command[:2] == ("pyright", "--pythonplatform")
        and command[2] in {"Linux", "Darwin", "Windows"}
    )


@dataclass(frozen=True)
class CachedCheck:
    name: str
    command: tuple[str, ...]
    mode: str
    inputs: str
    origin: str
    duration_s: float
    components: tuple[tuple[str, str], ...] = ()
