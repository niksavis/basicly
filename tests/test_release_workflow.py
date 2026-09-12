from __future__ import annotations

from pathlib import Path

import yaml

_RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _upload_files_glob() -> str:
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("softprops/action-gh-release"):
                return step["with"]["files"]
    raise AssertionError("no softprops/action-gh-release step found in release.yml")


def test_upload_glob_is_not_a_bare_wildcard() -> None:
    globs = _upload_files_glob().split()
    assert "dist/*" not in globs


def test_upload_glob_names_the_real_artifacts() -> None:
    globs = _upload_files_glob().split()
    assert "dist/*.whl" in globs
    assert "dist/*.tar.gz" in globs
