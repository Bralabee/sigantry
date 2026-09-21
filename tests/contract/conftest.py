"""Contract-test directory conftest.

The six ``test_<seam>_contract.py`` files in this directory share the same
plugin-discovery pattern: each uses ``pytest.importorskip`` for the plugin
impl, so the contract suite remains green even when a plugin is not
installed in the current environment.

All fixtures used by these tests ship in the base package via the
``pytest11`` entry point registered in ``pyproject.toml``. Downstream
plugin packages inherit the same fixtures automatically on
``pip install sigantry``.
"""
