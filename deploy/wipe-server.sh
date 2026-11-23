#!/usr/bin/env bash
# Снести с сервера всё, что касается Бакси и модератора: боевой Бакси (/opt/linkhub) с копиями клиентов,
# модератор (/opt/baxi-guard), тестовый стенд (/opt/baxi-test, /opt/baxi-guard-test), их сервисы,
# пользователей, базы, логи, кеш pip, подкачку. Сайт (Caddy) по отдельному вопросу.
# Запускать от root на СТАРОМ сервере:
#   bash wipe-server.sh
# Сначала показывает, что найдено, и сносит только после того, как напишешь СНЕСТИ. Отменить нельзя.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }

DIRS=()
for d in /opt/linkhub /opt/baxi-guard /opt/baxi-test /opt/baxi-guard-test; do [ -e "$d" ] && DIRS+=("$d"); done
UNITS=$(systemctl list-units --all --plain --no-legend 'linkhub*' 'baxi*' | awk '{print $1}' || true)
UNIT_FILES=$(ls /etc/systemd/system/ | grep -E '^(linkhub|baxi)' || true)
USERS=()
for u in linkhub baxi-guard baxitest guardtest; do id "$u" >/dev/null 2>&1 && USERS+=("$u"); done
CLIENTS=$(find /opt/linkhub/instances -mindepth 1 -maxdepth 1 ! -name '.*' 2>/dev/null | wc -l || true)

echo "Сервер: $(hostname), IP $(hostname -I | awk '{print $1}')"
echo "Сейчас: память $(free -h | awk 'NR==2 {print $3 " из " $2}'), диск свободно $(df -h / | awk 'NR==2 {print $4}')"
echo
echo "Будет снесено:"
for d in "${DIRS[@]}"; do echo "  папка $d ($(du -sh "$d" 2>/dev/null | cut -f1))"; done
for u in $UNITS; do echo "  сервис $u"; done
if [ ${#USERS[@]} -gt 0 ]; then echo "  пользователи: ${USERS[*]}"; fi
if [ "$CLIENTS" -gt 0 ]; then echo "  ⚠️ копий клиентов Бакси: $CLIENTS (их базы тоже удалятся)"; fi
if swapon --show=NAME --noheadings | grep -qx /swapfile; then echo "  подкачка /swapfile ($(du -h /swapfile | cut -f1))"; fi
echo "  логи systemd, кеш pip и apt"
if [ ${#DIRS[@]} -eq 0 ] && [ -z "$UNITS" ]; then echo "  (ничего от ботов не найдено)"; fi
SITE=0
if [ -f /etc/caddy/Caddyfile ]; then
    echo
    read -rp "Есть сайт на Caddy. Снести и его? [y/N] " s
    if [ "$s" = "y" ] || [ "$s" = "Y" ]; then SITE=1; fi
fi
echo
read -rp "Отменить будет нельзя. Напиши СНЕСТИ, чтобы удалить: " ok
[ "$ok" = "СНЕСТИ" ] || { echo "Отменено, ничего не тронуто."; exit 0; }

echo
echo "== Останавливаю"
systemctl disable --now linkhub-health.timer 2>/dev/null || true   # сторож первым, иначе поднимет ботов
for u in $UNITS; do systemctl disable --now "$u" 2>/dev/null || systemctl stop "$u" 2>/dev/null || true; done
for f in $UNIT_FILES; do rm -rf "/etc/systemd/system/$f"; done
rm -rf /etc/systemd/system/multi-user.target.wants/linkhub* /etc/systemd/system/multi-user.target.wants/baxi* \
       /etc/systemd/system/timers.target.wants/linkhub*
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true
echo "✅ Сервисы остановлены и убраны"

echo "== Удаляю файлы и базы"
for d in "${DIRS[@]}"; do rm -rf "$d"; done
for u in "${USERS[@]}"; do userdel "$u" 2>/dev/null || true; done
rm -f /etc/cron.daily/linkhub-cleanup /etc/systemd/journald.conf.d/linkhub.conf
if [ "$SITE" = 1 ]; then
    systemctl disable --now caddy 2>/dev/null || true
    apt-get purge -y -qq caddy >/dev/null 2>&1 || true
    rm -rf /etc/caddy /var/www/site /var/lib/caddy
    echo "✅ Сайт снесён"
fi
echo "✅ Папки, базы, пользователи"

echo "== Чищу кеш, логи и подкачку"
if swapon --show=NAME --noheadings | grep -qx /swapfile; then
    swapoff /swapfile && rm -f /swapfile
    sed -i '\#^/swapfile #d' /etc/fstab
fi
rm -f /etc/sysctl.d/99-linkhub-swap.conf
rm -rf /root/.cache/pip
apt-get clean -qq 2>/dev/null || true
systemctl restart systemd-journald
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
echo "✅ Готово"
echo
echo "Теперь: память $(free -h | awk 'NR==2 {print $3 " из " $2}'), диск свободно $(df -h / | awk 'NR==2 {print $4}')"
echo "Боты здесь больше не запущены, токены свободны для нового сервера."
