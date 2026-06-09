# Reporting bugs & requesting features

Found a `crm` bug, or need a capability the CLI doesn't have? **Surface it to the
user first and offer to file an issue — never file silently.**

On the user's approval, file it on the upstream repo with the `gh` CLI:

```bash
gh issue create --repo Gharib89/crm --label needs-triage \
  --title "<short summary>" \
  --body-file <path>
```

Issue body template:

```
## What I was doing
<the exact crm command(s) run>

## Expected
<what should have happened>

## Actual
<the --json envelope or error output>

## Environment
- crm --version: <x.y.z>
- target: on-prem (NTLM) | Dataverse online (OAuth)
- API version: <v9.1 | v9.2>
```

For a feature request, drop the Expected/Actual split: describe the capability and
the workflow it unblocks. Keep the `needs-triage` label so the maintainer sees it.
