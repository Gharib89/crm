#!/usr/bin/env bash
# Manage the on-demand Windows 11 test VM (docs/contributing/windows-test-vm.md).
#
#   scripts/windows-vm.sh create   # once: VM, RDP rule, nightly auto-shutdown, budget alert
#   scripts/windows-vm.sh up       # start, point the RDP rule at this machine's IP, print the address
#   scripts/windows-vm.sh down     # deallocate (stops compute billing; a guest OS shutdown does not)
#   scripts/windows-vm.sh status   # power state and address
#
# Settings come from WINVM_* lines in the main checkout's gitignored .env, then
# the environment, then the defaults below. `create` takes WINVM_ADMIN_PASSWORD
# from there (else prompts); `create` and `up` record WINVM_HOST there.
set -euo pipefail

ENV_FILE="${WINVM_ENV_FILE:-$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/.env}"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line; do export "${line?}"; done < <(grep '^WINVM_[A-Z_]*=' "$ENV_FILE")
fi

SUB="${WINVM_SUBSCRIPTION:-Visual Studio Professional Subscription}"
RG="${WINVM_RG:-crm-winvm-rg}"
VM="${WINVM_NAME:-crm-winvm}"
LOC="${WINVM_LOCATION:-westeurope}"
SIZE="${WINVM_SIZE:-Standard_B2s_v2}"
IMAGE="${WINVM_IMAGE:-MicrosoftWindowsDesktop:windows-11:win11-25h2-pro:latest}"
ADMIN="${WINVM_ADMIN:-crmadmin}"
SHUTDOWN_UTC="${WINVM_SHUTDOWN_UTC:-2000}" # 22:00 Cairo in winter
BUDGET_USD="${WINVM_BUDGET_USD:-35}"

az() { command az "$@" --subscription "$SUB"; }
my_ip() { curl -fsS -4 https://api.ipify.org; }

allow_rdp_from_here() {
  az network nsg rule create -g "$RG" --nsg-name "${VM}NSG" -n allow-rdp-me \
    --priority 1000 --access Allow --protocol Tcp --destination-port-ranges 3389 \
    --source-address-prefixes "$(my_ip)/32" -o none
}

address() {
  az vm show -d -g "$RG" -n "$VM" --query "{power:powerState, ip:publicIps}" -o tsv
}

record_host() {
  local ip
  ip=$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)
  [ -f "$ENV_FILE" ] || return 0
  sed -i '/^WINVM_HOST=/d' "$ENV_FILE"
  echo "WINVM_HOST=$ip" >> "$ENV_FILE"
}

case "${1:-}" in
create)
  PASS="${WINVM_ADMIN_PASSWORD:-}"
  [ -n "$PASS" ] || { read -rsp "VM admin password for $ADMIN: " PASS && echo; }
  az group create -n "$RG" -l "$LOC" -o none
  # Windows_Client applies the Visual Studio subscriber's Windows 11 license.
  az vm create -g "$RG" -n "$VM" --image "$IMAGE" --size "$SIZE" \
    --license-type Windows_Client --storage-sku StandardSSD_LRS \
    --public-ip-sku Standard --nsg-rule NONE \
    --admin-username "$ADMIN" --admin-password "$PASS" -o none
  allow_rdp_from_here
  az vm auto-shutdown -g "$RG" -n "$VM" --time "$SHUTDOWN_UTC" -o none
  email=$(command az account show --subscription "$SUB" --query user.name -o tsv)
  sub_id=$(command az account show --subscription "$SUB" --query id -o tsv)
  start=$(date -u +%Y-%m-01)
  command az rest --method put \
    --url "https://management.azure.com/subscriptions/$sub_id/providers/Microsoft.Consumption/budgets/crm-winvm?api-version=2023-05-01" \
    --body "{\"properties\":{\"category\":\"Cost\",\"amount\":$BUDGET_USD,\"timeGrain\":\"Monthly\",
      \"timePeriod\":{\"startDate\":\"${start}T00:00:00Z\"},
      \"notifications\":{\"actual80\":{\"enabled\":true,\"operator\":\"GreaterThan\",\"threshold\":80,
      \"contactEmails\":[\"$email\"]}}}}" -o none
  record_host
  address
  ;;
up)
  az vm start -g "$RG" -n "$VM" -o none
  allow_rdp_from_here
  record_host
  # RDP answers a few seconds after the start call returns.
  ip=$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)
  for _ in $(seq 30); do timeout 5 bash -c "</dev/tcp/$ip/3389" 2>/dev/null && break; sleep 5; done
  address
  ;;
down)
  az vm deallocate -g "$RG" -n "$VM" -o none
  address
  ;;
status)
  address
  ;;
*)
  sed -n '2,10p' "$0"
  exit 2
  ;;
esac
