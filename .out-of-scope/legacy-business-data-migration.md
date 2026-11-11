# Business-data migration from CRM 2011

This project does not migrate business records (accounts, contacts, activities,
notes, attachments) from a CRM 2011 org to Dataverse. The migration pipeline
(`crm inventory`) and the read-only CRM 2011 adapter (`crm legacy`) cover
**customizations** only.

## Why this is out of scope

**The inventory contract is customizations, by decision.** ADR 0032 defines the
inventory as a description of an org's customizations, and the `crm-migration`
skill's stages (extract, diff, map-source, assess, convert, packages, verify)
have no business-data step. `crm legacy` reads metadata, solution components,
dependencies and solution exports. It has no verb that reads arbitrary business
entities, and its read kinds are a fixed list of customization tables.

**The target half already exists, and it is the small half.** A record's GUID
is preserved on the target today:

```bash
crm data import contacts contacts.jsonl --mode upsert --id-column contactid
```

That verb also batches, retries on throttling and re-binds lookups. So the
request is not "preserve GUIDs", which works. It is everything between a 2011
SOAP record and a row that verb accepts:

- a 2011 read verb for arbitrary entities, with paging;
- a normalizer from SOAP attribute shapes to Web API shapes (lookups, option
  sets, money, party lists), with a bridge from 2011 logical names to v9 entity
  set names;
- a policy for `createdon` (`overriddencreatedon` and the plug-ins that fire on
  a backdated create);
- notes and attachments (`documentbody`);
- a cross-org owner and user map;
- ordering across entities so lookups resolve.

That is the feature set of a dedicated data-migration product. KingswaySoft's
SSIS toolkit already serves it, reading 2011 over SOAP and writing Dataverse
over the Web API. Microsoft's Configuration Migration Tool and `pac data` cover
configuration data. Building a partial copy here would spend the project's
effort on a market that is served, while the customization pipeline is the part
nobody else offers.

**A read verb alone does not help.** A read-only `crm legacy query` would be a
small change, but its output is SOAP-shaped, so without the normalizer it does
not feed `crm data import`. It would be an ad-hoc query tool, which is a
different request.

ADR 0031 is not the obstacle: a read verb fits its allowlist. The decision is
about scope, not about safety.

## If this is ever reconsidered

Reopen with a bounded entity set and a real migration that needs it, and start
from the normalizer, since that is the part that decides whether the path is
honest. Any 2011 access stays read-only (ADR 0031).

## Prior requests

- #1195: "feat(legacy): read-only 2011-to-Dataverse business-data path that preserves GUIDs"
