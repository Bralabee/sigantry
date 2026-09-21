# Install

Install `sigantry` (base) plus any plugin packages your
environment needs. The base ships no concrete `DeployProfile`,
`DataQualityGate`, `TelemetrySink`, `AuthProvider`, `RunbookRegistry`, or
`CapacityPolicy`; plugins register concrete implementations under the 11
`sigantry.*` entry-point groups.

## Base install

Install `sigantry` directly from PyPI:

```bash
pip install sigantry
```

Verify your installation:

```bash
sigantry --help
sigantry doctor
```

## Troubleshooting: `sigantry: command not found`

If you receive `sigantry: command not found` (or `'sigantry' is not recognized as an internal or external command` on Windows) immediately after running `pip install`:

1. **Clear Shell Hashing Cache (Bash/Zsh):**
   If you already had a terminal open when installing, your shell may have cached the list of available commands.
   ```bash
   # In Bash:
   hash -r

   # In Zsh:
   rehash
   ```

2. **Activate your Virtual Environment / Conda:**
   Verify that your virtual environment or Conda environment is active:
   ```bash
   # Conda
   conda activate <your-env-name>

   # Python venv (Linux/macOS)
   source .venv/bin/activate

   # Python venv (Windows PowerShell)
   .venv\Scripts\Activate.ps1
   ```

3. **Check your PATH:**
   If installing with `--user` outside a virtual environment, ensure your user Python scripts directory is on your system's `PATH`:
   - **Linux / macOS**: Typically `~/.local/bin`
   - **Windows**: Typically `%APPDATA%\Python\Python311\Scripts`

4. **Direct Python Module Invocation:**
   If the console script is still not resolving on your PATH, you can always invoke the CLI directly via Python:
   ```bash
   python -m sigantry_core.cli --help
   ```

## Add a plugin

```bash
pip install <your-plugin-package>
```

Reference plugin implementations live in sibling packages shipped
alongside the base (see the consumer repo's monorepo layout for
examples).

## Configuration

Create `.fabric-dataops.toml` in the repo root (or consumer repo):

```toml
[core]
tenant_id = "<your-tenant-id>"

[deploy]
profile = "<registered-profile-name>"

[dq]
gate = "<registered-gate-name>"

[telemetry]
sink = "<registered-sink-name>"
```

Per-plugin namespaced tables (for example
`[telemetry.log_analytics]`) pass through to the plugin's own
pydantic-settings model.

## Local development (contributors to the base)

### Path A — Conda (first-class, recommended)

Conda is the canonical environment mechanism for this project. The shipped
`environment.yml` at the repo root pins Python 3.11 (Fabric notebook runtime
parity) and installs the package itself, with the `dev`, `test` and `docs`
extras, through pip — so `pyproject.toml` stays the single dependency source
of truth.

```bash
git clone https://github.com/Bralabee/sigantry.git && cd sigantry

conda env create -f environment.yml
conda activate "$(cat .conda-env)"
```

That single command installs the editable package and every extra. There is
no Makefile in this repository; earlier revisions of this page described
`make conda-create` / `make install-dev` / `make conda-update` targets that
have never existed here. The equivalents are:

```bash
conda env update -f environment.yml --prune   # after the env file changes
pip install -e ".[dev,test]"                  # re-install extras only
```

The environment's name lives in one place -- the repo's `.conda-env` file,
which is also the machine-readable answer to "which interpreter belongs to
this checkout". `environment.yml` declares the same name, so
`conda activate "$(cat .conda-env)"` always lands in the right place.

!!! warning "An editable install elsewhere can shadow this checkout"

    If another clone of this project is editable-installed in the same
    environment, `import sigantry_core` resolves to **whichever tree comes
    first on `sys.path`** — and from a directory other than this repo root
    that can be the other tree, silently. Confirm which tree you are running
    before trusting any result:

    ```bash
    cd <this repo> && python -c "import sigantry_core, os; \
      print(os.path.dirname(sigantry_core.__file__), sigantry_core.__version__)"
    # expect: <this repo>/sigantry_core  and the version in sigantry_core/_version.py
    ```

Plugin packages install cleanly alongside the base:

```bash
pip install -e <path-to-plugin>
```

If a plugin declares optional extras for sibling-repo dependencies (for
example a plugin whose DQ gate wraps a peer project not published to
PyPI), install those extras explicitly — or ensure the peer is on your
local index / already editable-installed — when you need that seam.

Verify:

```bash
python -c "import sigantry_core; print(sigantry_core.__version__)"

sigantry doctor    # lists every discovered plugin
```

Keep the env current after pulls:

```bash
conda env update -f environment.yml --prune
```

### Path B — venv (fallback for contributors without conda)

Only use this if conda is not available on your workstation.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"
```
