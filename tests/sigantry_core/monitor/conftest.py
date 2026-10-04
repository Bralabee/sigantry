"""Shared fixtures for sigantry_core.monitor unit tests.

Plan 08-02 stripped the plugin-specific env-var fixtures; tests now wire
``InMemoryTelemetrySink`` via direct DI (see ``tests/.../test_emit.py``).
"""

from __future__ import annotations
