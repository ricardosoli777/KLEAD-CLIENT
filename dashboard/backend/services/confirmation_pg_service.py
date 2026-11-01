"""Persistência PostgreSQL aditiva para confirmações do CRM."""

from __future__ import annotations

import uuid
import secrets
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json, RealDictCursor

from services import crm_pg_service
from services.confirmation_service import (
    LEGACY_DEFAULT_MESSAGES,
    LEGACY_DEFAULT_OPTIONS,
    PRE_AUTO_REPLY_DEFAULT_OPTIONS,
    default_templates,
    normalize_options,
    validate_message_text,
)
from services.confirmation_service import match_reply


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crm_confirmation_templates (
    id uuid PRIMARY KEY,
    owner_key varchar(255) NOT NULL,
    template_key varchar(64) NOT NULL DEFAULT '',
    name varchar(255) NOT NULL,
    message_text text NOT NULL,
    options_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    active boolean NOT NULL DEFAULT true,
    is_system_default boolean NOT NULL DEFAULT false,
    created_by varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_confirmation_templates_owner_key_uidx
ON crm_confirmation_templates (owner_key, template_key)
WHERE template_key <> '';
CREATE INDEX IF NOT EXISTS crm_confirmation_templates_owner_idx
ON crm_confirmation_templates (owner_key, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS crm_confirmations (
    id uuid PRIMARY KEY,
    public_token varchar(128) UNIQUE,
    owner_key varchar(255) NOT NULL,
    -- Sem FK intencionalmente: crm_pg_service.sync_snapshot apaga e reinsere
    -- crm_leads na mesma sincronização. O vínculo lógico é preservado pelo UUID
    -- e a exclusão real é feita explicitamente por delete_for_lead().
    lead_id uuid NOT NULL,
    instancia_id varchar(128) NOT NULL,
    conversation_key varchar(180) NOT NULL,
    provider varchar(32) NOT NULL,
    delivery_format varchar(32) NOT NULL DEFAULT 'text',
    status varchar(32) NOT NULL,
    message_text text NOT NULL,
    rendered_text text NOT NULL,
    options_snapshot_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    provider_message_id varchar(255) NOT NULL DEFAULT '',
    public_link_enabled boolean NOT NULL DEFAULT false,
    expires_at timestamptz NULL,
    sent_at timestamptz NULL,
    responded_at timestamptz NULL,
    response_option_id varchar(64) NOT NULL DEFAULT '',
    response_message_id varchar(255) NOT NULL DEFAULT '',
    scheduled_for timestamptz NULL,
    auto_reply_status varchar(32) NOT NULL DEFAULT '',
    auto_reply_error text NOT NULL DEFAULT '',
    error_code varchar(128) NOT NULL DEFAULT '',
    error_detail text NOT NULL DEFAULT '',
    created_by varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS crm_confirmations_lead_idx
ON crm_confirmations (lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS crm_confirmations_pending_idx
ON crm_confirmations (instancia_id, conversation_key, status, created_at DESC);
ALTER TABLE crm_confirmations ADD COLUMN IF NOT EXISTS delivery_format varchar(32) NOT NULL DEFAULT 'text';
ALTER TABLE crm_confirmations ADD COLUMN IF NOT EXISTS scheduled_for timestamptz;
ALTER TABLE crm_confirmations ADD COLUMN IF NOT EXISTS auto_reply_status varchar(32) NOT NULL DEFAULT '';
ALTER TABLE crm_confirmations ADD COLUMN IF NOT EXISTS auto_reply_error text NOT NULL DEFAULT '';
ALTER TABLE crm_confirmations DROP CONSTRAINT IF EXISTS crm_confirmations_lead_id_fkey;
CREATE UNIQUE INDEX IF NOT EXISTS crm_confirmations_owner_schedule_uidx
ON crm_confirmations (owner_key, scheduled_for) WHERE scheduled_for IS NOT NULL;
"""


def _now():
    return datetime.now(timezone.utc)


def ensure_schema() -> tuple[bool, str]:
    if not crm_pg_service.pg_configured():
        return False, "postgres_not_configured"
    try:
        conn = crm_pg_service._connect()
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _row_dict(row) -> dict:
    result = dict(row)
    result["id"] = str(result["id"])
    return result


def seed_default_templates(owner_key: str, created_by: str = "") -> None:
    if not owner_key:
        raise ValueError("Tenant obrigatório.")
    ok, detail = ensure_schema()
    if not ok:
        raise RuntimeError(detail)
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    now = _now()
    for template in default_templates():
        cur.execute(
            """
            INSERT INTO crm_confirmation_templates
                (id, owner_key, template_key, name, message_text, options_json,
                 active, is_system_default, created_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, true, true, %s, %s, %s)
            ON CONFLICT (owner_key, template_key) WHERE template_key <> '' DO NOTHING
            """,
            (str(uuid.uuid4()), owner_key, template["key"], template["name"],
             template["message_text"], Json(template["options"]), created_by, now, now),
        )
        # Atualiza somente defaults intocados. Texto e opções têm condições
        # independentes para preservar qualquer personalização já realizada.
        cur.execute(
            """UPDATE crm_confirmation_templates SET message_text=%s, updated_at=%s
            WHERE owner_key=%s AND template_key=%s AND is_system_default=true
              AND message_text=%s""",
            (template["message_text"], now, owner_key, template["key"], LEGACY_DEFAULT_MESSAGES[template["key"]]),
        )
        cur.execute(
            """UPDATE crm_confirmation_templates SET options_json=%s, updated_at=%s
            WHERE owner_key=%s AND template_key=%s AND is_system_default=true
              AND (options_json=%s OR options_json=%s)""",
            (Json(template["options"]), now, owner_key, template["key"], Json(LEGACY_DEFAULT_OPTIONS), Json(PRE_AUTO_REPLY_DEFAULT_OPTIONS)),
        )
    conn.commit()
    cur.close()
    conn.close()


def list_templates(owner_key: str, include_inactive: bool = False) -> list[dict]:
    seed_default_templates(owner_key)
    conn = crm_pg_service._connect()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = "SELECT * FROM crm_confirmation_templates WHERE owner_key = %s"
    params: list = [owner_key]
    if not include_inactive:
        query += " AND active = true"
    query += " ORDER BY is_system_default DESC, created_at ASC"
    cur.execute(query, params)
    rows = [_row_dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def get_template(template_id: str, owner_key: str) -> dict | None:
    conn = crm_pg_service._connect()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM crm_confirmation_templates WHERE id = %s AND owner_key = %s", (template_id, owner_key))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return _row_dict(row) if row else None


def create_template(owner_key: str, name: str, message_text: str, options: list[dict], created_by: str) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Nome do modelo é obrigatório.")
    text = validate_message_text(message_text)
    clean_options = normalize_options(options)
    template_id = str(uuid.uuid4())
    now = _now()
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO crm_confirmation_templates
        (id, owner_key, template_key, name, message_text, options_json, active,
         is_system_default, created_by, created_at, updated_at)
        VALUES (%s, %s, '', %s, %s, %s, true, false, %s, %s, %s)""",
        (template_id, owner_key, clean_name[:255], text, Json(clean_options), created_by, now, now),
    )
    conn.commit()
    cur.close()
    conn.close()
    return get_template(template_id, owner_key)


def update_template(template_id: str, owner_key: str, fields: dict) -> dict | None:
    current = get_template(template_id, owner_key)
    if not current:
        return None
    name = str(fields.get("name", current["name"])).strip()
    if not name:
        raise ValueError("Nome do modelo é obrigatório.")
    text = validate_message_text(fields.get("message_text", current["message_text"]))
    options = normalize_options(fields.get("options", current["options_json"]))
    active = bool(fields.get("active", current["active"]))
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        """UPDATE crm_confirmation_templates
        SET name=%s, message_text=%s, options_json=%s, active=%s, updated_at=%s
        WHERE id=%s AND owner_key=%s""",
        (name[:255], text, Json(options), active, _now(), template_id, owner_key),
    )
    conn.commit()
    cur.close()
    conn.close()
    return get_template(template_id, owner_key)


def deactivate_template(template_id: str, owner_key: str) -> bool:
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE crm_confirmation_templates SET active=false, updated_at=%s WHERE id=%s AND owner_key=%s",
        (_now(), template_id, owner_key),
    )
    changed = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return changed


def create_confirmation(
    *, owner_key: str, lead_id: str, instancia_id: str, conversation_key: str,
    provider: str, message_text: str, rendered_text: str, options: list[dict],
    created_by: str, public_link_enabled: bool = False, public_token: str | None = None,
    delivery_format: str = "text",
) -> dict:
    confirmation_id = str(uuid.uuid4())
    now = _now()
    clean_options = normalize_options(options)
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        """UPDATE crm_confirmations SET status='superseded', updated_at=%s
        WHERE instancia_id=%s AND conversation_key=%s AND status IN ('sending', 'sent')""",
        (now, instancia_id, conversation_key),
    )
    cur.execute(
        """INSERT INTO crm_confirmations
        (id, public_token, owner_key, lead_id, instancia_id, conversation_key,
         provider, delivery_format, status, message_text, rendered_text, options_snapshot_json,
         public_link_enabled, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'sending', %s, %s, %s, %s, %s, %s, %s)""",
        (confirmation_id, public_token, owner_key, lead_id, instancia_id, conversation_key,
         provider, delivery_format, validate_message_text(message_text), rendered_text,
         Json(clean_options), public_link_enabled, created_by, now, now),
    )
    if public_link_enabled:
        cur.execute("UPDATE crm_confirmations SET expires_at=%s WHERE id=%s", (now + timedelta(days=7), confirmation_id))
    conn.commit()
    cur.close()
    conn.close()
    return get_confirmation(confirmation_id, owner_key)


def generate_public_token() -> str:
    return secrets.token_urlsafe(32)


def get_public_confirmation(public_token: str) -> dict | None:
    if not public_token or len(public_token) > 128:
        return None
    conn = crm_pg_service._connect()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT id, status, options_snapshot_json, expires_at, response_option_id, scheduled_for
        FROM crm_confirmations WHERE public_token=%s AND public_link_enabled=true""",
        (public_token,),
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None
    result = _row_dict(row)
    result["expired"] = bool(result.get("expires_at") and result["expires_at"] <= _now())
    if result.get("status") not in {"sent", "responded"}:
        return None
    return result


def resolve_public_reply(public_token: str, option_id: str) -> dict | None:
    conn = crm_pg_service._connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM crm_confirmations WHERE public_token=%s AND public_link_enabled=true FOR UPDATE", (public_token,))
        row = cur.fetchone()
        if not row or (row.get("expires_at") and row["expires_at"] <= _now()):
            conn.rollback(); cur.close(); conn.close(); return None
        valid_ids = {item.get("id") for item in row["options_snapshot_json"]}
        if option_id not in valid_ids:
            conn.rollback(); cur.close(); conn.close(); return None
        if row["status"] == "responded":
            if row["response_option_id"] != option_id:
                raise ValueError("response_already_recorded")
            result = _row_dict(row)
            result["_response_created"] = False
            conn.rollback(); cur.close(); conn.close(); return result
        if row["status"] != "sent":
            conn.rollback(); cur.close(); conn.close(); return None
        now = _now()
        cur.execute(
            """UPDATE crm_confirmations SET status='responded', response_option_id=%s,
            responded_at=%s, updated_at=%s WHERE id=%s RETURNING *""",
            (option_id, now, now, row["id"]),
        )
        resolved = cur.fetchone(); conn.commit(); cur.close(); conn.close()
        result = _row_dict(resolved)
        result["_response_created"] = True
        return result
    except Exception:
        try: conn.rollback()
        finally: conn.close()
        raise


def get_confirmation(confirmation_id: str, owner_key: str) -> dict | None:
    conn = crm_pg_service._connect()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM crm_confirmations WHERE id=%s AND owner_key=%s", (confirmation_id, owner_key))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return _row_dict(row) if row else None


def schedule_public_confirmation(public_token: str, scheduled_for) -> dict | None:
    """Reserva um horário futuro após a escolha inequívoca de reagendamento."""
    if not scheduled_for or scheduled_for.tzinfo is None:
        raise ValueError("Informe data e horário com fuso horário.")
    now = _now()
    if scheduled_for <= now or scheduled_for > now + timedelta(days=180):
        raise ValueError("Escolha um horário futuro dentro dos próximos 180 dias.")
    conn = crm_pg_service._connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT * FROM crm_confirmations
            WHERE public_token=%s AND public_link_enabled=true FOR UPDATE""",
            (public_token,),
        )
        row = cur.fetchone()
        if not row or row.get("response_option_id") != "reschedule" or (row.get("expires_at") and row["expires_at"] <= now):
            conn.rollback(); cur.close(); conn.close(); return None
        if row.get("scheduled_for"):
            result = _row_dict(row)
            result["_schedule_created"] = False
            conn.rollback(); cur.close(); conn.close(); return result
        try:
            cur.execute(
                """UPDATE crm_confirmations SET scheduled_for=%s, updated_at=%s
                WHERE id=%s AND scheduled_for IS NULL RETURNING *""",
                (scheduled_for, now, row["id"]),
            )
            result = cur.fetchone()
            conn.commit(); cur.close(); conn.close()
            output = _row_dict(result) if result else None
            if output:
                output["_schedule_created"] = True
            return output
        except Exception as exc:
            if getattr(exc, "pgcode", "") == "23505":
                conn.rollback()
                raise ValueError("slot_unavailable") from exc
            raise
    except Exception:
        try: conn.rollback()
        finally: conn.close()
        raise


def list_confirmations(lead_id: str, owner_key: str, limit: int = 50) -> list[dict]:
    conn = crm_pg_service._connect()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM crm_confirmations WHERE lead_id=%s AND owner_key=%s ORDER BY created_at DESC LIMIT %s",
        (lead_id, owner_key, max(1, min(int(limit), 200))),
    )
    rows = [_row_dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def mark_confirmation_sent(confirmation_id: str, owner_key: str, provider_message_id: str = "") -> dict | None:
    now = _now()
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        """UPDATE crm_confirmations SET status='sent', provider_message_id=%s,
        sent_at=%s, updated_at=%s WHERE id=%s AND owner_key=%s AND status='sending'""",
        (provider_message_id, now, now, confirmation_id, owner_key),
    )
    conn.commit()
    cur.close()
    conn.close()
    return get_confirmation(confirmation_id, owner_key)


def mark_confirmation_failed(confirmation_id: str, owner_key: str, error_code: str, error_detail: str) -> dict | None:
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        """UPDATE crm_confirmations SET status='failed', error_code=%s,
        error_detail=%s, updated_at=%s WHERE id=%s AND owner_key=%s AND status='sending'""",
        (str(error_code)[:128], str(error_detail)[:1000], _now(), confirmation_id, owner_key),
    )
    conn.commit()
    cur.close()
    conn.close()
    return get_confirmation(confirmation_id, owner_key)


def mark_auto_reply_result(confirmation_id: str, sent: bool, error: str = "") -> None:
    if not crm_pg_service.pg_configured():
        return
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute(
        """UPDATE crm_confirmations SET auto_reply_status=%s, auto_reply_error=%s, updated_at=%s
        WHERE id=%s""",
        ("sent" if sent else "failed", str(error or "")[:1000], _now(), confirmation_id),
    )
    conn.commit(); cur.close(); conn.close()


def delete_for_lead(lead_id: str) -> int:
    """Remove confirmações somente na exclusão real do lead."""
    if not crm_pg_service.pg_configured():
        return 0
    conn = crm_pg_service._connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM crm_confirmations WHERE lead_id=%s", (lead_id,))
    deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    return deleted


def resolve_text_reply(instancia_id: str, conversation_key: str, text: str, response_message_id: str = "") -> dict | None:
    """Resolve somente quando há uma única confirmação pendente e match exato."""
    if not crm_pg_service.pg_configured():
        return None
    conn = crm_pg_service._connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT * FROM crm_confirmations
            WHERE instancia_id=%s AND conversation_key=%s AND status='sent'
              AND delivery_format='text'
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY created_at DESC LIMIT 2 FOR UPDATE""",
            (instancia_id, conversation_key),
        )
        rows = cur.fetchall()
        if len(rows) != 1:
            conn.rollback()
            cur.close()
            conn.close()
            return None
        row = rows[0]
        option_id = match_reply(text, row["options_snapshot_json"])
        if not option_id:
            conn.rollback()
            cur.close()
            conn.close()
            return None
        now = _now()
        cur.execute(
            """UPDATE crm_confirmations SET status='responded', response_option_id=%s,
            response_message_id=%s, responded_at=%s, updated_at=%s
            WHERE id=%s AND status='sent' RETURNING *""",
            (option_id, response_message_id, now, now, row["id"]),
        )
        resolved = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return _row_dict(resolved) if resolved else None
    except Exception:
        try:
            conn.rollback()
        finally:
            conn.close()
        raise
