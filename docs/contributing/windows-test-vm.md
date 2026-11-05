# Windows test VM

An on-demand Windows 11 VM in Azure for checks that need Windows, such as a
released binary's behavior on Windows Credential Manager or the NTLM handshake
from a Windows host (issue #1046). It lives in the maintainer's Visual Studio
Professional subscription, whose monthly credit is 50 USD, so it runs only while
someone is using it.

`scripts/windows-vm.sh` manages it:

```bash
scripts/windows-vm.sh up       # start, allow RDP and SSH from this machine's IP, wait for RDP, print the address
scripts/windows-vm.sh down     # deallocate: stops compute billing
scripts/windows-vm.sh status   # power state and address
scripts/windows-vm.sh create   # once, already done: VM, OpenSSH server, auto-shutdown, budget alert
```

Always finish with `down`. Shutting Windows down from inside the guest leaves
the VM allocated, and compute keeps billing.

## Access

The VM accepts RDP (3389) and SSH (22) only from the IP that last ran `up`.
To connect from a second machine on another network, add its IP to both rules:

```bash
az network nsg rule update --subscription "Visual Studio Professional Subscription" \
  -g crm-winvm-rg --nsg-name crm-winvmNSG -n allow-rdp-me \
  --source-address-prefixes <first-ip>/32 <second-ip>/32   # same again with -n allow-ssh-me
```

- **RDP**, for anything that needs a desktop logon: on Windows,
  `mstsc /v:<WINVM_HOST>` as `.\crmadmin`; on Linux,
  `remmina -c rdp://crmadmin@<WINVM_HOST>`. The password is
  `WINVM_ADMIN_PASSWORD`.
- **SSH**, for scripted runs: `ssh crmadmin@<WINVM_HOST>` with the same
  password. The login shell is Windows PowerShell 5.1, and `scp` works for
  copying files both ways (`scp file crmadmin@<WINVM_HOST>:C:/t/`).

An SSH logon cannot write Windows Credential Manager: `crm profile add` there
fails with `(1312, 'CredRead', 'A specified logon session does not exist')`.
Run the keyring step from an RDP session, and pass `--password` per run in an
SSH session.

## Testing a release on Windows

1. Install the release under test. Copy `scripts/install.ps1` over with `scp`,
   then over SSH:
   `$env:CRM_VERSION="v1.92.5"; powershell -ExecutionPolicy Bypass -File C:\t\install.ps1`.
   The binary lands in `%LOCALAPPDATA%\Programs\crm\crm.exe`; a new shell
   has it on `PATH`.
2. Create the profile in an RDP session (`crm profile add …`), so the secret
   goes to Credential Manager, and confirm a read with `crm connection whoami`
   or, for a CRM 2011 profile, `crm legacy whoami`.
3. Run the command set over SSH, each with `--profile <name> --password <secret> --json`,
   writing each command's stdout and stderr to files under `C:\t\`. Copy the
   results back with `scp -r` and diff them against the same release's run on
   Ubuntu.

### An org that filters by source IP

Some orgs accept connections only from known addresses, and the VM's Azure IP
is not one of them: `Test-NetConnection <org-host> -Port 443` times out. Either
have the org's admin allow `WINVM_HOST`, or route the org's traffic through a
machine the org already accepts, with a reverse SSH tunnel run on that machine:

```bash
ssh -N -R 127.0.0.1:443:<org-host>:443 crmadmin@<WINVM_HOST>
```

and, on the VM (elevated), point the org's name at the tunnel:

```powershell
Add-Content C:\Windows\System32\drivers\etc\hosts "`n127.0.0.1 <org-host>"
```

TLS and NTLM still see the real host name. Remove the hosts line when you are
done.

## Settings and credentials

The script reads `WINVM_*` lines from the main checkout's gitignored `.env`,
then the environment, then its defaults. That `.env` holds
`WINVM_ADMIN_PASSWORD` (the RDP password) and `WINVM_HOST`, which `create` and
`up` record. The public IP is static, so `WINVM_HOST` survives a `down`. This
is a dev-only script: the crm CLI itself still reads no `.env`.

| Setting | Default |
|---|---|
| Subscription | Visual Studio Professional Subscription |
| Resource group, VM | `crm-winvm-rg`, `crm-winvm` |
| Region | `westeurope` |
| Size | `Standard_B2s_v2` (2 vCPU, 8 GB, burstable) |
| Image | Windows 11 Pro 25H2, licensed through the subscriber's Windows license (`Windows_Client`) |
| OS disk | 127 GB Standard SSD (E10) |

Standard_B2ms had no capacity in West Europe at creation time (November 2026),
so the default is B2s_v2, the same shape at the same price.

## Cost

Retail list prices, West Europe, November 2026:

| Item | Price | Billed |
|---|---|---|
| B2s_v2 compute, Windows | 0.105 USD/hour | while running |
| OS disk, E10 Standard SSD | 9.60 USD/month | always |
| Static public IP | about 3.65 USD/month | always |

About 13 USD a month is fixed, which leaves room for about 350 running hours.

## Guardrails

- **Auto-shutdown** at 20:00 UTC daily (22:00 Cairo in winter) deallocates a
  VM left running.
- **Budget alert** `crm-winvm`: 35 USD a month, with an email to the signed-in
  account at 80 percent.
- **RDP** opens only to the IP that last ran `create` or `up`. Run `up` again
  after your IP changes.
- The subscription's spending limit disables its resources when the credit runs
  out, rather than billing a card.
