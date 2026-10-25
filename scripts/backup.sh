#!/bin/bash
# Ежедневное резервное копирование базы posmshop (листинг 2 отчёта).
# Запуск из cron каждый день в 03:00:
#   0 3 * * * /opt/posmshop/scripts/backup.sh >> /var/log/posmshop/backup.log 2>&1
# Пароль роли backup_agent хранится в ~/.pgpass (права 600), не в скрипте.
set -euo pipefail
BACKUP_DIR=${BACKUP_DIR:-/var/backups/posmshop}
DB=${DB_NAME:-posmshop}
STAMP=$(date +%Y-%m-%d_%H-%M)
FILE="$BACKUP_DIR/${DB}_$STAMP.dump"

pg_dump -h "${DB_HOST:-127.0.0.1}" -U backup_agent -d "$DB" \
        --format=custom --compress=9 --file="$FILE"
pg_restore --list "$FILE" > /dev/null
find "$BACKUP_DIR" -name "${DB}_*.dump" -mtime +14 -delete
echo "$(date '+%F %T') OK $FILE $(du -h "$FILE" | cut -f1)"
