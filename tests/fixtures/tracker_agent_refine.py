from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

package, root = Path(sys.argv[1]), Path(sys.argv[2])
root.mkdir()
subprocess.run(["git", "init", "-q", str(root)], check=True)
boot = (
    "import importlib.util,sys; "
    "s=importlib.util.spec_from_file_location('basicly_tracker',sys.argv[1]); "
    "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
    "raise SystemExit(m.main(sys.argv[2:]))"
)


def run(*args: str) -> dict:
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", boot, str(package / "__init__.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        raise ValueError(done.stdout)
    return json.loads(done.stdout)


ledger = ".basicly/ledger"
created = run(
    "create",
    ledger,
    "--prefix",
    "demo",
    "--title",
    "An agent refines shared work",
    "--description",
    "## Context\n\nA person needs an agent to refine this tracked task.",
    "--acceptance",
    "When the agent refines the task, the refine label shall clear.",
    "--field",
    "issue_type=task",
    "--field",
    "size=small",
    "--field",
    'labels=["refine","keep"]',
)
record = created["record"]
run("update", ledger, record, "--remove-label", "refine")
shown = run("show", ledger, record)
labels = shown["fields"].get("labels", [])
labels = json.loads(labels) if isinstance(labels, str) else labels
if set(labels) != {"keep"}:
    raise ValueError("The coding agent did not complete refinement.")
print(json.dumps({"refinement_completed": True}))
