from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import subprocess  # nosec B404 # Execute explicit native consumer commands without a shell.
import sys
import tempfile
import threading
import time
from pathlib import Path

from validate_affected_consumer import Proof


class Measurement(Proof):
    def __init__(self, root: Path, env: dict[str, str], engine: Path, python: Path) -> None:
        super().__init__(root, env, engine, python, root / "check-executions")
        env["BASICLY_MEASUREMENT_LOG"] = str(self.calls_root)
        self.commands: list[dict] = []
        self.phase = "setup"
        self.checkpoints = 0
        self.last_output = ""
        self.log_lock = threading.Lock()

    def run(
        self, argv: list[str | Path], cwd: Path, *, combined: bool = False
    ) -> tuple[str, float, int]:
        started = time.perf_counter()
        result = subprocess.run(  # nosec B603 # Arguments are fixture-owned argv, never shell syntax.
            [str(arg) for arg in argv],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        duration = time.perf_counter() - started
        self.last_output = result.stdout + result.stderr
        row = {
            "phase": self.phase,
            "argv": [str(arg) for arg in argv],
            "cwd": str(cwd),
            "exit_code": result.returncode,
            "duration_s": duration,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        with self.log_lock:
            self.commands.append(row)
            with (self.root / "commands.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps(row) + "\n")
        return result.stdout + (result.stderr if combined else ""), duration, result.returncode

    def call(
        self, argv: list[str | Path], cwd: Path, expected: int = 0, *, combined: bool = False
    ) -> tuple[str, float]:
        stdout, duration, code = self.run(argv, cwd, combined=combined)
        if code != expected:
            raise RuntimeError(f"Expected exit {expected}, got {code}: {self.last_output[-2000:]}")
        return stdout, duration

    def write(self, repo: Path, *args: str) -> str:
        return self.call([self.engine, "tracker", "write", "--", *args], repo)[0]

    def show(self, repo: Path, record: str) -> dict:
        stdout, _ = self.call(
            [self.python, ".basicly/core/kit/tracker/cli.py", "show", ".basicly/ledger", record],
            repo,
        )
        return json.loads(stdout)

    def advance(self, repo: Path, record: str) -> str:
        argv = [
            self.engine,
            "loop",
            "run",
            record,
            "--work-type",
            "task",
            "--runner",
            "manual",
            "--tier",
            "high",
        ]
        base = list(argv)
        for _ in range(4):
            self.run(argv, repo)
            output = self.last_output
            checkpoint = re.search(
                r"checkpoint (classify|ship): CONFIRMATION REQUIRED \(" + re.escape(record) + r"\)",
                output,
            )
            challenge = re.search(r"--confirm ([a-f0-9]+)", output)
            if checkpoint is None or challenge is None:
                return output
            if checkpoint.group(1) == "ship" and "[merged]" not in output:
                raise RuntimeError("Ship challenge arrived without a merged result")
            self.checkpoints += 1
            argv = [*base, "--confirm", challenge.group(1)]
        raise RuntimeError("More than four checkpoint challenges in one fixture transition")

    def provision(self, repo: Path, filename: str, content: str) -> dict:
        criterion = f"When the change lands, {filename} shall contain the agreed fixture content."
        check = [
            "python",
            "-c",
            f"from pathlib import Path; assert Path({filename!r}).read_text() == {content!r}",
        ]
        created = json.loads(
            self.write(
                repo,
                "create",
                f"Measure the {filename} change",
                "-t",
                "task",
                "-d",
                "## Trigger\n\nWhen a fixture changes, I want to land it normally, "
                "so I can measure the lifecycle.\n\n## Scope\n\n- `" + filename + "`\n",
                "--acceptance",
                criterion,
                "--requirements",
                "Preserve hooks, staged content, foreign data and actual confirmation.",
                "--json",
            )
        )
        record = created["id"]
        self.write(
            repo,
            "comments",
            "add",
            record,
            "The owner approved deterministic fixture edits and checkpoint relays "
            "for this baseline.",
        )
        seq = self.show(repo, record)["comment_log"][-1]["seq"]
        review = {
            "invest": dict.fromkeys(
                ["independent", "negotiable", "valuable", "estimable", "small", "testable"],
                "One isolated fixture file is confirmed by its actual content.",
            ),
            "conversation": [seq],
            "checks": [
                {
                    "criterion": criterion,
                    "command": check,
                    "expected": "The exact agreed content is present.",
                }
            ],
        }
        self.write(repo, "review", record, "--evidence", json.dumps(review))
        output = self.advance(repo, record)
        shown = self.show(repo, record)
        reference = shown["fields"].get("external_ref", "")
        if not reference.startswith("worktree:"):
            return {"record": record, "blocked": output}
        name = reference.split(":")[1]
        return {
            "record": record,
            "tree": repo.parent / (repo.name + ".worktrees") / name,
            "filename": filename,
            "content": content,
            "criterion": criterion,
            "check": check,
        }

    def build(self, lane: dict) -> None:
        if "blocked" in lane:
            return
        tree = lane["tree"]
        (tree / lane["filename"]).write_text(lane["content"], encoding="utf-8")
        self.call(["git", "add", "--", lane["filename"]], tree)
        self.call(
            [
                "git",
                "commit",
                "-m",
                f"test(consumer): measure the fixture change ({lane['record']})",
            ],
            tree,
        )
        stdout, _ = self.call(lane["check"], tree)
        lane["check_stdout"] = stdout

    def finish(self, repo: Path, lane: dict) -> dict:
        if "blocked" in lane:
            return lane
        tree = lane["tree"]
        confirmation = {
            "checks": [
                {
                    "criterion": lane["criterion"],
                    "command": lane["check"],
                    "result": "The content assertion passed. " + lane["check_stdout"],
                    "exit_code": 0,
                }
            ]
        }
        self.write(repo, "confirm", lane["record"], "--evidence", json.dumps(confirmation))
        output = self.advance(repo, lane["record"])
        shown = self.show(repo, lane["record"])
        _, _, landed_check = self.run(lane["check"], repo)
        return {
            "record": lane["record"],
            "record_status": shown["status"],
            "worktree_exists": tree.exists(),
            "terminal_output": output,
            "landed_content_check_exit_code": landed_check,
            "completed": shown["status"] == "closed" and not tree.exists() and landed_check == 0,
        }


def measure(wheel: Path, output: Path, baseline_wheel: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    binaries = runtime / ("Scripts" if os.name == "nt" else "bin")
    env = dict(os.environ, UV_OFFLINE="1", UV_NO_SYNC="1", UV_LINK_MODE="copy", AI_AGENT="codex")
    env.pop("PYTHONPATH", None)
    meter = Measurement(
        output,
        env,
        binaries / ("basicly.exe" if os.name == "nt" else "basicly"),
        binaries / ("python.exe" if os.name == "nt" else "python"),
    )
    meter.call(["uv", "venv", "--python", sys.executable, runtime], output)
    env.update(PATH=str(binaries) + os.pathsep + env["PATH"], VIRTUAL_ENV=str(runtime))
    meter.call(
        ["uv", "pip", "install", "--offline", "--python", meter.python, baseline_wheel], output
    )
    meter.phase = "install-upgrade"
    started = time.perf_counter()
    repo, _, fresh = meter.prepare("install-consumer")
    config_before = (repo / "basicly.toml").read_bytes()
    hooks_before = (repo / "lefthook.yml").read_bytes()
    meter.call(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--reinstall-package",
            "basicly",
            "--python",
            meter.python,
            wheel,
        ],
        output,
    )
    _, upgrade = meter.call([meter.engine, "install"], repo)
    assert (repo / "basicly.toml").read_bytes() == config_before
    assert (repo / "lefthook.yml").read_bytes() == hooks_before
    _, _, candidate_fresh = meter.prepare("fresh-consumer")
    _, warm = meter.call([meter.engine, "install"], repo)
    workflows: dict[str, dict] = {
        "install-upgrade": {
            "duration_s": time.perf_counter() - started,
            "released_fresh_install_s": fresh,
            "upgrade_install_s": upgrade,
            "candidate_fresh_install_s": candidate_fresh,
            "idempotent_reinstall_s": warm,
            "consumer_config_preserved": True,
            "completed": True,
        }
    }
    for name, files in [
        ("small-fix", [("library.py", "value = 2\n")]),
        (
            "documentation",
            [("NOTES.md", "# Notes\n\nRun `basicly status` to inspect the installed harness.\n")],
        ),
        ("two-lane", [("left.py", "value = 2\n"), ("right.py", "value = 3\n")]),
    ]:
        meter.phase = name
        setup_started = time.perf_counter()
        repo, _, install_s = meter.prepare(name + "-consumer")
        setup_duration = time.perf_counter() - setup_started
        first_command = len(meter.commands)
        first_checkpoint = meter.checkpoints
        calls = meter.calls_file(repo)
        prior_checks = len(calls.read_text().splitlines())
        started = time.perf_counter()
        lanes = [meter.provision(repo, filename, content) for filename, content in files]
        if len(lanes) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(meter.build, lanes))
        else:
            meter.build(lanes[0])
        results = [meter.finish(repo, lane) for lane in lanes]
        histories = {}
        for filename in ["landing-timings.json", "verify-run.json"]:
            path = repo / ".basicly/usage" / filename
            histories[filename] = json.loads(path.read_text()) if path.exists() else None
        workflows[name] = {
            "duration_s": time.perf_counter() - started,
            "fixture_setup_s": setup_duration,
            "fixture_install_s": install_s,
            "commands": len(meter.commands) - first_command,
            "checkpoint_relays": meter.checkpoints - first_checkpoint,
            "lanes": results,
            "completed": all(x.get("completed", False) for x in results),
            "check_execution_log": calls.read_text().splitlines()[prior_checks:],
            "human_interventions": 0,
            "approval_mode": "Owner pre-approved; counted relays are automated, not human input.",
            "verification_history": histories,
            "lane_scheduling": (
                "Both lanes provisioned first; manual edits, checks and commits overlap; "
                "landing is serial; "
                "no model scheduler performance claim."
            ),
        }
    result = {
        "schema": "basicly-workflow-measurement/v1",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "baseline_wheel_sha256": hashlib.sha256(baseline_wheel.read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "workflows": workflows,
        "command_log": "commands.jsonl",
        "model_tokens": None,
        "cpu_saturation": None,
        "measurement_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(
            Path(__file__).with_name("validate_affected_consumer.py").read_bytes()
        ).hexdigest(),
        "conditions": (
            "Prepared offline dependency and OS caches. Fresh means a new consumer, "
            "not a cold machine. Manual deterministic edits; no paid agent calls. "
            "Two lanes build concurrently; landing is serial. "
            "The upgrade uses the separately identified published baseline wheel."
        ),
        "limitations": [
            "No end-to-end agent authoring or prompt-retention measurement.",
            "Model token use and CPU saturation are unavailable, not zero.",
            "Elapsed intervals exclude fixture preparation for change workflows; "
            "installation setup is reported separately.",
            "Check execution logs are raw observations; repeated names alone "
            "do not prove equivalent inputs.",
        ],
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure four bounded native consumer workflows without model calls"
    )
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--baseline-wheel", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        parser.error("--wheel must name a prepared local wheel; this command never downloads it")
    baseline_wheel = args.baseline_wheel.resolve()
    if not baseline_wheel.is_file():
        parser.error("--baseline-wheel must name a prepared published wheel")
    output = (
        args.output.resolve()
        if args.output
        else Path(tempfile.mkdtemp(prefix="basicly-workflow-parent-")) / "measurement"
    )
    result = measure(wheel, output, baseline_wheel)
    print(
        json.dumps({
            "report": str(output / "result.json"),
            "completed_workflows": [k for k, v in result["workflows"].items() if v["completed"]],
            "blocked_workflows": [k for k, v in result["workflows"].items() if not v["completed"]],
        })
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
