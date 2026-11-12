from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from basicly import redact as engine

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load("basicly_tracker_kit_redact", "redact.py")

TOKEN = "ghp" + "_" + "A1b2" * 9
HOME = "/" + "home" + "/someone/project/notes.md"


def _patterns(rules: tuple) -> list[tuple[str, str]]:
    return [(rule, pattern.pattern) for rule, pattern in rules]


def test_the_kit_repeats_every_engine_rule_exactly() -> None:
    assert _patterns(kit.SECRET_RULES) == _patterns(engine._RULES)
    assert _patterns(kit.MACHINE_PATH_RULES) == _patterns(engine.MACHINE_PATH_RULES)
    assert kit.PLACEHOLDER.pattern == engine._PLACEHOLDER.pattern
    assert kit.DUMP_PATTERN.pattern == engine._DUMP_PATTERN.pattern
    assert kit.CREDENTIAL_PATTERN.pattern == engine._CREDENTIAL_PATTERN.pattern


@pytest.mark.parametrize(
    ("raw", "rule"),
    [
        (f"token {TOKEN} here", "github-token"),
        (f"see {HOME}", "posix-home-path"),
        ("logs in /var/lib/app/state.db now", "posix-absolute-path"),
        ("C:\\Users\\someone\\x.txt", "windows-drive-path"),
        ("API_TOKEN=" + "s3cr3tvalue", "env-credential-assignment"),
    ],
)
def test_a_credential_shape_or_an_absolute_path_becomes_a_marker(raw: str, rule: str) -> None:
    assert f"<redacted:{rule}>" in kit.text(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "edit src/basicly/cli.py and refs/heads/main",
        "https://github.com/acme/vault.git",
        "and/or 12/11 <redacted:posix-home-path>",
        'password = "example-value"',
    ],
)
def test_relative_paths_urls_and_placeholders_are_kept(raw: str) -> None:
    assert kit.text(raw) == raw


def test_every_string_in_an_event_is_redacted_and_other_values_are_kept() -> None:
    redacted = kit.event({"payload": {"text": TOKEN, "list": [HOME, 3]}, "at": 1.5})

    assert redacted == {
        "payload": {"text": "<redacted:github-token>", "list": ["<redacted:posix-home-path>", 3]},
        "at": 1.5,
    }
