#!/usr/bin/env bash
# Защита нового сервера под Бакси и Бакси Гарда. Запускать от root один раз, повторный запуск ничего не ломает:
#   bash /opt/baxi-guard/deploy/server.sh
#   bash /opt/baxi-guard/deploy/server.sh --ssh-keys   # плюс вход по SSH только по ключу (если ключ уже есть)
#
# Что делает:
#   1. файрвол: снаружи открыты только SSH, 80 и 443 (сайт). API ботов слушают 127.0.0.1, их и так не видно
#   2. fail2ban: перебор пароля SSH, бан IP на час после 5 ошибок
#   3. автоматические обновления безопасности Ubuntu
#   4. swap 4 ГБ (если нет), логи не больше 300 МБ, часовой пояс Алматы, точное время
#   5. ядро: при зависании перезагрузка через 10 секунд, мягкая подкачка
#   6. памяти не хватило: убивается самый жадный процесс, а не сервер встаёт
# Боты, их папки и сервисы скрипт не трогает.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }
# любая ошибка: показать, где остановились, а не молча выйти
trap 'echo "❌ Остановился на строке $LINENO: $BASH_COMMAND"' ERR
KEYS_ONLY=0
[ "${1:-}" = "--ssh-keys" ] && KEYS_ONLY=1
ok() { echo "✅ $*"; }

export DEBIAN_FRONTEND=noninteractive
echo "== Пакеты"
apt-get update -qq
apt-get install -y -qq ufw fail2ban python3-systemd unattended-upgrades python3-venv python3-pip git rsync sqlite3 \
    curl ca-certificates >/dev/null
ok "пакеты"

# ---------- 1. файрвол ----------
# порт SSH: тот, через который ты сейчас подключён (надёжнее всего), иначе из настроек sshd, иначе 22.
# В Ubuntu 24.04 sshd запускается по запросу, и «sshd -T» без него падает: поэтому не обязателен
SSH_PORT=$(echo "${SSH_CONNECTION:-}" | awk '{print $4}')
[ -n "$SSH_PORT" ] || SSH_PORT=$( { sshd -T 2>/dev/null || true; } | awk '/^port / {print $2; exit}')
SSH_PORT=${SSH_PORT:-22}
ufw --force default deny incoming >/dev/null
ufw --force default allow outgoing >/dev/null
ufw limit "$SSH_PORT/tcp" comment "ssh" >/dev/null   # не больше 6 подключений за 30 секунд с одного IP
ufw allow 80/tcp comment "site" >/dev/null
ufw allow 443/tcp comment "site" >/dev/null
if ufw --force enable >/dev/null 2>&1; then
    ok "файрвол: открыты SSH ($SSH_PORT), 80, 443"
else
    echo "⚠️ файрвол не включился (ядро сервера без iptables?), иду дальше без него"
fi

# ---------- 2. fail2ban ----------
cat > /etc/fail2ban/jail.d/baxi.local <<CONF
[sshd]
enabled = true
port = $SSH_PORT
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
CONF
systemctl enable fail2ban >/dev/null 2>&1 || true
if systemctl restart fail2ban 2>/dev/null; then
    ok "fail2ban: 5 неверных паролей SSH, бан на час"
else
    echo "⚠️ fail2ban не запустился, смотри: journalctl -u fail2ban -n 20. Иду дальше"
fi

# ---------- 3. обновления безопасности ----------
cat > /etc/apt/apt.conf.d/20auto-upgrades <<CONF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
# перезагрузку после обновлений не делаем сами: боты перезапускать только осознанно
sed -i 's|^//\?\s*Unattended-Upgrade::Automatic-Reboot .*|Unattended-Upgrade::Automatic-Reboot "false";|' \
    /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null || true
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
ok "обновления безопасности ставятся сами"

# ---------- 4. swap, логи, время ----------
if ! swapon --show --noheadings | grep -q .; then
    fallocate -l 4G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
    chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "swap 4 ГБ"
else
    ok "swap уже есть: $(swapon --show=SIZE --noheadings | head -1)"
fi
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/baxi.conf <<CONF
[Journal]
SystemMaxUse=300M
MaxRetentionSec=1month
CONF
systemctl restart systemd-journald || true
timedatectl set-timezone Asia/Almaty 2>/dev/null || true
timedatectl set-ntp true 2>/dev/null || true
ok "логи до 300 МБ и месяц, время Алматы, синхронизация времени"

# ---------- 5. ядро ----------
cat > /etc/sysctl.d/99-baxi.conf <<CONF
# подкачка только на крайний случай
vm.swappiness = 10
# ядро зависло: перезагрузка через 10 секунд, боты поднимутся сами
kernel.panic = 10
kernel.panic_on_oops = 1
# защита от SYN-флуда и подмены адресов
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
CONF
sysctl -q --system 2>/dev/null || true
ok "ядро: автоперезагрузка при зависании, защита сети"

# ---------- 6. нехватка памяти ----------
# у каждого бота свой потолок памяти в сервисе (MemoryMax), а это последняя линия: systemd-oomd
if systemctl list-unit-files systemd-oomd.service >/dev/null 2>&1; then
    systemctl enable --now systemd-oomd >/dev/null 2>&1 || true
    ok "systemd-oomd: при нехватке памяти убивает самый жадный процесс"
fi

# ---------- SSH ----------
if [ "$KEYS_ONLY" = 1 ]; then
    if [ -s /root/.ssh/authorized_keys ]; then
        mkdir -p /etc/ssh/sshd_config.d
        printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
            > /etc/ssh/sshd_config.d/10-baxi.conf
        sshd -t && systemctl reload ssh 2>/dev/null || systemctl reload sshd
        ok "SSH: вход только по ключу"
    else
        echo "⚠️ В /root/.ssh/authorized_keys нет ключа: вход по паролю оставлен, иначе закроешь себе доступ"
    fi
else
    echo "ℹ️ Вход по SSH по паролю оставлен. Когда добавишь ключ: bash $0 --ssh-keys"
fi

echo
echo "Сервер: $(nproc) ядра, память $(free -h | awk 'NR==2 {print $2}'), диск свободно $(df -h / | awk 'NR==2 {print $4}')"
echo "Дальше: переезд Бакси (deploy/move-baxi.sh) и модератор (deploy/install.sh)."
