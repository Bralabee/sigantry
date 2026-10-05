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

> **Important: the 1.0.0 release on PyPI reads the old config names.** Installed from PyPI,
> `sigantry` 1.0.0 looks for `.fabric-dataops.toml` in the current directory when it
> is given no path (the CLI, or `FabricDataOps.from_config()` with no argument), and
> reads settings overrides as `FDT_<SECTION>__<KEY>`, not `SIGANTRY_<SECTION>__<KEY>`.
> The docs use `.sigantry.toml` and `SIGANTRY_<SECTION>__<KEY>`, which releases after
> 1.0.0, and a source install of `main`, read
> ([#31](https://github.com/Bralabee/sigantry/issues/31)). If you installed 1.0.0 from PyPI:
>
> - Name the file `.fabric-dataops.toml`; its contents are the same. Where the docs pass
>   the path explicitly, as in `from_config(".sigantry.toml")`, pass the name you used:
>   1.0.0 reads an explicit path under any name, and skips a missing one without a message.
> - Write settings overrides as `FDT_<SECTION>__<KEY>`, for example `FDT_CORE__TENANT_ID`.
>   Keep every other `SIGANTRY_` variable under its documented name. 1.0.0 itself reads
>   `SIGANTRY_TRUSTED_PLUGIN_DISTS`, `SIGANTRY_NOTIFICATION_SINK`, the webhook and
>   `SIGANTRY_SMTP_*` variables and `SIGANTRY_DRIFT_WORKSPACE_ID` under those names, and
>   the code that uses them ignores an `FDT_` spelling.
> - When you upgrade past 1.0.0, rename the file to `.sigantry.toml` and keep only that
>   one, change any path you pass explicitly, such as `from_config(".fabric-dataops.toml")`,
>   to the new name, and rename the overrides to `SIGANTRY_`. Later releases still read the
>   old names during a deprecation period, and the CLI and `from_config()` report that only
>   through a `DeprecationWarning`, which Python does not show by default.

Create the config file in the directory you run `sigantry` or your Python code from,
normally the repo root (or the consumer repo's root); parent directories are not searched.
Name it `.sigantry.toml`, or `.fabric-dataops.toml` if you installed 1.0.0 from PyPI (see
the note above):

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

> [!WARNING]
> **An editable install elsewhere can shadow this checkout.**
> If another clone of this project is editable-installed in the same
> environment, `import sigantry_core` resolves to **whichever tree comes
> first on `sys.path`** — and from a directory other than this repo root
> that can be the other tree, silently. Confirm which tree you are running
> before trusting any result:
>
> ```bash
> cd <this repo> && python -c "import sigantry_core, os; \
>   print(os.path.dirname(sigantry_core.__file__), sigantry_core.__version__)"
> # expect: <this repo>/sigantry_core  and the version in sigantry_core/_version.py
> ```

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
