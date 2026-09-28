#!/usr/bin/env bash
# infra/backup/restore_test.sh <arquivo.archive.gz> — prova que o backup restaura (TIE-35).
#
# Restaura num Mongo DESCARTÁVEL (RESTORE_URI) e compara a contagem de cada
# collection com o gabarito gravado no dump (.counts.json). Backup não testado
# não é backup. Nunca aponte RESTORE_URI para produção: usa --drop.
set -uo pipefail

arq=${1:?uso: restore_test.sh <arquivo.archive.gz>}
: "${RESTORE_URI:?defina RESTORE_URI apontando para um Mongo DESCARTÁVEL (nunca o de produção)}"
MONGO_DB=${MONGO_DB:-trends}
gabarito="${arq%.archive.gz}.counts.json"
[ -f "$gabarito" ] || { echo "sem gabarito: $gabarito" >&2; exit 2; }

if ! mongorestore --uri="$RESTORE_URI" --archive="$arq" --gzip --drop --nsInclude="${MONGO_DB}.*" >/dev/null 2>&1; then
  echo "FALHOU: mongorestore"
  exit 1
fi

obtido=$(mongosh "$RESTORE_URI" --quiet --eval "const d = db.getSiblingDB('$MONGO_DB'); const o = {}; d.getCollectionNames().forEach(c => o[c] = d.getCollection(c).countDocuments()); print(JSON.stringify(o))")

# JSON plano → "chave valor" por linha
plano() { tr -d '{} "\n' | tr ',' '\n' | sed 's/:/ /' | grep -v '^$'; }

divergencias=0
total=0
while read -r colecao esperado; do
  restaurado=$(printf '%s' "$obtido" | plano | awk -v c="$colecao" '$1 == c {print $2}')
  total=$((total + esperado))
  if [ "${restaurado:-ausente}" != "$esperado" ]; then
    echo "DIVERGE $colecao: esperado $esperado, restaurado ${restaurado:-ausente}"
    divergencias=$((divergencias + 1))
  else
    echo "  ok $colecao: $esperado"
  fi
done < <(plano <"$gabarito")

if [ "$divergencias" -gt 0 ]; then
  echo "FALHOU: $divergencias collection(s) divergente(s)"
  exit 1
fi
echo "OK: restauração conferida ($total documentos)"
