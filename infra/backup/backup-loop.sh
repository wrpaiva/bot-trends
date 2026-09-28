#!/usr/bin/env bash
# infra/backup/backup-loop.sh — entrypoint do serviço `backup` do compose (TIE-35)
while true; do
  bash /backup/backup.sh || echo "[backup] ciclo falhou (registrado em backups; o check de saúde alerta)"
  sleep $((${BACKUP_INTERVAL_HOURS:-24} * 3600))
done
