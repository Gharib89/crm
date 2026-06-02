# How-to: session

Inspect local session state, taken from the CRMWorx build (§1). See the
[CLI reference](../reference/cli.md) for every flag.

## Show the active profile and last query

```bash
crm --json session info
```
Reports the active profile, the current entity set, and the last query run this session.

## Review the recent command history

```bash
crm --json session history
```
In `--json` mode (as above) emits the full `history` array — raw command strings, no timestamps; text mode prints just the last 50.

## Clear the local session state

```bash
crm --json session clear
```
Wipes the cached session state (active entity set, last query, history); profiles are unaffected.
