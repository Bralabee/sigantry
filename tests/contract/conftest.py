"""Contract-test directory conftest.

Each ``test_<seam>_contract.py`` file in this directory runs that seam's
contract against the in-memory doubles in ``sigantry_core.testing.doubles``
and, where ``sigantry_core`` ships them, the seam's reference
implementations.

All fixtures used by these tests ship in the base package via the
``pytest11`` entry point registered in ``pyproject.toml``. Downstream
plugin packages inherit the same fixtures automatically on
``pip install sigantry``.

How many contract tests must execute is pinned by
``tests/ci/test_contract_floor.py`` (the contract floor, ADR-0016).
"""
