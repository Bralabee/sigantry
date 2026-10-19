"""Tests that the tag-gated publish step CANNOT run outside a tag push.

Mitigates T-1-03 (wheel supply chain). A PR or feature branch build MUST NOT
publish to the Azure Artifacts feed.

Phase 5 Plan 05-01 moved the publish stage into templates/stages/ci.yml.
These tests follow the move.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml


@pytest.fixture(scope="module")
def ci_template(repo_root: pathlib.Path) -> dict:
    return yaml.safe_load((repo_root / "templates" / "stages" / "ci.yml").read_text())


def test_publish_stage_has_tag_condition(ci_template: dict) -> None:
    publish = next(s for s in ci_template["stages"] if s["stage"] == "publish")
    cond = publish["condition"]
    assert "startsWith(variables['Build.SourceBranch'], 'refs/tags/v')" in cond
    assert "succeeded()" in cond


def test_publish_stage_depends_on_build(ci_template: dict) -> None:
    publish = next(s for s in ci_template["stages"] if s["stage"] == "publish")
    assert publish["dependsOn"] == "build"


def test_publish_stage_uses_twine_authenticate(ci_template: dict) -> None:
    publish = next(s for s in ci_template["stages"] if s["stage"] == "publish")
    job = publish["jobs"][0]
    tasks = [s.get("task", "") for s in job["steps"]]
    assert any(t.startswith("TwineAuthenticate@1") for t in tasks)


def test_publish_stage_targets_fabric_dataops_feed(
    ci_template: dict, repo_root: pathlib.Path
) -> None:
    """Post-refactor: feed is a parameter passed into ci.yml; default is
    'fabric-dataops-toolkits' on the ci.yml param block AND the extends template
    param block. TwineAuthenticate@1 reads it as ${{ parameters.artifactsFeed }}.
    """
    params = {p["name"]: p for p in ci_template["parameters"]}
    assert params["artifactsFeed"]["default"] == "fabric-dataops-toolkits"
    publish = next(s for s in ci_template["stages"] if s["stage"] == "publish")
    job = publish["jobs"][0]
    for step in job["steps"]:
        if isinstance(step, dict) and step.get("task", "").startswith("TwineAuthenticate"):
            feed_ref = step["inputs"]["artifactFeed"]
            # Feed comes from the templated parameter (ci.yml compile-time expansion).
            assert "${{ parameters.artifactsFeed }}" in str(feed_ref), feed_ref
            return
    pytest.fail("TwineAuthenticate step not found in publish stage")


def test_no_twine_upload_outside_publish_stage(ci_template: dict) -> None:
    """twine upload must only appear in the publish stage."""
    for stage in ci_template["stages"]:
        if stage["stage"] == "publish":
            continue
        for job in stage["jobs"]:
            if not isinstance(job, dict) or "template" in job:
                # Job templates are checked in their own file-scoped tests.
                continue
            for step in job.get("steps", []):
                if not isinstance(step, dict):
                    continue
                script = step.get("script", "") or step.get("inputs", {}).get("script", "")
                assert "twine upload" not in script, (
                    f"twine upload found in stage '{stage['stage']}', "
                    f"job '{job.get('job')}' - MUST only exist in the tag-gated publish stage"
                )


def test_publish_stage_redownloads_wheel_artifact(ci_template: dict) -> None:
    """Publish stage MUST NOT assume dist/ is present; it re-downloads."""
    publish = next(s for s in ci_template["stages"] if s["stage"] == "publish")
    job = publish["jobs"][0]
    tasks = [s.get("task", "") for s in job["steps"]]
    assert any(t.startswith("DownloadPipelineArtifact@2") for t in tasks)


def test_publish_downloads_the_artifact_the_build_published(
    ci_template: dict, repo_root: pathlib.Path
) -> None:
    """#17: one ``wheelArtifactName`` names the wheel in both stages.

    The publish stage downloaded the literal ``sigantry-core-wheel`` while the
    build stage published under ``build-python.yml``'s default, so changing
    one name without the other would leave publish with no wheel to upload.
    """
    ref = "${{ parameters.wheelArtifactName }}"
    params = {p["name"]: p for p in ci_template["parameters"]}
    build_job = yaml.safe_load(
        (repo_root / "templates" / "jobs" / "build-python.yml").read_text(encoding="utf-8")
    )
    job_default = next(p for p in build_job["parameters"] if p["name"] == "wheelArtifactName")
    assert params["wheelArtifactName"]["default"] == job_default["default"]

    build = next(s for s in ci_template["stages"] if s["stage"] == "build")
    (build_ref,) = [
        j for j in build["jobs"] if str(j.get("template", "")).endswith("build-python.yml")
    ]
    assert build_ref["parameters"]["wheelArtifactName"] == ref

    publish = next(s for s in ci_template["stages"] if s["stage"] == "publish")
    (download,) = [
        s
        for s in publish["jobs"][0]["steps"]
        if str(s.get("task", "")).startswith("DownloadPipelineArtifact@2")
    ]
    assert download["inputs"]["artifact"] == ref

    text = (repo_root / "templates" / "stages" / "ci.yml").read_text(encoding="utf-8")
    assert text.count(job_default["default"]) == 1, "the name is spelled once: the default"
