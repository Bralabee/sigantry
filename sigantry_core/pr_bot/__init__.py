"""sigantry_core.pr_bot -- Phase 14 PR-review bot package.

Wave 0 (Plan 14-00) stamps this docstring-only package marker so
Plans 14-02..14-06 can land independent modules under
``sigantry_core/pr_bot/`` without an __init__.py race.

Modules added by feature plans:
- ``sigantry_core.pr_bot.payload``       -- Plan 14-02 (PrCommentPayload + render_markdown)
- ``sigantry_core.pr_bot.tmdl_diff``     -- Plan 14-03 (TMDL parser + diff)
- ``sigantry_core.pr_bot.lakehouse_diff``-- Plan 14-04 (Lakehouse metadata diff)
- ``sigantry_core.pr_bot.providers``     -- Plan 14-05 (Provider Protocol + GithubProvider + AdoProvider)
- ``sigantry_core.pr_bot.cli``           -- Plan 14-06 (Typer subapp `sigantry pr-bot`)
"""
