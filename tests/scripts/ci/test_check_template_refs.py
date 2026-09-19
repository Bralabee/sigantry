"""Unit tests for scripts/ci/check-template-refs.py (Pitfall A mitigation).

Tag-pin enforcement contract:

- Any `resources.repositories` entry whose `repository` alias is NOT `self`
  AND whose `ref` does not start with `refs/tags/` is a violation.
- Missing `ref:` altogether is a violation (silently resolves to `refs/heads/main`).
- `repository: self` is exempt (same-repo; no cross-trust boundary).
- YAML parse errors surface as a single "YAML parse error" string.
- `main()` exits 0 on a clean repo and 1 on any violation.
- Errors print in ADO-native `##[error] <file>: <msg>` format.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

# Load the lint script as an importable module for direct audit() testing.
_SCRIPT = Path("scripts/ci/check-template-refs.py").resolve()
_spec = importlib.util.spec_from_file_location("check_template_refs", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write(tmp: Path, body: str) -> Path:
    p = tmp / "pipeline.yml"
    p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return p


def test_accepts_tag_ref(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        resources:
          repositories:
            - repository: hs2Templates
              type: git
              name: 'COE Fabric AIMS/fabric-dataops'
              ref: refs/tags/v0.5.0
        """,
    )
    assert _mod.audit(p) == []


def test_rejects_branch_ref(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        resources:
          repositories:
            - repository: hs2Templates
              type: git
              name: 'COE Fabric AIMS/fabric-dataops'
              ref: refs/heads/master
        """,
    )
    issues = _mod.audit(p)
    assert len(issues) == 1
    assert "hs2Templates" in issues[0]
    assert "refs/tags/" in issues[0]


def test_rejects_head_ref_as_default(tmp_path: Path) -> None:
    """Pitfall A: missing `ref:` silently resolves to refs/heads/main."""
    p = _write(
        tmp_path,
        """
        resources:
          repositories:
            - repository: hs2Templates
              type: git
              name: 'COE Fabric AIMS/fabric-dataops'
        """,
    )
    issues = _mod.audit(p)
    assert len(issues) == 1
    assert "hs2Templates" in issues[0]
    assert "refs/tags/" in issues[0]


def test_exempts_self(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        resources:
          repositories:
            - repository: self
        """,
    )
    assert _mod.audit(p) == []


def test_exempts_self_alongside_violator(tmp_path: Path) -> None:
    """Multi-repo: a self entry is exempted but a sibling branch-ref is rejected."""
    p = _write(
        tmp_path,
        """
        resources:
          repositories:
            - repository: self
            - repository: hs2Templates
              type: git
              name: 'COE Fabric AIMS/fabric-dataops'
              ref: refs/heads/master
        """,
    )
    issues = _mod.audit(p)
    assert len(issues) == 1
    assert "hs2Templates" in issues[0]


def test_reports_yaml_parse_error(tmp_path: Path) -> None:
    p = tmp_path / "broken.yml"
    p.write_text("resources:\n  repositories: [\n  - invalid", encoding="utf-8")
    issues = _mod.audit(p)
    assert len(issues) == 1
    assert "YAML parse error" in issues[0]


def test_no_resources_block_returns_empty(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
        trigger:
          branches:
            include: [master]
        """,
    )
    assert _mod.audit(p) == []


def test_reports_line_context(tmp_path: Path) -> None:
    """Error string contains the filename + alias for log parsing."""
    p = _write(
        tmp_path,
        """
        resources:
          repositories:
            - repository: myAlias
              type: git
              name: 'org/repo'
              ref: refs/heads/feature
        """,
    )
    issues = _mod.audit(p)
    assert len(issues) == 1
    assert "myAlias" in issues[0]


def test_main_exits_zero_on_clean_repo(tmp_path: Path) -> None:
    # Minimal tmp repo: templates/ + azure-pipelines.yml, all clean.
    (tmp_path / "templates" / "extends").mkdir(parents=True)
    (tmp_path / "templates" / "extends" / "base.yml").write_text(
        "# sample\nresources:\n  repositories:\n    - repository: self\n",
        encoding="utf-8",
    )
    (tmp_path / "azure-pipelines.yml").write_text(
        "# sample\nresources:\n  repositories:\n"
        "    - repository: hs2Templates\n"
        "      type: git\n"
        "      name: 'COE Fabric AIMS/fabric-dataops'\n"
        "      ref: refs/tags/v0.5.0\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_main_exits_nonzero_on_branch_ref(tmp_path: Path) -> None:
    (tmp_path / "azure-pipelines.yml").write_text(
        "# sample\nresources:\n  repositories:\n"
        "    - repository: hs2Templates\n"
        "      type: git\n"
        "      name: 'COE Fabric AIMS/fabric-dataops'\n"
        "      ref: refs/heads/master\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    combined = r.stdout + r.stderr
    assert "refs/tags/" in combined


def test_main_emits_ado_error_prefix(tmp_path: Path) -> None:
    (tmp_path / "azure-pipelines.yml").write_text(
        "# sample\nresources:\n  repositories:\n"
        "    - repository: hs2Templates\n"
        "      type: git\n"
        "      name: 'COE Fabric AIMS/fabric-dataops'\n"
        "      ref: refs/heads/master\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    combined = r.stdout + r.stderr
    assert "##[error]" in combined


def test_main_zero_when_no_targets(tmp_path: Path) -> None:
    # No templates/ dir and no azure-pipelines.yml at all -> clean.
    r = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
