"""A shipped GitHub workflow never asks setup-python for a pip cache it cannot key.

``actions/setup-python`` with ``cache: pip`` looks for ``**/requirements.txt``
or ``**/pyproject.toml`` (or the files named by ``cache-dependency-path``) and
fails the step when the checked-out tree has none, on any runner with a cache
service (issue #72). The starter and demo trees ship no dependency file, so a
workflow in them must not set the input.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
_DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml")


def _workflow_roots(templates: Path) -> list[Path]:
    """Every template tree that ships ``.github/workflows``."""
    return sorted(p.parent.parent for p in templates.rglob(".github/workflows") if p.is_dir())


def _uncacheable_pip_steps(root: Path) -> list[str]:
    """``file: step`` for each setup-python step whose pip cache has nothing to key on."""
    if any(any(root.rglob(name)) for name in _DEPENDENCY_FILES):
        return []
    found: list[str] = []
    for workflow in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        doc = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                uses = str(step.get("uses", ""))
                inputs = step.get("with") or {}
                if (
                    uses.startswith("actions/setup-python@")
                    and str(inputs.get("cache", "")).strip() == "pip"
                    and not inputs.get("cache-dependency-path")
                ):
                    found.append(
                        f"{workflow.relative_to(root).as_posix()}: {job_name} step {index}"
                    )
    return found


def test_the_template_trees_are_found() -> None:
    names = {root.name for root in _workflow_roots(_TEMPLATES)}
    assert {"starter", "demo"} <= names


@pytest.mark.parametrize("root", _workflow_roots(_TEMPLATES), ids=lambda p: p.name)
def test_no_template_workflow_sets_a_pip_cache_without_a_dependency_file(root: Path) -> None:
    assert _uncacheable_pip_steps(root) == []


def test_a_planted_pip_cache_is_reported(tmp_path: Path) -> None:
    """Fail direction: the check reports the defect it exists to catch."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "bot.yml").write_text(
        "jobs:\n"
        "  bot:\n"
        "    steps:\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.11'\n"
        "          cache: 'pip'\n",
        encoding="utf-8",
    )
    assert _uncacheable_pip_steps(tmp_path) == [".github/workflows/bot.yml: bot step 0"]
    (tmp_path / "requirements.txt").write_text("sigantry\n", encoding="utf-8")
    assert _uncacheable_pip_steps(tmp_path) == []
