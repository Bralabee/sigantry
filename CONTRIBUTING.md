# Contributing to Sigantry

Thank you for your interest in contributing to **Sigantry**, the governance, sync, audit, and rollback layer for Microsoft Fabric.

## Ground Rules

- **Feature branches + PR for every change:** Never commit directly to `main` or `master`.
- **Python 3.11+:** Support Python 3.11, 3.12, and 3.13.
- **Strict Quality Gates:** All tests, Ruff lint/formatting, and Mypy strict checks must pass.
- **One HTTP Client:** `sigantry_core.client` is the single centralized gateway for HTTP communication (`httpx`).
- **Read-Only / Safe by Default:** Any CLI command or API function that mutates remote Fabric resources must require explicit user intent and emit a cryptographic audit ledger entry.
- **Vendor-Agnostic Core:** The base library is 100% tenant-neutral and client-neutral. Client-specific integrations, proprietary runbooks, or tenant URLs plug in via the 11 `sigantry.<seam>` protocol entry points.

## Local Development Setup

Conda (the environment the project is developed in; `environment.yml` and
`.conda-env` are both committed):

```bash
git clone https://github.com/Bralabee/sigantry.git
cd sigantry
conda env create -f environment.yml
conda activate "$(cat .conda-env)"
```

Or a plain virtualenv:

```bash
git clone https://github.com/Bralabee/sigantry.git
cd sigantry
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"
```

Both extras are needed: `dev` carries the toolchain (ruff, mypy, build,
pre-commit) and `test` carries test-only runtime dependencies (`freezegun`,
`jsonschema`, `respx`). With `dev` alone, `pytest` aborts during collection
and runs zero tests.

Verify your setup:

```bash
python -c "import sigantry_core; print(sigantry_core.__version__)"
ruff check sigantry_core/ tests/
ruff format --check sigantry_core/ tests/
pytest -q      # expect a non-zero test count, not just exit 0
```

### Confirm which tree you are running

If another clone of this project is editable-installed in the same
environment, `import sigantry_core` resolves to whichever tree comes first on
`sys.path` -- and from any directory other than this repo root that can
silently be the *other* clone, at a different version. Every result you take
from a shell, including a green test run, is then about a tree you did not
mean to test. Check before you trust it:

```bash
cd <this repo>
python -c "import sigantry_core, os; \
  print(os.path.dirname(sigantry_core.__file__), sigantry_core.__version__)"
# expect: <this repo>/sigantry_core   and the version in sigantry_core/_version.py
```

Anything else means the environment is resolving a different checkout: install
this one editable (`pip install -e .`) into the environment you are using, or
use the conda environment above.

## Commit & PR Workflow

1. Open an issue or select an existing issue to discuss intended changes.
2. Branch from `main`: `git checkout -b feature/<descriptive-name>`.
3. Make focused, atomic commits in imperative mood (`feat: add preflight probe engine`).
4. Ensure all unit and contract tests pass (`pytest tests/`).
5. Open a Pull Request on GitHub against `main`. Ensure all automated CI checks pass.

## Code of Conduct

Please note that this project is released with a Contributor Code of Conduct. By participating in this project you agree to abide by its terms.
