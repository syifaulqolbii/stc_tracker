#!/bin/bash
# Backup database before clearing data
# Usage: bash scripts/backup-db.sh
# Output: backup_YYYYMMDD_HHMMSS.sql (+ schema_YYYYMMDD_HHMMSS.sql) in ./backups
# Keeps the last 14 days; runs unattended from cron.
#
# Restore (data-only dump needs the schema applied first):
#   docker exec -i moban-db psql -U postgres -d moban < backups/schema_<ts>.sql
#   docker exec -i moban-db psql -U postgres -d moban < backups/backup_<ts>.sql

set -e

BACKUP_DIR="/home/ubuntu/stc_tracker/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_${TIMESTAMP}.sql"

echo "=== Database Backup ==="
echo "Container: moban-db"
echo "Output: $BACKUP_FILE"
echo ""

# Dump all data (with INSERT statements)
docker exec moban-db pg_dump -U postgres -d moban \
    --data-only \
    --column-inserts \
    --disable-triggers \
    -f /tmp/backup.sql

docker cp moban-db:/tmp/backup.sql "$BACKUP_FILE"
docker exec moban-db rm /tmp/backup.sql

# Also dump schema for reference
SCHEMA_FILE="$BACKUP_DIR/schema_${TIMESTAMP}.sql"
docker exec moban-db pg_dump -U postgres -d moban \
    --schema-only \
    --disable-triggers \
    -f /tmp/schema.sql

docker cp moban-db:/tmp/schema.sql "$SCHEMA_FILE"
docker exec moban-db rm /tmp/schema.sql

echo ""
echo "=== Backup Complete ==="
echo "Data backup: $BACKUP_FILE"
echo "Schema backup: $SCHEMA_FILE"
echo ""
echo "File size:"
ls -lh "$BACKUP_FILE" "$SCHEMA_FILE"

echo ""
echo "=== Pruning dumps older than 14 days ==="
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'backup_*.sql' -o -name 'schema_*.sql' \) \
    -mtime +14 -print -delete
