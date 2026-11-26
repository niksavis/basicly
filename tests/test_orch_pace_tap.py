from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from tests.test_orch_pace import PACKAGE, SEVEN_RESET, snapshot, status

GLYPHS = b"model \xe2\x96\x88\xe2\x96\x91 42%\n"


def tap(stdin: bytes, env: dict[str, str], *command: str) -> subprocess.CompletedProcess[bytes]:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from orch_pace.tap import main; raise SystemExit(main(sys.argv[2:]))"
    )
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", "-c", code, str(PACKAGE), *command],
        input=stdin,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", **env},
        timeout=20,
        check=False,
    )


def echo_child(tmp_path: Path, exit_code: int = 0) -> list[str]:
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        f"sys.stdout.buffer.write({GLYPHS!r} + str(len(data)).encode())\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return ["--", sys.executable, str(script)]


def test_the_tap_passes_stdin_through_and_writes_the_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "state" / "statusline.json"
    stdin = json.dumps(status()).encode()
    done = tap(stdin, {snapshot.DIR_ENV: str(target.parent)}, *echo_child(tmp_path, 3))
    assert done.returncode == 3
    assert done.stdout == GLYPHS + str(len(stdin)).encode()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["format"] == snapshot.FORMAT
    assert written["version"] == 1
    assert written["writer"] == "statusline"
    assert written["windows"]["five_hour"]["used_percentage"] == 40.0
    assert written["windows"]["seven_day"]["resets_at"] == SEVEN_RESET
    assert list(target.parent.glob(".usage-snapshot.*")) == []


@pytest.mark.parametrize(
    "stdin",
    [b"", b"{not json", json.dumps({"model": {}}).encode(), b"\xff\xfe"],
    ids=["empty", "invalid-json", "no-rate-limits", "not-utf8"],
)
def test_the_tap_prints_the_output_when_the_input_has_no_usage(
    tmp_path: Path, stdin: bytes
) -> None:
    done = tap(stdin, {snapshot.DIR_ENV: str(tmp_path / "s")}, *echo_child(tmp_path))
    assert done.returncode == 0
    assert done.stdout == GLYPHS + str(len(stdin)).encode()
    assert not (tmp_path / "s").exists()


def test_the_tap_prints_the_output_when_the_snapshot_cannot_be_written(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    stdin = json.dumps(status()).encode()
    env = {snapshot.DIR_ENV: str(blocker / "snapshots")}
    done = tap(stdin, env, *echo_child(tmp_path))
    assert done.returncode == 0
    assert done.stdout == GLYPHS + str(len(stdin)).encode()


def test_the_tap_names_a_command_that_cannot_start(tmp_path: Path) -> None:
    target = tmp_path / "statusline.json"
    stdin = json.dumps(status()).encode()
    done = tap(stdin, {snapshot.DIR_ENV: str(tmp_path)}, "--", str(tmp_path / "missing"))
    assert done.returncode == 127
    assert b"cannot start" in done.stderr
    assert b"orch-statusline-tap -- COMMAND" in done.stderr
    assert target.is_file()


def test_the_tap_refuses_no_command_by_name(tmp_path: Path) -> None:
    done = tap(b"{}", {snapshot.DIR_ENV: str(tmp_path)}, "--")
    assert done.returncode == 2
    assert b"no status line command given" in done.stderr


def test_the_snapshot_folder_follows_the_documented_order(tmp_path: Path) -> None:
    named = tmp_path / "named"
    tail = Path("basicly") / "usage-snapshots"
    assert snapshot.folder({snapshot.DIR_ENV: str(named)}, "linux", tmp_path) == named
    xdg = {"XDG_STATE_HOME": str(tmp_path / "x")}
    assert snapshot.folder(xdg, "linux", tmp_path) == tmp_path / "x" / tail
    relative = {"XDG_STATE_HOME": "relative"}
    assert snapshot.folder(relative, "linux", tmp_path) == tmp_path / ".local" / "state" / tail
    local = {"LOCALAPPDATA": str(tmp_path / "L")}
    assert snapshot.folder(local, "win32", tmp_path) == tmp_path / "L" / tail
    with pytest.raises(ValueError, match="writer name"):
        snapshot.own_file({snapshot.DIR_ENV: str(named)}, "../x")
