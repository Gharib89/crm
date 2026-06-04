#!/bin/sh
# Install or update the crm CLI on Linux from Cloudflare R2.
# Set CRM_VERSION (e.g. v0.6.0) to pin a version; default is latest.
# Run with --uninstall to remove the install.
set -eu

BASE_URL="${CRM_INSTALL_BASE_URL:-https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev}"   # Cloudflare R2 public base URL (CRM_INSTALL_BASE_URL overrides, for tests; not the CLI's CRM_BASE_URL)
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
ARCHIVE="crm-linux-x86_64.tar.gz"
URL="${BASE_URL}/${VERSION}/${ARCHIVE}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading ${URL} ..."
curl -fsSL "$URL" -o "${TMP}/crm.tar.gz"

# Verify SHA-256 before extracting. Expected hash comes from the published
# SHA256SUMS next to the archive in R2, or CRM_SHA256 if the user pins one.
if [ -n "${CRM_SHA256:-}" ]; then
    expected="$CRM_SHA256"
else
    sums="$(curl -fsSL "${BASE_URL}/${VERSION}/SHA256SUMS")" || {
        echo "Could not fetch SHA256SUMS from ${BASE_URL}/${VERSION}/; set CRM_SHA256 to verify against a hash you supply instead." >&2
        exit 1
    }
    # First matching line only; strip a trailing CR in case the file is CRLF.
    expected="$(printf '%s\n' "$sums" | awk -v f="$ARCHIVE" '{ sub(/\r$/, "") } $2 == f { print $1; exit }')"
    if [ -z "$expected" ]; then
        echo "SHA256SUMS has no entry for ${ARCHIVE}; set CRM_SHA256 to verify against a hash you supply instead." >&2
        exit 1
    fi
fi
# Normalize: drop surrounding whitespace (a pasted CRM_SHA256 may carry it) and
# lowercase, since sha256sum emits lowercase but a pinned hash may be uppercase.
expected="$(printf '%s' "$expected" | tr -d '[:space:]' | tr 'A-Z' 'a-z')"
# Capture first (not `sha256sum | awk`) so a sha256sum failure isn't masked by the pipe.
actual_line="$(sha256sum "${TMP}/crm.tar.gz")" || {
    echo "Failed to compute SHA-256 of the downloaded archive." >&2
    exit 1
}
actual="${actual_line%% *}"
if [ "$expected" != "$actual" ]; then
    echo "Checksum mismatch for ${ARCHIVE}: expected ${expected:-<none>}, got ${actual}" >&2
    exit 1
fi
echo "Checksum verified (${actual})."

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
