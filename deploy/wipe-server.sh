#!/usr/bin/env bash
# Убрать с сервера модератора (Бакси Гард) и тестовый стенд. Боевой Бакси (/opt/linkhub, сервис linkhub,
# копии клиентов, сторож, подкачка, сайт) НЕ трогается никак.
# Запускать от root:
#   bash wipe-server.sh
# Удаляет: /opt/baxi-guard, /opt/baxi-test, /opt/baxi-guard-test, сервисы baxi-*, пользователей
# baxi-guard, baxitest, guardtest, кеш pip.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }

UNITS=$(systemctl list-units --all --plain --no-legend 'baxi*' | awk '{print $1}' | grep -E '^baxi' || true)
UNIT_FILES=$(ls /etc/systemd/system/ | grep -E '^baxi' || true)

for u in $UNITS; do systemctl disable --now "$u" 2>/dev/null || systemctl stop "$u" 2>/dev/null || true; done
for f in $UNIT_FILES; do rm -rf "/etc/systemd/system/$f"; done
rm -rf /etc/systemd/system/multi-user.target.wants/baxi*
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true

for d in /opt/baxi-guard /opt/baxi-test /opt/baxi-guard-test; do
    if [ -e "$d" ]; then rm -rf "$d"; echo "🗑 $d"; fi
done
for u in baxi-guard baxitest guardtest; do
    if id "$u" >/dev/null 2>&1; then userdel "$u" 2>/dev/null || true; fi
done
for u in $UNITS; do echo "🗑 сервис $u"; done
rm -rf /root/.cache/pip
sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

echo
echo "✅ Гард и тестовый стенд убраны, их токены свободны."
echo "Боевой Бакси: $(systemctl is-active linkhub 2>/dev/null || echo 'не найден')"
echo "Память: $(free -h | awk 'NR==2 {print $3 " из " $2}'), диск свободно $(df -h / | awk 'NR==2 {print $4}')"
