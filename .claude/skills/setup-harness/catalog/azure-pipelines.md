# Azure Pipelines

## Signals
Kind: file kind
Names: azure-pipelines.yml azure-pipelines.yaml
Paths: None.
Extensions: None.
Shebangs: None.

## lint

### Azure DevOps pipeline validation
Publisher: Microsoft
Tier: 2: https://learn.microsoft.com/en-us/rest/api/azure/devops/pipelines/preview/preview
Evidence: None.
Rung: full
Run: None.
Hook: local
Pin: None.
Route: None.
Constraints: None.
Unavailable: Azure DevOps validates a pipeline only server-side (a preview run through its REST API), which needs `az` auth and the network, so no check can run offline and no two runs are guaranteed to agree. Revisit this entry when an offline vendor linter appears.
Traps: other pipeline YAML (a template under another name) is not this entry's: it falls to whatever entry claims `*.yml`, else it is `unclaimed`. check-jsonschema's `vendor.azure-pipelines` schema is a third-party copy, not a vendor linter, and at 0.38.2 it fails to load (`schemafile was not valid`) on a valid pipeline.
