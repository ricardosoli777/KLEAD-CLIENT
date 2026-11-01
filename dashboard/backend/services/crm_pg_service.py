"""
Espelhamento gradual do CRM em PostgreSQL.

Fase 1 segura:
- crm.json continua como fonte principal
- cada snapshot salvo pode ser replicado ao Postgres
- validacao compara contagens antes do corte definitivo
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import pg_pool


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crm_columns (
    id varchar(64) PRIMARY KEY,
    name varchar(255) NOT NULL,
    color varchar(32) NOT NULL,
    sort_order integer NOT NULL,
    capi_event varchar(64) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
ALTER TABLE crm_columns ADD COLUMN IF NOT EXISTS capi_event varchar(64) NOT NULL DEFAULT '';
ALTER TABLE crm_columns ADD COLUMN IF NOT EXISTS instancia_id varchar(128) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS crm_columns_instancia_idx ON crm_columns (instancia_id);

CREATE TABLE IF NOT EXISTS crm_leads (
    id uuid PRIMARY KEY,
    instancia_id varchar(128) NOT NULL,
    nome varchar(255) NOT NULL,
    telefone varchar(64) NULL,
    bsuid varchar(180) NULL,
    parent_bsuid varchar(180) NULL,
    whatsapp_username varchar(80) NULL,
    phone_available boolean NOT NULL DEFAULT true,
    origem varchar(64) NOT NULL,
    campanha text NOT NULL DEFAULT '',
    email varchar(255) NOT NULL DEFAULT '',
    empresa varchar(255) NOT NULL DEFAULT '',
    cidade varchar(255) NOT NULL DEFAULT '',
    estado varchar(64) NOT NULL DEFAULT '',
    cep varchar(32) NOT NULL DEFAULT '',
    pais varchar(64) NOT NULL DEFAULT '',
    valor_estimado varchar(255) NOT NULL DEFAULT '',
    score integer NULL,
    score_reason text NOT NULL DEFAULT '',
    followup_date varchar(64) NOT NULL DEFAULT '',
    coluna_id varchar(64) NULL REFERENCES crm_columns(id),
    assigned_to varchar(128) NOT NULL DEFAULT '',
    assigned_to_name varchar(255) NOT NULL DEFAULT '',
    assigned_at timestamptz NULL,
    assignment_status varchar(64) NOT NULL DEFAULT 'unassigned',
    segmento varchar(64) NOT NULL DEFAULT '',
    poder_aquisitivo varchar(64) NOT NULL DEFAULT '',
    leadgen_id varchar(128) NOT NULL DEFAULT '',
    form_id varchar(128) NOT NULL DEFAULT '',
    page_id varchar(128) NOT NULL DEFAULT '',
    source_meta_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    historico_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS leadgen_id varchar(128) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS form_id varchar(128) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS page_id varchar(128) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS source_meta_json jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS cidade varchar(255) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS estado varchar(64) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS cep varchar(32) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS pais varchar(64) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS lead_status varchar(64) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS ctwa_clid varchar(255) NOT NULL DEFAULT '';
ALTER TABLE crm_leads ALTER COLUMN coluna_id DROP NOT NULL;

CREATE TABLE IF NOT EXISTS crm_lead_notes (
    id uuid PRIMARY KEY,
    lead_id uuid NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    text text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_lead_contact_logs (
    id uuid PRIMARY KEY,
    lead_id uuid NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    tipo varchar(64) NOT NULL,
    nota text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_lead_tags (
    lead_id uuid NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    tag varchar(128) NOT NULL,
    PRIMARY KEY (lead_id, tag)
);

CREATE TABLE IF NOT EXISTS crm_lead_timeline (
    id uuid PRIMARY KEY,
    lead_id uuid NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    action varchar(64) NOT NULL,
    detail text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_lists (
    id varchar(64) PRIMARY KEY,
    name varchar(255) NOT NULL,
    filtros_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    followup_date varchar(16) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL
);

ALTER TABLE crm_lists ADD COLUMN IF NOT EXISTS followup_date varchar(16) NOT NULL DEFAULT '';
ALTER TABLE crm_lists ADD COLUMN IF NOT EXISTS owner_key varchar(255) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS crm_list_leads (
    list_id varchar(64) NOT NULL REFERENCES crm_lists(id) ON DELETE CASCADE,
    lead_id uuid NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    PRIMARY KEY (list_id, lead_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_leads_bsuid_uidx
ON crm_leads (instancia_id, bsuid)
WHERE bsuid IS NOT NULL AND bsuid <> '';

CREATE UNIQUE INDEX IF NOT EXISTS crm_leads_phone_uidx
ON crm_leads (instancia_id, telefone)
WHERE telefone IS NOT NULL AND telefone <> '';

CREATE INDEX IF NOT EXISTS crm_leads_coluna_updated_idx
ON crm_leads (coluna_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS crm_leads_instancia_updated_idx
ON crm_leads (instancia_id, updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pg_settings() -> dict:
    host = os.getenv("CRM_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("CRM_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("CRM_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("CRM_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    # Fallback fixo em "klead": EVO_PG_DB aponta pro banco do Evolution GO e já
    # causou dados do app gravados lá quando CRM_PG_DB não estava definida.
    dbname = os.getenv("CRM_PG_DB") or "klead"
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": dbname,
    }


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


def backup_crm_file(crm_file: Path) -> tuple[bool, str]:
    if not crm_file.exists():
        return False, "crm_file_not_found"
    backup_dir = crm_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"crm.{stamp}.json"
    shutil.copy2(crm_file, backup_path)
    return True, str(backup_path)


def _truncate_all(cur):
    cur.execute("DELETE FROM crm_list_leads")
    cur.execute("DELETE FROM crm_lists")
    cur.execute("DELETE FROM crm_lead_tags")
    cur.execute("DELETE FROM crm_lead_timeline")
    cur.execute("DELETE FROM crm_lead_contact_logs")
    cur.execute("DELETE FROM crm_lead_notes")
    cur.execute("DELETE FROM crm_leads")
    cur.execute("DELETE FROM crm_columns")


def sync_snapshot(crm_data: dict) -> tuple[bool, str]:
    from psycopg2.extras import Json

    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()

        if not crm_data.get("columns"):
            cur.execute("SELECT COUNT(*) FROM crm_columns")
            if cur.fetchone()[0] > 0:
                cur.close()
                conn.close()
                return False, "refused_to_wipe_nonempty_columns_with_empty_snapshot"

        _truncate_all(cur)

        now = _now()
        for col in crm_data.get("columns", []):
            cur.execute(
                """
                INSERT INTO crm_columns (id, name, color, sort_order, capi_event, instancia_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    col["id"],
                    col.get("name", ""),
                    col.get("color", "#8696A0"),
                    int(col.get("order", 0)),
                    col.get("capi_event") or "",
                    col.get("instancia_id") or "",
                    now,
                    now,
                ),
            )

        for lead in crm_data.get("leads", []):
            cur.execute(
                """
                INSERT INTO crm_leads (
                    id, instancia_id, nome, telefone, bsuid, parent_bsuid, whatsapp_username,
                    phone_available, origem, campanha, email, empresa, cidade, estado, cep, pais,
                    valor_estimado, score,
                    score_reason, followup_date, coluna_id, assigned_to, assigned_to_name,
                    assigned_at, assignment_status, segmento, poder_aquisitivo,
                    leadgen_id, form_id, page_id, source_meta_json, historico_json,
                    lead_status, ctwa_clid, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    lead["id"],
                    lead.get("instancia_id", ""),
                    lead.get("nome", ""),
                    lead.get("telefone") or None,
                    lead.get("bsuid") or None,
                    lead.get("parent_bsuid") or None,
                    lead.get("whatsapp_username") or None,
                    bool(lead.get("phone_available", True)),
                    lead.get("origem", "manual"),
                    lead.get("campanha", ""),
                    lead.get("email", ""),
                    lead.get("empresa", ""),
                    lead.get("cidade", ""),
                    lead.get("estado", ""),
                    lead.get("cep", ""),
                    lead.get("pais", ""),
                    lead.get("valor_estimado", ""),
                    lead.get("score"),
                    lead.get("score_reason", ""),
                    lead.get("followup_date", ""),
                    # Leads na lista de espera ficam fora do Kanban. A coluna
                    # aceita NULL; string vazia, porém, é um valor real e viola
                    # a FK porque não existe uma crm_column com id="".
                    lead.get("coluna_id") or None,
                    lead.get("assigned_to", ""),
                    lead.get("assigned_to_name", ""),
                    lead.get("assigned_at") or None,
                    lead.get("assignment_status", "unassigned"),
                    lead.get("segmento", ""),
                    lead.get("poder_aquisitivo", ""),
                    lead.get("leadgen_id", ""),
                    lead.get("form_id", ""),
                    lead.get("page_id", ""),
                    Json(lead.get("source_meta", {})),
                    Json(lead.get("historico", [])),
                    lead.get("lead_status", ""),
                    lead.get("ctwa_clid", ""),
                    lead.get("created_at", now),
                    lead.get("updated_at", now),
                ),
            )
            for note in lead.get("notas", []) or []:
                cur.execute(
                    "INSERT INTO crm_lead_notes (id, lead_id, text, created_at) VALUES (%s, %s, %s, %s)",
                    (str(uuid.uuid4()), lead["id"], note.get("text", ""), note.get("at", now)),
                )
            for contact in lead.get("contatos", []) or []:
                cur.execute(
                    "INSERT INTO crm_lead_contact_logs (id, lead_id, tipo, nota, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        lead["id"],
                        contact.get("tipo", "contato"),
                        contact.get("nota", ""),
                        contact.get("at", now),
                    ),
                )
            for tag in lead.get("tags", []) or []:
                cur.execute(
                    "INSERT INTO crm_lead_tags (lead_id, tag) VALUES (%s, %s)",
                    (lead["id"], tag),
                )
            for event in lead.get("timeline", []) or []:
                cur.execute(
                    "INSERT INTO crm_lead_timeline (id, lead_id, action, detail, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        lead["id"],
                        event.get("action", ""),
                        event.get("detail", ""),
                        event.get("at", now),
                    ),
                )

        for lista in crm_data.get("listas", []) or []:
            cur.execute(
                "INSERT INTO crm_lists (id, name, filtros_json, followup_date, owner_key, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    lista["id"],
                    lista.get("name", ""),
                    Json(lista.get("filtros", {})),
                    lista.get("followup_date", ""),
                    lista.get("owner_key", ""),
                    lista.get("created_at", now),
                ),
            )
            for lead_id in lista.get("lead_ids", []) or []:
                cur.execute(
                    "INSERT INTO crm_list_leads (list_id, lead_id) VALUES (%s, %s)",
                    (lista["id"], lead_id),
                )

        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def validate_snapshot(crm_data: dict) -> tuple[bool, dict]:
    ok, detail = ensure_schema()
    if not ok:
        return False, {"reason": detail}
    try:
        conn = _connect()
        from psycopg2 import sql as pg_sql
        cur = conn.cursor()
        counts = {}
        for table in (
            "crm_columns",
            "crm_leads",
            "crm_lead_notes",
            "crm_lead_contact_logs",
            "crm_lead_tags",
            "crm_lead_timeline",
            "crm_lists",
            "crm_list_leads",
        ):
            cur.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(pg_sql.Identifier(table)))
            counts[table] = int(cur.fetchone()[0])
        cur.close()
        conn.close()

        expected = {
            "crm_columns": len(crm_data.get("columns", [])),
            "crm_leads": len(crm_data.get("leads", [])),
            "crm_lead_notes": sum(len(lead.get("notas", []) or []) for lead in crm_data.get("leads", [])),
            "crm_lead_contact_logs": sum(len(lead.get("contatos", []) or []) for lead in crm_data.get("leads", [])),
            "crm_lead_tags": sum(len(lead.get("tags", []) or []) for lead in crm_data.get("leads", [])),
            "crm_lead_timeline": sum(len(lead.get("timeline", []) or []) for lead in crm_data.get("leads", [])),
            "crm_lists": len(crm_data.get("listas", []) or []),
            "crm_list_leads": sum(len(lista.get("lead_ids", []) or []) for lista in crm_data.get("listas", []) or []),
        }
        return counts == expected, {"expected": expected, "actual": counts}
    except Exception as exc:
        return False, {"reason": str(exc)}


def load_snapshot() -> tuple[bool, dict | str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id, name, color, sort_order, capi_event, instancia_id
            FROM crm_columns
            ORDER BY sort_order ASC, id ASC
            """
        )
        columns = [
            {
                "id": row[0],
                "name": row[1],
                "color": row[2],
                "order": row[3],
                **({"capi_event": row[4]} if row[4] else {}),
                **({"instancia_id": row[5]} if row[5] else {}),
            }
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT
                id, instancia_id, nome, telefone, bsuid, parent_bsuid, whatsapp_username,
                phone_available, origem, campanha, email, empresa, cidade, estado, cep, pais,
                valor_estimado, score,
                score_reason, followup_date, coluna_id, assigned_to, assigned_to_name,
                assigned_at, assignment_status, segmento, poder_aquisitivo,
                leadgen_id, form_id, page_id, source_meta_json, historico_json,
                lead_status, ctwa_clid, created_at, updated_at
            FROM crm_leads
            ORDER BY created_at ASC, id ASC
            """
        )
        leads: dict[str, dict] = {}
        for row in cur.fetchall():
            lead_id = str(row[0])
            leads[lead_id] = {
                "id": lead_id,
                "instancia_id": row[1] or "",
                "nome": row[2] or "",
                "telefone": row[3] or "",
                "bsuid": row[4] or "",
                "parent_bsuid": row[5] or "",
                "whatsapp_username": row[6] or "",
                "phone_available": bool(row[7]),
                "origem": row[8] or "",
                "campanha": row[9] or "",
                "email": row[10] or "",
                "empresa": row[11] or "",
                "cidade": row[12] or "",
                "estado": row[13] or "",
                "cep": row[14] or "",
                "pais": row[15] or "",
                "valor_estimado": row[16] or "",
                "score": row[17],
                "score_reason": row[18] or "",
                "followup_date": row[19] or "",
                "coluna_id": row[20] or "",
                "assigned_to": row[21] or "",
                "assigned_to_name": row[22] or "",
                "assigned_at": row[23].isoformat() if row[23] else "",
                "assignment_status": row[24] or "unassigned",
                "segmento": row[25] or "",
                "poder_aquisitivo": row[26] or "",
                "leadgen_id": row[27] or "",
                "form_id": row[28] or "",
                "page_id": row[29] or "",
                "source_meta": row[30] or {},
                "historico": row[31] or [],
                "lead_status": row[32] or "",
                "ctwa_clid": row[33] or "",
                "notas": [],
                "contatos": [],
                "tags": [],
                "timeline": [],
                "created_at": row[34].isoformat() if row[34] else "",
                "updated_at": row[35].isoformat() if row[35] else "",
            }

        cur.execute(
            "SELECT lead_id, text, created_at FROM crm_lead_notes ORDER BY created_at ASC"
        )
        for lead_id, text, created_at in cur.fetchall():
            lead = leads.get(str(lead_id))
            if lead is not None:
                lead["notas"].append({"text": text or "", "at": created_at.isoformat() if created_at else ""})

        cur.execute(
            "SELECT lead_id, tipo, nota, created_at FROM crm_lead_contact_logs ORDER BY created_at ASC"
        )
        for lead_id, tipo, nota, created_at in cur.fetchall():
            lead = leads.get(str(lead_id))
            if lead is not None:
                lead["contatos"].append({
                    "tipo": tipo or "contato",
                    "nota": nota or "",
                    "at": created_at.isoformat() if created_at else "",
                })

        cur.execute("SELECT lead_id, tag FROM crm_lead_tags ORDER BY lead_id ASC, tag ASC")
        for lead_id, tag in cur.fetchall():
            lead = leads.get(str(lead_id))
            if lead is not None and tag:
                lead["tags"].append(tag)

        cur.execute(
            "SELECT lead_id, action, detail, created_at FROM crm_lead_timeline ORDER BY created_at ASC"
        )
        for lead_id, action, detail, created_at in cur.fetchall():
            lead = leads.get(str(lead_id))
            if lead is not None:
                lead["timeline"].append({
                    "action": action or "",
                    "detail": detail or "",
                    "at": created_at.isoformat() if created_at else "",
                })

        cur.execute(
            "SELECT id, name, filtros_json, followup_date, owner_key, created_at FROM crm_lists ORDER BY created_at ASC, id ASC"
        )
        listas: dict[str, dict] = {}
        for list_id, name, filtros_json, followup_date, owner_key, created_at in cur.fetchall():
            listas[str(list_id)] = {
                "id": str(list_id),
                "name": name or "",
                "filtros": filtros_json or {},
                "lead_ids": [],
                "followup_date": followup_date or "",
                "owner_key": owner_key or "",
                "created_at": created_at.isoformat() if created_at else "",
            }

        cur.execute("SELECT list_id, lead_id FROM crm_list_leads ORDER BY list_id ASC")
        for list_id, lead_id in cur.fetchall():
            lista = listas.get(str(list_id))
            if lista is not None:
                lista["lead_ids"].append(str(lead_id))

        cur.close()
        conn.close()
        return True, {
            "columns": columns,
            "leads": list(leads.values()),
            "listas": list(listas.values()),
        }
    except Exception as exc:
        return False, str(exc)
