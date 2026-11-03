#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/kaushaljain7000/ERP"
BACKUP_DIR="$APP_DIR/backups"
STAMP="$(date +%Y%m%d_%H%M%S)"
DB_TMP="$BACKUP_DIR/erp_db_${STAMP}.dump.tmp"
MEDIA_TMP="$BACKUP_DIR/erp_media_${STAMP}.tar.gz.tmp"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

cleanup() {
  rm -f "$DB_TMP" "$MEDIA_TMP"
}
trap cleanup EXIT

docker exec erp-db-1 pg_dump -U erp_user -d erp_db -Fc > "$DB_TMP"
test -s "$DB_TMP"
docker exec -i erp-db-1 pg_restore --list < "$DB_TMP" > /dev/null
mv "$DB_TMP" "$BACKUP_DIR/erp_db_${STAMP}.dump"

docker exec erp-backend-1 tar -C /app -czf - media > "$MEDIA_TMP"
test -s "$MEDIA_TMP"
mv "$MEDIA_TMP" "$BACKUP_DIR/erp_media_${STAMP}.tar.gz"
chmod 600 "$BACKUP_DIR"/erp_db_${STAMP}.dump "$BACKUP_DIR"/erp_media_${STAMP}.tar.gz

find "$BACKUP_DIR" -type f \( -name 'erp_db_*.dump' -o -name 'erp_media_*.tar.gz' \) -mtime +30 -delete
printf 'ERP database and media backup complete: %s\n' "$STAMP"
