"""Persistência aditiva de aliases de identidade do WhatsApp.

O cache serve somente ao resolvedor em shadow mode. Este módulo nunca move,
renomeia ou funde conversas.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from typing import Any

from services import pg_pool


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_identity_aliases (
    id bigserial PRIMARY KEY,
    instancia_id varchar(128) NOT NULL,
    conversation_key varchar(180) NOT NULL,
    identity_type varchar(32) NOT NULL,
    identity_value varchar(255) NOT NULL,
    provider varchar(32) NOT NULL DEFAULT '',
    confidence smallint NOT NULL DEFAULT 0,
    source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (instancia_id, identity_type, identity_value, conversation_key)
);
CREATE INDEX IF NOT EXISTS conversation_identity_alias_lookup_idx
ON conversation_identity_aliases (instancia_id, identity_type, identity_value);
CREATE INDEX IF NOT EXISTS conversation_identity_alias_conversation_idx
ON conversation_identity_aliases (instancia_id, conversation_key);
"""

_cache_lock = threading.RLock()
_alias_cache: dict[str, dict[tuple[str, str], set[str]]] = {}
_schema_lock = threading.Lock()
_schema_ready = False


def _pg_settings() -> dict:
    host = os.getenv("CONV_PG_HOST") or os.getenv("CRM_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("CONV_PG_PORT") or os.getenv("CRM_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("CONV_PG_USER") or os.getenv("CRM_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("CONV_PG_PASSWORD") or os.getenv("CRM_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    dbname = os.getenv("CONV_PG_DB") or os.getenv("CRM_PG_DB") or "klead"
    return {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}


def pg_configured() -> bool:
    cfg = _pg_settings()
    return bool(cfg["host"] and cfg["password"] and cfg["dbname"])


def enabled() -> bool:
    return os.getenv("IDENTITY_ALIAS_PERSIST_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _connect():
    return pg_pool.get_connection(_pg_settings())


def ensure_schema() -> tuple[bool, str]:
    global _schema_ready
    if not pg_configured():
        return False, "postgres_not_configured"
    if _schema_ready:
        return True, ""
    with _schema_lock:
        if _schema_ready:
            return True, ""
        try:
            conn = _connect()
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            conn.commit()
            cur.close()
            conn.close()
            _schema_ready = True
            return True, ""
        except Exception as exc:
            return False, str(exc)


def _classify(value: str) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if "@" not in raw:
        return None
    user, server = raw.rsplit("@", 1)
    user = user.split(":", 1)[0].strip()
    server = server.strip().lower()
    if not user:
        return None
    identity_type = {"s.whatsapp.net": "pn_jid", "lid": "lid_jid"}.get(server)
    if not identity_type:
        return None
    return identity_type, f"{user}@{server}"


def extract_evolution_aliases(source_meta: dict[str, Any] | None) -> list[tuple[str, str]]:
    identity = (source_meta or {}).get("evolution_identity") or {}
    values = list(identity.get("jid_aliases") or [])
    values.extend(identity.get(key) for key in ("pn_jid", "lid_jid", "raw_jid"))
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        classified = _classify(str(value or ""))
        if classified and classified not in seen:
            rows.append(classified)
            seen.add(classified)
    return rows


def _clean_meta_identity(identity_type: str, value: Any) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if identity_type == "phone":
        digits = "".join(ch for ch in raw if ch.isdigit())
        return (identity_type, digits) if digits else None
    if identity_type == "username":
        normalized = raw.removeprefix("@").strip().lower()
        return (identity_type, normalized) if normalized else None
    return identity_type, raw


def extract_meta_aliases(source_meta: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Extrai aliases de usuário; username comercial não entra aqui."""
    meta = source_meta or {}
    profile = meta.get("profile") or {}
    candidates = (
        ("phone", meta.get("wa_id")),
        ("bsuid", meta.get("user_id") or meta.get("new_user_id")),
        ("bsuid", meta.get("old_user_id")),
        ("parent_bsuid", meta.get("parent_user_id")),
        ("username", meta.get("username") or profile.get("username")),
        ("username", meta.get("previous_username")),
    )
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for identity_type, value in candidates:
        cleaned = _clean_meta_identity(identity_type, value)
        if cleaned and cleaned not in seen:
            rows.append(cleaned)
            seen.add(cleaned)
    return rows


def _replace_cache(rows: list[tuple[str, str, str, str]]) -> None:
    rebuilt: dict[str, dict[tuple[str, str], set[str]]] = defaultdict(lambda: defaultdict(set))
    for instance_id, conversation_key, identity_type, identity_value in rows:
        rebuilt[instance_id][(identity_type, identity_value)].add(conversation_key)
    with _cache_lock:
        _alias_cache.clear()
        _alias_cache.update({iid: dict(values) for iid, values in rebuilt.items()})


def load_cache() -> tuple[bool, int | str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT instancia_id, conversation_key, identity_type, identity_value "
            "FROM conversation_identity_aliases"
        )
        rows = list(cur.fetchall())
        cur.close()
        conn.close()
        _replace_cache(rows)
        return True, len(rows)
    except Exception as exc:
        return False, str(exc)


def record_evolution_observation(
    instance_id: str,
    conversation_key: str,
    source_meta: dict[str, Any] | None,
) -> tuple[bool, int | str]:
    aliases = extract_evolution_aliases(source_meta)
    if not aliases:
        return True, 0
    if not enabled():
        return False, "identity_alias_persist_disabled"
    ok, detail = ensure_schema()
    if not ok:
        return False, detail

    identity = (source_meta or {}).get("evolution_identity") or {}
    mapped_pair = bool(identity.get("mapping_observed")) and {
        kind for kind, _ in aliases
    } >= {"pn_jid", "lid_jid"}
    return _record_alias_rows(
        instance_id=instance_id,
        conversation_key=conversation_key,
        aliases=aliases,
        provider=str((source_meta or {}).get("provider") or "evolution"),
        confidence=100 if mapped_pair else 70,
        metadata={"channel": (source_meta or {}).get("channel", "")},
    )


def record_meta_observation(
    instance_id: str,
    conversation_key: str,
    source_meta: dict[str, Any] | None,
    *,
    confidence: int = 90,
) -> tuple[bool, int | str]:
    aliases = extract_meta_aliases(source_meta)
    if not aliases:
        return True, 0
    return _record_alias_rows(
        instance_id=instance_id,
        conversation_key=conversation_key,
        aliases=aliases,
        provider="meta",
        confidence=confidence,
        metadata={
            "channel": (source_meta or {}).get("channel", "whatsapp_cloud_api"),
            "waba_id": (source_meta or {}).get("waba_id", ""),
            "phone_number_id": (source_meta or {}).get("phone_number_id", ""),
        },
    )


def _record_alias_rows(
    *,
    instance_id: str,
    conversation_key: str,
    aliases: list[tuple[str, str]],
    provider: str,
    confidence: int,
    metadata: dict[str, Any],
) -> tuple[bool, int | str]:
    if not aliases:
        return True, 0
    if not enabled():
        return False, "identity_alias_persist_disabled"
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        from psycopg2.extras import Json

        conn = _connect()
        cur = conn.cursor()
        for identity_type, identity_value in aliases:
            cur.execute(
                """
                INSERT INTO conversation_identity_aliases
                    (instancia_id, conversation_key, identity_type, identity_value,
                     provider, confidence, source_metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (instancia_id, identity_type, identity_value, conversation_key)
                DO UPDATE SET
                    provider = EXCLUDED.provider,
                    confidence = GREATEST(conversation_identity_aliases.confidence, EXCLUDED.confidence),
                    source_metadata = conversation_identity_aliases.source_metadata || EXCLUDED.source_metadata,
                    last_seen_at = now()
                """,
                (
                    instance_id, conversation_key, identity_type, identity_value,
                    str(provider or "")[:32], max(0, min(int(confidence), 100)), Json(metadata),
                ),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        return False, str(exc)

    with _cache_lock:
        instance_cache = _alias_cache.setdefault(instance_id, {})
        for identity_type, identity_value in aliases:
            instance_cache.setdefault((identity_type, identity_value), set()).add(conversation_key)
    return True, len(aliases)


def find_matches(
    instance_id: str,
    current_key: str,
    source_meta: dict[str, Any] | None,
) -> dict[str, list[str]]:
    matches: dict[str, set[str]] = defaultdict(set)
    with _cache_lock:
        instance_cache = _alias_cache.get(instance_id, {})
        for alias in extract_evolution_aliases(source_meta):
            for conversation_key in instance_cache.get(alias, set()):
                if conversation_key != current_key:
                    matches[conversation_key].add(alias[1])
    return {key: sorted(values) for key, values in matches.items()}


def purge_instance(instance_id: str) -> tuple[bool, str]:
    with _cache_lock:
        _alias_cache.pop(instance_id, None)
    if not pg_configured():
        return True, ""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM conversation_identity_aliases WHERE instancia_id = %s", (instance_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)
