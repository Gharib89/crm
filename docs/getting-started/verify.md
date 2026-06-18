# Verify it works

After [adding a profile](add-profile.md), confirm the connection.

```bash
crm connection whoami
```

Prints the authenticated user id and organization. If you see them, you're
connected. A deeper check:

```bash
crm connection test      # round-trips a request to the Web API
crm connection doctor    # diagnoses URL, auth, and API-version issues
crm connection status    # shows the active profile and target
```

If any of these fail — a 401, a hang, or a "VPN down?" style message — see
[Troubleshooting](troubleshooting.md).
