"""Deploy profiles live in plugin packages.

The base :mod:`sigantry_core.deploy` module ships no concrete
:class:`~sigantry_core.protocols.DeployProfile`. Plugins register
their own profiles under the
``sigantry.deploy_profiles`` entry-point group (legacy
``fabric_dataops_toolkits.deploy_profiles`` is dual-read during v3.0) and resolve
through :func:`sigantry_core.deploy.orchestrator.deploy`.
"""
