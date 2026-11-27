#!/usr/bin/env bash
# Обновить модератора до свежей версии из GitHub:
#   bash /opt/baxi-guard/deploy/update.sh
set -euo pipefail
cd /opt/baxi-guard
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
cp deploy/baxi-guard.service /etc/systemd/system/baxi-guard.service
cp deploy/baxi-guard-health.service deploy/baxi-guard-health.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now baxi-guard-health.timer >/dev/null
systemctl restart baxi-guard
sleep 2
systemctl --no-pager --lines=5 status baxi-guard || true
