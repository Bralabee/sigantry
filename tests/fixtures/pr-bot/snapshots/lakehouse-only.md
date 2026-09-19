Sigantry PR review

## Summary

| Field | Value |
|-------|-------|
| PR | 42 |
| Provider | github |
| Base | aaaa1111 |
| Head | bbbb2222 |
| Changed files | 3 |

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
audit_hash: `4281b9f879edcb0baa23acf4056123e9fd8ae16d5abf6b8e721d43f085c19f17`