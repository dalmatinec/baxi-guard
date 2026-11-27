#!/usr/bin/env bash
# Обновить модератора до свежей версии из GitHub:
#   bash /opt/baxi-guard/deploy/update.sh
set -euo pipefail
cd /opt/baxi-guard
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
cp deploy/baxi-guard.service /etc/systemd/system/baxi-guard.service
systemctl daemon-reload
systemctl restart baxi-guard
sleep 2
systemctl --no-pager --lines=5 status baxi-guard || true
