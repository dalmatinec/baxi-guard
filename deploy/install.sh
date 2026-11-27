#!/usr/bin/env bash
# Установка модератора на тот же сервер, где Бакси. Запускать от root:
#   git clone https://github.com/dalmatinec/baxi-guard /opt/baxi-guard
#   bash /opt/baxi-guard/deploy/install.sh
# При первом запуске спросит токен бота и твой Telegram ID, запишет в /opt/baxi-guard/.env.
# Ключ внутреннего API (BAXI_API_KEY) берёт из /opt/linkhub/.env, если он там уже есть, иначе создаёт новый.
set -euo pipefail
trap 'echo "❌ Остановился на строке $LINENO: $BASH_COMMAND"' ERR

DIR=/opt/baxi-guard
cd "$DIR" || { echo "Сначала: git clone https://github.com/dalmatinec/baxi-guard $DIR"; exit 1; }

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "Нужен Python 3.12 или новее, сейчас: $(python3 --version)"; exit 1
fi
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    apt-get update -qq && apt-get install -y -qq python3-venv
fi
id baxi-guard >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin baxi-guard

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

set_env() {  # set_env КЛЮЧ ЗНАЧЕНИЕ: заменить строку в .env или дописать
    local key="$1" value="$2"
    if grep -q "^$key=" .env; then
        KEY="$key" VALUE="$value" python3 - <<'PY'
import os, re
key, value = os.environ["KEY"], os.environ["VALUE"]
text = open(".env", encoding="utf-8").read()
text = re.sub(rf"^{re.escape(key)}=.*$", lambda _m: f"{key}={value}", text, flags=re.M)
open(".env", "w", encoding="utf-8").write(text)
PY
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

if [ ! -f .env ]; then
    cp .env.example .env
    while true; do
        read -rsp "Токен модератора от @BotFather (ввод скрыт): " TOKEN; echo
        TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')
        BAXI_TOKEN=$(grep '^BOT_TOKEN=' /opt/linkhub/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]"' || true)
        if [ -n "$BAXI_TOKEN" ] && [ "$TOKEN" = "$BAXI_TOKEN" ]; then
            echo "Это токен Бакси. Модератору нужен свой, отдельный бот от @BotFather"
            continue
        fi
        break
    done
    read -rp "Твой Telegram ID (несколько через запятую): " OWNERS
    set_env BOT_TOKEN "$TOKEN"
    set_env OWNER_IDS "$OWNERS"
fi

# общий ключ внутреннего API: одинаковый у Бакси и модератора
if ! grep -q '^BAXI_API_KEY=.\+' .env; then
    KEY=""
    if [ -f /opt/linkhub/.env ]; then
        KEY=$(grep '^BAXI_API_KEY=' /opt/linkhub/.env | head -1 | cut -d= -f2- || true)
    fi
    if [ -z "$KEY" ]; then
        KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
        echo "Создан новый ключ внутреннего API. Когда у Бакси появится API, скопируй строку BAXI_API_KEY"
        echo "из $DIR/.env в /opt/linkhub/.env (ключ на экран не выводится)."
    else
        echo "Ключ внутреннего API взят из /opt/linkhub/.env"
    fi
    set_env BAXI_API_KEY "$KEY"
fi

# ключ API будущей веб-админки (свой, на экран не выводится)
if ! grep -q '^WEB_API_KEY=.\+' .env; then
    set_env WEB_API_KEY "$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
fi

mkdir -p data
chown -R baxi-guard:baxi-guard data
chown baxi-guard:baxi-guard .env && chmod 600 .env

command -v sqlite3 >/dev/null || apt-get install -y -qq sqlite3 >/dev/null
cp deploy/baxi-guard.service /etc/systemd/system/baxi-guard.service
cp deploy/baxi-guard-health.service deploy/baxi-guard-health.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable baxi-guard >/dev/null
systemctl enable --now baxi-guard-health.timer >/dev/null   # сторож и копия базы раз в сутки
systemctl restart baxi-guard
sleep 3
systemctl --no-pager --lines=8 status baxi-guard || true
echo
echo "Готово. Логи: journalctl -u baxi-guard -f   Настройки: nano $DIR/.env, потом systemctl restart baxi-guard"
