#!/usr/bin/env bash
# Сторож модератора: раз в 5 минут (таймер baxi-guard-health.timer).
#   • сервис упал и не поднялся: запуск
#   • процесс жив, но 10 минут нет пульса (data/heartbeat): завис, перезапуск
#   • раз в сутки копия базы в data/backups (14 последних), без остановки бота
# Пишет владельцам бота в Telegram, если что-то сделал. Ручной запуск: bash /opt/baxi-guard/deploy/guard-health.sh
set -uo pipefail
DIR=/opt/baxi-guard
UNIT=baxi-guard
STALE=600
NOW=$(date +%s)
ALERTS=()

if ! systemctl is-active --quiet "$UNIT"; then
    systemctl restart "$UNIT" && ALERTS+=("♻️ модератор был остановлен, запустил заново")
else
    beat=$(cat "$DIR/data/heartbeat" 2>/dev/null || echo 0)
    started=$(date -d "$(systemctl show -p ActiveEnterTimestamp --value "$UNIT")" +%s 2>/dev/null || echo "$NOW")
    if [ $((NOW - started)) -gt $STALE ] && [ $((NOW - beat)) -gt $STALE ]; then
        systemctl restart "$UNIT"
        ALERTS+=("♻️ модератор завис (нет пульса $(( (NOW - beat) / 60 )) мин), перезапустил")
    fi
fi

# копия базы раз в сутки
BK="$DIR/data/backups"
TODAY="$BK/guard-$(date +%Y%m%d).db.gz"
if [ -f "$DIR/data/guard.db" ] && [ ! -f "$TODAY" ]; then
    mkdir -p "$BK"
    TMP=$(mktemp)
    if sqlite3 "$DIR/data/guard.db" ".backup '$TMP'" 2>/dev/null && gzip -c "$TMP" > "$TODAY"; then
        chown -R --reference="$DIR/data" "$BK" 2>/dev/null || true
        ls -1t "$BK"/guard-*.db.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
    else
        rm -f "$TODAY"
        ALERTS+=("⚠️ не получилась копия базы модератора")
    fi
    rm -f "$TMP"
fi

if [ ${#ALERTS[@]} -gt 0 ] && [ -f "$DIR/.env" ]; then
    TOKEN=$(grep -E '^BOT_TOKEN=' "$DIR/.env" | cut -d= -f2-)
    OWNERS=$(grep -E '^OWNER_IDS=' "$DIR/.env" | cut -d= -f2- | tr ',' ' ')
    TEXT="🩺 Сторож модератора:"$'\n'"$(printf '%s\n' "${ALERTS[@]}")"
    for id in $OWNERS; do
        curl -fsS --max-time 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${id}" --data-urlencode "text=${TEXT}" >/dev/null 2>&1 || true
    done
    printf '%s\n' "${ALERTS[@]}"
fi
exit 0
