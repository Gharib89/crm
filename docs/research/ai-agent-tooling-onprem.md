# AI Coding-Agent Tooling for D365 CE On-Prem v9.x ALM — Inventory & Pipeline

> **Status:** complete (2026-06-03). Single pass, run `wf_348c95ae-0b5`.
>
> **Scope:** survey every AI-assistant tool (skills/plugins/MCP servers) across four ecosystems —
> Claude Code, GitHub Copilot, Microsoft-native AI, generic/OSS MCP — for automating the D365 CE
> **on-prem v9.x** solution/customization ALM lifecycle, with explicit on-prem compatibility verdicts;
> then an opinionated end-to-end pipeline.
>
> **Method:** deep-research harness — 5 angles, 23 sources, 113 claims → 25 adversarially verified
> (3-vote, 2/3-refute kills). 23 confirmed, 2 refuted, 6 findings after synthesis. 105 agents.
>
> **Sister doc — read first:** [`onprem-automation.md`](./onprem-automation.md) covers the *platform*
> side (customization surface, Web API metadata contract, on-prem auth models, solution Web API actions,
> `pac`/SolutionPackager on-prem verdicts, `crm`-CLI guardrail design). This file is the *AI-tooling
> ecosystem* layer and defers to the sister doc on platform/SDK facts.

## Confidence legend

- 🟢 **verified** — 3-0 unanimous adversarial vote, primary source.
- 🟡 **sourced-unverified** — appeared in fetched sources but not in this run's 25-claim verify batch;
  directionally correct, confirm before relying.
- 🔵 **cross-doc** — established by the sister doc [`onprem-automation.md`](./onprem-automation.md).
- 🔧 **synthesis** — design inference from verified gaps + repo context, not a direct citation.

---

## TL;DR

**No turnkey AI path reaches on-prem v9.x.** Every first-party + popular community Dataverse-AI tool is
architecturally cloud-only — Entra ID auth, `*.crm.dynamics.com` URLs, Power Platform Admin Center /
Managed Environments. None exist on an IFD/NTLM box.

Viable path = **generic coding agent (Claude Code or Copilot CLI) orchestrating an NTLM-aware Web API
bridge + classic legacy SDK tooling.** This repo's `crm` CLI + `crm` skill *are* that bridge. Everything
cloud-native is dead on arrival on-prem.

---

## Compatibility matrix

| Tool | Ecosystem | On-prem v9.x | Why | Conf |
|---|---|---|---|---|
| **Dataverse Skills** plugin (`dataverse@claude-plugins-official` / `@awesome-copilot`) | Claude Code + Copilot | ❌ online-only | Entra-only auth; 3-tier stack (MCP→Python SDK→Web API) all cloud; needs Managed Environment | 🟢 |
| **Dataverse MCP server** (`@microsoft/dataverse`) | Claude + Copilot | ❌ online-only | URL hard-coded `https://{org}.crm.dynamics.com/api/mcp`; enabled via PPAC + Managed Env; Entra consent | 🟢 |
| **Power Platform CLI `pac`** (+ built-in MCP `pac copilot mcp`) | any MCP client | ❌ online-only | `pac auth` = Entra only; `--cloud` = Azure clouds only; no NTLM/IFD flag. Sister doc: op-9-1 says verbatim "pac not available on-prem, use CrmSvcUtil.exe" | 🟢 / 🔵 |
| **`mwhesse/dataverse-mcp`** (community) | any MCP client | ❌ online-only | OAuth2 client-creds vs `login.microsoftonline.com`; 4 cloud env vars; no NTLM (code-inspected) | 🟢 |
| **Copilot for Power Platform / Copilot Studio** | MS AI | ❌ online-only | Cloud maker-portal feature; no on-prem surface | 🟢 |
| **GitHub Copilot CLI** (terminal agent) | Copilot | ✅ orchestrator | Runs shell/executables, `!`-prefix bypasses model, adds any STDIO/HTTP/SSE MCP via `/mcp add` | 🟢 |
| **Copilot agent mode** (VS Code) | Copilot | ✅ orchestrator | MCP-extensible by design | 🟢 |
| **Copilot coding agent** (cloud) | Copilot | ⚠️ not ruled out | 2 negatives (cloud-ephemeral, single-repo-locked) **REFUTED 0-3** — don't over-constrain; cloud→on-prem-LAN reach untested | 🟢 (refuted negatives) |
| **Claude Code** (skills/subagents/hooks/MCP) | Claude Code | ✅ orchestrator | Same generic mechanism: shell + custom MCP | 🔧 |
| **`crm` CLI + `crm` skill** (this repo) | Claude Code | ✅ **the bridge** | Real Web API/OData v4 over HTTPS + NTLM; CRUD, FetchXML, metadata, solution import/export | repo fact |
| **SolutionPackager** (`Microsoft.CrmSdk.CoreTools` NuGet) | classic SDK | ✅ | op-9-1 documented; pack/extract managed+unmanaged | 🔵 |
| **CrmSvcUtil.exe** (CoreTools NuGet) | classic SDK | ✅ | op-9-1: the on-prem early-bound codegen tool (pac's replacement) | 🔵 |
| **Solution import/export over Web API** (`ImportSolution`/`ExportSolution`) | Web API | ✅ | OData v4 actions; `crm` CLI already ships `solution import`/`export` | 🔵 |
| **Plugin Registration Tool** | classic SDK | ⚠️ GUI, separate | Standard on-prem reg, GUI-first; **NOT confirmed in CoreTools** (sister doc refuted 0-3 that CoreTools bundles the full legacy set) | 🔵 |
| **Configuration Migration / PackageDeployer** | classic SDK | ⚠️ uncertain bundle | On-prem exists; whether in CoreTools NuGet unconfirmed | 🔵 open |
| **`oisee/odata_mcp_go`** | OSS MCP | ⚠️ maybe | Generic OData v4 MCP — *could* front on-prem Web API; NTLM support unconfirmed | 🟡 |
| **Playwright MCP** | OSS MCP | ✅ fallback | Drive D365 web UI when no API path exists (brittle) | 🟡 |
| **Microsoft Learn MCP** | MS AI | ✅ docs-only | Grounds answers in MS docs; not an automation actuator | 🟢 |

---

## Inventory by ecosystem

### 1. Claude Code
- **Dataverse Skills / plugin** 🟢 — `/plugin install dataverse@claude-plugins-official`. Skills
  `dv-connect/dv-metadata/dv-solution/dv-query/dv-data/dv-admin`. **Unusable on-prem** — auth =
  `pac auth create` (Entra sign-in) or `CLIENT_ID/CLIENT_SECRET`; Python client
  (`PowerPlatform-Dataverse-Client` + `azure-identity`) takes OAuth TokenCredentials only, no NTLM;
  needs Managed Environment.
- **Custom MCP / skills / hooks** 🔧 — the real lever. Attaches arbitrary STDIO MCP + runs shell →
  wrap `crm` CLI as MCP, or let agent shell out to it directly.
- **`crm` skill** — already the NTLM Web API bridge. Foundation. (Sister doc: MCP wrapper was considered
  and **dropped** — `SKILL.md` + `--json` already make the CLI agent-usable.)

### 2. GitHub Copilot
- **Copilot CLI** 🟢 (GA Feb 2026) — terminal agent; reads/modifies/executes files; runs shell (`!`
  bypasses model). Ships GitHub MCP pre-wired; add more via `/mcp add` → `~/.copilot/mcp-config.json`.
  Spawns `CrmSvcUtil`/`SolutionPackager`/MSBuild/`crm` CLI as ordinary processes.
- **Agent mode (VS Code)** 🟢 — MCP-extensible by design.
- **Coding agent (cloud)** 🟢 — negatives refuted; reach not as narrow as assumed. Open practical
  question: cloud→on-prem-LAN network access.
- **Caveat (verified):** docs prove the *mechanism* (attach MCP, run tools) — they do NOT supply a
  working NTLM/OData v9.x MCP. That implementation is on you.

### 3. Microsoft-native
- **Dataverse MCP, `pac` (+ built-in MCP), Copilot Studio** — all online-only (matrix). `pac` built-in
  MCP just wraps `pac`, inherits cloud-only targeting.
- **Classic on-prem SDK (CoreTools NuGet)** 🔵 — `SolutionPackager.exe` (pack/extract solutions) +
  `CrmSvcUtil.exe` (early-bound codegen). These are your packaging/codegen actuators. Plugin
  Registration Tool / Configuration Migration / PackageDeployer bundling **not confirmed** — treat as
  separate downloads.
- **Microsoft Learn MCP** 🟢 — docs grounding only (live in this session).

### 4. Generic / OSS
- **`oisee/odata_mcp_go`** 🟡 — generic OData v4 MCP; candidate to front the on-prem Web API *if* it (or
  a fork) does NTLM. Worth a spike.
- **`codeurali/mcp-dataverse`** 🟡 — community Dataverse MCP (cloud-assumed).
- **Playwright MCP** 🟡 — browser-drive the web UI for anything with no API (last resort).

---

## Recommended end-to-end pipeline 🔧

Agent = orchestrator. `crm` CLI = NTLM data/metadata/solution bridge. CoreTools = codegen/package.
Reconciled with sister doc: **deploy via Web API `ImportSolution` (`crm` CLI), NOT `pac`.**

```
1. AUTHOR     Agent edits solution XML + customizations;
              metadata/record CRUD via `crm` CLI (Web API/NTLM).
              ⚠ retrieve-merge-write on every metadata PUT (no partial PUT → silent prop wipe).
              → verify: crm query confirms metadata applied
   (cloud Dataverse Skills/MCP CANNOT be used)

2. GENERATE   Agent writes C# plugins / custom workflow activities + JS web resources locally.
              Early-bound types via CrmSvcUtil.exe (CoreTools) — NOT pac.
              → verify: files compile (MSBuild)

3. TEST       MSBuild + test runner for plugin units;
              integration tests through `crm` CLI vs on-prem org.
              → verify: tests green

4. PACKAGE    SolutionPackager.exe (CoreTools NuGet) packs managed/unmanaged;
              Plugin Registration Tool registers plugin assembly (GUI — likely manual step).
              → verify: solution zip builds

5. DEPLOY     Import via on-prem Web API ImportSolution (`crm solution import`) — pac CANNOT connect.
              Use SYNCHRONOUS ImportSolution on-prem (StageSolution/ImportSolutionAsync = cloud-era).
              → verify: ImportJobId → query ImportJob table / RetrieveFormattedImportJobResults

6. VERIFY     Agent re-queries via `crm` CLI (OData/FetchXML).
              → verify: live state matches intent
```

Optional net-new build: wrap the `crm` CLI as an **MCP server** for first-class agent integration in
both Claude Code and Copilot. (Sister doc dropped this as unnecessary given `--json` + `SKILL.md`; revisit
only if Copilot-side MCP integration is required.)

---

## Gaps & open questions

**Reach the pipeline must accept:**
- No AI-native authoring against on-prem. Authoring = agent editing files + `crm` CLI.
- No on-prem MCP server exists. Build a custom NTLM OData/HTTP MCP (wrap `crm`) or skip MCP.
- No `pac` / no Power Platform Build Tools on-prem (🔵 definitive). Deploy = Web API via `crm`.
- Plugin Registration Tool GUI-first → likely an unavoidable semi-manual step unless spkl/xrm-ci-framework
  fully scripts registration for v9.x.

**Answered by sister doc (no longer open):** pac-on-prem (no), SolutionPackager/CrmSvcUtil source
(CoreTools NuGet), solution import/export Web API actions (exist; `crm` CLI ships them).

**Still genuinely open:**
1. On-prem metadata API limits/throttling under NTLM (sister-doc Q3 — still 🔴).
2. `StageSolution`/`ImportSolutionAsync` on op-9-1, or sync-only?
3. PackageDeployer + Configuration Migration in CoreTools, or separate NuGet?
4. Any OSS MCP doing NTLM/IFD against the Web API (e.g. `odata_mcp_go` fork), or must every team wrap
   `crm`?

---

## Time-sensitivity

All MS AI tooling is brand-new — Dataverse Skills/MCP = 2026, `pac` built-in MCP = Dec 2025, Copilot CLI
GA = Feb 2026. "Online-only" verdicts accurate **as of 2026-06-03**; Microsoft may add on-prem later —
re-check.

---

## Refuted (myths — do not repeat)

- ❌ "Copilot coding agent runs only in a cloud-ephemeral GitHub Actions env" — refuted 0-3.
- ❌ "Copilot coding agent is locked to a single GitHub.com repo, can't act on external/on-prem systems"
  — refuted 0-3.

---

## Sources

Primary (🟢 verified):

- Dataverse Skills devblog: <https://devblogs.microsoft.com/powerplatform/dataverse-skills-your-coding-agent-now-speaks-dataverse/>
- Dataverse plugin / Claude Marketplace devblog: <https://devblogs.microsoft.com/powerplatform/dataverse-plugin-claude-marketplace/>
- <https://github.com/microsoft/Dataverse-skills>
- Dataverse MCP: <https://learn.microsoft.com/en-us/power-apps/maker/data-platform/data-platform-mcp>
- Dataverse MCP other clients: <https://learn.microsoft.com/en-us/power-apps/maker/data-platform/data-platform-mcp-other-clients>
- pac auth reference: <https://learn.microsoft.com/en-us/power-platform/developer/cli/reference/auth>
- pac built-in MCP: <https://learn.microsoft.com/en-us/power-platform/developer/howto/use-mcp>
- <https://github.com/mwhesse/dataverse-mcp>
- Copilot CLI overview: <https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/overview>
- Copilot CLI add MCP: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers>
- Copilot agent mode + MCP: <https://docs.github.com/en/copilot/tutorials/enhance-agent-mode-with-mcp>
- Copilot coding agent: <https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent>
- On-prem auth: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/authenticate-users?view=op-9-1>

Sourced-unverified (🟡) / OSS:

- <https://github.com/oisee/odata_mcp_go>
- <https://github.com/codeurali/mcp-dataverse>
- <https://github.com/microsoft/playwright-mcp>
- <https://github.com/WaelHamze/xrm-ci-framework>
- SolutionPackager op-9-1: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/compress-extract-solution-file-solutionpackager?view=op-9-1>
- xrm-tooling PowerShell cmdlets op-9-1: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/xrm-tooling/use-powershell-cmdlets-xrm-tooling-connect?view=op-9-1>
- spkl: <https://benediktbergmann.eu/2021/09/12/spkl-setup-for-multiple-assemblies/>

Cross-doc (🔵): [`onprem-automation.md`](./onprem-automation.md) (runs incl. `wsva87sna`, 2026-05-30) —
pac-not-on-prem, CoreTools, solution Web API actions, on-prem auth models, `crm`-CLI guardrails.
