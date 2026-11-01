"""
settings_pg_service — JSONB key-value store no Postgres para configurações globais:
  'users'       → dict de usuários (klead_settings)
  'assignments' → estado de atribuições
  'ai_config'   → configuração global de IA (valores Fernet-criptografados)
"""

import json
import logging
import os

from services import pg_pool

logger = logging.getLogger("klead.settings_pg")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS klead_settings (
    key varchar(64) PRIMARY KEY,
    value jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


def _pg_settings() -> dict:
    host = os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("EVO_PG_PASSWORD", "")
    # Não usa CRM_PG_DB de propósito: configurações/usuários/auditoria vivem no
    # banco "klead" (via INST_PG_DB), independente de onde o CRM (crm_pg_service)
    # estiver apontando — evita que uma mudança em CRM_PG_DB arraste dados de
    # autenticação junto sem querer.
    dbname = os.getenv("INST_PG_DB", "klead")
    return {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}


def pg_configured() -> bool:
    cfg = _pg_settings()
    return bool(cfg["host"] and cfg["password"] and cfg["dbname"])


def _connect():
    cfg = _pg_settings()
    return pg_pool.get_connection(cfg)


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


def save_setting(key: str, value: dict | list) -> tuple[bool, str]:
    """Upsert a single setting by key."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO klead_settings (key, value, updated_at)
            VALUES (%s, %s::jsonb, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        logger.warning(f"[settings_pg] save '{key}' falhou: {exc}")
        return False, str(exc)


def delete_setting(key: str) -> tuple[bool, str]:
    """Delete a setting by key (no-op if not found)."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM klead_settings WHERE key = %s", (key,))
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        logger.warning(f"[settings_pg] delete '{key}' falhou: {exc}")
        return False, str(exc)


def load_setting(key: str) -> tuple[bool, dict | list | str]:
    """Load a setting by key. Returns (True, value) or (False, error_str)."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT value FROM klead_settings WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row is None:
            return False, f"chave '{key}' não encontrada"
        return True, row[0]
    except Exception as exc:
        logger.warning(f"[settings_pg] load '{key}' falhou: {exc}")
        return False, str(exc)
