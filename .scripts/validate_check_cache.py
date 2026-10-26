from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess  # nosec B404 # Public consumer CLI validation requires child processes.
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

FOREIGN_CONFIG = (
    "# consumer-owned configuration\npre-commit:\n  commands:\n"
    "    consumer-proof:\n      run: git --version\n"
)


def _prepare(root: Path, env: dict[str, str], call: Callable[..., float], uv: str) -> None:
    call(["git", "init", "-q", "-b", "main"])
    (root / "pyproject.toml").write_text(
        '[project]\nname="check-cache-consumer"\nversion="0.0.0"\nrequires-python=">=3.14"\n'
        '[dependency-groups]\ndev=["pyright==1.1.411"]\n'
        '[tool.pyright]\nvenvPath="."\nvenv=".venv"\ninclude=["src"]\ntypeCheckingMode="strict"\n',
        encoding="utf-8",
    )
    call([uv, "sync", "--offline"])
    repository = Path(__file__).resolve().parents[1]
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(repository / name, root / name)
    call(["npm", "ci", "--offline", "--no-audit", "--no-fund"])
    binary = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    env["PATH"] = str(binary) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(root / ".venv")
    hook = root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nprintf foreign >> foreign-count.txt\n", encoding="utf-8")
    hook.chmod(0o755)
    (root / "lefthook.yml").write_text(FOREIGN_CONFIG, encoding="utf-8")


def _install(root: Path, call: Callable[..., float], uvx: str, wheels: list[Path]) -> None:
    for wheel in wheels:
        call([uvx, "--offline", "--from", str(wheel), "basicly", "install"])
        assert FOREIGN_CONFIG in (root / "lefthook.yml").read_text(encoding="utf-8")
        assert (root / ".git/hooks/pre-commit.basicly-original").read_text(
            encoding="utf-8"
        ) == "#!/bin/sh\nprintf foreign >> foreign-count.txt\n"
        call([
            str(root / ".basicly/bin" / ("lefthook.exe" if os.name == "nt" else "lefthook")),
            "version",
        ])
        assert (root / ".basicly/bin/LICENSE.lefthook").is_file()


def _measure(
    root: Path, env: dict[str, str], run: Callable[..., float], configure: Callable[[bool], None]
) -> dict:
    source = root / "src"
    configure(False)
    default_fresh = [run(reused=False), run(reused=False)]
    configure(True)
    cold = run(reused=False)
    warm = [run(reused=True) for _ in range(30)]
    run(reused=False, fresh=True)
    target = source / "module_0.py"
    original = target.read_bytes()
    target.write_text('value: int = "wrong"\n', encoding="utf-8")
    run(reused=False, code=1)
    target.write_bytes(original)
    run(reused=True)
    (root / ".basicly/usage/verify-cache.json").write_text("malformed", encoding="utf-8")
    run(reused=False)
    env["TZ"] = "UTC" if env.get("TZ") != "UTC" else "Etc/GMT+7"
    run(reused=False)
    return {
        "default_fresh_s": default_fresh,
        "cold_s": cold,
        "warm_samples_s": warm,
        "warm_median_s": statistics.median(warm),
        "compiler_executions_in_30_warm_runs": 0,
        "unscoped_executions_in_30_warm_runs": 30,
        "source_mutation_failure": "preserved",
        "fresh_control": "passed",
        "corrupt_cache": "fresh",
        "environment_mutation": "fresh",
        "limits": "10,000-line Python fixture; compiler-only evidence, no whole-loop scaling claim",
    }


def _source_checks(root: Path) -> list[dict]:
    source = root / "src"
    source.mkdir()
    for index in range(200):
        (source / f"module_{index}.py").write_text(
            "\n".join(f"value_{number}: int = {number}" for number in range(50)) + "\n",
            encoding="utf-8",
        )
    (root / "unscoped.py").write_text(
        'from pathlib import Path\np=Path("unscoped-count.txt")\n'
        'p.write_text(str(int(p.read_text())+1) if p.exists() else "1")\n',
        encoding="utf-8",
    )
    commands = [
        {
            "name": f"types-{target.lower()}",
            "command": ["pyright", "--pythonplatform", target],
            "modes": ["full"],
            "reuse": "pyright",
        }
        for target in ("Linux", "Darwin", "Windows")
    ]
    commands.append({"name": "unscoped", "command": ["python", "unscoped.py"], "modes": ["full"]})

    return commands


def main() -> None:
    baseline = Path(os.environ["BASICLY_BASELINE_WHEEL"]).resolve(strict=True)
    candidate = Path(os.environ["BASICLY_VALIDATION_WHEEL"]).resolve(strict=True)
    latest = os.environ.get("BASICLY_LATEST_WHEEL")
    uv = shutil.which("uv")
    uvx = shutil.which("uvx")
    if uv is None or uvx is None:
        raise SystemExit("consumer proof requires uv and uvx")
    directory = Path(tempfile.mkdtemp(prefix="basicly-check-cache-"))
    root = directory / "consumer"
    root.mkdir()
    env = {**os.environ, "UV_OFFLINE": "1", "UV_LINK_MODE": "copy"}
    for name in (
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_NO_SYNC",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ):
        env.pop(name, None)
    log = directory / "commands.log"

    def call(argv: list[str], *, code: int = 0) -> float:
        started = time.perf_counter()
        process = subprocess.run(  # nosec B603 # Explicit offline fixture argv without a shell.
            argv, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=180
        )
        elapsed = time.perf_counter() - started
        with log.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(argv)
                + "\n"
                + process.stdout
                + process.stderr
                + f"\nexit={process.returncode}\n"
            )
        if process.returncode != code:
            raise AssertionError(
                f"expected exit {code}, got {process.returncode}: {argv}; see {log}"
            )
        return elapsed

    setup = time.perf_counter()
    _prepare(root, env, call, uv)
    wheels = [baseline, *([Path(latest).resolve(strict=True)] if latest else []), candidate]
    _install(root, call, uvx, wheels)
    commands = _source_checks(root)

    def configure(enabled: bool) -> None:
        entries = f"[verify]\ncache = {str(enabled).lower()}\n"
        for check in commands:
            entries += (
                "\n[[verify.checks]]\n"
                + "\n".join(f"{key} = {json.dumps(value)}" for key, value in check.items())
                + "\n"
            )
        (root / "basicly.toml").write_text(entries, encoding="utf-8")

    executable = [uvx, "--offline", "--from", str(candidate), "basicly", "verify", "--mode", "full"]

    def run(*, reused: bool, code: int = 0, fresh: bool = False) -> float:
        counter = root / "unscoped-count.txt"
        before = int(counter.read_text()) if counter.exists() else 0
        elapsed = call([*executable, *(["--no-cache"] if fresh else [])], code=code)
        report = json.loads((root / ".basicly/usage/verify-run.json").read_text())
        assert int(counter.read_text()) == before + 1
        assert [row["disposition"] for row in report["checks"][:3]] == [
            "reused" if reused else "executed"
        ] * 3
        assert report["checks"][-1]["disposition"] == "executed"
        assert report["passed"] is (code == 0)
        if reused:
            assert all(
                row["reused_from"] and row["input_fingerprint"] for row in report["checks"][:3]
            )
        return elapsed

    setup_s = time.perf_counter() - setup
    result = _measure(root, env, run, configure)
    result.update(setup_s=setup_s, upgrade_paths=len(wheels) - 1)
    (directory / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"consumer proof: {directory}")


if __name__ == "__main__":
    main()
