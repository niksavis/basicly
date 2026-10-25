from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

CRITERION = (
    "When the consumer commits, installed hooks shall preserve policy "
    "and refuse unsafe staged code."
)


class Consumer:
    def __init__(self, root: Path, wheel: Path) -> None:
        self.root = root
        self.wheel = wheel
        runtime = root / "runtime"
        self.env = {**os.environ, "AI_AGENT": "compatibility-smoke"}
        self.env.pop("PYTHONPATH", None)
        self.call(["uv", "venv", "--python", sys.executable, str(runtime)], root)
        binary = runtime / ("Scripts" if os.name == "nt" else "bin")
        self.python = binary / ("python.exe" if os.name == "nt" else "python")
        self.engine = binary / ("basicly.exe" if os.name == "nt" else "basicly")
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        self.call(["uv", "pip", "install", "--python", str(self.python), str(wheel)], root)

    def call(self, argv: list[str], cwd: Path, *, success: bool = True) -> str:
        result = subprocess.run(  # nosec B603 B607
            argv, cwd=cwd, env=self.env, input=b"", capture_output=True, check=False, timeout=180
        )
        output = result.stdout.decode("utf-8", "backslashreplace")
        errors = result.stderr.decode("utf-8", "backslashreplace")
        print(json.dumps({"argv": argv[:4], "exit": result.returncode}), flush=True)
        if (result.returncode == 0) != success:
            raise RuntimeError(f"consumer command failed its expected result\n{output}\n{errors}")
        return output + errors if not success else output

    def basicly(self, repo: Path, *args: str) -> str:
        return self.call([str(self.engine), *args], repo)

    def claim(self, repo: Path) -> str:
        self.call(
            [
                str(self.python),
                ".basicly/core/kit/tracker/cli.py",
                "config",
                ".basicly/ledger",
                "set",
                "prefix",
                "smoke",
            ],
            repo,
        )
        result = self.basicly(
            repo,
            "tracker",
            "write",
            "--",
            "create",
            "Exercise installed native hooks",
            "-t",
            "task",
            "-d",
            "## Trigger\n\nWhen the consumer commits, I want installed gates to check it, "
            "so I can preserve staged code and comment policy.\n\n## Scope\n\n- `application.py`\n",
            "--acceptance",
            CRITERION,
            "--requirements",
            "Preserve native hook refusal and user data.",
            "--json",
        )
        record = json.loads(result)["id"]
        review = {
            "checks": [
                {
                    "criterion": CRITERION,
                    "command": ["git", "hook", "run", "pre-commit"],
                    "expected": "Installed gates accept valid code and refuse unsafe staged input.",
                }
            ]
        }
        self.basicly(
            repo, "tracker", "write", "--", "review", record, "--evidence", json.dumps(review)
        )
        shown = json.loads(self.basicly(repo, "tracker", "show", record))
        if "refine" in shown["fields"].get("labels", []):
            self.basicly(
                repo, "tracker", "write", "--", "update", record, "--remove-label", "refine"
            )
        self.basicly(repo, "tracker", "write", "--", "update", record, "--status", "in_progress")
        return record

    def commit(self, repo: Path, record: str, description: str, *, success: bool = True) -> str:
        self.call(["git", "add", "--all"], repo)
        return self.call(
            ["git", "commit", "-m", f"test(consumer): {description} ({record})"],
            repo,
            success=success,
        )

    def upgrade(self, baseline: str) -> dict[str, bool]:
        self.call(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(self.python),
                "--reinstall-package",
                "basicly",
                baseline,
            ],
            self.root,
        )
        repo = self.root / "upgrade"
        repo.mkdir()
        self.call(["git", "init", "-q", "-b", "main"], repo)
        for key, value in [
            ("user.name", "Hook Fixture Identity"),
            ("user.email", "fixture@example.invalid"),
        ]:
            self.call(["git", "config", key, value], repo)
        (repo / "README.md").write_text("# Consumer data\n", encoding="utf-8")
        (repo / "basicly.toml").write_text('[catalog]\ntechnologies=["python"]\n', encoding="utf-8")
        self.basicly(repo, "install", "--technologies", "python")
        record = self.claim(repo)
        preserved = {
            name: (repo / name).read_bytes()
            for name in ("README.md", "basicly.toml", "lefthook.yml")
        }
        ledger = {
            path.name: path.read_bytes() for path in (repo / ".basicly/ledger").glob("*.jsonl")
        }
        self.call(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(self.python),
                "--reinstall-package",
                "basicly",
                str(self.wheel),
            ],
            self.root,
        )
        self.basicly(repo, "install")
        assert all((repo / name).read_bytes() == content for name, content in preserved.items())
        assert all(
            (repo / ".basicly/ledger" / name).read_bytes() == content
            for name, content in ledger.items()
        )
        assert json.loads(self.basicly(repo, "tracker", "show", record))["status"] == "in_progress"
        (repo / "application.py").write_text("# existing prose\nvalue = 1\n", encoding="utf-8")
        result = self.call(
            [str(self.python), ".basicly/core/hooks/no-comments.py", "application.py"],
            repo,
            success=False,
        )
        assert "no-comments" in result
        self.basicly(repo, "check")
        self.basicly(repo, "hooks-check")
        return {
            "upgrade": True,
            "user_data_preserved": True,
            "ledger_preserved": True,
            "strict_default": True,
        }

    def exercise(self, policy: str) -> dict[str, bool | str]:
        repo = self.root / policy
        repo.mkdir()
        self.call(["git", "init", "-q", "-b", "main"], repo)
        for key, value in [
            ("user.name", "Hook Fixture Identity"),
            ("user.email", "fixture@example.invalid"),
        ]:
            self.call(["git", "config", key, value], repo)
        legacy = "# legacy prose\n" if policy == "no-comment-ratchet" else ""
        good = legacy + 'value = 1\ntext = "Ő"\n'
        (repo / "application.py").write_text(good, encoding="utf-8")
        if legacy:
            self.call(["git", "add", "application.py"], repo)
            self.call(["git", "commit", "-qm", "seed legacy prose"], repo)
        config = (
            '[[verify.checks]]\nname="consumer"\nmodes=["fast","full"]\n'
            'command=["python","check.py"]\n'
        )
        if legacy:
            config += '\n[hooks.comments]\npolicy="no-comment-ratchet"\n'
        (repo / "basicly.toml").write_text(config, encoding="utf-8")
        (repo / "check.py").write_text(
            "import runpy\nassert runpy.run_path('application.py')['value'] == 1\n",
            encoding="utf-8",
        )
        self.basicly(repo, "install", "--technologies", "python")
        record = self.claim(repo)
        self.commit(repo, record, "install the selected policy")
        before = (repo / "basicly.toml").read_bytes()
        self.basicly(repo, "install")
        assert (repo / "basicly.toml").read_bytes() == before
        self.basicly(repo, "check")
        self.basicly(repo, "hooks-check")
        (repo / "application.py").write_text(good + "# Ő new prose\n", encoding="utf-8")
        refused = self.commit(repo, record, "refuse new prose", success=False)
        assert "no-comments" in refused
        bad = good.replace("value = 1", "value = 2")
        (repo / "application.py").write_text(bad, encoding="utf-8")
        self.call(["git", "add", "application.py"], repo)
        (repo / "application.py").write_text(good, encoding="utf-8")
        self.call(
            ["git", "commit", "-m", f"test(consumer): refuse staged failure ({record})"],
            repo,
            success=False,
        )
        assert (repo / "application.py").read_text(encoding="utf-8") == good
        index = self.call(["git", "show", ":application.py"], repo)
        assert "value = 2" in index
        self.commit(repo, record, "preserve working code after refusal")
        return {
            "policy": policy,
            "install": True,
            "repeat_install": True,
            "unicode_commit": True,
            "prose_refused": True,
            "staged_failure_refused": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--baseline-wheel")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="basicly-compatibility-") as temporary:
        consumer = Consumer(Path(temporary), wheel)
        upgrade = consumer.upgrade(args.baseline_wheel) if args.baseline_wheel else None
        results = [consumer.exercise(policy) for policy in ("no-comments", "no-comment-ratchet")]
        print(
            json.dumps({"platform": sys.platform, "upgrade": upgrade, "results": results}),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
