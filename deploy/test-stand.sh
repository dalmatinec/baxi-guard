#!/usr/bin/env bash
# Тестовый стенд: копия Бакси из ветки с правками и модератор, на ТЕСТОВЫХ токенах.
# Боевой Бакси (/opt/linkhub, сервис linkhub) и боевой модератор (/opt/baxi-guard) скрипт не трогает:
# не читает и не меняет их файлы, кроме проверки, что тестовый токен не совпадает с боевым.
#
# Поставить (от root):
#   git clone -b guard-v1 https://github.com/dalmatinec/baxi-guard /opt/baxi-guard-test
#   bash /opt/baxi-guard-test/deploy/test-stand.sh install
#
#   bash /opt/baxi-guard-test/deploy/test-stand.sh update    # свежий код из веток и перезапуск
#   bash /opt/baxi-guard-test/deploy/test-stand.sh status    # живы ли оба
#   bash /opt/baxi-guard-test/deploy/test-stand.sh logs      # логи обоих (Ctrl+C выйти)
#   bash /opt/baxi-guard-test/deploy/test-stand.sh stop      # остановить оба
#   bash /opt/baxi-guard-test/deploy/test-stand.sh remove    # снести стенд целиком
#
# Ветки можно поменять: BAXI_BRANCH=... GUARD_BRANCH=... bash test-stand.sh update
set -euo pipefail

BAXI_DIR=/opt/baxi-test
GUARD_DIR=/opt/baxi-guard-test
BAXI_BRANCH=${BAXI_BRANCH:-guard-api}
GUARD_BRANCH=${GUARD_BRANCH:-guard-v1}
BAXI_SVC=baxi-test
GUARD_SVC=baxi-guard-test
BAXI_PORT=8720     # у боевых 8710 и 8711, тестовые на своих портах
GUARD_PORT=8721
PROD_ENVS=(/opt/linkhub/.env /opt/baxi-guard/.env)

[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }
# защита от опечаток: стенд никогда не живёт в боевых папках
for d in "$BAXI_DIR" "$GUARD_DIR"; do
    case "$d" in /opt/linkhub|/opt/linkhub/*|/opt/baxi-guard|/opt/baxi-guard/*) echo "Отказ: $d боевая папка"; exit 1;; esac
done

get_env() { grep "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- || true; }
set_env() {  # set_env ФАЙЛ КЛЮЧ ЗНАЧЕНИЕ
    FILE="$1" KEY="$2" VALUE="$3" python3 - <<'PY'
import os, re
path, key, value = os.environ["FILE"], os.environ["KEY"], os.environ["VALUE"]
text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
if re.search(rf"^{re.escape(key)}=", text, flags=re.M):
    text = re.sub(rf"^{re.escape(key)}=.*$", lambda _m: f"{key}={value}", text, flags=re.M)
else:
    text = text.rstrip("\n") + ("\n" if text else "") + f"{key}={value}\n"
open(path, "w", encoding="utf-8").write(text)
PY
}

is_prod_token() {  # тестовый токен не должен совпадать с боевым: два бота на одном токене ломают боевого
    local token="$1" f
    for f in "${PROD_ENVS[@]}"; do
        [ -f "$f" ] && [ "$(get_env BOT_TOKEN "$f")" = "$token" ] && return 0
    done
    return 1
}

ask_token() {  # ask_token "подсказка" -> печатает токен
    local prompt="$1" token
    while true; do
        read -rp "$prompt" token
        token=$(echo "$token" | tr -d '[:space:]')
        if ! [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then echo "Не похоже на токен, ещё раз" >&2; continue; fi
        if is_prod_token "$token"; then echo "Это токен БОЕВОГО бота. Нужен отдельный тестовый от @BotFather" >&2; continue; fi
        echo "$token"; return
    done
}

unit() {  # unit ИМЯ ОПИСАНИЕ ПАПКА ПОЛЬЗОВАТЕЛЬ МОДУЛЬ ПАМЯТЬ_HIGH ПАМЯТЬ_MAX
    cat > "/etc/systemd/system/$1.service" <<UNIT
# Тестовый стенд, ставится deploy/test-stand.sh из baxi-guard. Боевые сервисы не трогает.
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$4
Group=$4
WorkingDirectory=$3
ExecStart=$3/.venv/bin/python -m $5
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$3/data
MemoryHigh=$6
MemoryMax=$7
CPUQuota=50%
TasksMax=64
LimitNOFILE=4096
LogRateLimitIntervalSec=30s
LogRateLimitBurst=300

[Install]
WantedBy=multi-user.target
UNIT
}

venv() {  # venv ПАПКА
    [ -d "$1/.venv" ] || python3 -m venv "$1/.venv"
    "$1/.venv/bin/pip" install -q --upgrade pip
    "$1/.venv/bin/pip" install -q -r "$1/requirements.txt"
}

checkout() {  # checkout ПАПКА РЕПО ВЕТКА
    if [ -d "$1/.git" ]; then
        git -C "$1" fetch -q origin "$3"
        git -C "$1" checkout -q "$3" 2>/dev/null || git -C "$1" checkout -q -b "$3" "origin/$3"
        git -C "$1" reset -q --hard "origin/$3"
    else
        git clone -q -b "$3" "https://github.com/dalmatinec/$2" "$1"
    fi
}

cmd_install() {
    python3 -c "import venv, ensurepip" 2>/dev/null || { apt-get update -qq && apt-get install -y -qq python3-venv; }
    echo "== Код: Бакси из ветки $BAXI_BRANCH, модератор из ветки $GUARD_BRANCH"
    checkout "$BAXI_DIR" baxi "$BAXI_BRANCH"
    checkout "$GUARD_DIR" baxi-guard "$GUARD_BRANCH"
    id baxitest >/dev/null 2>&1 || useradd --system --home "$BAXI_DIR" --shell /usr/sbin/nologin baxitest
    id guardtest >/dev/null 2>&1 || useradd --system --home "$GUARD_DIR" --shell /usr/sbin/nologin guardtest
    echo "== Библиотеки (пару минут)"
    venv "$BAXI_DIR"
    venv "$GUARD_DIR"

    local benv="$BAXI_DIR/.env" genv="$GUARD_DIR/.env"
    if [ ! -f "$benv" ] || [ ! -f "$genv" ]; then
        echo "== Настройка. Нужны ДВА новых тестовых бота от @BotFather, не боевые"
        local btoken gtoken owners aikey
        btoken=$(ask_token "Токен ТЕСТОВОГО Бакси: ")
        gtoken=$(ask_token "Токен ТЕСТОВОГО модератора: ")
        [ "$btoken" != "$gtoken" ] || { echo "Токены одинаковые, нужны два разных бота"; exit 1; }
        read -rp "Твой Telegram ID (несколько через запятую): " owners
        read -rp "Ключ нейросети для тестового Бакси (можно пусто, тогда без ИИ): " aikey
        cp "$BAXI_DIR/.env.example" "$benv"
        cp "$GUARD_DIR/.env.example" "$genv"
        local key
        key=$(python3 -c 'import secrets; print(secrets.token_hex(24))')   # свой ключ стенда, не боевой
        set_env "$benv" BOT_TOKEN "$btoken"
        set_env "$benv" OWNER_IDS "$owners"
        set_env "$benv" AI_API_KEY "$aikey"
        set_env "$benv" DAILY_LIMIT_USD 0.10
        set_env "$benv" BAXI_API_KEY "$key"
        set_env "$benv" BAXI_API_PORT "$BAXI_PORT"
        set_env "$benv" GUARD_API_URL "http://127.0.0.1:$GUARD_PORT"
        set_env "$genv" BOT_TOKEN "$gtoken"
        set_env "$genv" OWNER_IDS "$owners"
        set_env "$genv" BAXI_API_KEY "$key"
        set_env "$genv" GUARD_API_PORT "$GUARD_PORT"
        set_env "$genv" BAXI_API_URL "http://127.0.0.1:$BAXI_PORT"
        set_env "$genv" BAXI_BOT_ID "${btoken%%:*}"
    fi
    # ключ API веб-админки (свой, на экран не выводится); у старого .env дописывается
    if ! grep -q '^WEB_API_KEY=.\+' "$genv"; then
        set_env "$genv" WEB_API_KEY "$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    fi
    mkdir -p "$BAXI_DIR/data" "$GUARD_DIR/data"
    chown -R baxitest:baxitest "$BAXI_DIR/data" && chown baxitest:baxitest "$benv" && chmod 600 "$benv"
    chown -R guardtest:guardtest "$GUARD_DIR/data" && chown guardtest:guardtest "$genv" && chmod 600 "$genv"

    unit "$BAXI_SVC" "Baxi TEST stand" "$BAXI_DIR" baxitest bot 260M 300M
    unit "$GUARD_SVC" "baxi-guard TEST stand" "$GUARD_DIR" guardtest guard 135M 150M
    systemctl daemon-reload
    systemctl enable "$BAXI_SVC" "$GUARD_SVC" >/dev/null
    systemctl restart "$BAXI_SVC" "$GUARD_SVC"
    sleep 4
    cmd_status
    echo
    echo "Готово. Добавь обоих тестовых ботов в ТЕСТОВУЮ группу, модератору дай права админа."
    echo "Тестовый Бакси: в /admin одобри группу. Модератор: /settings в личке."
}

cmd_update() {
    checkout "$BAXI_DIR" baxi "$BAXI_BRANCH"
    checkout "$GUARD_DIR" baxi-guard "$GUARD_BRANCH"
    venv "$BAXI_DIR"
    venv "$GUARD_DIR"
    systemctl restart "$BAXI_SVC" "$GUARD_SVC"
    sleep 4
    cmd_status
}

cmd_status() {
    local svc port
    for svc in "$BAXI_SVC" "$GUARD_SVC"; do
        if systemctl is-active -q "$svc"; then echo "✅ $svc работает"; else echo "❌ $svc не работает: journalctl -u $svc -n 50"; fi
    done
    # API поднимается после первых запросов к Telegram: ждём до 30 секунд
    for port in "$BAXI_PORT" "$GUARD_PORT"; do
        local i=0
        until ss -ltn 2>/dev/null | grep -q "127.0.0.1:$port " || [ $i -ge 30 ]; do sleep 1; i=$((i + 1)); done
        if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$port "; then echo "✅ API 127.0.0.1:$port"
        else echo "⚠️ API 127.0.0.1:$port не слушает, смотри: journalctl -u $BAXI_SVC -u $GUARD_SVC -n 80 | grep -i api"; fi
    done
    echo "Код: Бакси $(git -C "$BAXI_DIR" log -1 --format='%h %s' 2>/dev/null | cut -c1-70)"
    echo "     модератор $(git -C "$GUARD_DIR" log -1 --format='%h %s' 2>/dev/null | cut -c1-70)"
}

cmd_logs() { journalctl -u "$BAXI_SVC" -u "$GUARD_SVC" -f -n 50; }
cmd_stop() { systemctl stop "$BAXI_SVC" "$GUARD_SVC"; echo "Стенд остановлен"; }

cmd_remove() {
    read -rp "Снести тестовый стенд целиком ($BAXI_DIR, $GUARD_DIR, базы стенда)? Боевые боты не пострадают. [y/N] " ok
    [ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Отменено"; exit 0; }
    systemctl disable --now "$BAXI_SVC" "$GUARD_SVC" 2>/dev/null || true
    rm -f "/etc/systemd/system/$BAXI_SVC.service" "/etc/systemd/system/$GUARD_SVC.service"
    systemctl daemon-reload
    rm -rf "$BAXI_DIR" "$GUARD_DIR"
    userdel baxitest 2>/dev/null || true
    userdel guardtest 2>/dev/null || true
    echo "Стенд снесён"
}

case "${1:-install}" in
    install) cmd_install ;;
    update) cmd_update ;;
    status) cmd_status ;;
    logs) cmd_logs ;;
    stop) cmd_stop ;;
    remove) cmd_remove ;;
    *) echo "Команды: install | update | status | logs | stop | remove"; exit 1 ;;
esac
