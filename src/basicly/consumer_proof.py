from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from basicly import worktree

FORMAT = 1
CONFIG_FILE = "basicly.toml"
ROOT_ENV = "BASICLY_CONSUMERS_ROOT"
CPUS_ENV = "BASICLY_CONSUMER_CPUS"
CPU_SET = re.compile(r"\d+(?:-\d+)?(?::\d+)?")
RESULTS = Path("basicly-consumer-proof")
HELD_STATUSES = ("in_progress", "open")
COMMAND = "basicly consumer-proof"
WHEEL = "{wheel}"
SUBJECT = "chore(basicly): install the kit for a consumer proof ({record})"
CI_ENV = {"GIT_TERMINAL_PROMPT": "0"}
IDENTITY = ("user.name", "user.email", "basicly.holder")
VAULT_CONFIG = ".basicly-tracker.toml"
KIT_CLI = Path(".basicly") / "core" / "kit" / "tracker" / "cli.py"
PROBE = "chore(basicly): ask which record this session holds\n"
NO_SESSION = (
    "the proof has no tracker session: set BASICLY_TRACKER_SESSION, or run it from an agent "
    "session, and claim a record in this consumer with `claim RECORD --vault {url}`"
)
NOT_HELD = (
    "this session holds no open record in this consumer; claim one with "
    "`claim RECORD --vault {url}`, then run basicly consumer-proof again"
)

Run = Callable[[list[str], Path, dict[str, str]], int]
Records = Callable[[Path, dict[str, str], str], list[dict[str, object]]]
Held = Callable[[Path, dict[str, str], str], dict[str, object]]


@dataclass(frozen=True)
class Consumer:
    name: str
    ci: tuple[tuple[str, ...], ...]
    path: str = ""


class ProofError(Exception):
    pass


def consumers(repo_root: Path) -> list[Consumer]:
    path = repo_root / CONFIG_FILE
    if not path.is_file():
        return []
    table = tomllib.loads(path.read_text(encoding="utf-8")).get("release", {})
    found = []
    for entry in table.get("consumers", []):
        name, ci = entry.get("name"), entry.get("ci", [])
        if not isinstance(name, str) or not name or "/" in name or name.startswith("."):
            raise ProofError(
                f"{CONFIG_FILE} [[release.consumers]] needs a plain name, got {name!r}"
            )
        if not isinstance(ci, list) or not all(
            isinstance(c, list) and c and all(isinstance(a, str) for a in c) for c in ci
        ):
            raise ProofError(
                f"{CONFIG_FILE} [[release.consumers]] {name}: ci must be a list of argv lists"
            )
        path = entry.get("path", "")
        if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ProofError(
                f"{CONFIG_FILE} [[release.consumers]] {name}: "
                "path must be relative, inside the root"
            )
        found.append(Consumer(name, tuple(tuple(c) for c in ci), path))
    return found


def git(repo: Path, *args: str) -> str:
    return worktree.run(["git", "-C", str(repo), *args], check=True).stdout.strip()


def results_dir(repo_root: Path) -> Path:
    return Path(git(repo_root, "rev-parse", "--absolute-git-dir")) / RESULTS


def result_path(repo_root: Path, commit: str) -> Path:
    return results_dir(repo_root) / f"{commit}.json"


def source(repo_root: Path, consumer: Consumer, environ: dict[str, str]) -> Path:
    root = Path(environ[ROOT_ENV]) if environ.get(ROOT_ENV) else repo_root.parent
    return (root / consumer.path) if consumer.path else root / consumer.name


def execute(argv: list[str], cwd: Path, env: dict[str, str]) -> int:
    done = worktree.run(argv, cwd=cwd, env=env, check=False)
    with (cwd.parent / f"{cwd.name}.log").open("a", encoding="utf-8") as log:
        log.write(f"$ {' '.join(argv)}\n{done.stdout}{done.stderr}")
    return done.returncode


def list_records(clone: Path, env: dict[str, str], wheel: str) -> list[dict[str, object]]:
    argv = ["uvx", "--from", wheel, "basicly", "tracker", "list", "--summary"]
    found: list[dict[str, object]] = []
    for status in HELD_STATUSES:
        done = worktree.run([*argv, "--status", status], cwd=clone, env=env, check=False)
        if done.returncode != 0:
            continue
        records = json.loads(done.stdout).get("records", [])
        found += (
            [item for item in records if isinstance(item, dict)]
            if isinstance(records, list)
            else []
        )
    return found


def vault_setting(clone: Path, key: str) -> str:
    path = clone / VAULT_CONFIG
    if not path.is_file():
        return ""
    value = tomllib.loads(path.read_text(encoding="utf-8")).get(key)
    return value if isinstance(value, str) else ""


def vault_url(clone: Path) -> str:
    return vault_setting(clone, "vault")


def vault_held(clone: Path, env: dict[str, str], url: str) -> dict[str, object]:
    kit_cli = clone / KIT_CLI
    with tempfile.NamedTemporaryFile(
        "w", suffix=".msg", dir=clone.parent, delete=False, encoding="utf-8"
    ) as probe:
        probe.write(PROBE)
    try:
        argv = [sys.executable, str(kit_cli), "commit-msg", "--vault", url, probe.name]
        done = worktree.run(argv, cwd=clone, env=env, check=False)
    finally:
        Path(probe.name).unlink(missing_ok=True)
    try:
        judged = json.loads(done.stdout)
    except ValueError:
        judged = {}
    return judged if isinstance(judged, dict) else {}


def vault_record(clone: Path, env: dict[str, str], held: Held) -> tuple[str | None, str]:
    url = vault_url(clone)
    found = held(clone, env, url)
    if not found.get("session"):
        return None, NO_SESSION.format(url=url)
    own = f"{vault_setting(clone, 'prefix')}-"
    listed = found.get("held")
    named = [found.get("filled"), *(listed if isinstance(listed, list) else [])]
    mine = [r for r in named if isinstance(r, str) and r.startswith(own)]
    return (mine[0], "") if mine else (None, NOT_HELD.format(url=url))


def held_record(clone: Path, listed: list[dict[str, object]]) -> str | None:
    names = {git(clone, "config", "--default", "", key) for key in ("user.name", "basicly.holder")}
    for item in listed:
        fields = item.get("fields")
        if isinstance(fields, dict) and fields.get("assignee") in names - {""}:
            record = item.get("record")
            return record if isinstance(record, str) else None
    return None


@dataclass(frozen=True)
class Tools:
    run: Run
    records: Records
    env: dict[str, str]


def chosen_record(clone: Path, wheel: str, tools: Tools) -> tuple[str | None, str]:
    if vault_url(clone):
        return vault_record(clone, tools.env, vault_held)
    record = held_record(clone, tools.records(clone, tools.env, wheel))
    return record, "" if record else "no in-progress record of the holder"


def prove(clone: Path, consumer: Consumer, wheel: str, tools: Tools) -> dict[str, object]:
    run, env = tools.run, tools.env
    steps: list[dict[str, object]] = []

    def step(name: str, argv: list[str]) -> bool:
        code = run(argv, clone, env)
        steps.append({"step": name, "argv": argv, "exit": code})
        return code == 0

    passed = step("install", ["uvx", "--from", wheel, "basicly", "install"])
    record, why = chosen_record(clone, wheel, tools) if passed else (None, "")
    if passed and record is None:
        steps.append({"step": "record", "exit": 1, "error": why})
        passed = False
    if passed:
        passed = step("stage", ["git", "add", "-A"]) and step(
            "commit",
            [
                "git",
                "commit",
                "-q",
                "-m",
                SUBJECT.format(record=record),
            ],
        )
    for argv in consumer.ci if passed else ():
        if not step("ci", [wheel if a == WHEEL else a for a in argv]):
            passed = False
            break
    return {"name": consumer.name, "passed": passed, "steps": steps}


def failure(item: dict[str, object]) -> str:
    steps = item.get("steps")
    failed = (
        [s for s in steps if isinstance(s, dict) and s.get("exit")]
        if isinstance(steps, list)
        else []
    )
    if not failed:
        return ""
    last = failed[-1]
    return str(last.get("error") or f"step {last.get('step')} exited {last.get('exit')}")


def build_wheel(repo_root: Path, out: Path, run: Run, env: dict[str, str]) -> Path:
    if run(["uv", "build", "--wheel", "-o", str(out)], repo_root, env) != 0:
        raise ProofError(f"uv build --wheel failed; see {out.parent / (repo_root.name + '.log')}")
    wheels = sorted(out.glob("*.whl"))
    if len(wheels) != 1:
        raise ProofError(f"expected one wheel in {out}, found {len(wheels)}")
    return wheels[0]


def cpu_sets(environ: dict[str, str]) -> list[str]:
    value = environ.get(CPUS_ENV, "")
    sets = [part.strip() for part in value.split(",") if part.strip()]
    if any(not CPU_SET.fullmatch(part) for part in sets):
        raise ProofError(f"{CPUS_ENV} takes core sets like 16-21,22-26; got {value!r}")
    return sets if sets and shutil.which("taskset") else []


def pinned(run: Run, cpus: str) -> Run:
    return lambda argv, cwd, env: run(["taskset", "-c", cpus, *argv], cwd, env)


def one(
    origin: Path, clone: Path, consumer: Consumer, wheel: str, tools: Tools
) -> dict[str, object]:
    if not (origin / ".git").exists():
        missing = {"step": "clone", "exit": 1, "error": f"no repository at {origin}"}
        return {"name": consumer.name, "passed": False, "steps": [missing]}
    git(clone.parent, "clone", "-q", str(origin), str(clone))
    for key in IDENTITY:
        value = git(origin, "config", "--default", "", key)
        if value:
            git(clone, "config", key, value)
    if (origin / "node_modules").is_dir():
        shutil.copytree(origin / "node_modules", clone / "node_modules", symlinks=True)
    return prove(clone, consumer, wheel, tools)


def run_proof(
    repo_root: Path,
    environ: dict[str, str],
    run: Run = execute,
    records: Records = list_records,
    work: Path | None = None,
) -> dict[str, object]:
    listed = consumers(repo_root)
    if not listed:
        raise ProofError(f"{CONFIG_FILE} names no [[release.consumers]]; add one before a release")
    if git(repo_root, "status", "--porcelain"):
        raise ProofError(
            "the working tree is not clean; commit first, so the proof names one commit"
        )
    commit = git(repo_root, "rev-parse", "HEAD")
    folder = work or Path(tempfile.mkdtemp(prefix="basicly-consumer-proof-"))
    env = {**environ, **CI_ENV}
    wheel = build_wheel(repo_root, folder / "dist", run, env)
    sets = cpu_sets(environ)
    jobs = [
        (consumer, Tools(pinned(run, sets[i % len(sets)]) if sets else run, records, env))
        for i, consumer in enumerate(listed)
    ]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(
            pool.map(
                lambda job: one(
                    source(repo_root, job[0], environ),
                    folder / job[0].name,
                    job[0],
                    str(wheel),
                    job[1],
                ),
                jobs,
            )
        )
    result = {
        "v": FORMAT,
        "commit": commit,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "consumers": results,
        "passed": all(r["passed"] for r in results),
    }
    target = result_path(repo_root, commit)
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def consumer_results(result: dict[str, object]) -> list[dict[str, object]]:
    items = result.get("consumers")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def refusal(repo_root: Path) -> str | None:
    if not consumers(repo_root):
        return None
    commit = git(repo_root, "rev-parse", "HEAD")
    path = result_path(repo_root, commit)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return f"no consumer proof for {commit[:12]}; run: {COMMAND}"
    if result.get("v") != FORMAT or result.get("commit") != commit:
        return f"the consumer proof at {path} is not for {commit[:12]}; run: {COMMAND}"
    failed = [c.get("name") for c in result.get("consumers", []) if not c.get("passed")]
    if failed or not result.get("passed"):
        names = ", ".join(map(str, failed))
        return f"the consumer proof for {commit[:12]} failed for {names}; fix it, run: {COMMAND}"
    return None


def environ() -> dict[str, str]:
    return dict(os.environ)
