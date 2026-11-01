#!/bin/bash
# Backup diário dos bancos Postgres de produção do KLEAD.
#
# Cópia versionada do script que roda na VPS em /usr/local/bin/klead-pg-backup.sh,
# disparado pelo timer systemd klead-pg-backup.timer (diário às 03:00, Persistent=true,
# RandomizedDelaySec=300). Se editar aqui, replique na VPS — e vice-versa.
#
# Bancos cobertos: klead, evolution_go_auth e evolution_go_users (este último é o
# banco ATIVO do Evolution GO — ver incidente de 2026-07-05; nunca deixar de fora).
set -euo pipefail
umask 077
KLEAD_CONFIG_FILE="${KLEAD_CONFIG_FILE:-/etc/klead/klead.env}"
CONFIG_HELPER="/usr/local/lib/klead/install-env.py"
if [ ! -f "$CONFIG_HELPER" ]; then
    CONFIG_HELPER="$(dirname "$(readlink -f "$0")")/install-env.py"
fi
if [ -f "$KLEAD_CONFIG_FILE" ]; then
    CONFIG_EXPORTS=$(python3 "$CONFIG_HELPER" export "$KLEAD_CONFIG_FILE")
    eval "$CONFIG_EXPORTS"
    unset CONFIG_EXPORTS
fi
BACKUP_DIR="/var/backups/klead-postgres"
RETENTION_DAYS=14
PGC=$(docker ps --filter name=postgres_postgres -q | head -n1)
TS=$(date +%Y%m%d_%H%M%S)

if [ -z "$PGC" ] && [ -z "${EVO_PG_HOST:-}" ]; then
    echo "ERRO: container postgres_postgres nao encontrado" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

for DB in "${INST_PG_DB:-klead}" "${CRM_PG_DB:-klead}" evolution_go_auth "${EVO_PG_DB:-evolution_go_users}"; do
    # CRM e instâncias compartilham klead por padrão: não repetir o dump.
    [ -f "$BACKUP_DIR/${DB}_${TS}.dump" ] && continue
    if [ -n "${EVO_PG_HOST:-}" ]; then
        docker run --rm --network "${TRAEFIK_NETWORK:-klead}" \
            -e PGPASSWORD="$EVO_PG_PASSWORD" postgres:14-alpine \
            pg_dump -h "$EVO_PG_HOST" -p "${EVO_PG_PORT:-5432}" \
            -U "${EVO_PG_USER:-postgres}" -Fc "$DB" > "$BACKUP_DIR/${DB}_${TS}.dump.tmp"
    else
        docker exec "$PGC" pg_dump -U postgres -Fc "$DB" > "$BACKUP_DIR/${DB}_${TS}.dump.tmp"
    fi
    mv "$BACKUP_DIR/${DB}_${TS}.dump.tmp" "$BACKUP_DIR/${DB}_${TS}.dump"
    echo "Backup ok: ${DB}_${TS}.dump ($(du -h "$BACKUP_DIR/${DB}_${TS}.dump" | cut -f1))"
done

find "$BACKUP_DIR" -name "*.dump" -mtime "+$RETENTION_DAYS" -delete

# Cópia off-site (Cloudflare R2 via AWS CLI): backups na mesma máquina do banco
# morrem junto com ela. Requer:
#   - /root/.aws/credentials com profile [r2] (chaves do token R2, chmod 600)
#   - /root/.aws/klead-r2.env com R2_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
# Fail-soft de propósito: sem credenciais/endpoint, o backup local segue valendo.
R2_BUCKET="klead-backups"
R2_PREFIX="postgres"
OFFSITE_RETENTION_DAYS=30
AWS_BIN=$(command -v aws || echo /snap/bin/aws)
[ -f /root/.aws/klead-r2.env ] && . /root/.aws/klead-r2.env
if [ -x "$AWS_BIN" ] && [ -n "${R2_ENDPOINT:-}" ] && [ -f /root/.aws/credentials ]; then
    OFFSITE_OK=1
    for F in "$BACKUP_DIR"/*_"${TS}".dump; do
        "$AWS_BIN" s3 cp "$F" "s3://${R2_BUCKET}/${R2_PREFIX}/$(basename "$F")" \
            --endpoint-url "$R2_ENDPOINT" --profile r2 --only-show-errors || OFFSITE_OK=0
    done
    if [ "$OFFSITE_OK" = 1 ]; then
        echo "Off-site ok: dumps ${TS} enviados para s3://${R2_BUCKET}/${R2_PREFIX}/"
        # Retenção no bucket: apaga objetos com mais de OFFSITE_RETENTION_DAYS dias.
        CUTOFF=$(date -d "-${OFFSITE_RETENTION_DAYS} days" +%Y-%m-%dT%H:%M:%S)
        "$AWS_BIN" s3api list-objects-v2 --bucket "$R2_BUCKET" --prefix "${R2_PREFIX}/" \
            --endpoint-url "$R2_ENDPOINT" --profile r2 \
            --query "Contents[?LastModified<'${CUTOFF}'].Key" --output text 2>/dev/null \
        | tr '\t' '\n' | { grep -v '^None$' || true; } | while read -r KEY; do
            [ -n "$KEY" ] && "$AWS_BIN" s3 rm "s3://${R2_BUCKET}/${KEY}" \
                --endpoint-url "$R2_ENDPOINT" --profile r2 --only-show-errors || true
        done
    else
        echo "ERRO: envio off-site falhou — backup local preservado" >&2
    fi
else
    echo "AVISO: R2 nao configurado (credenciais/endpoint ausentes) — backup ficou APENAS local"
fi
