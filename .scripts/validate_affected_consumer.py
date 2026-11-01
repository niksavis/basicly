from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess  # nosec B404 # Native consumer commands require subprocess execution.
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

PROVIDER = """import json, sys
from pathlib import Path
request = json.load(sys.stdin)
paths = {
    change[key] for change in request['changes']
    for key in ('old_path', 'path') if key in change
}
graph = json.loads(Path('graph.json').read_text())
selected = {name for name, node in graph.items() if paths.intersection(node['files'])}
global_inputs = {'basicly.toml', 'graph.json', 'provider.py'}
unknown = paths - {path for node in graph.values() for path in node['files']} - global_inputs
if paths.intersection(global_inputs):
    selected = set(graph)
while True:
    expanded = selected | {
        name for name, node in graph.items() if selected.intersection(node['depends_on'])
    }
    if expanded == selected:
        break
    selected = expanded
print(json.dumps({
    **request, 'complete': not unknown,
    'selected': sorted(selected.intersection(request['checks'])),
    'reasons': {
        name: 'affected dependency closure' if name in selected else 'unrelated project'
        for name in request['checks']
    },
    'unverified': sorted(unknown)
}))
"""
FOREIGN = '#!/bin/sh\nprintf "foreign\\n" >> "$(git rev-parse --git-path foreign-count)"\n'


@dataclass
class Proof:
    root: Path
    env: dict[str, str]
    engine: Path
    python: Path
    calls_root: Path | None = None

    def calls_file(self, repo: Path) -> Path:
        return (
            self.calls_root / (repo.name + ".log")
            if self.calls_root is not None
            else repo / ".basicly/usage/calls"
        )

    def call(
        self, argv: list[str | Path], cwd: Path, expected: int = 0, *, combined: bool = False
    ) -> tuple[str, float]:
        started = time.perf_counter()
        result = subprocess.run(  # nosec B603 # Fixed fixture argv avoids shell parsing.
            [str(arg) for arg in argv],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        duration = time.perf_counter() - started
        with (self.root / "transcript.log").open("a", encoding="utf-8") as output:
            output.write(
                json.dumps({
                    "argv": [str(arg) for arg in argv],
                    "cwd": str(cwd),
                    "exit": result.returncode,
                    "duration_s": duration,
                })
                + "\n"
            )
            output.write(result.stdout + result.stderr + "\n")
        if result.returncode != expected:
            raise RuntimeError(
                f"{argv[0]} returned {result.returncode}, expected {expected}: "
                f"{result.stdout[-1500:]}{result.stderr[-1500:]}"
            )
        return result.stdout + (result.stderr if combined else ""), duration

    def commit(self, repo: Path, record: str, description: str) -> None:
        self.call(["git", "add", "--all"], repo)
        self.call(["git", "commit", "-m", f"test(consumer): {description} ({record})"], repo)

    def prepare(self, name: str) -> tuple[Path, str, float]:
        repo = self.root / name
        repo.mkdir()
        self.call(["git", "init", "-q", "--initial-branch=main"], repo)
        for key, value in [
            ("user.name", "Consumer Fixture"),
            ("user.email", "consumer@example.invalid"),
            ("core.hooksPath", ".githooks"),
        ]:
            self.call(["git", "config", key, value], repo)
        (repo / ".gitignore").write_text(
            ".basicly/usage/\n__pycache__/\nnode_modules/\n", encoding="utf-8"
        )
        (repo / ".githooks").mkdir()
        hook = repo / ".githooks/pre-commit"
        hook.write_text(FOREIGN, encoding="utf-8")
        hook.chmod(0o755)
        (repo / "library.py").write_text("value = 1\n", encoding="utf-8")
        (repo / "application.py").write_text("from library import value\n", encoding="utf-8")
        (repo / "unrelated.py").write_text("value = 1\n", encoding="utf-8")
        graph = {
            name: {
                "files": [name + ".py"],
                "depends_on": ["library"] if name == "application" else [],
            }
            for name in ["library", "application", "unrelated"]
        }
        (repo / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        (repo / "provider.py").write_text(PROVIDER, encoding="utf-8")
        config = ""
        for check in ["library", "application", "unrelated", "guard"]:
            destination = (
                f"Path(os.environ['BASICLY_MEASUREMENT_LOG']) / {repo.name + '.log'!r}"
                if self.calls_root is not None
                else "Path('.basicly/usage/calls')"
            )
            program = (
                f"from pathlib import Path; import os; p={destination}; "
                "p.parent.mkdir(parents=True,exist_ok=True); "
                + f"p.open('a').write('{check}\\n'); "
            )
            if check != "guard":
                program += (
                    f"import runpy; value=runpy.run_path('{check}.py')['value']; "
                    "assert isinstance(value,int)"
                )
            config += (
                f'[[verify.checks]]\nname={json.dumps(check)}\nmodes=["fast","full"]\ncommand='
                + json.dumps(["python", "-c", program])
                + "\n"
            )
        (repo / "basicly.toml").write_text(config, encoding="utf-8")
        shutil.copytree(Path(__file__).resolve().parents[1] / "node_modules", repo / "node_modules")
        shutil.copyfile(
            Path(__file__).resolve().parents[1] / ".markdownlint-cli2.yaml",
            repo / ".markdownlint-cli2.yaml",
        )
        self.call(["git", "add", "--all"], repo)
        self.call(["git", "commit", "-m", "chore: prepare consumer repository"], repo)
        _, install_duration = self.call([self.engine, "install"], repo)
        self.call(
            [
                self.python,
                ".basicly/core/kit/tracker/cli.py",
                "config",
                ".basicly/ledger",
                "set",
                "prefix",
                "probe",
            ],
            repo,
        )
        created, _ = self.call(
            [
                self.engine,
                "tracker",
                "write",
                "--",
                "create",
                "Exercise installed consumer hooks",
                "-t",
                "task",
                "-d",
                "## Trigger\n\nWhen the consumer changes, I want its installed hooks to verify it, "
                "so I can prove native execution.\n\n## Scope\n\n- `library.py`\n",
                "--acceptance",
                "When the fixture commits, the native Git hooks shall execute configured checks "
                "and preserve the foreign hook.",
                "--requirements",
                "Preserve native Git hook execution.",
            ],
            repo,
        )
        match = re.search(r"created:\s+(probe-[a-z0-9]+)", created)
        if match is None:
            raise ValueError("consumer tracker did not return its created record")
        record = match.group(1)
        self.call(
            [
                self.engine,
                "tracker",
                "write",
                "--",
                "comments",
                "add",
                record,
                "Review native installed hook execution and preserve consumer-owned foreign hooks.",
            ],
            repo,
        )
        shown, _ = self.call(
            [self.python, ".basicly/core/kit/tracker/cli.py", "show", ".basicly/ledger", record],
            repo,
        )
        criterion = (
            "When the fixture commits, the native Git hooks shall execute configured checks "
            "and preserve the foreign hook."
        )
        review = {
            "invest": dict.fromkeys(
                ["independent", "negotiable", "valuable", "estimable", "small", "testable"],
                "An installed consumer exercises hooks and records real output.",
            ),
            "conversation": [json.loads(shown)["comment_log"][-1]["seq"]],
            "checks": [
                {
                    "criterion": criterion,
                    "command": ["git", "hook", "run", "pre-commit"],
                    "expected": "Configured checks and the preserved foreign hook execute.",
                }
            ],
        }
        self.call(
            [
                self.engine,
                "tracker",
                "write",
                "--",
                "review",
                record,
                "--evidence",
                json.dumps(review),
            ],
            repo,
        )
        self.call(
            [self.engine, "tracker", "write", "--", "update", record, "--status", "in_progress"],
            repo,
        )
        self.commit(repo, record, "prepare installed fixture")
        binary = repo / ".basicly/bin" / ("lefthook.exe" if os.name == "nt" else "lefthook")
        version, _ = self.call([binary, "version"], repo)
        assert version.strip() == "2.2.2"
        assert (binary.parent / "LICENSE.lefthook").is_file()
        return repo, record, install_duration

    def exercise(self, repo: Path, record: str, selected: list[str], scoped: bool) -> dict:
        source = repo / "library.py"
        source.write_text('value = "invalid"\n', encoding="utf-8")
        self.call(["git", "add", "--", "library.py"], repo)
        source.write_text("value = 1\n", encoding="utf-8")
        index, _ = self.call(["git", "diff", "--cached", "--binary"], repo)
        calls = self.calls_file(repo)
        before = calls.read_text().splitlines()
        foreign, _ = self.call(["git", "rev-parse", "--git-path", "foreign-count"], repo)
        foreign_path = repo / foreign.strip()
        foreign_count = len(foreign_path.read_text().splitlines())
        self.call(
            ["git", "commit", "-m", f"test(consumer): reject staged error ({record})"], repo, 1
        )
        assert calls.read_text().splitlines()[len(before) :] == selected
        assert len(foreign_path.read_text().splitlines()) == foreign_count + 1
        after, _ = self.call(["git", "diff", "--cached", "--binary"], repo)
        assert index == after and source.read_text() == "value = 1\n"
        command = [self.engine, "verify"]
        before = calls.read_text().splitlines()
        self.call(command, repo, 1 if scoped else 0)
        assert calls.read_text().splitlines()[len(before) :] == (
            selected if scoped else ["library", "application", "unrelated", "guard"]
        )
        source.write_text("value = 2\n", encoding="utf-8")
        self.call(["git", "add", "--", "library.py"], repo)
        source.write_text("value = 1\n", encoding="utf-8")
        samples = []
        for _ in range(3):
            before = calls.read_text().splitlines()
            output, duration = self.call(["git", "hook", "run", "pre-commit"], repo, combined=True)
            executed = calls.read_text().splitlines()[len(before) :]
            assert executed == [name for name in selected if name in executed]
            reused = [name for name in selected if name not in executed]
            if reused:
                tree, _ = self.call(["git", "write-tree"], repo)
                interpreter, _ = self.call(
                    [self.python, "-c", "import sys; print(sys.base_prefix)"], repo
                )
                receipts = json.loads((repo / ".basicly/usage/check-passes.json").read_text())
                receipt = receipts[tree.strip() + ":" + interpreter.strip()]
                for name in reused:
                    assert name in receipt["checks"]
                    assert f"{name} SKIPPED: passed with these inputs" in output
            samples.append(duration)
        return {
            "required_checks": len(selected),
            "selected": selected,
            "warm_hook_samples_s": samples,
            "warm_hook_median_s": statistics.median(samples),
            "staged_failure_blocks_commit": True,
            "working_tree_repair_preserved": True,
            "foreign_hook_executed_once": True,
        }


def _wheel(name: str) -> Path:
    value = os.environ.get(name)
    if not value or not Path(value).is_file():
        raise ValueError(
            f"Set {name} to a prepared wheel; validation never downloads missing inputs"
        )
    return Path(value).resolve()


def main() -> int:
    baseline, candidate = _wheel("BASICLY_BASELINE_WHEEL"), _wheel("BASICLY_VALIDATION_WHEEL")
    root = Path(tempfile.mkdtemp(prefix="basicly-affected-consumer-"))
    print(root, flush=True)
    runtime = root / "runtime"
    bin_dir = runtime / ("Scripts" if os.name == "nt" else "bin")
    env = dict(os.environ, UV_OFFLINE="1", UV_NO_SYNC="1", UV_LINK_MODE="copy")
    env.pop("PYTHONPATH", None)
    proof = Proof(
        root,
        env,
        bin_dir / ("basicly.exe" if os.name == "nt" else "basicly"),
        bin_dir / ("python.exe" if os.name == "nt" else "python"),
    )
    proof.call(["uv", "venv", "--python", sys.executable, runtime], root)
    env.update(PATH=str(bin_dir) + os.pathsep + env["PATH"], VIRTUAL_ENV=str(runtime))
    proof.call(["uv", "pip", "install", "--offline", "--python", proof.python, baseline], root)
    upgrade, record, setup = proof.prepare("upgrade-consumer")
    before = proof.exercise(
        upgrade, record, ["library", "application", "unrelated", "guard"], False
    )
    config = (upgrade / "basicly.toml").read_bytes()
    hooks = (upgrade / "lefthook.yml").read_bytes()
    proof.call(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--reinstall-package",
            "basicly",
            "--python",
            proof.python,
            candidate,
        ],
        root,
    )
    proof.call([proof.engine, "install"], upgrade)
    assert (upgrade / "basicly.toml").read_bytes() == config
    assert (upgrade / "lefthook.yml").read_bytes() == hooks
    fresh, fresh_record, fresh_setup = proof.prepare("fresh-consumer")
    results = {}
    for repo, identifier in [(upgrade, record), (fresh, fresh_record)]:
        (repo / "library.py").write_text("value = 1\n", encoding="utf-8")
        with (repo / "basicly.toml").open("a", encoding="utf-8") as output:
            output.write(
                '\n[verify.affected]\ncommand=["python","provider.py"]\nchecks=["library","application","unrelated"]\n'
            )
        proof.commit(repo, identifier, "enable consumer graph provider")
        results[repo.name] = proof.exercise(
            repo, identifier, ["library", "application", "guard"], True
        )
        value, _ = proof.call(["git", "config", "core.hooksPath"], repo)
        assert value.strip() == ".githooks"
    result = {
        "result": "PASS",
        "baseline_wheel_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "candidate_wheel_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "baseline": before,
        "candidate": results,
        "fresh_repository_install_s": fresh_setup,
        "released_repository_install_s": setup,
        "conditions": (
            "Prepared offline Python dependencies and OS caches; "
            "no cold-machine, mixed-stack or repository-size scaling claim."
        ),
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
