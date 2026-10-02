from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS = _REPO_ROOT / ".basicly" / "core" / "skills"
_SCRIPTS = _SKILLS / "skill-creator" / "scripts"
_ADAPTED_SKILLS = (_SKILLS / "skill-creator", _SKILLS / "find-skills")

_ENTRY_POINTS = (
    "aggregate_benchmark.py",
    "generate_report.py",
    "generate_review.py",
    "improve_description.py",
    "run_eval.py",
    "run_loop.py",
)

_NETWORK_AT_RUNTIME = re.compile(
    r"fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.sheetjs\.com|cdn\.jsdelivr\.net|unpkg\.com"
    r"|\.claude/commands"
)


def _env(path: str | None = None) -> dict[str, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if path is not None:
        env["PATH"] = path
    return env


def _run_script(
    name: str, *args: str, cwd: Path, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-S", str(_SCRIPTS / name), *args],
        cwd=cwd,
        env=_env(path),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize("name", _ENTRY_POINTS)
def test_each_script_starts_with_the_standard_library_alone(name: str, tmp_path: Path) -> None:
    completed = _run_script(name, "--help", cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "iteration-1"
    eval_dir = workspace / "eval-axis-labels"
    (eval_dir / "eval_metadata.json").parent.mkdir(parents=True)
    (eval_dir / "eval_metadata.json").write_text(
        json.dumps({"eval_id": 0, "prompt": "chart the quarterly sales"}), encoding="utf-8"
    )
    for config, passed in (("with_skill", 1), ("without_skill", 0)):
        run_dir = eval_dir / config / "run-1"
        (run_dir / "outputs").mkdir(parents=True)
        (run_dir / "outputs" / "page.html").write_text("<p></script></p>", encoding="utf-8")
        grading = {
            "summary": {"pass_rate": float(passed), "passed": passed, "failed": 1 - passed},
            "expectations": [{"text": "has axis labels", "passed": bool(passed), "evidence": ""}],
        }
        (run_dir / "grading.json").write_text(json.dumps(grading), encoding="utf-8")
    return workspace


def test_the_benchmark_and_the_static_review_build_from_a_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    aggregated = _run_script("aggregate_benchmark.py", str(workspace), cwd=tmp_path)
    review = tmp_path / "review.html"
    reviewed = _run_script(
        "generate_review.py",
        str(workspace),
        "--benchmark",
        str(workspace / "benchmark.json"),
        "--static",
        str(review),
        cwd=tmp_path,
    )

    assert aggregated.returncode == 0, aggregated.stderr
    benchmark = json.loads((workspace / "benchmark.json").read_text(encoding="utf-8"))
    assert benchmark["run_summary"]["delta"]["pass_rate"] == "+1.00"
    assert reviewed.returncode == 0, reviewed.stderr
    page = review.read_text(encoding="utf-8")
    assert "chart the quarterly sales" in page
    assert "__EMBEDDED_DATA__" not in page
    assert "const EMBEDDED_DATA = {" in page
    assert "<\\/script>" in page
    assert "<p></script></p>" not in page


def _fake_claude(bin_dir: Path, seen: Path) -> None:
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "probes = sorted(p.name for p in Path('.claude/skills').iterdir())\n"
        f"Path({str(seen)!r}).write_text(json.dumps(probes))\n"
        "start = {'type': 'content_block_start',"
        " 'content_block': {'type': 'tool_use', 'name': 'Skill'}}\n"
        "delta = {'type': 'content_block_delta', 'delta': {'type': 'input_json_delta',"
        " 'partial_json': json.dumps({'skill': probes[0]})}}\n"
        "for event in (start, delta):\n"
        "    print(json.dumps({'type': 'stream_event', 'event': event}), flush=True)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _eval_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".claude" / "skills").mkdir(parents=True)
    (project / "skill").mkdir()
    (project / "skill" / "SKILL.md").write_text(
        "---\nname: charts\ndescription: Draws charts from tables.\n---\n\n# Charts\n",
        encoding="utf-8",
    )
    (project / "evals.json").write_text(
        json.dumps([{"query": "chart the quarterly sales", "should_trigger": True}]),
        encoding="utf-8",
    )
    return project


def _run_eval(project: Path, path: str) -> subprocess.CompletedProcess[str]:
    return _run_script(
        "run_eval.py",
        "--eval-set",
        "evals.json",
        "--skill-path",
        "skill",
        "--runs-per-query",
        "1",
        "--num-workers",
        "1",
        "--timeout",
        "20",
        cwd=project,
        path=path,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the fake claude is a shebang script")
def test_run_eval_removes_its_probe_skill_after_a_triggered_run(tmp_path: Path) -> None:
    project = _eval_project(tmp_path)
    seen = tmp_path / "seen.json"
    _fake_claude(tmp_path / "bin", seen)

    completed = _run_eval(project, str(tmp_path / "bin"))

    assert completed.returncode == 0, completed.stderr
    probes = json.loads(seen.read_text(encoding="utf-8"))
    assert len(probes) == 1
    assert probes[0].startswith("charts-eval-")
    assert json.loads(completed.stdout)["summary"] == {"total": 1, "passed": 1, "failed": 0}
    assert list((project / ".claude" / "skills").iterdir()) == []


def test_run_eval_removes_its_probe_skill_when_claude_cannot_start(tmp_path: Path) -> None:
    project = _eval_project(tmp_path)
    empty_path = tmp_path / "no-claude-here"
    empty_path.mkdir()

    completed = _run_eval(project, str(empty_path))

    assert completed.returncode == 0, completed.stderr
    assert "Warning: query failed" in completed.stderr
    assert json.loads(completed.stdout)["summary"]["failed"] == 1
    assert list((project / ".claude" / "skills").iterdir()) == []


def _offending_lines(root: Path) -> list[str]:
    return [
        f"{path.relative_to(_REPO_ROOT)}:{number}"
        for path in sorted(root.rglob("*"))
        if path.is_file()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _NETWORK_AT_RUNTIME.search(line)
    ]


def test_the_runtime_network_pattern_matches_what_the_sources_shipped() -> None:
    sample = '<link href="https://fonts.googleapis.com/css2?family=Lora">'

    assert _NETWORK_AT_RUNTIME.search(sample)
    assert _NETWORK_AT_RUNTIME.search("project_root / .claude/commands / name")


@pytest.mark.parametrize("skill_dir", _ADAPTED_SKILLS, ids=lambda path: path.name)
def test_no_adapted_skill_file_loads_a_cdn_or_writes_claude_commands(skill_dir: Path) -> None:
    assert any(skill_dir.rglob("*.yaml"))

    assert _offending_lines(skill_dir) == []
