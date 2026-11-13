from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "cli.py"
GENESIS = ("vault.toml", "FORMAT.md", "reader.py", "README.md")
P99_LIMIT_S = 10.0
SETTLE_LIMIT_S = 60.0
CLAIM_DEADLINE_S = 30.0
ENDED = (0, 3)
CPU_EVERY_S = 3.0
FOREIGN_CPU_LIMIT = 0.03
NOT_COUNTED = 3

_SHIM = """#!{python}
import os, subprocess, sys, time
args = sys.argv[1:]
scale = float(os.environ["CLAIM_LOAD_SCALE"])
started = time.monotonic()
fetching = "fetch" in args or "ls-remote" in args
verb = "push" if "push" in args else ("fetch" if fetching else "local")
if verb == "push":
    with open(os.environ["CLAIM_LOAD_PUSHES"], "a", encoding="utf-8") as log:
        log.write(f"{{time.time()}} {{os.environ.get('CLAIM_LOAD_TAG', '-')}}\\n")
    time.sleep(float(os.environ["CLAIM_LOAD_PUSH_S"]) / scale)
elif verb == "fetch":
    time.sleep(float(os.environ["CLAIM_LOAD_FETCH_S"]) / scale)
code = subprocess.call([os.environ["CLAIM_LOAD_GIT"], *args])
tag = os.environ.get("CLAIM_LOAD_TAG")
if tag:
    with open(os.environ["CLAIM_LOAD_CALLS"], "a", encoding="utf-8") as log:
        log.write(f"{{tag}} {{verb}} {{time.monotonic() - started}}\\n")
sys.exit(code)
"""
EXIT_MEANINGS = {
    0: "landed: the first valid claim",
    1: "fix printed",
    3: "held or won by another session",
    4: "unsettled: the deadline passed first",
    5: "vault unreachable",
    6: "internal error",
    7: "already held by this session",
}


@dataclass(frozen=True)
class Config:
    sessions: int = 52
    machines: int = 13
    scale: float = 4.0
    minutes: float = 30.0
    seed: int = 1
    push_s: float = 1.5
    fetch_s: float = 0.5
    claims_per_hour: float = 8.85
    contention: float = 0.2


@dataclass
class Result:
    session: str
    record: str
    code: int
    seconds: float
    tag: str = ""
    timing: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Setup:
    bare: Path
    root: Path
    env: dict
    config: Config


@dataclass
class Run:
    results: list[Result] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    recent: list[str] = field(default_factory=list)
    won: dict[str, str] = field(default_factory=dict)
    count: int = 0
    cpu: list[float] = field(default_factory=list)
    foreign: list[float] = field(default_factory=list)


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(  # nosec B603 B607
        ["git", "-c", "user.name=load", "-c", "user.email=load@load.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def make_vault(root: Path) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=source)
    for name in GENESIS:
        (source / name).write_text(f"{name}\n", encoding="utf-8")
    _git("add", "-A", cwd=source)
    _git("commit", "-q", "-m", "genesis", cwd=source)
    bare = root / "vault.git"
    _git("clone", "-q", "--bare", str(source), str(bare), cwd=root)
    return bare


def make_shim(root: Path, config: Config) -> dict[str, str]:
    real = shutil.which("git")
    if real is None:
        raise SystemExit("claim_load needs git on PATH")
    folder = root / "shim"
    folder.mkdir()
    shim = folder / "git"
    shim.write_text(_SHIM.format(python=sys.executable), encoding="utf-8")
    shim.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{folder}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAIM_LOAD_GIT": real,
        "CLAIM_LOAD_SCALE": str(config.scale),
        "CLAIM_LOAD_PUSH_S": str(config.push_s),
        "CLAIM_LOAD_FETCH_S": str(config.fetch_s),
        "CLAIM_LOAD_PUSHES": str(root / "pushes.log"),
        "CLAIM_LOAD_CALLS": str(root / "calls.log"),
    }


def _pick(run: Run, rng: random.Random, config: Config, session: str) -> str:
    with run.lock:
        others = [r for r in run.recent[-20:] if run.won.get(r) != session]
        if others and rng.random() < config.contention:
            return rng.choice(others)
        run.count += 1
        record = f"load-{run.count:05d}"
        run.recent.append(record)
        return record


def _timing(stdout: str) -> dict[str, float]:
    try:
        shown = json.loads(stdout)
    except ValueError:
        return {}
    found = shown.get("timing") if isinstance(shown, dict) else None
    return {str(k): float(v) for k, v in found.items()} if isinstance(found, dict) else {}


def _claim(
    setup: Setup, state: Path, session: str, record: str, tag: str
) -> tuple[int, dict[str, float]]:
    deadline = CLAIM_DEADLINE_S / setup.config.scale
    argv = [sys.executable, str(CLI), "claim", record, "--vault", str(setup.bare)]
    argv += ["--state", str(state), "--session", session, "--deadline", str(deadline)]
    env = {**setup.env, "CLAIM_LOAD_TAG": tag, "BASICLY_HOLDER": f"load {session}"}
    done = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)  # nosec B603
    return done.returncode, _timing(done.stdout)


def _session(n: int, setup: Setup, run: Run) -> None:
    config = setup.config
    rng = random.Random(config.seed * 1000 + n)  # nosec B311
    state = setup.root / "machines" / f"m{n % config.machines:02d}"
    session = f"s{n:02d}"
    rate = config.claims_per_hour / 3600 * config.scale
    span = config.minutes * 60 / config.scale
    end = time.monotonic() + span
    wait = rng.uniform(0, span / 5)
    while True:
        time.sleep(min(wait, max(0.0, end - time.monotonic())))
        wait = rng.expovariate(rate)
        if time.monotonic() >= end:
            return
        record = _pick(run, rng, config, session)
        tag = f"{session}-{record}-{time.monotonic()}"
        started = time.monotonic()
        code, timing = _claim(setup, state, session, record, tag)
        seconds = (time.monotonic() - started) * config.scale
        timing["startup_s"] = max(0.0, seconds / config.scale - timing.get("claim_s", 0.0))
        scaled = {key: value * config.scale for key, value in timing.items()}
        with run.lock:
            run.results.append(Result(session, record, code, seconds, tag, scaled))
            if code == 0:
                run.won[record] = session


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1)]


def _per_minute(pushes: Path, config: Config) -> tuple[float, int]:
    if not pushes.exists():
        return 0.0, 0
    lines = pushes.read_text(encoding="utf-8").splitlines()
    stamps = [float(line.split(" ", 1)[0]) for line in lines if line.strip()]
    minutes = Counter(int((stamp - min(stamps)) * config.scale // 60) for stamp in stamps)
    span = max(1.0, config.minutes)
    return round(len(stamps) / span, 2), max(minutes.values(), default=0)


def _cut(pushes: Path, calls: dict[str, dict[str, float]]) -> dict[str, int]:
    started: Counter[str] = Counter()
    if pushes.exists():
        for line in pushes.read_text(encoding="utf-8").splitlines():
            parts = line.split(" ", 1)
            started[parts[1] if len(parts) > 1 else "-"] += 1
    ended = {tag: int(found["pushes"]) for tag, found in calls.items()}
    return {
        "outside_claims": started.get("-", 0),
        "cut_by_timeout": sum(
            max(0, count - ended.get(tag, 0)) for tag, count in started.items() if tag != "-"
        ),
    }


def _calls(calls: Path, config: Config) -> dict[str, dict[str, float]]:
    found: dict[str, dict[str, float]] = defaultdict(lambda: {"pushes": 0.0, "git_s": 0.0})
    if calls.exists():
        for line in calls.read_text(encoding="utf-8").splitlines():
            tag, verb, spent = line.rsplit(" ", 2)
            found[tag]["git_s"] += float(spent) * config.scale
            found[tag]["pushes"] += verb == "push"
    return found


def _busy() -> tuple[int, int] | None:
    stat = Path("/proc/stat")
    if not stat.exists():
        return None
    fields = [int(v) for v in stat.read_text(encoding="utf-8").split("\n", 1)[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields) - idle, sum(fields)


def _own_s() -> float:
    spent = os.times()
    return spent.user + spent.system + spent.children_user + spent.children_system


def _sample_cpu(run: Run, stop: threading.Event, every: float) -> None:
    tick = 100.0 if sys.platform == "win32" else float(os.sysconf("SC_CLK_TCK"))
    before, own = _busy(), _own_s()
    while before is not None and not stop.wait(every):
        after, mine = _busy(), _own_s()
        if after is None:
            return
        spent = after[1] - before[1]
        if spent:
            busy = (after[0] - before[0]) / spent
            run.cpu.append(round(busy, 3))
            share = (mine - own) * tick / spent
            run.foreign.append(round(max(0.0, busy - share), 3))
        before, own = after, mine


def counted(shown: dict[str, object]) -> str | None:
    foreign = shown.get("cpu_foreign")
    mean = float(foreign.get("mean", 0.0)) if isinstance(foreign, dict) else 0.0
    if mean > FOREIGN_CPU_LIMIT:
        return (
            f"not counted: other processes used {mean:.3f} of the machine on average, "
            f"above {FOREIGN_CPU_LIMIT}; rerun in a quiet window"
        )
    return None


def _phases(results: list[Result]) -> dict[str, dict[str, float]]:
    names = ("claim_s", "startup_s", "share_lock_wait_s", "fetch_lock_wait_s", "joined_wait_s")
    return {name: _spread([r.timing.get(name, 0.0) for r in results]) for name in names}


def _spread(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(sum(values) / len(values), 2) if values else 0.0,
        "p99": round(_percentile(values, 0.99), 2),
        "max": round(max(values, default=0.0), 2),
    }


def report(run: Run, pushes: Path, config: Config) -> dict[str, object]:
    seconds = [r.seconds for r in run.results]
    calls = _calls(pushes.with_name("calls.log"), config)
    per = [calls.get(r.tag, {"pushes": 0.0, "git_s": 0.0}) for r in run.results]
    retries = [max(0.0, c["pushes"] - 1) for c in per]
    waiting = [max(0.0, r.seconds - c["git_s"]) for r, c in zip(run.results, per, strict=True)]
    winners: dict[str, set[str]] = defaultdict(set)
    for result in run.results:
        if result.code == 0:
            winners[result.record].add(result.session)
    mean, busiest = _per_minute(pushes, config)
    return {
        "claims": len(run.results),
        "exits": dict(sorted(Counter(r.code for r in run.results).items())),
        "exit_meanings": EXIT_MEANINGS,
        "p50_s": round(_percentile(seconds, 0.50), 2),
        "p95_s": round(_percentile(seconds, 0.95), 2),
        "push_retries_per_claim": _spread(retries),
        "git_work_s_per_claim": _spread([c["git_s"] for c in per]),
        "waiting_s_per_claim": _spread(waiting),
        "phases_s_per_claim": _phases(run.results),
        "cpu_busy": _spread(run.cpu),
        "cpu_foreign": _spread(run.foreign),
        "foreign_cpu_limit": FOREIGN_CPU_LIMIT,
        "pushes_unfinished": _cut(pushes, calls),
        "p99_s": round(_percentile(seconds, 0.99), 2),
        "max_s": round(max(seconds, default=0.0), 2),
        "records": len({r.record for r in run.results}),
        "claims_per_session": dict(sorted(Counter(r.session for r in run.results).items())),
        "sessions_without_claims": config.sessions - len({r.session for r in run.results}),
        "records_with_two_winners": sorted(r for r, s in winners.items() if len(s) > 1),
        "pushes_per_minute": mean,
        "busiest_minute": busiest,
        "config": config.__dict__,
    }


def gate(shown: dict[str, object]) -> list[str]:
    failed = []
    exits = shown["exits"]
    assert isinstance(exits, dict)
    if any(code not in ENDED for code in exits):
        failed.append(f"a claim did not end 0 or 3: {exits}")
    if float(str(shown["p99_s"])) > P99_LIMIT_S:
        failed.append(f"p99 {shown['p99_s']} s is above {P99_LIMIT_S} s")
    if float(str(shown["max_s"])) > SETTLE_LIMIT_S:
        failed.append(f"a claim took {shown['max_s']} s, above {SETTLE_LIMIT_S} s")
    if shown["sessions_without_claims"]:
        failed.append(f"{shown['sessions_without_claims']} sessions made no claim")
    if shown["records_with_two_winners"]:
        failed.append(f"records with two holders: {shown['records_with_two_winners']}")
    return failed


def run_load(config: Config, root: Path) -> dict[str, object]:
    setup = Setup(make_vault(root), root, make_shim(root, config), config)
    run = Run()
    threads = [
        threading.Thread(target=_session, args=(n, setup, run)) for n in range(config.sessions)
    ]
    stop = threading.Event()
    sampler = threading.Thread(target=_sample_cpu, args=(run, stop, CPU_EVERY_S))
    sampler.start()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    sampler.join()
    return report(run, root / "pushes.log", config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="drive the real claim command under load")
    parser.add_argument("--sessions", type=int, default=52)
    parser.add_argument("--machines", type=int, default=13)
    parser.add_argument("--scale", type=float, default=4.0)
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--push-s", type=float, default=Config.push_s)
    parser.add_argument("--fetch-s", type=float, default=Config.fetch_s)
    parser.add_argument("--gate", action="store_true", help="exit 1 when a D1 limit fails")
    args = parser.parse_args(argv)
    config = Config(
        args.sessions,
        args.machines,
        args.scale,
        args.minutes,
        args.seed,
        args.push_s,
        args.fetch_s,
    )
    with tempfile.TemporaryDirectory(prefix="claim-load-") as folder:
        shown = run_load(config, Path(folder))
    refused = counted(shown)
    failed = gate(shown)
    verdict: object = refused if refused is not None else (failed or "pass")
    print(json.dumps({**shown, "gate": verdict}, indent=2, sort_keys=True))
    if refused is not None:
        return NOT_COUNTED if args.gate else 0
    return 1 if args.gate and failed else 0


if __name__ == "__main__":
    sys.exit(main())
