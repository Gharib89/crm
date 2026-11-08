# Microsoft's Dataverse MCP server alongside `crm`

Reach here when the developer already has Microsoft's Dataverse MCP server
configured in the agent (tools such as `read_query`, `invoke_api`), or asks
whether to use it. `crm` stays the default; MCP is an optional shortcut for
interactive discovery on a **cloud** org. Microsoft changes the tool surface
between releases, so read the tool names the session actually lists, not this file.

## Setup facts (the agent cannot discover these)

- Supported stdio route: a local proxy,
  `claude mcp add dataverse -t stdio -- npx -y @microsoft/dataverse mcp <org url>`,
  then restart the agent. The **org URL in that command is the environment
  selection**; there is no other switch. Needs Node.js 18+.
- A tenant admin grants consent once for Microsoft's "Dataverse CLI" app, and the
  environment's admin settings (Power Platform admin center, the "Dataverse Model
  Context Protocol" feature) must list that client as enabled. First use asks the
  user to sign in.
- None of this is checkable from `crm`. A missing server, a refused sign-in or a
  client not on the allowlist are the user's to fix; do not try to fix them.

## When MCP is not usable: fall back to `crm`, silently

| Situation | Do |
|---|---|
| No Dataverse MCP tools in the session | Use `crm`. Do not suggest installing MCP unless asked. |
| Tools listed but a call errors (consent, allowlist, sign-in, 4xx/5xx) | Use `crm` for the task. Mention the MCP error in one line only if the user asked for MCP. |
| MCP points at a different environment than the task | Do not use its results. Use `crm` with `--profile`. |
| Target is on-premises | MCP does not apply (the proxy is for Dataverse online). Use `crm`. |

## Target evidence before trusting an MCP result

Before relying on MCP for anything beyond a throwaway look, prove it is the
same org as the `crm` profile:

1. `crm --profile <name> --json connection whoami` and read `OrganizationId`; the
   envelope's `meta.url` host is the org the CLI talks to.
2. Compare that host with the org URL in the MCP server's command (`claude mcp
   list`). Different hosts: stop, MCP is the wrong environment.
3. Where a read of the `organization` table through MCP returns an id, compare it
   with `OrganizationId` (inferred, not verified on every tenant). No match: stop.

**Identity differs.** MCP runs as the signed-in person; `crm` runs as the profile's
application user (cloud) or account. Their security roles can differ, so a row
visible to one may be hidden from the other, and `createdby` will differ. Never
read "MCP sees it" as "the CLI user can". Compare roles before concluding a
permission problem.

## Routing

| Task | Use |
|---|---|
| Browse tables and columns, peek at a few rows, one-off question | MCP is fine; `crm` (`metadata`, `query`) is equal and needs no allowlist |
| Exercise a deployed Custom API once | MCP `invoke_api` or `crm action`, see below |
| Any customization write (tables, columns, forms, views, apps, ribbons, web resources, plug-ins) | `crm` only |
| Bulk reads or writes, imports, exports | `crm` only |
| Anything that needs `--dry-run`, `--validate`, a saved plan or `--from-plan` | `crm` only |
| Scripted or repeatable verification, evidence for a PR | `crm` (deterministic, `--json` envelope) |
| Solution pack, unpack, deploy, plug-in registration | `crm`, or explicit `pac` where the task names it |

MCP record writes (`create_record`, `update_record`, `delete_record`) have no dry-run
and no journal. Prefer `crm` for them even on a throwaway org.

## `invoke_api` may mutate

A Custom API can change data, and the tool's description does not say whether this
one does. Treat every call as a write:

1. Do not call it on a production-looking org without the user's say-so.
2. After a timeout, an ambiguous reply or an error, **read the outcome back before
   any retry**: query the rows the API would have changed (`crm query` or MCP
   `read_query`), or its documented side effect.
3. Never retry through a second tool (`crm action invoke` after a failed
   `invoke_api`, or the reverse) until step 2 shows the first call did nothing.
   Two tools doubling the call is the failure this rule prevents.
4. For a read-only function use `crm action function` (GET), which is safe to retry.
