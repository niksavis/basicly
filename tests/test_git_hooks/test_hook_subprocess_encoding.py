from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks"


def _text_calls_without_encoding(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        text = keywords.get("text", keywords.get("universal_newlines"))
        if isinstance(text, ast.Constant) and text.value is True and "encoding" not in keywords:
            offenders.append(f"{path.name}:{node.lineno}")
    return offenders


def test_every_text_mode_subprocess_call_in_a_hook_declares_its_encoding() -> None:
    offenders = [
        offender
        for path in sorted(HOOKS.glob("*.py"))
        for offender in _text_calls_without_encoding(path)
    ]
    assert offenders == []


def _load_identity_guard():
    script_path = HOOKS / "identity-guard.py"
    spec = importlib.util.spec_from_file_location("identity_guard_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_identity_guard_reads_a_staged_utf8_diff_that_the_ansi_code_page_cannot_decode(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "notes.txt").write_bytes("Ő is 0xC5 0x90 in UTF-8\n".encode())
    _git(tmp_path, "add", "notes.txt")

    added = _load_identity_guard().added_lines(tmp_path)

    assert ("notes.txt", "Ő is 0xC5 0x90 in UTF-8") in added
