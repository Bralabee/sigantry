# `parameters.yml` Reference

> Reference doc for the fabric-cicd `parameters.yml` shape consumed by
> `sigantry deploy run` and validated by `sigantry config validate`.
> Phase 14 / STARTER-02. Resolves the inline-comment cross-references
> in `templates/starter/parameters.yml`.

The `parameters.yml` file is the single source of per-environment
substitution declarations applied at deploy time by `sigantry deploy
run`. Sigantry consumes the file shape published by
[fabric-cicd](https://microsoft.github.io/fabric-cicd/latest/parameter-yaml/),
the upstream library that drives all Fabric item deploys. Sigantry
adds:

- A new `sigantry config validate <file>` Typer subcommand that loads
  + validates the file against the same `load_settings` path that
  `sigantry deploy run` uses (DEPLOY-03 + STARTER-02 closure).
- A documented placeholder env-var convention (`$ENV:SIGANTRY_FABRIC_*`)
  for adopters who want to keep secrets out of the repo.

This page documents the surface that adopters following the
[starter quickstart](../../templates/starter/docs/QUICKSTART.md) will
edit; the upstream fabric-cicd doc (linked above) remains the canonical
reference for the underlying shape.

## File shape

A complete `parameters.yml` has up to four top-level blocks. Empty
blocks are allowed; sigantry's validator does NOT reject a file that
omits a block.

```yaml
find_replace:
  - find_value: "<exact-string-to-replace>"
    item_type: <Notebook|Lakehouse|...>
    replace_value:
      DEV:     "<dev-target>"
      PREPROD: "<preprod-target>"
      PROD:    "<prod-target>"

key_value_replace:
  - find_key: "$.json.path[?(@.predicate==1)]"
    replace_value:
      _ALL_: "<single-target-for-every-env>"

spark_pool: []

semantic_model_binding: []
```

### `find_replace`

Bulk text substitution applied to every item body of the declared
`item_type`. Two-or-more-character match is recommended; one-character
matches risk over-replacement in templated bodies.

```yaml
find_replace:
  - find_value: "placeholder-workspace-id"
    item_type: Notebook
    replace_value:
      DEV:     "$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_DEV"
      PREPROD: "$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD"
      PROD:    "$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_PROD"
```

Allowed `replace_value` reference forms (anything else fails sigantry's
validator):

| Form | Resolved by | Example |
|---|---|---|
| literal string | unchanged | `"00000000-0000-0000-0000-000000000abc"` |
| `$workspace.$id` | fabric-cicd at deploy time | `$workspace.$id` |
| `$items.<Type>.<Name>.$id` | fabric-cicd at deploy time | `$items.Lakehouse.Bronze.$id` |
| `$ENV:<VAR>` | sigantry at load time | `$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_DEV` |
| `_ALL_` (env-name slot) | env wildcard | `_ALL_: "every-env-target"` |

### `key_value_replace`

JSONPath-driven substitution for structured item bodies (DataPipeline,
SemanticModel, Report). The `find_key` is a JSONPath expression
following the [JSONPath spec](https://goessner.net/articles/JsonPath/);
the `replace_value` follows the same env-mapping shape as
`find_replace`.

```yaml
key_value_replace:
  - find_key: "$.properties.activities[?(@.name=='Stage1')].typeProperties.url"
    replace_value:
      _ALL_: "$items.DataPipeline.Stage1.$id"
```

### `spark_pool`

Per-environment Spark pool selection. The shape is identical to
fabric-cicd's:

```yaml
spark_pool:
  - instance_pool_id: "<pool-guid-placeholder>"
    replace_value:
      DEV:     "$ENV:SIGANTRY_FABRIC_SPARK_POOL_ID_DEV"
      PREPROD: "$ENV:SIGANTRY_FABRIC_SPARK_POOL_ID_PREPROD"
      PROD:    "$ENV:SIGANTRY_FABRIC_SPARK_POOL_ID_PROD"
```

### `semantic_model_binding`

Per-environment binding from a SemanticModel item to its source
Lakehouse / Warehouse. fabric-cicd applies the binding update via the
"Update Datasource" REST endpoint at deploy time.

```yaml
semantic_model_binding:
  - logical_name: "Sales"
    replace_value:
      DEV:     "$items.Lakehouse.BronzeDev.$id"
      PREPROD: "$items.Lakehouse.BronzePreprod.$id"
      PROD:    "$items.Lakehouse.BronzeProd.$id"
```

## `sigantry config validate`

Validate a `parameters.yml` file before commit / deploy:

```bash
sigantry config validate parameters.yml
# Exit 0 on success; non-zero with a clear violations list on failure.
```

Validation includes:

- YAML well-formedness (`yaml.safe_load`-only).
- Every entry's `replace_value` keys are valid env names (`DEV`,
  `PREPROD`, `PROD`, `_ALL_`) -- no typos.
- Every reference form (`$workspace.$id`, `$items.<Type>.<Name>.$id`,
  `$ENV:<VAR>`, literal) is recognised.
- `key_value_replace[*].find_key` parses as JSONPath.
- `spark_pool[*].instance_pool_id` is a valid placeholder shape (UUID
  or `$ENV:` reference).

The validator runs through `sigantry_core.config.load_settings()` --
the same code path `sigantry deploy run` uses at deploy time. A file
that passes `sigantry config validate` will not trip on YAML / shape
errors during the deploy itself.

## The starter convention: `$ENV:SIGANTRY_FABRIC_*`

The [starter quickstart](../../templates/starter/docs/QUICKSTART.md)
ships a `parameters.yml` that uses placeholder env-var references for
every per-environment substitution:

```yaml
find_replace:
  - find_value: "placeholder-workspace-id"
    item_type: Notebook
    replace_value:
      DEV:     "$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_DEV"
      PREPROD: "$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD"
      PROD:    "$ENV:SIGANTRY_FABRIC_WORKSPACE_ID_PROD"
```

The `SIGANTRY_FABRIC_*` prefix is a sigantry convention. Any
adopter-controlled env var name works; the prefix exists so:

- Operators can grep their env file for "SIGANTRY_FABRIC_" and see
  every Fabric resource they've wired.
- Sigantry's pipeline templates can selectively forward the
  prefix to deploy jobs without leaking unrelated env vars.

The starter file does NOT require the prefix; rename to whatever your
adopter convention is.

## See also

- [fabric-cicd parameter.yml reference](https://microsoft.github.io/fabric-cicd/latest/parameter-yaml/) -- canonical upstream doc.
- [ADR-0010: Commercial model](../decisions/ADR-0010-commercial-model.md) -- the Apache-2.0 stance for Sigantry.
- [PR-bot operator runbook](../runbooks/pr-bot-operator.md) -- enabling the PR-review bot in a consumer repo.
- [Starter QUICKSTART](../../templates/starter/docs/QUICKSTART.md) -- 15-minute adopter walkthrough.
