# Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Install blocked; SmartScreen / Defender ASR / AppLocker flags the binary | The prebuilt binary is unsigned. Use the isolated [`uv tool install`](install.md#uv-tool-install-isolated) path, which runs through your trusted Python instead of a standalone executable. |
| On-prem commands hang or "can't reach server" | VPN is down. The on-prem org is only reachable on the corporate network — any HTTP response (including 401/403) counts as reachable. Connect the VPN and retry. |
| Secret won't save to the keyring (WSL / headless) | `crm` falls back automatically to a `0600` plaintext entry in the profile file. Force it with `crm profile add --store-password-plaintext`. |
| `WhoAmI` returns 401 / 403 | Wrong identity, or (OAuth) the app registration has no **application user** with a security role in Dynamics. Re-check credentials with `crm profile edit` and the role assignment in the org. |
| On-prem returns HTTP 501 for v9.2 | On-prem caps at v9.1. Omit `--api-version` and the CLI auto-steps-down. |

Still stuck? Run `crm connection doctor` for a guided diagnosis, or open an issue at
[github.com/Gharib89/crm](https://github.com/Gharib89/crm/issues).
