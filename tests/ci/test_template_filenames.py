"""Plan 08-03 guard: the shipped ADO template files keep their generic names (PROD-10).

Consumer pipelines reference these paths by name, so renaming either is a
breaking change:

- `templates/extends/secure-pipeline.yml`
- `templates/environments/spark-diagnostic-emitter.yml`

Keeping organisation-specific names out of the tree, including the
pre-v2.0 filenames, is the name gate's job (`scripts/ci/check-name-gate.py`),
which scans every path and line in the repository.
"""

from __future__ import annotations

from pathlib import Path

NEW_SECURE = Path("templates/extends/secure-pipeline.yml")
NEW_SPARK = Path("templates/environments/spark-diagnostic-emitter.yml")


def test_new_secure_pipeline_filename_exists():
    assert NEW_SECURE.exists(), f"Plan 08-03: {NEW_SECURE} must exist"


def test_new_spark_emitter_filename_exists():
    assert NEW_SPARK.exists(), f"Plan 08-03: {NEW_SPARK} must exist"
