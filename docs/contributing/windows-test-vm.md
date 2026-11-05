# Windows test VM

An on-demand Windows 11 VM in Azure for checks that need Windows, such as a
released binary's behavior on Windows Credential Manager or the NTLM handshake
from a Windows host (issue #1046). It lives in the maintainer's Visual Studio
Professional subscription, whose monthly credit is 50 USD, so it runs only while
someone is using it.

`scripts/windows-vm.sh` manages it:

```bash
scripts/windows-vm.sh up       # start, allow RDP from this machine's IP, wait for RDP, print the address
scripts/windows-vm.sh down     # deallocate: stops compute billing
scripts/windows-vm.sh status   # power state and address
scripts/windows-vm.sh create   # once, already done: VM, RDP rule, auto-shutdown, budget alert
```

Connect with any RDP client to the printed address as `crmadmin`. Always finish
with `down`. Shutting Windows down from inside the guest leaves the VM
allocated, and compute keeps billing.

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
