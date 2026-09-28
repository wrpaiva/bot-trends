#!/usr/bin/env bash
# infra/backup/backup.sh — um ciclo de backup do MongoDB (TIE-35).
#
#   1. mongodump comprimido → $BACKUP_DIR/trends-<UTC>.archive.gz (atômico: .tmp + mv)
#   2. contagem de documentos por collection → trends-<UTC>.counts.json, tirada
#      da própria saída do mongodump: é o gabarito do restore_test.sh
#   3. BACKUP_UPLOAD_CMD opcional ({} = caminho do arquivo) para tirar da máquina
#   4. retenção: apaga os mais velhos que BACKUP_RETENTION_DAYS, mas sempre
#      mantém os BACKUP_KEEP_MIN mais recentes
#   5. registra o resultado em <db>.backups — o check de saúde (TIE-39) alerta
#      quando falha ou quando o último sucesso fica velho
#
# Saída != 0 em qualquer falha. Senha de URI nunca vai para log nem registro.
set -uo pipefail

: "${MONGO_URI:?defina MONGO_URI}"
: "${MONGO_DB:?defina MONGO_DB}"
BACKUP_DIR=${BACKUP_DIR:-/backups}
RETENCAO_DIAS=${BACKUP_RETENTION_DAYS:-14}
MANTER_MIN=${BACKUP_KEEP_MIN:-3}
REGISTRAR=${BACKUP_RECORD:-1}
UPLOAD=${BACKUP_UPLOAD_CMD:-}

mascara() { sed -E 's#(://[^:/@ ]*:)[^@ ]+@#\1***@#g'; }
log() { echo "[backup] $*" | mascara; }

ts=$(date -u +%Y%m%dT%H%M%SZ)
base="$BACKUP_DIR/trends-$ts"
arq="$base.archive.gz"
tmp="$arq.tmp"
errf=$(mktemp)
inicio=$(date +%s)

registra() { # <status> [erro]
  [ "$REGISTRAR" = "1" ] || return 0
  local status=$1 erro=${2:-} tamanho=0 contagens="{}"
  [ -f "$arq" ] && tamanho=$(stat -c %s "$arq")
  [ -f "$base.counts.json" ] && contagens=$(cat "$base.counts.json")
  # Sem senha, sem aspas/barras que quebrem o JS, uma linha só
  erro=$(printf '%s' "$erro" | mascara | tr -d "'\\\\" | tr '\n' ' ' | cut -c1-500)
  mongosh "$MONGO_URI" --quiet --eval "db.getSiblingDB('$MONGO_DB').backups.insertOne({ts: new Date(), status: '$status', file: '$(basename "$arq")', size_bytes: $tamanho, duration_s: $(($(date +%s) - inicio)), counts: $contagens, error: '$erro'})" \
    >/dev/null 2>&1 || log "aviso: não foi possível gravar o registro em $MONGO_DB.backups"
}

falha() {
  log "FALHOU: $1"
  rm -f "$tmp" "$errf"
  registra failed "$1"
  exit 1
}

mkdir -p "$BACKUP_DIR" || falha "não consegui criar $BACKUP_DIR"

# 1-2. dump + contagens
if ! mongodump --uri="$MONGO_URI" --db="$MONGO_DB" --archive="$tmp" --gzip 2>"$errf"; then
  falha "mongodump: $(grep -v '^[[:space:]]*$' "$errf" | tail -1)"
fi
mv "$tmp" "$arq"
# Formato real (mongodump 100.x): "done dumping `trends.metrics` (1220 documents)"
padrao='s/.*done dumping `?'"${MONGO_DB}"'\.([^` ]+)`? \(([0-9]+) documents?\).*/"\1": \2/p'
contagens=$(sed -nE "$padrao" "$errf" | paste -sd, -)
echo "{${contagens}}" >"$base.counts.json"
log "ok: $(basename "$arq") ($(stat -c %s "$arq") bytes) {${contagens}}"

# 3. upload opcional
if [ -n "$UPLOAD" ]; then
  cmd=${UPLOAD//\{\}/$arq}
  if ! bash -c "$cmd" 2>"$errf"; then
    falha "upload: $(grep -v '^[[:space:]]*$' "$errf" | tail -1)"
  fi
  log "upload ok"
fi

# 4. retenção — só arquivos com o nome exato do backup; timestamp UTC no nome
#    compara como string, sem depender de mtime
corte=$(date -u -d "-${RETENCAO_DIAS} days" +%Y%m%dT%H%M%SZ)
i=0
while IFS= read -r nome; do
  i=$((i + 1))
  [ "$i" -le "$MANTER_MIN" ] && continue
  t=${nome#trends-}
  t=${t%.archive.gz}
  if [[ "$t" < "$corte" ]]; then
    rm -f "$BACKUP_DIR/$nome" "$BACKUP_DIR/trends-$t.counts.json"
    log "retenção: removido $nome"
  fi
done < <(ls -1 "$BACKUP_DIR" | grep -E '^trends-[0-9]{8}T[0-9]{6}Z\.archive\.gz$' | sort -r)

# 5. registro
rm -f "$errf"
registra ok
