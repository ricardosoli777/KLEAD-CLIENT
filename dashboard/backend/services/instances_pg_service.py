import json
import logging
import os

from services import pg_pool

logger = logging.getLogger("klead.instances_pg")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instances (
    instance_id varchar(128) PRIMARY KEY,
    api_url text NOT NULL DEFAULT '',
    api_key text NOT NULL DEFAULT '',
    openai_key text NOT NULL DEFAULT '',
    prompt text NOT NULL DEFAULT '',
    contacts jsonb NOT NULL DEFAULT '[]',
    provider varchar(32) NOT NULL DEFAULT 'evolution',
    meta_token text NOT NULL DEFAULT '',
    meta_app_secret text NOT NULL DEFAULT '',
    meta_capi_access_token text NOT NULL DEFAULT '',
    phone_number_id varchar(128) NOT NULL DEFAULT '',
    template_name varchar(255) NOT NULL DEFAULT '',
    meta_page_id varchar(128) NOT NULL DEFAULT '',
    meta_page_access_token text NOT NULL DEFAULT '',
    meta_capi_enabled boolean NOT NULL DEFAULT false,
    meta_capi_event_source_id varchar(128) NOT NULL DEFAULT '',
    meta_capi_test_event_code varchar(128) NOT NULL DEFAULT '',
    meta_ad_account_id varchar(64) NOT NULL DEFAULT '',
    meta_page_subscribed boolean NOT NULL DEFAULT false,
    meta_waba_subscribed boolean NOT NULL DEFAULT false,
    inbound_ai_enabled boolean NOT NULL DEFAULT true,
    qualification_prompt text NOT NULL DEFAULT '',
    max_ai_turns integer NOT NULL DEFAULT 8,
    ai_response_delay integer NOT NULL DEFAULT 3,
    auto_handoff_keywords jsonb NOT NULL DEFAULT '[]',
    model varchar(128) NOT NULL DEFAULT 'openai/gpt-oss-120b',
    llm_model varchar(128) NOT NULL DEFAULT 'openai/gpt-oss-120b',
    llm_provider varchar(32) NOT NULL DEFAULT 'groq',
    llm_api_key text NOT NULL DEFAULT '',
    key_openai text NOT NULL DEFAULT '',
    key_anthropic text NOT NULL DEFAULT '',
    key_groq text NOT NULL DEFAULT '',
    followup_enabled boolean NOT NULL DEFAULT true,
    followup_delay_hours integer NOT NULL DEFAULT 24,
    followup_max integer NOT NULL DEFAULT 3,
    scoring_criteria text NOT NULL DEFAULT '',
    evo_instance_name varchar(128) NOT NULL DEFAULT '',
    auto_release_minutes integer NOT NULL DEFAULT 5,
    tts_enabled boolean NOT NULL DEFAULT false,
    webhook_secret varchar(64) NOT NULL DEFAULT '',
    auto_suggest_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE instances ADD COLUMN IF NOT EXISTS webhook_secret varchar(64) NOT NULL DEFAULT '';
ALTER TABLE instances ADD COLUMN IF NOT EXISTS meta_waba_subscribed boolean NOT NULL DEFAULT false;
ALTER TABLE instances ADD COLUMN IF NOT EXISTS auto_suggest_enabled boolean NOT NULL DEFAULT true;
CREATE UNIQUE INDEX IF NOT EXISTS instances_instance_id_lower_uidx
    ON instances (lower(instance_id));
"""

_COLUMNS = [
    "api_url", "api_key", "openai_key", "prompt", "contacts",
    "provider", "meta_token", "meta_app_secret", "meta_capi_access_token",
    "phone_number_id", "template_name", "meta_page_id", "meta_page_access_token",
    "meta_capi_enabled", "meta_capi_event_source_id", "meta_capi_test_event_code",
    "meta_ad_account_id", "meta_page_subscribed", "meta_waba_subscribed", "inbound_ai_enabled",
    "qualification_prompt", "max_ai_turns", "ai_response_delay", "auto_handoff_keywords",
    "model", "llm_model", "llm_provider", "llm_api_key",
    "key_openai", "key_anthropic", "key_groq",
    "followup_enabled", "followup_delay_hours", "followup_max",
    "scoring_criteria", "evo_instance_name", "auto_release_minutes", "tts_enabled",
    "webhook_secret", "auto_suggest_enabled",
]

_JSONB_COLUMNS = {"contacts", "auto_handoff_keywords"}

_DEFAULTS: dict = {
    "api_url": "", "api_key": "", "openai_key": "", "prompt": "", "contacts": [],
    "provider": "evolution", "meta_token": "", "meta_app_secret": "",
    "meta_capi_access_token": "", "phone_number_id": "", "template_name": "",
    "meta_page_id": "", "meta_page_access_token": "", "meta_capi_enabled": False,
    "meta_capi_event_source_id": "", "meta_capi_test_event_code": "",
    "meta_ad_account_id": "", "meta_page_subscribed": False, "meta_waba_subscribed": False,
    "inbound_ai_enabled": True, "qualification_prompt": "",
    "max_ai_turns": 8, "ai_response_delay": 3, "auto_handoff_keywords": [],
    "model": "openai/gpt-oss-120b", "llm_model": "openai/gpt-oss-120b", "llm_provider": "groq",
    "llm_api_key": "", "key_openai": "", "key_anthropic": "", "key_groq": "",
    "followup_enabled": True, "followup_delay_hours": 24, "followup_max": 3,
    "scoring_criteria": "", "evo_instance_name": "", "auto_release_minutes": 5,
    "tts_enabled": False, "webhook_secret": "", "auto_suggest_enabled": True,
}


def _pg_settings() -> dict:
    host = os.getenv("INST_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("INST_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("INST_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("INST_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    # Fallback fixo em "klead": EVO_PG_DB aponta pro banco do Evolution GO e já
    # causou dados do app gravados lá quando INST_PG_DB não estava definida.
    dbname = os.getenv("INST_PG_DB") or "klead"
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


def ping() -> tuple[bool, str]:
    """Health check barato (SELECT 1). NUNCA roda DDL — ensure_schema é só no startup."""
    if not pg_configured():
        return False, "postgres_not_configured"
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc)


def _cfg_to_params(cfg: dict) -> list:
    params = []
    for col in _COLUMNS:
        val = cfg.get(col, _DEFAULTS.get(col, ""))
        if col in _JSONB_COLUMNS:
            params.append(json.dumps(val if isinstance(val, (list, dict)) else _DEFAULTS.get(col, [])))
        else:
            params.append(val if val is not None else _DEFAULTS.get(col, ""))
    return params


def _row_to_cfg(row: tuple) -> dict:
    cfg = {}
    for i, col in enumerate(_COLUMNS):
        val = row[i]
        if col in _JSONB_COLUMNS:
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:
                    val = _DEFAULTS.get(col, [])
            elif val is None:
                val = _DEFAULTS.get(col, [])
        elif val is None:
            val = _DEFAULTS.get(col, "")
        cfg[col] = val
    return cfg


def _build_upsert_sql() -> str:
    cols = ", ".join(_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_COLUMNS))
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in _COLUMNS) + ", updated_at = now()"
    return (
        f"INSERT INTO instances (instance_id, {cols}, updated_at) "
        f"VALUES (%s, {placeholders}, now()) "
        f"ON CONFLICT (instance_id) DO UPDATE SET {updates}"
    )


def upsert_instance(instance_id: str, cfg: dict) -> tuple[bool, str]:
    """Upsert a single instance. cfg values for sensitive fields are Fernet-encrypted."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(_build_upsert_sql(), [instance_id] + _cfg_to_params(cfg))
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        logger.warning(f"[instances_pg] upsert {instance_id} falhou: {exc}")
        return False, str(exc)


def delete_instance(instance_id: str) -> tuple[bool, str]:
    """Remove an instance row from Postgres."""
    if not pg_configured():
        return False, "postgres_not_configured"
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM instances WHERE instance_id = %s", (instance_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        logger.warning(f"[instances_pg] delete {instance_id} falhou: {exc}")
        return False, str(exc)


def load_all() -> tuple[bool, dict | str]:
    """Load all instances. Returns (True, {instance_id: cfg_dict}) or (False, error_str)."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        col_list = ", ".join(["instance_id"] + _COLUMNS)
        cur.execute(f"SELECT {col_list} FROM instances ORDER BY instance_id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = {}
        for row in rows:
            iid = row[0]
            result[iid] = _row_to_cfg(row[1:])
        return True, result
    except Exception as exc:
        logger.warning(f"[instances_pg] load_all falhou: {exc}")
        return False, str(exc)


def sync_all(configs: dict) -> tuple[bool, str]:
    """
    Full sync: upsert all instances in configs, delete rows no longer present.
    configs: {instance_id: cfg_dict} — sensitive values are Fernet-encrypted.
    """
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        upsert_sql = _build_upsert_sql()
        for iid, cfg in configs.items():
            cur.execute(upsert_sql, [iid] + _cfg_to_params(cfg))
        if configs:
            ids_ph = ", ".join(["%s"] * len(configs))
            cur.execute(f"DELETE FROM instances WHERE instance_id NOT IN ({ids_ph})", list(configs.keys()))
        else:
            cur.execute("SELECT COUNT(*) FROM instances")
            count = cur.fetchone()[0]
            if count > 0:
                logger.warning(
                    f"[instances_pg] sync_all: configs vazio mas PG tem {count} linhas — delete abortado"
                )
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        logger.warning(f"[instances_pg] sync_all falhou: {exc}")
        return False, str(exc)
