from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_CLI = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "cli.py"
GENESIS_FILES = ("vault.toml", "FORMAT.md", "reader.py", "README.md")
CLEARED = ("CI", "BASICLY_TRACKER_EPHEMERAL", "BASICLY_TRACKER_SESSION")
IDENTITY = {
    f"GIT_{role}_{key}": value
    for role in ("AUTHOR", "COMMITTER")
    for key, value in (("NAME", "bulk-load"), ("EMAIL", "bulk-load@example.invalid"))
}
WRITES_PER_HOUR = 10
PROMPT_EVERY_S = 120.0
READER_EVERY_S = 30.0
P95_LIMIT_S = 240.0
CALL_TIMEOUT_S = 60.0
DRAIN_S = 30.0
UNSCALED = (
    "the 180 s bulk slot and the 20 s fetch throttle are kit constants and do not scale; "
    "visibility is in real seconds"
)


@dataclass(frozen=True)
class Load:
    sessions: int = 52
    machines: int = 13
    minutes: float = 70.0
    seed: int = 1
    scale: float = 1.0

    def real(self, seconds: float) -> float:
        return seconds / self.scale


@dataclass(frozen=True)
class Action:
    at: float
    kind: str
    session: int


@dataclass
class Tally:
    written: dict[str, float] = field(default_factory=dict)
    seen: dict[str, float] = field(default_factory=dict)
    per_session: Counter = field(default_factory=Counter)
    stops: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def child_env(base: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in base.items() if key not in CLEARED}


def percentile(values: Sequence[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def failures(report: Mapping[str, object]) -> list[str]:
    found = []
    p95 = report.get("p95_s")
    if not isinstance(p95, (int, float)):
        found.append("no written event was seen, so p95 visibility is unknown")
    elif p95 > P95_LIMIT_S:
        found.append(f"p95 visibility {p95} s is above {P95_LIMIT_S} s")
    if report.get("seen") != report.get("written"):
        found.append(f"{report.get('written')} events written but {report.get('seen')} seen")
    if silent := report.get("sessions_without_events"):
        found.append(f"sessions wrote no event: {silent}")
    queues = report.get("queued_after_stop")
    if not isinstance(queues, Mapping) or any(count != 0 for count in queues.values()):
        found.append(f"a queue is not empty after stop: {queues}")
    if report.get("errors"):
        found.append(f"{report.get('errors')} kit commands failed")
    return found


def stop_answer(answer: Mapping[str, object] | None) -> str:
    if answer is None:
        return "failed"
    shared = answer.get("shared")
    if "skipped" in answer:
        return "skipped"
    return "deferred" if isinstance(shared, Mapping) and "deferred" in shared else "shared"


def schedule(load: Load) -> list[Action]:
    rng = random.Random(load.seed)  # nosec B311
    end = load.real(load.minutes * 60.0)
    plan = []
    for session in range(load.sessions):
        for kind, every in (("write", 3600.0 / WRITES_PER_HOUR), ("prompt", PROMPT_EVERY_S)):
            step = load.real(every)
            at = rng.uniform(0.0, step)
            while at < end:
                plan.append(Action(at, kind, session))
                at += step
    step = load.real(READER_EVERY_S)
    plan += [Action(n * step, "read", -1) for n in range(math.ceil(end / step))]
    return sorted(plan, key=lambda action: action.at)


def _git(argv: list[str], cwd: Path) -> None:
    env = {**child_env(os.environ), **IDENTITY}
    subprocess.run(  # nosec B603 B607
        ["git", *argv], cwd=cwd, env=env, check=True, capture_output=True, timeout=CALL_TIMEOUT_S
    )


def make_vault(root: Path) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], source)
    for name in GENESIS_FILES:
        (source / name).write_text(f"{name}\n", encoding="utf-8")
    _git(["add", "-A"], source)
    _git(["commit", "-q", "-m", "genesis"], source)
    target = root / "vault.git"
    _git(["clone", "-q", "--bare", str(source), str(target)], root)
    return target


def seed_targets(root: Path, vault: Path, sessions: int) -> None:
    spec = importlib.util.spec_from_file_location("bulk_load_tracker", KIT_CLI)
    if spec is None or spec.loader is None:
        raise ValueError("the bulk load needs the standalone tracker cli")
    cli = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cli
    spec.loader.exec_module(cli)
    events = cli.events
    made = events.append(
        root / "source-ledger",
        [
            draft
            for session in range(sessions)
            for draft in (
                events.Draft(
                    f"load-{session}", events.KIND_CREATED, {"title": f"Bulk target {session}"}
                ),
                events.Draft(f"load-{session}", events.KIND_STATUS, {"status": "open"}),
            )
        ],
    )
    state = root / "fixture-publisher"
    native = cli.vault_cli
    clone = native.vault_clone.locate(state, str(vault))
    drafts = [
        {"kind": "ledger", "record": event.record, "ledger": json.loads(events.to_json(event))}
        for event in made
    ]
    native.vault_queue.write(
        clone, drafts, state=state, origin=native.vault_queue.Origin("fixture", False, time.time)
    )
    result = native.vault_share.share(clone, state, plan=native.vault_share.Plan(time.time))
    if result.get("pending") != 0 or result.get("shared") != len(made):
        raise ValueError("the bulk load could not publish every comment target")


class Driver:
    def __init__(self, load: Load, root: Path) -> None:
        self.load = load
        self.root = root
        self.vault = make_vault(root)
        seed_targets(root, self.vault, load.sessions)
        self.tally = Tally()
        self.env = child_env(os.environ)
        self.reader = root / "reader"

    def state(self, session: int) -> Path:
        return self.root / "machines" / f"m{session % self.load.machines}"

    def kit(self, *argv: str, state: Path) -> dict | None:
        command = [sys.executable, str(KIT_CLI), *argv, "--vault", str(self.vault)]
        try:
            done = subprocess.run(  # nosec B603
                [*command, "--state", str(state)],
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=CALL_TIMEOUT_S,
            )
            answer = json.loads(done.stdout) if done.returncode == 0 else None
            fault = f"exit {done.returncode}: {(done.stderr or done.stdout)[-300:]}"
        except (subprocess.TimeoutExpired, ValueError) as exc:
            answer, fault = None, type(exc).__name__
        if not isinstance(answer, dict):
            with self.tally.lock:
                self.tally.errors.append(f"{' '.join(argv[:2])} {fault}")
            return None
        return answer

    def write(self, session: int) -> None:
        text = f"bulk event from session {session}"
        started = time.monotonic()
        answer = self.kit("comment", f"load-{session}", "--text", text, state=self.state(session))
        ids = [str(i) for i in (answer or {}).get("events", [])]
        with self.tally.lock:
            self.tally.written.update(dict.fromkeys(ids, started))
            self.tally.per_session[session] += len(ids)

    def observe(self, verb: str, *argv: str) -> None:
        self.kit(verb, *argv, state=self.reader)
        now = time.monotonic()
        status = self.kit("status", state=self.reader) or {}
        ids = {str(last.get("id")) for last in status.get("writers", {}).values()}
        for path in self.reader.glob("vaults/*/valid.ids"):
            ids.update(path.read_text(encoding="utf-8").split())
        with self.tally.lock:
            for event in ids:
                self.tally.seen.setdefault(event, now)

    def act(self, action: Action) -> None:
        if action.kind == "write":
            self.write(action.session)
        elif action.kind == "prompt":
            self.kit("hook", "prompt", state=self.state(action.session))
        else:
            self.observe("hook", "session-start")

    def stop_machine(self, machine: int) -> int:
        for session in range(machine, self.load.sessions, self.load.machines):
            answer = stop_answer(self.kit("hook", "stop", state=self.state(session)))
            with self.tally.lock:
                self.tally.stops[answer] += 1
        status = self.kit("status", state=self.state(machine)) or {}
        queued = status.get("queued")
        return queued if isinstance(queued, int) else -1

    def run(self) -> dict[str, object]:
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = []
            for action in schedule(self.load):
                time.sleep(max(0.0, start + action.at - time.monotonic()))
                futures.append(pool.submit(self.act, action))
        crashed = [repr(exc) for future in futures if (exc := future.exception()) is not None]
        self.tally.errors.extend(crashed)
        machines = range(min(self.load.machines, self.load.sessions))
        with ThreadPoolExecutor(max_workers=len(machines) or 1) as pool:
            answers = pool.map(self.stop_machine, machines)
            queues = dict(zip((f"m{m}" for m in machines), answers, strict=True))
        before_drain = set(self.tally.seen)
        deadline = time.monotonic() + DRAIN_S
        while not self.tally.written.keys() <= self.tally.seen.keys():
            if time.monotonic() > deadline:
                break
            self.observe("fetch")
        return self.report(queues, before_drain, time.monotonic() - start)

    def report(self, queues: dict, before_drain: set, elapsed: float) -> dict[str, object]:
        tally = self.tally
        found = [max(0.0, tally.seen[i] - at) for i, at in tally.written.items() if i in tally.seen]
        late = set(tally.written) & set(tally.seen) - before_drain
        report: dict[str, object] = {
            **vars(self.load),
            "real_seconds": round(elapsed, 1),
            "written": len(tally.written),
            "seen": len(found),
            "seen_after_stop": len(late),
            **{
                f"{name}_s": None if value is None else round(value, 1)
                for name, value in (
                    ("p50", percentile(found, 0.5)),
                    ("p95", percentile(found, 0.95)),
                    ("max", max(found, default=None)),
                )
            },
            "events_per_session": {
                f"s{n}": tally.per_session[n] for n in range(self.load.sessions)
            },
            "sessions_without_events": [
                f"s{n}" for n in range(self.load.sessions) if not tally.per_session[n]
            ],
            "stop_answers": dict(tally.stops),
            "queued_after_stop": queues,
            "errors": len(tally.errors),
            "first_errors": tally.errors[:5],
        }
        if self.load.scale > 1:
            report["note"] = UNSCALED
        return report


def measure(load: Load) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="bulk-load-") as folder:
        return Driver(load, Path(folder)).run()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="measure bulk visibility of a local vault")
    parser.add_argument("--sessions", type=int, default=52)
    parser.add_argument("--machines", type=int, default=13)
    parser.add_argument("--minutes", type=float, default=70.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--gate", action="store_true", help="exit 1 when the proof fails")
    args = parser.parse_args(argv)
    if args.sessions < 1 or args.machines < 1 or args.minutes <= 0 or args.scale < 1:
        parser.error("--sessions and --machines need 1 or more, --minutes above 0, --scale 1+")
    load = Load(args.sessions, args.machines, args.minutes, args.seed, args.scale)
    report = measure(load)
    report["failures"] = failures(report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if args.gate and report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
