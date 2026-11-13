from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("host", [".agents", ".claude"])
def test_projected_climb_runs_without_engine_or_site_packages(tmp_path: Path, host: str) -> None:
    source = Path(__file__).resolve().parents[1] / host / "skills/swarm/scripts"
    scripts = tmp_path / "scripts"
    shutil.copytree(source, scripts)
    script = scripts / "swarm.py"
    state = tmp_path / "climb.json"

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-S", "-B", str(script), *args],
            cwd=tmp_path,
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )

    result = run("climb-init", "--state", str(state), "--properties", "coverage,order,safety")
    assert result.returncode == 0, result.stderr
    for number, lenses in enumerate([["a", "b", "c"], ["b", "c", "d"]], 1):
        directory = tmp_path / f"r{number}"
        directory.mkdir()
        hashes = {}
        for lens in lenses:
            reply = directory / f"{lens}.md"
            data = {
                "lens": lens,
                "verdict": "pass",
                "grades": dict.fromkeys(["coverage", "order", "safety"], number + 4),
                "findings": [
                    {
                        "severity": "important",
                        "claim": "synthetic defect",
                        "evidence": "plan.md:1",
                        "fix": "repair",
                    }
                ],
            }
            reply.write_text(f"```json\n{json.dumps(data)}\n```\n", encoding="utf-8")
            hashes[lens] = hashlib.sha256(reply.read_bytes()).hexdigest()
        usage = directory / "usage.json"
        usage.write_text(json.dumps({"reports_sha256": hashes, "agents": []}), encoding="utf-8")
        args = (
            "climb-round",
            "--state",
            str(state),
            "--lenses",
            ",".join(lenses),
            "--dir",
            str(directory),
            "--usage",
            str(usage),
        )
        if number == 2:
            before = state.read_bytes()
            data = json.loads(usage.read_text())
            data["reports_sha256"]["d"] = "stale"
            usage.write_text(json.dumps(data), encoding="utf-8")
            result = run(*args)
            assert result.returncode != 0
            assert "does not match" in result.stderr
            assert state.read_bytes() == before
            data["reports_sha256"] = hashes
            usage.write_text(json.dumps(data), encoding="utf-8")
        result = run(*args)
        assert result.returncode == 0, result.stderr
        assert "decision: continue" in result.stdout
        assert f"| {number} | tokens | unknown |" in result.stdout
        assert f"| {number} | distinct_cited_important | 1 |" in result.stdout
        assert f"| {number} | repeated_cited_important | {number - 1} |" in result.stdout
