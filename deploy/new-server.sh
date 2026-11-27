#!/usr/bin/env bash
# Новый сервер с нуля: защита сервера, НОВЫЙ Бакси (новый токен, новый ключ нейросети) и Бакси Гард, связанные
# между собой. Старый Бакси на старом сервере не трогается и продолжает работать.
# Запускать от root:
#   git clone -b guard-v1 https://github.com/dalmatinec/baxi-guard /opt/baxi-guard
#   bash /opt/baxi-guard/deploy/new-server.sh
#
# Спросит (ввод токенов и ключей не виден на экране):
#   • токен нового Бакси от @BotFather, твой Telegram ID, ключ нейросети (DeepSeek, можно пусто)
#   • токен Бакси Гарда
# Повторный запуск безопасен: что уже стоит, не переустанавливается, .env не перезаписываются.
# Ветка Бакси: BAXI_BRANCH=... (по умолчанию guard-api, в ней связь с модератором)
set -euo pipefail
trap 'echo "❌ Остановился на строке $LINENO: $BASH_COMMAND"' ERR
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }
BAXI_BRANCH=${BAXI_BRANCH:-guard-api}
HERE=$(cd "$(dirname "$0")" && pwd)
step() { echo; echo "======== $*"; }

step "1/5 Защита сервера"
bash "$HERE/server.sh"

step "2/5 Бакси (ветка $BAXI_BRANCH)"
if [ -d /opt/linkhub/.git ]; then
    echo "Бакси уже есть в /opt/linkhub, код не трогаю"
else
    git clone -q -b "$BAXI_BRANCH" https://github.com/dalmatinec/baxi /opt/linkhub
fi
if [ -f /opt/linkhub/.env ]; then
    echo "Настройки Бакси уже есть (/opt/linkhub/.env), токен не спрашиваю"
fi
bash /opt/linkhub/deploy/install.sh

step "3/5 Бакси Гард"
bash "$HERE/install.sh"

step "4/5 Связь ботов (общий ключ, только 127.0.0.1)"
bash /opt/linkhub/deploy/guard-link.sh

step "5/5 Проверка"
sleep 3
for unit in linkhub baxi-guard linkhub-health.timer baxi-guard-health.timer; do
    printf '%-26s %s\n' "$unit" "$(systemctl is-active "$unit" 2>/dev/null || true)"
done
free -h | awk 'NR==2 {print "Память: занято " $3 " из " $2}'
echo
echo "Готово. Дальше:"
echo "  • добавь нового Бакси и Гарда в тестовую группу, Гарду дай права админа"
echo "  • Бакси: /admin в личке, одобри группу. Гард: /start в личке, ⚙️ Мои чаты"
echo "  • обновлять: bash /opt/linkhub/deploy/update.sh и bash /opt/baxi-guard/deploy/update.sh"
