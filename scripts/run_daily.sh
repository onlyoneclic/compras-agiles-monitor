#!/usr/bin/env bash
# Corrida diaria del monitor (alternativa a GitHub Actions, para cron local).
# Ejemplo de crontab (10:30 todos los días, hora de Chile):
#   30 10 * * * /ruta/compras-agiles-monitor/scripts/run_daily.sh >> /ruta/logs/cam.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

# Cargue .env si existe (solo define MP_TICKET / ML_TOKEN)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

python -m cam_monitor run --max-ca "${MAX_CA:-30}"
