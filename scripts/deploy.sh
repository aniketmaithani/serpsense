#!/usr/bin/env bash
# Ship a committed revision (default HEAD) to the production host and restart the stack:
#   scripts/deploy.sh deploy@serpsense.ai [ref]
# Only committed files go; the working copy and the host's .env are left alone. The migrate
# service upgrades the schema before web and the workers start. Rolling back = deploying an
# older ref (docs/operations.md#production).
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${1:?usage: scripts/deploy.sh user@host [ref]}
REF=${2:-HEAD}
APP_DIR=/opt/serpsense
SHA=$(git rev-parse --short "$REF")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git archive "$REF" | tar -x -C "$TMP"
echo "$SHA" > "$TMP/REVISION"
chmod 750 "$TMP"
rsync -az --delete --exclude=/.env "$TMP/" "$HOST:$APP_DIR/"
ssh "$HOST" "cd $APP_DIR && docker compose -f docker-compose.prod.yml up -d --build --remove-orphans --wait"
echo "Deployed $SHA to $HOST"
