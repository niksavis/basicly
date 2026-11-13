from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import runner


def load_driver(monkeypatch: pytest.MonkeyPatch) -> Any:
    scripts = Path(__file__).resolve().parents[1] / ".scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "eval_harness_trials", scripts / "eval_harness_trials.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.runner, "adapter_version", lambda _spec: "fixture-version")
    return module


def arguments(tmp_path: Path) -> argparse.Namespace:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"files": {"AGENTS.md": "Follow the task."}}))
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps({
            "cases": [
                {
                    "id": "control",
                    "prompt": "fixture",
                    "files": {"value.txt": "before"},
                    "check": [
                        "{python}",
                        "-c",
                        "from pathlib import Path; assert Path('value.txt').read_text() == 'after'",
                    ],
                }
            ]
        })
    )
    return argparse.Namespace(
        arm=[f"before={snapshot}", f"after={snapshot}"],
        cases=cases,
        case=None,
        repetitions=3,
        seed=17,
        family="codex",
        model="fixture-model",
        effort="medium",
        output=tmp_path / "output",
        dry_run=True,
        timeout=10,
    )


def test_eval_harness_trials_dry_run_records_plan_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    assert module.run_batch(args) == []
    experiment = json.loads((args.output / "experiment.json").read_text())
    assert len(experiment["schedule"]) == 6
    assert set(experiment["arms"]) == {"before", "after"}
    assert experiment["runtime"]["adapter_version"] == "fixture-version"
    assert experiment["runtime"]["model"] == args.model
    assert experiment["runtime"]["sandbox"] == "workspace-write"
    assert experiment["runtime"]["approval"] == "never"
    assert experiment["runtime"]["host_configuration_verified"] is False
    assert not (args.output / "trials.jsonl").exists()


@pytest.mark.parametrize("effort", ["medium", "high", "xhigh", "max"])
def test_claude_comparisons_support_the_reviewed_effort_levels(
    effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = load_driver(monkeypatch).make_spec("claude", "claude-fable-5-2", effort)
    assert spec.model == "claude-fable-5-2"
    assert spec.command[-2:] == ("--effort", effort)


@pytest.mark.parametrize(
    "family,model,effort",
    [
        ("claude", "claude-opus-4-6", "xhigh"),
        ("claude", "claude-haiku-4-5", "max"),
        ("codex", "gpt-5", "xhigh"),
        ("claude", "opus[1m]", "high"),
        ("claude", "opus", "high"),
        ("claude", "unreviewed-model", "xhigh"),
    ],
)
def test_comparisons_refuse_unsupported_or_ambiguous_model_settings(
    family: str, model: str, effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match=r"model|effort"):
        load_driver(monkeypatch).make_spec(family, model, effort)


def test_comparisons_record_explicit_advisor_configuration_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.family = "claude"
    args.model = "claude-opus-5-5"
    args.advisor_model = "claude-fable-5-2"
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)

    assert module.run_batch(args) == []

    experiment = json.loads((args.output / "experiment.json").read_text())
    assert experiment["advisor_model"] == "claude-fable-5-2"
    assert "--advisor" in experiment["runtime"]["command"]


def test_comparisons_refuse_an_inherited_effort_override_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.family = "claude"
    args.model = "claude-fable-5-2"
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")

    with pytest.raises(ValueError, match="CLAUDE_CODE_EFFORT_LEVEL"):
        module.run_batch(args)
    assert not args.output.exists()


def test_an_explicitly_disabled_advisor_cannot_be_counted_as_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.family = "claude"
    args.model = "claude-opus-5-5"
    args.advisor_model = "claude-fable-5-2"
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "1")
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)

    with pytest.raises(ValueError, match="CLAUDE_CODE_DISABLE_ADVISOR_TOOL"):
        module.run_batch(args)
    assert not args.output.exists()


def test_eval_harness_trials_refuses_missing_upgrade_inputs_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    cases = json.loads(args.cases.read_text())
    cases["cases"][0]["requires"] = ["baseline_wheel", "candidate_wheel"]
    args.cases.write_text(json.dumps(cases))
    with pytest.raises(ValueError, match="baseline-wheel"):
        module.run_batch(args)
    assert not args.output.exists()


def test_eval_harness_trials_fingerprints_executable_without_recording_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    executable = tmp_path / "host"
    executable.write_bytes(b"fixture executable")
    monkeypatch.setattr(module.shutil, "which", lambda _binary: str(executable))
    manifest = module.runtime_manifest(runner.RunnerSpec("fixture", command=("host",)), [])
    assert manifest["executable_sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert str(tmp_path) not in json.dumps(manifest)


def test_eval_harness_trials_refuses_missing_evaluator_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        module.runtime_manifest(runner.RunnerSpec("fixture"), [])


def test_eval_harness_trials_runs_every_attempt_in_its_own_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.dry_run = False
    spec = runner.RunnerSpec(
        "fixture",
        command=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text() == 'before'; "
            "Path('value.txt').write_text('after')",
            runner.PROMPT_PLACEHOLDER,
        ),
    )
    monkeypatch.setattr(module, "make_spec", lambda *_: spec)
    reports = module.run_batch(args)
    assert len(reports) == 6
    assert all(report["outcome"] == "passed" for report in reports)
    assert len((args.output / "trials.jsonl").read_text().splitlines()) == 6
    summaries = json.loads((args.output / "summary.json").read_text())
    assert [group["attempts"] for group in summaries] == [3, 3]


def test_eval_harness_trials_rejects_arm_overwriting_task_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    (tmp_path / "snapshot.json").write_text(json.dumps({"files": {"value.txt": "after"}}))
    with pytest.raises(ValueError, match="overwrites"):
        module.run_batch(args)
    assert not args.output.exists()


def test_eval_harness_trials_retains_interruption_in_batch_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.dry_run = False

    def interrupted(*_args: Any, **_kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "execute_trial", interrupted)
    with pytest.raises(KeyboardInterrupt):
        module.run_batch(args)
    lines = (args.output / "trials.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["outcome"] == "interrupted"
    assert json.loads((args.output / "summary.json").read_text())[0]["passed"] == 0


def test_eval_harness_trials_applies_arm_after_consumer_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.dry_run = False
    payload = json.loads(args.cases.read_text())
    payload["cases"][0]["setup"] = [
        [
            "{python}",
            "-c",
            "from pathlib import Path; Path('AGENTS.md').write_text('installed default')",
        ]
    ]
    args.cases.write_text(json.dumps(payload))
    spec = runner.RunnerSpec(
        "fixture",
        command=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('AGENTS.md').read_text() == 'Follow the task.'; "
            "Path('value.txt').write_text('after')",
            runner.PROMPT_PLACEHOLDER,
        ),
    )
    monkeypatch.setattr(module, "make_spec", lambda *_: spec)
    reports = module.run_batch(args)
    assert all(report["outcome"] == "passed" for report in reports)


def test_eval_harness_trials_refuses_inherited_repository_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Always run a different harness.")
    with pytest.raises(ValueError, match="inherits"):
        module.run_batch(args)
    assert not args.output.exists()


def test_value_comparison_requires_separate_task_and_harness_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    for name, mode in [("plain", "plain"), ("gates", "gates-only"), ("full", "full-basicly")]:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"files": {}, "mode": mode}))
    args.arm = [f"{name}={tmp_path / (name + '.json')}" for name in ["plain", "gates", "full"]]
    with pytest.raises(ValueError, match=r"task_setup.*harness_setup"):
        module.run_batch(args)
    assert not args.output.exists()


@pytest.mark.parametrize(
    "modes", [["plain"], ["plain", "gates-only", "guidance"], ["plain", "plain", "full-basicly"]]
)
def test_value_comparison_refuses_missing_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modes: list[str]
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.arm = []
    for index, mode in enumerate(modes):
        path = tmp_path / f"arm-{index}.json"
        path.write_text(json.dumps({"files": {}, "mode": mode}))
        args.arm.append(f"arm-{index}={path}")
    with pytest.raises(ValueError, match="requires plain, gates-only and full-basicly"):
        module.run_batch(args)
    assert not args.output.exists()


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_value_comparison_records_modes_and_shared_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str
) -> None:
    module = load_driver(monkeypatch)
    args = arguments(tmp_path)
    args.family = family
    payload = json.loads(args.cases.read_text())
    payload["cases"][0].update(task_setup=[], harness_setup=[])
    args.cases.write_text(json.dumps(payload))
    args.arm = []
    for mode in ["plain", "gates-only", "full-basicly"]:
        path = tmp_path / f"{mode}.json"
        path.write_text(json.dumps({"files": {}, "mode": mode}))
        args.arm.append(f"{mode}={path}")
    module.run_batch(args)
    experiment = json.loads((args.output / "experiment.json").read_text())
    assert experiment["experiment"] == "repository harness value comparison"
    assert set(experiment["arm_modes"].values()) == {"plain", "gates-only", "full-basicly"}
    assert experiment["case_sha256"] == hashlib.sha256(args.cases.read_bytes()).hexdigest()
    assert experiment["stopping_limits"]["timeout_s"] == args.timeout


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra"])
@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_codex_comparisons_support_reviewed_current_models(
    model: str, effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = load_driver(monkeypatch).make_spec("codex", model, effort)
    assert spec.model == model
    assert spec.command[-2:] == ("-c", f'model_reasoning_effort="{effort}"')
