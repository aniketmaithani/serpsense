#!/usr/bin/env bash
# Dump the production database and keep 14 days of dumps. Runs nightly from cron
# (deploy/provision.sh); run it by hand before anything risky. Restore: docs/operations.md#backups.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=${BACKUP_DIR:-/var/backups/serpsense}
OUT="$DIR/serpsense-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U serpsense -Fc serpsense > "$OUT.partial"
mv "$OUT.partial" "$OUT"
find "$DIR" -name 'serpsense-*.dump' -mtime +14 -delete
echo "$(date -u +%FT%TZ) backup.completed $(basename "$OUT")"
