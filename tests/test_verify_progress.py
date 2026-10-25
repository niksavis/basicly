from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from basicly import hooks, lefthook_binary, lefthook_config
from basicly.hooks import HookSpec

ROOT = Path(__file__).resolve().parents[1]


def _command(root: Path, kind: str) -> list[str]:
    if kind == "engine":
        return [sys.executable, "-m", "basicly.cli", "verify", "--mode", "full"]
    if kind == "portable":
        return [sys.executable, str(ROOT / ".basicly/core/hooks/pre-push.py")]
    shutil.copytree(
        ROOT / ".basicly/core/hooks",
        root / ".basicly/core/hooks",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    lefthook_binary.sync_binary(root)
    lefthook_config.sync_config(
        root, [HookSpec("verify", "pre-push.py", "pre-push")], ".basicly/core/hooks"
    )
    assert hooks.install_hooks(root, ["pre-push"])[0]
    return ["git", "hook", "run", "pre-push"]


def _waiting_child(root: Path, code: int) -> None:
    (root / "barrier.py").write_text(
        "from pathlib import Path\nimport time\n"
        "Path('ready').write_text('waiting')\n"
        "deadline=time.monotonic()+30\n"
        "while not Path('release').exists():\n"
        "    if time.monotonic()>deadline: raise SystemExit(99)\n"
        "    time.sleep(0.01)\n"
        f"raise SystemExit({code})\n",
        encoding="utf-8",
    )
    checks = [
        ("barrier", [sys.executable, "barrier.py"]),
        (
            "later",
            [sys.executable, "-c", "from pathlib import Path; Path('later').write_text('ran')"],
        ),
    ]
    (root / "basicly.toml").write_text(
        "\n".join(
            f'[[verify.checks]]\nname={json.dumps(name)}\ncommand={json.dumps(command)}\nmodes=["full"]\n'
            for name, command in checks
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("kind", ["engine", "portable", "native"])
@pytest.mark.parametrize("code", [0, 7])
def test_progress_arrives_before_child_exit_and_preserves_all_checks(
    tmp_path: Path, kind: str, code: int
) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "chore: initialize fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    _waiting_child(tmp_path, code)
    command = _command(tmp_path, kind)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
    }
    marker = threading.Event()
    output: list[str] = []
    with subprocess.Popen(
        command,
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    ) as process:

        def read() -> None:
            for line in process.stdout or ():
                output.append(line)
                if "==> barrier" in line:
                    marker.set()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + 15
        try:
            while (
                not (tmp_path / "ready").exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert (tmp_path / "ready").exists(), "".join(output)
            assert marker.wait(3), "the named progress marker was buffered while its child waited"
            assert process.poll() is None
        finally:
            (tmp_path / "release").write_text("continue", encoding="utf-8")
            process.wait(timeout=15)
            reader.join(timeout=15)
        assert not reader.is_alive()
        assert process.returncode == (1 if code else 0), "".join(output)
    assert (tmp_path / "later").read_text(encoding="utf-8") == "ran"
