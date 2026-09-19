Sigantry PR review

## Summary

| Field | Value |
|-------|-------|
| PR | 42 |
| Provider | github |
| Base | aaaa1111 |
| Head | bbbb2222 |
| Changed files | 3 |

## TMDL diff

~~~json
{
  "columns_added": [
    {
      "name": "'Discount Amount'",
      "table": "Sales"
    }
  ],
  "columns_removed": [],
  "measures_added": [
    {
      "name": "'Avg Order Value'",
      "table": "Sales"
    }
  ],
  "measures_modified": [
    {
      "name": "'Total Sales'",
      "table": "Sales"
    }
  ],
  "measures_removed": [],
  "relationships_added": [],
  "relationships_removed": [],
  "schema_version": "1.0",
  "tables_added": [],
  "tables_modified": [
    "Sales"
  ],
  "tables_removed": []
}
~~~

## Lakehouse diff

~~~json
{
  "identity_changes": [],
  "role_changes": [],
  "schema_toggle": null,
  "schema_version": "1.0",
  "shortcuts_added": [
    {
      "name": "raw_customers",
      "path": "Tables/raw_customers",
      "target_path": "Tables/customers"
    }
  ],
  "shortcuts_modified": [],
  "shortcuts_removed": [],
  "tracked_tables_added": [],
  "tracked_tables_removed": [],
  "warnings": [
    "Note: Microsoft Fabric does not track Lakehouse table column types in Git."
  ]
}
~~~

Note: Microsoft Fabric does not track Lakehouse table column types in Git. Column-level changes happen at runtime against OneLake and surface via `sigantry diff` against a live workspace, not via PR review.

---
audit_hash: `41cb2cc2a85f9db3b0bb186db61b25b6c8b9461c0ef391e14354b52eb0d09495`