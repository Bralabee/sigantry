"""Stock ``.platform.j2`` Jinja2 templates for the Phase 13 sync engine.

Each ``<Type>.platform.j2`` template renders the schema-2.0
``.platform`` JSON for one Fabric item type. The
:class:`GenericPackager
<sigantry_core.sync.packagers.generic.GenericPackager>` resolves
templates by item-type filename via ``importlib.resources``; this
package being importable is what lets a wheel-installed sigantry
locate the templates without ``importlib.resources.as_file`` falling
back to a brittle filesystem walk.

Templates ship for: DataPipeline, SemanticModel, Report,
SparkJobDefinition (the four most-used types Council E identified).
Notebook has a dedicated packager (``NotebookPackager``) and does NOT
ship a template here -- its ``.platform`` is synthesised in code.

Lakehouse / Warehouse / SQLDatabase / MLExperiment are explicitly
out-of-scope per SPEC; v3.x can extend this directory once the
``.platform`` shape for each is locked.
"""
