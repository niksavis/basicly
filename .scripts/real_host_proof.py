from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
CLI = KIT / "cli.py"
RECORD = "proof-0001"
RACE_REF = "refs/heads/proof-race"


def _kit(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    if spec is None or spec.loader is None:
        raise SystemExit(f"real_host_proof needs {file_name} in the tracker kit")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _env() -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "BASICLY_TRACKER_SESSION"):
        env.pop(name, None)
    return env


def _cli(argv: list[str]) -> tuple[int, dict, float]:
    started = time.monotonic()
    done = subprocess.run(  # nosec B603
        [sys.executable, str(CLI), *argv], capture_output=True, text=True, env=_env(), check=False
    )
    took = round(time.monotonic() - started, 2)
    try:
        shown = json.loads(done.stdout)
    except ValueError:
        shown = {"stdout": done.stdout[-400:], "stderr": done.stderr[-400:]}
    return done.returncode, shown, took


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@proof.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )


def _race(vault: str, root: Path) -> list[dict]:
    results: list[dict] = []
    lock = threading.Lock()

    def one(session: str) -> None:
        state = root / f"state-{session}"
        argv = ["claim", RECORD, "--vault", vault, "--state", str(state), "--session", session]
        code, shown, took = _cli(argv)
        with lock:
            results.append({"session": session, "exit": code, "seconds": took, "report": shown})

    threads = [threading.Thread(target=one, args=(name,)) for name in ("s1", "s2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(results, key=lambda row: row["session"])


def _share(vault: str, root: Path) -> list[dict]:
    shown = []
    for session in ("s1", "s2"):
        state = str(root / f"state-{session}")
        comment = ["comment", RECORD, "--vault", vault, "--state", state, "--text", "proof"]
        _cli(comment)
        code, report, took = _cli(["sync", "--vault", vault, "--state", state, "--urgent"])
        shown.append({"session": session, "exit": code, "seconds": took, "shared": report})
    return shown


def _rejected(vault: str, root: Path) -> dict:
    clones = []
    for name in ("a", "b"):
        folder = root / f"plain-{name}"
        _git(["clone", "-q", vault, str(folder)], root)
        _git(["commit", "-q", "--allow-empty", "-m", f"race {name}"], folder)
        clones.append(folder)
    started = time.monotonic()
    first = _git(["push", "-q", "origin", f"HEAD:{RACE_REF}"], clones[0])
    second = _git(["push", "-q", "origin", f"HEAD:{RACE_REF}"], clones[1])
    return {
        "ref": RACE_REF,
        "first_exit": first.returncode,
        "second_exit": second.returncode,
        "second_said": second.stderr.strip()[-200:],
        "seconds": round(time.monotonic() - started, 2),
    }


def _holders(vault: str, root: Path) -> list:
    clone_mod = _kit("basicly_tracker_kit_vault_clone", "vault_clone.py")
    fold = _kit("basicly_tracker_kit_vault_fold", "vault_fold.py")
    clone = clone_mod.locate(root / "state-reader", vault)
    clone_mod.fetch(clone, clock=time.time, timeout=120)
    state = fold.fold(clone_mod.valid_events(clone)).records.get(RECORD)
    return [] if state is None or state.holder is None else [list(state.holder)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="prove the vault on a real git host")
    parser.add_argument("--vault", required=True, help="the empty testbed vault's git address")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="real-host-proof-") as folder:
        root = Path(folder)
        init = _cli(["vault-init", "--vault", args.vault, "--yes"])
        race = _race(args.vault, root)
        shared = _share(args.vault, root)
        holders = _holders(args.vault, root)
        rejected = _rejected(args.vault, root)
    exits = sorted(row["exit"] for row in race)
    shown = {
        "init": {"exit": init[0], "seconds": init[2], "pushed": init[1].get("pushed")},
        "race": race,
        "shared": shared,
        "holders": holders,
        "rejected_push": rejected,
        "pass": init[0] == 0
        and exits == [0, 3]
        and len(holders) == 1
        and rejected["second_exit"] != 0,
    }
    print(json.dumps(shown, indent=2, sort_keys=True))
    return 0 if shown["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
