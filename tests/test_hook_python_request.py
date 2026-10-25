from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from basicly import (
    claude_settings,
    codex_hooks,
    hook_python,
    hooks,
    lefthook_binary,
    lefthook_config,
)

ROOT = Path(__file__).resolve().parents[1]


def test_dispatcher_carries_the_minimum_in_environment_without_redirection() -> None:
    spec = hooks.HookSpec("gate", "gate.py", "pre-commit", always_run=True)
    config = yaml.safe_load(lefthook_config.managed_config([spec], ".basicly/core/hooks"))
    job = config["pre-commit"]["commands"]["basicly-dispatch"]
    assert job["env"]["UV_PYTHON"] == ">=3.14"
    assert not set("<>|&;`") & set(job["run"])
    assert job["env"]["PYTHONUTF8"] == "1"


def test_managed_launchers_share_the_supported_runtime_request() -> None:
    launcher = lefthook_config.render_git_launcher(
        "pre-commit", Path(".basicly/core/hooks"), b""
    ).decode("utf-8")
    assert hook_python.COMMAND in launcher
    assert claude_settings.HOOK_INTERPRETER == hook_python.COMMAND
    script = codex_hooks.windows_command(".basicly/core/hooks/lefthook-runtime.py", "pretooluse")
    decoded = base64.b64decode(script.rsplit(" ", 1)[1]).decode("utf-16-le")
    assert '--python ">=3.14"' in decoded
    assert "python -X utf8" in decoded


def test_installed_git_hook_ignores_an_unsupported_consumer_version_pin(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / ".basicly/core/hooks"
    source.mkdir(parents=True)
    for name in ("lefthook-runtime.py",):
        shutil.copyfile(ROOT / ".basicly/core/hooks" / name, source / name)
    (source / "runtime.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path('runtime.json').write_text(json.dumps(list(sys.version_info[:2])))\n",
        encoding="utf-8",
    )
    (tmp_path / ".python-version").write_text("3.12\n", encoding="utf-8")
    specs = [hooks.HookSpec("runtime", "runtime.py", "post-merge", always_run=True)]
    lefthook_binary.sync_binary(tmp_path)
    lefthook_config.sync_config(tmp_path, specs, ".basicly/core/hooks")
    assert hooks.install_hooks(tmp_path, ["post-merge"])[0]
    result = subprocess.run(
        ["git", "hook", "run", "post-merge"],
        cwd=tmp_path,
        env={**os.environ, "UV_PYTHON": "3.12", "UV_PYTHON_DOWNLOADS": "never"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert tuple(json.loads((tmp_path / "runtime.json").read_text())) >= (3, 14)
    assert sys.version_info >= (3, 14)
