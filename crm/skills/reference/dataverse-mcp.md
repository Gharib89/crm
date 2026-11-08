# Microsoft's Dataverse MCP server alongside `crm`

Reach here when the session lists Microsoft's Dataverse MCP server tools (names
as of writing: `read_query`, `invoke_api`), or the user asks whether to use it.
`crm` stays the default; MCP is an optional shortcut for interactive discovery on
a Dataverse online org (Microsoft documents it for online only). Microsoft
changes the tool surface between releases: use the names the session lists.

## Setup facts (the agent cannot discover these)

- Supported stdio route: a local proxy,
  `claude mcp add dataverse -t stdio -- npx -y @microsoft/dataverse mcp <org url>`,
  then restart the agent. The **org URL in that command is the environment
  selection**; there is no other switch. Needs Node.js 18+.
- A tenant admin grants consent once for Microsoft's "Dataverse CLI" app, and the
  environment's admin settings (Power Platform admin center, the "Dataverse Model
  Context Protocol" feature) must list that client as enabled. First use asks the
  user to sign in.
- None of this is checkable from `crm`. For a missing server, a refused sign-in or
  a client not on the allowlist, report it in one line; the user fixes it.

## When MCP is not usable: fall back to `crm`

| Situation | Do |
|---|---|
| No Dataverse MCP tools in the session | Use `crm`; suggest MCP only if asked. |
| Tools listed but a call errors (consent, allowlist, sign-in, 4xx/5xx) | Use `crm` for the task. Mention the MCP error in one line only if the user asked for MCP. |
| MCP points at a different environment than the task | Do not use its results. Use `crm` with `--profile`. |
| Target is on-premises | Use `crm`; Microsoft documents the MCP server for Dataverse online only. |

## Target evidence before trusting an MCP result

Before relying on MCP for anything beyond a throwaway look, prove it is the
same org as the `crm` profile. **Proven means step 2 matched**; step 3 adds
confidence where it works.

1. `crm --profile <name> --json connection whoami` and read `OrganizationId`; the
   envelope's `meta.url` host is the org the CLI talks to.
2. Compare that host (the host only, not the whole URL) with the org URL in the
   MCP server's command (`claude mcp list`). Different hosts: stop, MCP is the
   wrong environment.
3. Where a `read_query` (a SQL `SELECT`) on the `organization` table returns an
   id, compare it with `OrganizationId` (a guess: not verified on every tenant).
   No match: stop.

**Identity differs.** MCP runs as the person who signed in to the proxy (inferred); `crm` runs as the profile's
application user (cloud) or account. Their security roles can differ, so a row
visible to one may be hidden from the other, and `createdby` will differ. Never
read "MCP sees it" as "the CLI user can". Compare roles before concluding a
permission problem.

## Routing

| Task | Use |
|---|---|
| Browse tables and columns, peek at a few rows, one-off question | MCP is fine; `crm` (`metadata`, `query`) is equal and needs no allowlist |
| Exercise a deployed Custom API once | MCP `invoke_api` or `crm action invoke`, see below |
| Any customization write (tables, columns, forms, views, apps, ribbons, web resources, plug-ins) | `crm` only |
| Bulk reads or writes, imports, exports | `crm` only |
| Anything that needs `--dry-run`, `--validate`, a saved plan or `--from-plan` | `crm` only |
| Scripted or repeatable verification, evidence for a PR | `crm` (deterministic, `--json` envelope) |
| Solution pack, unpack, deploy, plug-in registration | `crm`, or explicit `pac` where the task names it |

Prefer `crm` for record writes (MCP's `create_record`, `update_record`,
`delete_record` offer no `--dry-run`, as far as Microsoft's docs show).

## `invoke_api` may mutate

A Custom API can change data, and Microsoft's tool description ("Invoke Dataverse
Custom API") does not say whether one does. Treat every call as a write unless it
is known read-only (step 4):

1. Do not call it on an org whose profile name or host looks like production
   without the user's say-so.
2. After a timeout, an ambiguous reply or an error, **read the outcome back before
   any retry**: query the rows the API would have changed (`crm query` or MCP
   `read_query`), or its documented side effect.
3. Never retry through a second tool (`crm action invoke` after a failed
   `invoke_api`, or the reverse) until step 2 shows the first call did nothing.
   Two tools doubling the call is the failure this rule prevents.
4. For a read-only function use `crm action function` (GET), which is safe to retry.
