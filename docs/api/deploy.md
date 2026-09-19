# sigantry_core.deploy

fabric-cicd wrapper (`deploy_workspace`), wheel-upload primitive
(`sync_wheel`), and the deploy orchestrator
(`deploy_orchestrator.deploy`). The base package ships no concrete
`DeployProfile`; profiles register under the
`sigantry.deploy_profiles` entry-point group from plugin
packages.

CLI mirror: `sigantry deploy {run|validate}`. Plugin
packages can contribute additional subcommands via the Typer plugin
discovery hook (TBD).

::: sigantry_core.deploy
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      separate_signature: true
      show_signature_annotations: true
