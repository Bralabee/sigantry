# Quickstart

Get from a fresh clone of `sigantry-starter` to a green
`sigantry deploy run --dry-run` against `DEV` placeholder values in
under 15 minutes.

This walkthrough assumes you have Python 3.11 or 3.12 (`sigantry`
declares `requires-python = ">=3.11"`)
and a recent `git`.
You will need a Fabric workspace ID + capacity ID for each of `DEV`,
`PREPROD`, and `PROD` -- the dry-run flow below does NOT contact
Fabric, so dummy values work for the first pass.

## Steps

1. **Create a fresh conda env** (recommended) and activate it:

   ```bash
   conda create -n sigantry-starter python=3.11 -y
   conda activate sigantry-starter
   ```

2. **Install `sigantry`** from PyPI:

   ```bash
   pip install sigantry
   ```

3. **Verify the install** by running:

   ```bash
   sigantry --help
   ```

   You should see 16+ subcommands listed (`workspace`, `deploy`,
   `release`, `sync`, `diff`, `config`, ...).

4. **Open `parameters.yml`** at the repo root. This file declares the
   per-environment substitutions `sigantry deploy run` will apply at
   deploy time. The shape is fabric-cicd's
   (`find_replace` / `key_value_replace` / `spark_pool`); see the
   inline comments in the file or
   `docs/reference/parameters-yml.md` (Sigantry docs).

5. **Set the placeholder env vars** referenced from `parameters.yml`
   so the validator is happy. For a first dry-run, dummy GUIDs are
   fine:

   ```bash
   export SIGANTRY_FABRIC_WORKSPACE_ID_DEV=00000000-0000-0000-0000-000000000001
   export SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD=00000000-0000-0000-0000-000000000002
   export SIGANTRY_FABRIC_WORKSPACE_ID_PROD=00000000-0000-0000-0000-000000000003
   export SIGANTRY_FABRIC_CAPACITY_ID_DEV=00000000-0000-0000-0000-00000000000a
   export SIGANTRY_FABRIC_CAPACITY_ID_PREPROD=00000000-0000-0000-0000-00000000000b
   export SIGANTRY_FABRIC_CAPACITY_ID_PROD=00000000-0000-0000-0000-00000000000c
   ```

   (Adopters with a `scripts/live-creds.template` source the real
   GUIDs from there instead.)

6. **Validate `parameters.yml`** with the new config validator:

   ```bash
   sigantry config validate parameters.yml
   ```

   Expected output:

   ```
   OK -- 3 environment(s) parsed: DEV, PREPROD, PROD
   ```

7. **Run a dry-run deploy** against `DEV` to see what `sigantry
   deploy run` would publish without touching Fabric:

   ```bash
   sigantry deploy run --dry-run --env DEV
   ```

   The dry-run prints the substitution plan + the items it would
   publish. No REST calls are made.

8. **Open your first PR** by branching off `main` with a
   conventional name and pushing:

   ```bash
   git checkout -b feature/0001-quickstart-walkthrough
   git commit --allow-empty -m "feat: quickstart smoke"
   git push -u origin feature/0001-quickstart-walkthrough
   ```

   Then open the PR via GitHub/ADO. The Sigantry PR-bot will run
   only if your PR touches `*.tmdl`, `**/.platform`, or
   `**/*.Lakehouse/**` (see [BRANCHING.md](BRANCHING.md) for the
   draft-PR caveat).

9. **Read [BRANCHING.md](BRANCHING.md)** for the trunk-based flow
   (DEV/PREPROD/PROD env mapping, promotion gates, hot-fix path).

10. **Read the runbook**
    [docs/runbooks/pr-bot-operator.md](../../docs/runbooks/pr-bot-operator.md)
    for the PR-bot lifecycle (auth, what triggers a comment, how to
    interpret a TMDL or Lakehouse-metadata diff).

11. **Run `sigantry doctor`** any time to surface plugin discovery +
    config-load diagnostics:

    ```bash
    sigantry doctor
    ```

12. **Bookmark `docs/reference/parameters-yml.md`** for the full
    `find_replace` / `key_value_replace` / `spark_pool` /
    `semantic_model_binding` shape reference. The validator's error
    messages cite the dotted-path location so you can jump straight
    to the offending YAML node.

That's the 15-minute walkthrough. From here, replace the placeholder
GUIDs in your env-var block with real Fabric IDs, register a service
principal with workspace contributor role, and run
`sigantry deploy run --env DEV` (without `--dry-run`) to publish.
