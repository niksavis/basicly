from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_tracker_plugin
from tests.test_tracker_mcp import BOOT

FIXTURE_LEDGER = Path(__file__).resolve().parent / "fixtures/work_repo/ledger"

package = test_tracker_plugin.package


@pytest.fixture(autouse=True)
def _outside_the_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "cwd"
    shutil.copytree(FIXTURE_LEDGER, repo / ".basicly/ledger")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)


LEAF = """import json,os,subprocess,sys
inherited = subprocess.check_output(
    [sys.executable, '-I', '-S', '-c',
     'import os;print(os.environ["BASICLY_TRACKER_SESSION"])'], text=True
).strip()
print(json.dumps({
    'session':os.environ['BASICLY_TRACKER_SESSION'],
    'child_session':inherited, 'arguments':sys.argv[1:]
}))
"""


@pytest.mark.parametrize("interface", ["package", "harness"])
def test_launched_clients_and_children_get_distinct_sessions_and_literal_arguments(
    package: Path, tmp_path: Path, interface: str
) -> None:
    script = tmp_path / "client.py"
    script.write_text(LEAF)
    argv = (
        [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py"), "agent"]
        if interface == "package"
        else [
            str(Path(sys.executable).parent / ("basicly.exe" if os.name == "nt" else "basicly")),
            "tracker",
            "agent",
        ]
    )
    env = os.environ.copy()
    env["BASICLY_TRACKER_SESSION"] = "parent-session"
    literal = ["a value with spaces", "$(not-a-command)", "--client-option=value"]

    def run(extra: list[str]) -> dict:
        done = subprocess.run(
            [*argv, *extra, "--", sys.executable, "-I", "-S", str(script), *literal],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    first, second = run([]), run([])
    assert first["session"] != second["session"]
    assert first["session"] != "parent-session"
    assert first["session"] == first["child_session"]
    assert second["session"] == second["child_session"]
    assert first["arguments"] == second["arguments"] == literal
    resumed = run(["--session", first["session"]])
    assert resumed["session"] == resumed["child_session"] == first["session"]
    assert env["BASICLY_TRACKER_SESSION"] == "parent-session"


def test_launcher_refuses_an_invalid_session_before_starting_the_client(
    package: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "should-not-exist"
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "agent",
            "--session",
            "bad/session",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 2
    assert "--session must" in done.stderr
    assert not marker.exists()


def test_launcher_preserves_the_clients_failure_exit_code(package: Path) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "agent",
            "--",
            sys.executable,
            "-I",
            "-S",
            "-c",
            "raise SystemExit(7)",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 7


def test_launched_coding_agent_can_complete_refinement_without_inherited_agent_markers(
    package: Path, tmp_path: Path
) -> None:
    env = os.environ.copy()
    for marker in ("AI_AGENT", "BR_AGENT_NAME", "CLAUDECODE"):
        env.pop(marker, None)
    script = Path(__file__).parent / "fixtures/tracker_agent_refine.py"
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "agent",
            "--",
            sys.executable,
            "-I",
            "-S",
            str(script),
            str(package),
            str(tmp_path / "repo"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert json.loads(done.stdout)["refinement_completed"]


@pytest.mark.parametrize("interface", ["package", "harness"])
def test_foreground_agent_shares_its_session_with_mcp_and_real_git_hooks(
    package: Path, tmp_path: Path, interface: str
) -> None:
    script = Path(__file__).parent / "fixtures/tracker_agent_client.py"
    argv = (
        [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py"), "agent"]
        if interface == "package"
        else [
            str(Path(sys.executable).parent / ("basicly.exe" if os.name == "nt" else "basicly")),
            "tracker",
            "agent",
        ]
    )
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        BASICLY_TRACKER_SESSION="shared-parent",
        BASICLY_TRACKER_STATE=str(tmp_path / "state"),
        BASICLY_TRACKER_EPHEMERAL="1",
        BASICLY_HOLDER="agent-fixture",
        GIT_AUTHOR_NAME="agent-fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="agent-fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )
    done = subprocess.run(
        [
            *argv,
            "--",
            sys.executable,
            "-I",
            "-S",
            str(script),
            str(package),
            str(tmp_path / "repo"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    report = json.loads(done.stdout)
    assert report["session"] != "shared-parent"
    assert report["missing_id_commit_exit"] != 0
    assert report["claimed_commit_exit"] == 0
    assert report["guard_preserved"]
