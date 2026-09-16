#!/usr/bin/env bash
# Generates and loads the launchd agent for daily automatic sync (macOS only).
set -euo pipefail

VAULT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.$(whoami).zotero-vault-sync"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

sed -e "s|__VAULT_DIR__|${VAULT_DIR}|g" \
    -e "s|com.example.zotero-vault-sync|${LABEL}|g" \
    "${VAULT_DIR}/launchd/com.example.zotero-vault-sync.plist.template" > "${DEST}"

mkdir -p "${VAULT_DIR}/.zotero-sync"
launchctl unload "${DEST}" 2>/dev/null || true
launchctl load "${DEST}"

echo "Installed ${DEST}, runs daily at 09:00. Log: ${VAULT_DIR}/.zotero-sync/sync.log"
echo "To remove: launchctl unload ${DEST} && rm ${DEST}"
