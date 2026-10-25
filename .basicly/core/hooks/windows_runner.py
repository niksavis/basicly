from __future__ import annotations

import json
import subprocess  # nosec B404
from pathlib import Path


def _quote_jobs(section: dict) -> None:
    jobs = [*section.get("commands", {}).values(), *section.get("jobs", [])]
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("Lefthook jobs must be mappings")
        if isinstance(job.get("run"), str):
            job["run"] = job["run"].replace('"', '\\"')
        if isinstance(job.get("group"), dict):
            _quote_jobs(job["group"])


def configuration(binary: Path, root: Path, directory: Path, env: dict[str, str]) -> dict[str, str]:
    result = subprocess.run(  # nosec B603
        [str(binary), "dump", "--format", "json"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("Lefthook configuration must be a mapping")
    for section in data.values():
        if isinstance(section, dict):
            _quote_jobs(section)
    path = directory / "lefthook-windows.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {**env, "LEFTHOOK_CONFIG": str(path)}
