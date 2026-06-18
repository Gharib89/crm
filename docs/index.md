---
hide:
  - navigation
  - toc
---

<div class="crm-hero" markdown>

![crm](assets/logo.svg){ .crm-hero-logo }

# crm

### Drive Dynamics 365 CE from your shell — on-prem (NTLM) or Dataverse (OAuth), one CLI.

[Get started](getting-started/quickstart.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/Gharib89/crm){ .md-button }

</div>

<div class="grid cards" markdown>

-   __Records__

    ---

    Create, read, update, delete and bulk-import accounts, contacts, and custom
    entities over the Dataverse Web API.

    [entity how-to →](how-to/entity.md)

-   __Queries__

    ---

    OData v4 (`$filter`/`$select`/`$top`) or FetchXML, with `--json` output ready
    for scripts and agents.

    [query how-to →](how-to/query.md)

-   __Solutions__

    ---

    Export, import, clone-as-patch, stage-and-upgrade, and uninstall managed and
    unmanaged solutions.

    [solution how-to →](how-to/solution.md)

-   __Metadata__

    ---

    Browse and write entity, attribute, and relationship definitions; declarative
    `apply` from a spec file.

    [metadata how-to →](how-to/metadata.md)

-   __Plug-ins__

    ---

    Register plug-in assemblies and steps, manage workflows, SLAs, and async
    operations.

    [plugin how-to →](how-to/plugin.md)

-   __/crm agent skill__

    ---

    Ships an agent skill so Claude Code or Copilot CLI can drive D365 for you in
    plain language.

    [use with an agent →](getting-started/agent.md)

</div>

## A first taste

```bash
crm profile add                  # (1)!
crm connection whoami --json      # (2)!
```

1. One-time interactive wizard: enter your server URL, the CLI infers NTLM vs
   OAuth, stores the secret, and verifies the connection.
2. Confirms you're connected — prints your user id and org, machine-readable.

## Why crm

- **`--json` everywhere** — every command emits a structured envelope for agents and scripts.
- **`--dry-run`** — preview mutations before they touch the server.
- **One CLI, both targets** — the same commands hit on-prem v9.x and Dataverse online.
- **Optional metadata cache** — `CRM_CACHE_METADATA=1` speeds up repeated one-shot agent calls.

## For agents

Point a web-fetch agent at [`/llms.txt`](/llms.txt) (curated index) or
[`/llms-full.txt`](/llms-full.txt) (full corpus), or read the complete
[CLI reference](reference/cli.md).
