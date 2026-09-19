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

```bash
git clone https://github.com/Bralabee/sigantry.git
cd sigantry
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Verify your setup:

```bash
python -c "import sigantry_core; print(sigantry_core.__version__)"
ruff check sigantry_core/ tests/
ruff format --check sigantry_core/ tests/
pytest -q
```

## Commit & PR Workflow

1. Open an issue or select an existing issue to discuss intended changes.
2. Branch from `main`: `git checkout -b feature/<descriptive-name>`.
3. Make focused, atomic commits in imperative mood (`feat: add preflight probe engine`).
4. Ensure all unit and contract tests pass (`pytest tests/`).
5. Open a Pull Request on GitHub against `main`. Ensure all automated CI checks pass.

## Code of Conduct

Please note that this project is released with a Contributor Code of Conduct. By participating in this project you agree to abide by its terms.
