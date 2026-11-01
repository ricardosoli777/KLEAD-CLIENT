"""
Persistencia PostgreSQL para eventos brutos de webhook e formularios Meta.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from services import pg_pool


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta_form_submissions (
    id uuid PRIMARY KEY,
    lead_id uuid NULL,
    instancia_id varchar(128) NOT NULL,
    origem varchar(64) NOT NULL,
    page_id varchar(128) NOT NULL DEFAULT '',
    form_id varchar(128) NOT NULL DEFAULT '',
    leadgen_id varchar(128) NOT NULL DEFAULT '',
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id uuid PRIMARY KEY,
    provider varchar(64) NOT NULL,
    instancia_id varchar(128) NOT NULL,
    event_type varchar(128) NOT NULL,
    dedupe_key varchar(255) NULL,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    processed_at timestamptz NULL,
    created_at timestamptz NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS webhook_events_dedupe_uidx
ON webhook_events (dedupe_key)
WHERE dedupe_key IS NOT NULL AND dedupe_key <> '';

CREATE INDEX IF NOT EXISTS meta_form_submissions_leadgen_idx
ON meta_form_submissions (leadgen_id);

CREATE INDEX IF NOT EXISTS webhook_events_provider_created_idx
ON webhook_events (provider, created_at DESC);
"""


def _pg_settings() -> dict:
    host = os.getenv("CRM_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("CRM_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("CRM_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("CRM_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    # Fallback fixo em "klead" — nunca cair no banco do Evolution GO (EVO_PG_DB).
    dbname = os.getenv("CRM_PG_DB") or "klead"
    return {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}


def pg_configured() -> bool:
    cfg = _pg_settings()
    return bool(cfg["host"] and cfg["password"] and cfg["dbname"])


def _connect():
    cfg = _pg_settings()
    return pg_pool.get_connection(cfg)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_schema() -> tuple[bool, str]:
    if not pg_configured():
        return False, "postgres_not_configured"
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def record_webhook_event(provider: str, instancia_id: str, event_type: str, payload: dict, dedupe_key: str = "") -> tuple[bool, str]:
    from psycopg2.extras import Json

    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    safe_payload = payload if isinstance(payload, dict) else {"raw": payload}
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO webhook_events (
                id, provider, instancia_id, event_type, dedupe_key, payload_json, processed_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND dedupe_key <> '' DO NOTHING
            """,
            (
                str(uuid.uuid4()),
                provider or "meta",
                instancia_id or "",
                event_type or "",
                dedupe_key or _json_hash(safe_payload),
                Json(safe_payload),
                _now(),
                _now(),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def record_meta_form_submission(
    lead_id: str,
    instancia_id: str,
    origem: str,
    page_id: str,
    form_id: str,
    leadgen_id: str,
    payload: dict,
) -> tuple[bool, str]:
    from psycopg2.extras import Json

    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO meta_form_submissions (
                id, lead_id, instancia_id, origem, page_id, form_id, leadgen_id, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                lead_id or None,
                instancia_id or "",
                origem or "",
                page_id or "",
                form_id or "",
                leadgen_id or "",
                Json(payload if isinstance(payload, dict) else {"raw": payload}),
                _now(),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def find_leadgen_id_by_phone(phone: str) -> str:
    """Busca o leadgen_id mais recente de uma submissão de formulário Meta para esse telefone.

    Usado para cruzar leads cadastrados manualmente (sem leadgen_id) com um formulário
    que a Meta já recebeu anteriormente para o mesmo número, evitando reenviar 'Lead' via
    CAPI para alguém que a Meta já contabilizou nativamente.
    """
    phone = (phone or "").strip()
    if not phone:
        return ""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT leadgen_id FROM meta_form_submissions
            WHERE leadgen_id != '' AND payload_json->>'telefone' = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (phone,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return str(row[0]) if row and row[0] else ""
    except Exception:
        return ""


def get_form_submissions_with_phone() -> list[tuple[str, str]]:
    """Retorna pares (telefone, leadgen_id) de meta_form_submissions para backfill."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT payload_json->>'telefone', leadgen_id
            FROM meta_form_submissions
            WHERE leadgen_id != ''
              AND payload_json->>'telefone' IS NOT NULL
              AND payload_json->>'telefone' != ''
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [(str(r[0]), str(r[1])) for r in rows if r[0] and r[1]]
    except Exception:
        return []
