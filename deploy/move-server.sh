#!/usr/bin/env bash
# Переезд на новый сервер: всё, что крутится на старом, переносится сюда как было.
# Запускать на НОВОМ сервере от root:
#   bash move-server.sh 5.230.135.125        # IP старого сервера
#
# Что переносит (что найдёт на старом): Бакси (/opt/linkhub) с копиями клиентов и бэкапами,
# модератора (/opt/baxi-guard), тестовый стенд (/opt/baxi-test, /opt/baxi-guard-test), сайт (Caddy).
# Базы, .env с токенами и настройки копируются сервер-сервер напрямую, на экран ничего не выводится.
#
# Порядок: сначала копия на ходу (боты работают), потом вопрос «переносить?», потом короткая остановка
# на старом (обычно меньше минуты), докопирование, запуск здесь. На старом всё остаётся на месте,
# только выключено: откат одной командой (скрипт напечатает её в конце).
set -euo pipefail

OLD="${1:-}"
[ -n "$OLD" ] || { echo "Укажи IP старого сервера: bash $0 1.2.3.4"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@$OLD")
CANDIDATES=(/opt/linkhub /opt/baxi-guard /opt/baxi-test /opt/baxi-guard-test)
PATTERN='^(linkhub|baxi)'

step() { echo; echo "== $*"; }

# ---------- 0. доступ к старому серверу ----------
if [ ! -f /root/.ssh/id_ed25519 ]; then
    ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/id_ed25519
fi
if ! "${SSH[@]}" true 2>/dev/null; then
    echo "Нет доступа к старому серверу по ключу. Сделай один раз:"
    echo
    echo "1) Скопируй эту строку (это открытый ключ, его можно показывать):"
    echo
    cat /root/.ssh/id_ed25519.pub
    echo
    echo "2) На СТАРОМ сервере выполни (вставь строку вместо КЛЮЧ, в одинарных кавычках):"
    echo "   mkdir -p ~/.ssh && echo 'КЛЮЧ' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    echo
    echo "3) Запусти этот скрипт ещё раз: bash $0 $OLD"
    exit 1
fi
echo "✅ Старый сервер $OLD доступен"

# ---------- 1. что есть на старом ----------
step "Смотрю, что есть на старом сервере"
DIRS=()
for d in "${CANDIDATES[@]}"; do
    if "${SSH[@]}" test -d "$d"; then DIRS+=("$d"); echo "  папка $d"; fi
done
[ ${#DIRS[@]} -gt 0 ] || { echo "Ничего не нашёл на старом сервере"; exit 1; }
UNIT_FILES=$("${SSH[@]}" "ls /etc/systemd/system/ | grep -E '$PATTERN' | grep -E '\.(service|timer)$'" || true)
# что сейчас запущено и включено: здесь включим и запустим ровно это
RUNNING=$("${SSH[@]}" "systemctl list-units --all --plain --no-legend 'linkhub*' 'baxi*' | awk '\$3==\"active\" {print \$1}'" || true)
ALL_UNITS=$("${SSH[@]}" "systemctl list-units --all --plain --no-legend 'linkhub*' 'baxi*' | awk '{print \$1}'" || true)
HAS_CADDY=0
"${SSH[@]}" test -f /etc/caddy/Caddyfile && HAS_CADDY=1
echo "  сервисы: $(echo "$RUNNING" | xargs)"
[ "$HAS_CADDY" = 1 ] && echo "  сайт на Caddy"

# ---------- 2. подготовка нового ----------
step "Готовлю этот сервер (пакеты, подкачка)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip rsync sqlite3 ca-certificates >/dev/null
"${SSH[@]}" "command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync >/dev/null)"
if ! swapon --show --noheadings | grep -q .; then
    fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-linkhub-swap.conf && sysctl -q -w vm.swappiness=10
    echo "✅ Подкачка 4 ГБ"
fi

# юниты systemd и пользователи, от которых работают боты (до копирования: rsync отдаёт файлы им)
for f in $UNIT_FILES; do
    rsync -a "root@$OLD:/etc/systemd/system/$f" /etc/systemd/system/
done
for u in $(cat /etc/systemd/system/linkhub* /etc/systemd/system/baxi* 2>/dev/null | sed -n 's/^User=//p' | sort -u); do
    id "$u" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$u"
done
echo "✅ Сервисы и пользователи"

copy_all() {
    for d in "${DIRS[@]}"; do
        mkdir -p "$d"
        rsync -a --delete --exclude '.venv/' --exclude '__pycache__/' "root@$OLD:$d/" "$d/"
    done
    if [ "$HAS_CADDY" = 1 ]; then
        mkdir -p /etc/caddy /var/www
        rsync -a "root@$OLD:/etc/caddy/" /etc/caddy/
        if "${SSH[@]}" test -d /var/www/site; then
            rsync -a --delete "root@$OLD:/var/www/site/" /var/www/site/
        fi
    fi
    return 0
}

# ---------- 3. копия на ходу ----------
step "Копирую на ходу (боты на старом пока работают)"
copy_all
for d in "${DIRS[@]}"; do echo "  $d: $(du -sh "$d" | cut -f1)"; done

step "Ставлю библиотеки (пару минут)"
for d in "${DIRS[@]}"; do
    if [ -f "$d/requirements.txt" ]; then
        [ -d "$d/.venv" ] || python3 -m venv "$d/.venv"
        "$d/.venv/bin/pip" install -q --upgrade pip
        "$d/.venv/bin/pip" install -q -r "$d/requirements.txt"
        echo "  ✅ $d"
    fi
done
if [ "$HAS_CADDY" = 1 ] && ! command -v caddy >/dev/null; then
    apt-get install -y -qq caddy >/dev/null && echo "  ✅ caddy"
fi

# ---------- 4. переключение ----------
echo
echo "Всё готово к переключению. Сейчас боты на старом сервере остановятся (обычно меньше минуты),"
echo "докопирую свежие базы и запущу всё здесь."
read -rp "Переключать? [y/N] " ok
[ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Отменено. Старый сервер работает как работал."; exit 0; }

step "Останавливаю на старом"
# сначала сторож: иначе он сам запустит остановленного бота, и два бота на одном токене мешают друг другу
"${SSH[@]}" "systemctl disable --now linkhub-health.timer 2>/dev/null || true"
for u in $ALL_UNITS; do
    "${SSH[@]}" "systemctl disable --now '$u' 2>/dev/null || systemctl stop '$u' 2>/dev/null || true"
done
"${SSH[@]}" "systemctl list-units --plain --no-legend 'linkhub*' 'baxi*' | awk '\$3==\"active\"' | grep -q . && exit 1 || exit 0" \
    || { echo "⚠️ На старом что-то ещё работает, проверь: systemctl list-units 'linkhub*' 'baxi*'"; exit 1; }
echo "✅ На старом всё остановлено и не запустится после перезагрузки"

step "Докопирую свежие базы"
copy_all
for d in "${DIRS[@]}"; do
    for db in "$d"/data/*.db; do
        [ -f "$db" ] || continue
        r=$(sqlite3 "$db" "PRAGMA integrity_check;" 2>&1 | head -1)
        echo "  $(basename "$d")/$(basename "$db"): $r"
    done
done

step "Запускаю здесь"
systemctl daemon-reload
for u in $RUNNING; do
    systemctl enable "$u" >/dev/null 2>&1 || true
    systemctl restart "$u" || echo "  ⚠️ $u не запустился"
done
if [ -f /opt/linkhub/deploy/harden.sh ]; then
    bash /opt/linkhub/deploy/harden.sh --quiet || true   # логи, автозапуск, сторож
fi
if [ "$HAS_CADDY" = 1 ]; then
    systemctl enable caddy >/dev/null 2>&1 || true
    systemctl restart caddy || true
fi
sleep 8

step "Итог"
for u in $RUNNING; do
    if systemctl is-active -q "$u"; then echo "✅ $u"; else echo "❌ $u: journalctl -u $u -n 50"; fi
done
NEW_IP=$(hostname -I | awk '{print $1}')
echo
[ "$HAS_CADDY" = 1 ] && echo "Сайт: в DNS домена поменяй A-записи @ и www на $NEW_IP. Сертификат Caddy выпустит сам."
echo "Проверь ботов в Telegram. Если что-то не так, откат на старый сервер:"
echo "  здесь:   systemctl disable --now linkhub-health.timer $(echo "$RUNNING" | xargs)"
echo "  на старом: systemctl enable --now $(echo "$RUNNING" | xargs) linkhub-health.timer"
echo "Старый сервер не трогай хотя бы пару дней, потом можно не продлевать."
