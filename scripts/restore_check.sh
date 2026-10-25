#!/bin/bash
# Еженедельная проверка восстановления (понедельник):
#   0 9 * * 1 /opt/posmshop/scripts/restore_check.sh >> /var/log/posmshop/restore.log 2>&1
# Восстанавливает последнюю копию в тестовую базу и сравнивает количество
# записей в основных таблицах с рабочей базой. Запускается от имени
# системного пользователя postgres (локальный вход peer).
set -euo pipefail
BACKUP_DIR=${BACKUP_DIR:-/var/backups/posmshop}
DB=${DB_NAME:-posmshop}
TEST_DB=${DB}_restore_test
PSQL="psql -X -q -t -A"

LAST=$(ls -1t "$BACKUP_DIR"/"${DB}"_*.dump | head -n 1)
echo "$(date '+%F %T') проверка копии $LAST"

dropdb --if-exists "$TEST_DB"
createdb "$TEST_DB"
pg_restore --exit-on-error -d "$TEST_DB" "$LAST"

STATUS=OK
for TABLE in users products orders order_items; do
    PROD=$($PSQL -d "$DB" -c "SELECT count(*) FROM shop.$TABLE")
    TEST=$($PSQL -d "$TEST_DB" -c "SELECT count(*) FROM shop.$TABLE")
    echo "  shop.$TABLE: рабочая $PROD, из копии $TEST"
    # копия ночная, поэтому в копии может быть меньше строк, но не больше
    if [ "$TEST" -gt "$PROD" ]; then STATUS=FAIL; fi
done

dropdb "$TEST_DB"
echo "$(date '+%F %T') $STATUS"
[ "$STATUS" = OK ]
