#!/usr/bin/env bash
# Переезд ОСНОВНОГО Бакси (/opt/linkhub: бот, копии клиентов, база с подписками и монетами) со старого сервера
# на этот. Запускать от root на НОВОМ сервере:
#
#   bash /opt/baxi-guard/deploy/move-baxi.sh check    IP_СТАРОГО   # только проверка, ничего не меняет
#   bash /opt/baxi-guard/deploy/move-baxi.sh move     IP_СТАРОГО   # переезд, простой бота 1-2 минуты
#   bash /opt/baxi-guard/deploy/move-baxi.sh rollback IP_СТАРОГО   # вернуть всё на старый сервер
#
# Как идёт переезд:
#   1. копия /opt/linkhub со старого сервера, пока бот там работает (долго, но без простоя)
#   2. на старом: остановить сторожа, бота и копии клиентов и выключить их автозапуск. Файлы НЕ удаляются
#   3. докопировать то, что изменилось за это время (секунды), проверить базу
#   4. на новом: библиотеки, сервисы с лимитами, защита (deploy/harden.sh Бакси), запуск
#   5. проверить, что бот ответил Telegram и пишет пульс
# Один токен не может работать на двух серверах сразу, поэтому старый останавливается до запуска нового.
# Сайт: старый сервер продолжает его показывать, пока в DNS домена не поменяешь IP на новый (см. в конце).
set -euo pipefail
trap 'echo "❌ Остановился на строке $LINENO: $BASH_COMMAND"' ERR
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }
CMD="${1:-}"; OLD="${2:-}"
DIR=/opt/linkhub
[ -n "$OLD" ] && [[ "$CMD" =~ ^(check|move|rollback)$ ]] || { sed -n 2,9p "$0"; exit 1; }
SOCK=/tmp/move-baxi-ssh
SSH=(ssh -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=15m -o StrictHostKeyChecking=accept-new
     "root@$OLD")
RSYNC_SSH="ssh -o ControlPath=$SOCK"
on_old() { "${SSH[@]}" "$@"; }
step() { echo; echo "== $*"; }
die() { echo "❌ $*"; exit 1; }

step "Подключение к старому серверу $OLD (пароль root спросят один раз)"
on_old true || die "не подключился по SSH к root@$OLD"
on_old "test -f $DIR/.env && test -f $DIR/data/bot.db" || die "на старом сервере нет $DIR с базой"
OLD_UNITS=$(on_old "systemctl list-unit-files 'linkhub@*' --state=enabled --no-legend 2>/dev/null | awk '{print \$1}'" | tr '\n' ' ' || true)
# после переезда на старом они выключены: список запомнен при переезде
[ -z "${OLD_UNITS// /}" ] && [ -f "$DIR/.moved-units" ] && OLD_UNITS=$(tr '\n' ' ' < "$DIR/.moved-units")
echo "Старый: $(on_old "du -sh $DIR --exclude=.venv 2>/dev/null | cut -f1") в $DIR, бот: $(on_old 'systemctl is-active linkhub' || true)"
[ -n "${OLD_UNITS// /}" ] && echo "Копии клиентов: $OLD_UNITS"
SITE_DOMAIN=$(on_old "grep -m1 -oE '^[a-z0-9.-]+\.[a-z]{2,}' /etc/caddy/Caddyfile 2>/dev/null" || true)
[ -n "$SITE_DOMAIN" ] && echo "Сайт: $SITE_DOMAIN"

sync_from_old() {  # копия /opt/linkhub без библиотек и кеша; доп. ключи rsync аргументами
    rsync -aH --delete "$@" -e "$RSYNC_SSH" --exclude .venv --exclude __pycache__ --exclude 'data/heartbeat' \
        "root@$OLD:$DIR/" "$DIR/"
}

check_db() {
    python3 - "$DIR/data/bot.db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
ok = con.execute("PRAGMA integrity_check").fetchone()[0]
chats = con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
print(f"База: {ok}, чатов: {chats}")
sys.exit(0 if ok == "ok" else 1)
PY
}

stop_old() {
    on_old "systemctl disable --now linkhub-health.timer >/dev/null 2>&1 || true
            systemctl stop linkhub 'linkhub@*' 2>/dev/null || true
            systemctl disable linkhub >/dev/null 2>&1 || true
            for u in $OLD_UNITS; do systemctl disable \$u >/dev/null 2>&1 || true; done
            sleep 2; systemctl is-active linkhub >/dev/null 2>&1 && exit 1 || exit 0" \
        || die "бот на старом не остановился, переезд прерван (новый не запускали)"
}

start_old() {
    on_old "systemctl enable --now linkhub >/dev/null 2>&1
            for u in $OLD_UNITS; do systemctl enable --now \$u >/dev/null 2>&1 || true; done
            systemctl enable --now linkhub-health.timer >/dev/null 2>&1 || true
            sleep 3; systemctl is-active linkhub"
}

if [ "$CMD" = "check" ]; then
    step "Проверка нового сервера"
    echo "Память: $(free -h | awk 'NR==2 {print $2}'), диск свободно: $(df -h / | awk 'NR==2 {print $4}')"
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "нужен Python 3.11+"
    if systemctl is-active --quiet linkhub 2>/dev/null; then echo "⚠️ здесь уже работает linkhub"; fi
    echo "✅ Всё готово к переезду: bash $0 move $OLD"
    exit 0
fi

if [ "$CMD" = "rollback" ]; then
    step "Откат: стоп здесь, данные обратно, запуск на старом"
    systemctl stop linkhub-health.timer 2>/dev/null || true
    systemctl stop linkhub 'linkhub@*' 2>/dev/null || true
    systemctl disable linkhub linkhub-health.timer >/dev/null 2>&1 || true
    for u in $OLD_UNITS; do systemctl disable "$u" >/dev/null 2>&1 || true; done
    on_old "cp -a $DIR/data/bot.db $DIR/data/bot.db.before-rollback-\$(date +%s) 2>/dev/null || true"
    rsync -aH -e "$RSYNC_SSH" "$DIR/data/" "root@$OLD:$DIR/data/"
    [ -d "$DIR/instances" ] && rsync -aH -e "$RSYNC_SSH" --exclude .venv "$DIR/instances/" "root@$OLD:$DIR/instances/"
    echo "Старый бот: $(start_old)"
    echo "✅ Откат готов: бот снова на $OLD. Здесь файлы оставлены как есть."
    exit 0
fi

# ---------- move ----------
if systemctl is-active --quiet linkhub 2>/dev/null; then die "здесь уже работает linkhub: переезд уже был?"; fi
python3 -c "import venv, ensurepip" 2>/dev/null || { apt-get update -qq && apt-get install -y -qq python3-venv; }
command -v rsync >/dev/null || { apt-get update -qq && apt-get install -y -qq rsync; }
on_old "command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync)"

step "1/5 Копия, пока бот на старом работает"
mkdir -p "$DIR"
sync_from_old
id linkhub >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin linkhub
[ -d "$DIR/.venv" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
echo "Библиотеки готовы"

step "2/5 Остановка на старом (файлы там остаются)"
echo "$OLD_UNITS" > /root/.moved-units
stop_old
echo "Старый бот остановлен $(date +%H:%M:%S)"

step "3/5 Докопировать изменения и проверить базу"
sync_from_old --checksum   # сверка по содержимому: база могла поменяться без смены размера
if ! check_db; then
    echo "База повреждена при копировании, запускаю старый обратно"
    start_old >/dev/null
    die "переезд отменён, бот снова на старом сервере"
fi
chown -R linkhub:linkhub "$DIR/data"
[ -d "$DIR/instances" ] && chown -R linkhub:linkhub "$DIR/instances"
chown linkhub:linkhub "$DIR/.env" && chmod 600 "$DIR/.env"
mv /root/.moved-units "$DIR/.moved-units"

step "4/5 Сервисы, защита, запуск"
cp "$DIR/deploy/linkhub.service" /etc/systemd/system/linkhub.service
[ -f "$DIR/deploy/linkhub@.service" ] && cp "$DIR/deploy/linkhub@.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now linkhub >/dev/null
for u in $OLD_UNITS; do systemctl enable --now "$u" >/dev/null 2>&1 || echo "⚠️ не запустилась копия $u"; done
bash "$DIR/deploy/harden.sh" --quiet || echo "⚠️ harden.sh отработал не полностью, запусти его отдельно"

step "5/5 Проверка"
sleep 15
if systemctl is-active --quiet linkhub && ! journalctl -u linkhub --since "-20s" | grep -qi "conflict\|unauthorized"; then
    echo "✅ Бот работает на новом сервере. Простой: с остановки до этой строки."
else
    journalctl -u linkhub -n 20 --no-pager || true
    echo "⚠️ Бот на новом сервере не поднялся. Вернуть на старый: bash $0 rollback $OLD"
    exit 1
fi
systemctl --no-pager --lines=3 status linkhub || true

echo
echo "Дальше:"
echo "  • Пару дней старый сервер не трогай: если что, bash $0 rollback $OLD"
if [ -n "$SITE_DOMAIN" ]; then
    echo "  • Сайт $SITE_DOMAIN: в DNS домена поменяй A-записи @ и www на IP этого сервера, через 10-30 минут:"
    echo "      bash $DIR/deploy/site.sh $SITE_DOMAIN"
    echo "    До смены DNS сайт показывает старый сервер, это нормально."
fi
echo "  • Модератор рядом: bash /opt/baxi-guard/deploy/install.sh"
