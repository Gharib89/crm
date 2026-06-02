#!/bin/sh
# Install or update the crm CLI on Linux from Cloudflare R2.
# Set CRM_VERSION (e.g. v0.6.0) to pin a version; default is latest.
# Run with --uninstall to remove the install.
set -eu

BASE_URL="https://pub-REPLACE_ME.r2.dev"   # set during R2 setup (Task 0/7)
INSTALL_DIR="${HOME}/.local/share/crm"
BIN_DIR="${HOME}/.local/bin"
BIN_LINK="${BIN_DIR}/crm"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$INSTALL_DIR"
    rm -f "$BIN_LINK"
    echo "crm uninstalled."
    exit 0
fi

VERSION="${CRM_VERSION:-latest}"
URL="${BASE_URL}/${VERSION}/crm-linux-x86_64.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading ${URL} ..."
curl -fsSL "$URL" -o "${TMP}/crm.tar.gz"

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
tar -xzf "${TMP}/crm.tar.gz" -C "$INSTALL_DIR"

mkdir -p "$BIN_DIR"
ln -sf "${INSTALL_DIR}/crm" "$BIN_LINK"
echo "Installed to ${INSTALL_DIR}; linked at ${BIN_LINK}."

case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) echo "Add ${BIN_DIR} to your PATH:  export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac

"${BIN_LINK}" --version || echo "Warning: 'crm --version' failed — the binary may be incompatible with this system."
