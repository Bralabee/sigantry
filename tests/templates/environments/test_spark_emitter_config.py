"""Regression guard for the Fabric Environment Spark diagnostic emitter YAML.

Pitfall P6-2: the YAML MUST use `AzureLogIngestion`, not the legacy
`AzureLogAnalytics` type. The legacy type silently drops events.
"""

from __future__ import annotations

from pathlib import Path

SPARK_YAML = Path("templates/environments/spark-diagnostic-emitter.yml")


def _read() -> str:
    return SPARK_YAML.read_text(encoding="utf-8")


def test_spark_emitter_uses_azurelogingestion_not_legacy():
    src = _read()
    assert "AzureLogIngestion" in src, (
        "Spark emitter must declare type: AzureLogIngestion (Pitfall P6-2)"
    )
    assert "AzureLogAnalytics" not in src, (
        "Legacy emitter type AzureLogAnalytics detected (Pitfall P6-2)"
    )


def test_spark_emitter_has_no_legacy_log_analytics_prefix():
    """The legacy emitter keys all begin with `spark.synapse.logAnalytics.`."""
    src = _read()
    assert "spark.synapse.logAnalytics." not in src, (
        "Legacy logAnalytics key prefix detected (Pitfall P6-2)"
    )


def test_spark_emitter_references_dcr_placeholders():
    """All four Spark DCR categories (log, event, metric, meta) must be declared."""
    src = _read()
    for key in ("logDcr", "eventDcr", "metricDcr", "metaDcr"):
        assert key in src, f"Missing Spark emitter key: {key}"


def test_spark_emitter_declares_skip_starter_pools():
    src = _read()
    assert "spark.fabric.pools.skipStarterPools" in src
    # Accept either 'true' or "true"
    assert (
        '"true"' in src
        or "'true'" in src
        or "true" in src.split("spark.fabric.pools.skipStarterPools")[1].split("\n")[0]
    )
