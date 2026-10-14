#!/usr/bin/env bash
# Retention V1.2 COLD pull, run on the operator's PC (RETENTION_CTX_V1_2.md section 7).
# Fetches the server's archive manifests, pulls the listed journal files with rsync, verifies
# every sha256 locally, writes a receipt per manifest and uploads it. Idempotent; prints JSON.
# Prints no secret: the only credential is the ssh key behind the host alias.
set -euo pipefail

HOST="${CTX_ARCHIVE_HOST:-traderj}"
REMOTE_ROOT="${CTX_ARCHIVE_REMOTE_ROOT:-/root/usb_runtime/crypto_context/journal}"
REMOTE_ARCHIVE="${CTX_ARCHIVE_REMOTE_DIR:-/root/usb_runtime/crypto_context/archive}"
DEST="${CTX_ARCHIVE_DEST:-$HOME/usb_archive/crypto_context}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# archive.py is standard library only, so any python3 works; no venv is needed.
exec env PYTHONPATH="$here/backend" python3 -m app.crypto.context_collector_v1.archive pull \
  --host "$HOST" --remote-root "$REMOTE_ROOT" --remote-archive "$REMOTE_ARCHIVE" \
  --dest "$DEST" "$@"
