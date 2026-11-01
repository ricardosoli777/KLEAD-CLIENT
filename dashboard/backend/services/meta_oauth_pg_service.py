"""
Persistência PostgreSQL para conexões OAuth exclusivas por instância e os
ativos selecionados (Business Manager, conta de anúncio, página,
Instagram, WABA e número de telefone).

Paralelo ao modo manual de credenciais (meta_token/meta_app_secret direto
na tabela instances) — não substitui nem depende dele.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from services import pg_pool

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta_connections (
    id uuid PRIMARY KEY,
    user_email varchar(255) NOT NULL,
    meta_user_id varchar(64) NOT NULL DEFAULT '',
    access_token_encrypted text NOT NULL DEFAULT '',
    token_expires_at timestamptz NULL,
    scopes text NOT NULL DEFAULT '',
    status varchar(32) NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_meta_connections_user ON meta_connections (user_email);

CREATE TABLE IF NOT EXISTS instance_meta_assets (
    id uuid PRIMARY KEY,
    instance_id varchar(128) NOT NULL REFERENCES instances(instance_id) ON DELETE CASCADE,
    meta_connection_id uuid NOT NULL REFERENCES meta_connections(id) ON DELETE CASCADE,
    business_id varchar(64) NOT NULL DEFAULT '',
    business_name varchar(255) NOT NULL DEFAULT '',
    ad_account_id varchar(64) NOT NULL DEFAULT '',
    ad_account_name varchar(255) NOT NULL DEFAULT '',
    page_id varchar(64) NOT NULL DEFAULT '',
    page_name varchar(255) NOT NULL DEFAULT '',
    instagram_id varchar(64) NOT NULL DEFAULT '',
    waba_id varchar(64) NOT NULL DEFAULT '',
    phone_number_id varchar(64) NOT NULL DEFAULT '',
    phone_display_number varchar(64) NOT NULL DEFAULT '',
    pixel_id varchar(64) NOT NULL DEFAULT '',
    pixel_name varchar(255) NOT NULL DEFAULT '',
    whatsapp_enabled boolean NULL DEFAULT NULL,
    lead_ads_enabled boolean NULL DEFAULT NULL,
    status varchar(32) NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE instance_meta_assets ADD COLUMN IF NOT EXISTS pixel_id varchar(64) NOT NULL DEFAULT '';
ALTER TABLE instance_meta_assets ADD COLUMN IF NOT EXISTS pixel_name varchar(255) NOT NULL DEFAULT '';
ALTER TABLE instance_meta_assets ADD COLUMN IF NOT EXISTS whatsapp_enabled boolean NULL DEFAULT NULL;
ALTER TABLE instance_meta_assets ADD COLUMN IF NOT EXISTS lead_ads_enabled boolean NULL DEFAULT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_instance_meta_assets_instance ON instance_meta_assets (instance_id);

CREATE TABLE IF NOT EXISTS meta_lead_page_routes (
    page_id varchar(64) PRIMARY KEY,
    instance_id varchar(128) NOT NULL REFERENCES instances(instance_id) ON DELETE CASCADE,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS meta_lead_form_routes (
    form_id varchar(128) PRIMARY KEY,
    page_id varchar(64) NOT NULL,
    form_name varchar(255) NOT NULL DEFAULT '',
    instance_id varchar(128) NOT NULL REFERENCES instances(instance_id) ON DELETE CASCADE,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meta_lead_form_routes_page_idx ON meta_lead_form_routes (page_id);
"""


def _pg_settings() -> dict:
    # instance_meta_assets tem FK para instances(instance_id) — precisa cair no
    # mesmo banco que instances_pg_service usa (INST_PG_DB, normalmente "klead"),
    # não no banco do Evolution GO (EVO_PG_DB="evolution_go_users").
    host = os.getenv("META_OAUTH_PG_HOST") or os.getenv("INST_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("META_OAUTH_PG_PORT") or os.getenv("INST_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("META_OAUTH_PG_USER") or os.getenv("INST_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("META_OAUTH_PG_PASSWORD") or os.getenv("INST_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    dbname = os.getenv("META_OAUTH_PG_DB") or os.getenv("INST_PG_DB") or "klead"
    return {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}


def pg_configured() -> bool:
    cfg = _pg_settings()
    return bool(cfg["host"] and cfg["password"] and cfg["dbname"])


def _connect():
    cfg = _pg_settings()
    return pg_pool.get_connection(cfg)


def _now():
    return datetime.now(timezone.utc)


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


def upsert_connection(
    instance_id: str,
    user_email: str,
    meta_user_id: str,
    access_token_encrypted: str,
    token_expires_at,
    scopes: str,
) -> tuple[bool, str]:
    """
    Cria ou atualiza a conexão Meta exclusiva da instância. O vínculo é salvo
    em instance_meta_assets já no callback (status ``pending``); depois a
    seleção de ativos preenche a mesma linha. Assim, duas instâncias do mesmo
    usuário nunca reutilizam silenciosamente o mesmo token.
    """
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT mc.id,
                   (SELECT count(*) FROM instance_meta_assets linked
                    WHERE linked.meta_connection_id = mc.id) AS link_count
            FROM instance_meta_assets ima
            JOIN meta_connections mc ON mc.id = ima.meta_connection_id
            WHERE ima.instance_id = %s
            LIMIT 1
            """,
            (instance_id,),
        )
        row = cur.fetchone()
        # Conexões legadas podiam estar ligadas a várias instâncias do mesmo
        # usuário. Ao reconectar uma delas, crie uma conexão nova em vez de
        # substituir o token que as demais ainda usam.
        if row and int(row[1] or 0) <= 1:
            cur.execute(
                """
                UPDATE meta_connections
                SET user_email = %s, meta_user_id = %s, access_token_encrypted = %s, token_expires_at = %s,
                    scopes = %s, status = 'active', updated_at = now()
                WHERE id = %s
                """,
                (user_email, meta_user_id, access_token_encrypted, token_expires_at, scopes, row[0]),
            )
            connection_id = str(row[0])
        else:
            connection_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO meta_connections (
                    id, user_email, meta_user_id, access_token_encrypted,
                    token_expires_at, scopes, status, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'active', now(), now())
                """,
                (connection_id, user_email, meta_user_id, access_token_encrypted, token_expires_at, scopes),
            )
        cur.execute(
            """
            INSERT INTO instance_meta_assets (id, instance_id, meta_connection_id, status, created_at, updated_at)
            VALUES (%s, %s, %s, 'pending', now(), now())
            ON CONFLICT (instance_id) DO UPDATE SET
                meta_connection_id = EXCLUDED.meta_connection_id,
                updated_at = now()
            """,
            (str(uuid.uuid4()), instance_id, connection_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, connection_id
    except Exception as exc:
        return False, str(exc)


def get_connection_for_instance(instance_id: str) -> dict | None:
    """Retorna somente a conexão vinculada à instância informada."""
    ok, detail = ensure_schema()
    if not ok:
        return None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT mc.id, mc.user_email, mc.meta_user_id,
                   mc.access_token_encrypted, mc.token_expires_at,
                   mc.scopes, mc.status, mc.updated_at
            FROM instance_meta_assets ima
            JOIN meta_connections mc ON mc.id = ima.meta_connection_id
            WHERE ima.instance_id = %s AND mc.status = 'active'
            LIMIT 1
            """,
            (instance_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "user_email": row[1],
            "meta_user_id": row[2],
            "access_token_encrypted": row[3],
            "token_expires_at": row[4].isoformat() if row[4] else None,
            "scopes": row[5],
            "status": row[6],
            "updated_at": row[7].isoformat() if row[7] else None,
        }
    except Exception:
        return None


def claim_unlinked_connection(instance_id: str, user_email: str) -> bool:
    """Vincula a uma única instância uma conexão legada ainda sem vínculo."""
    ok, detail = ensure_schema()
    if not ok:
        return False
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT mc.id
            FROM meta_connections mc
            WHERE mc.user_email = %s AND mc.status = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM instance_meta_assets ima
                  WHERE ima.meta_connection_id = mc.id
              )
            ORDER BY mc.updated_at DESC
            LIMIT 1
            """,
            (user_email,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return False
        cur.execute(
            """
            INSERT INTO instance_meta_assets (id, instance_id, meta_connection_id, status, created_at, updated_at)
            VALUES (%s, %s, %s, 'pending', now(), now())
            ON CONFLICT (instance_id) DO NOTHING
            """,
            (str(uuid.uuid4()), instance_id, row[0]),
        )
        claimed = cur.rowcount > 0
        conn.commit()
        cur.close()
        conn.close()
        return claimed
    except Exception:
        return False


def get_connection_for_user(user_email: str) -> dict | None:
    ok, detail = ensure_schema()
    if not ok:
        return None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, meta_user_id, access_token_encrypted, token_expires_at, scopes, status, updated_at
            FROM meta_connections
            WHERE user_email = %s AND status = 'active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (user_email,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "meta_user_id": row[1],
            "access_token_encrypted": row[2],
            "token_expires_at": row[3].isoformat() if row[3] else None,
            "scopes": row[4],
            "status": row[5],
            "updated_at": row[6].isoformat() if row[6] else None,
        }
    except Exception:
        return None


def get_connection_by_id(connection_id: str) -> dict | None:
    """Usado pelo CAPI (sem sessão de usuário) para resolver o token OAuth a
    partir do meta_connection_id salvo em instance_meta_assets."""
    ok, detail = ensure_schema()
    if not ok:
        return None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_email, access_token_encrypted, status
            FROM meta_connections WHERE id = %s
            """,
            (connection_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row or row[3] != "active":
            return None
        return {"id": str(row[0]), "user_email": row[1], "access_token_encrypted": row[2]}
    except Exception:
        return None


def list_connections_expiring_soon(within_days: int = 7) -> list[dict]:
    """
    Conexões ativas vinculadas a instâncias cujo token tem validade registrada
    dentro de `within_days`, inclusive tokens vencidos. Tokens de longa
    duração da Meta duram ~60 dias; sem isso, o usuário só descobre que
    expirou quando algo parar de funcionar.
    """
    ok, detail = ensure_schema()
    if not ok:
        return []
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_email, access_token_encrypted, token_expires_at
            FROM meta_connections
            WHERE status = 'active'
              AND token_expires_at IS NOT NULL
              AND token_expires_at < now() + (%s * interval '1 day')
              AND EXISTS (
                  SELECT 1 FROM instance_meta_assets ima
                  WHERE ima.meta_connection_id = meta_connections.id
              )
            """,
            (within_days,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {
                "id": str(r[0]),
                "user_email": r[1],
                "access_token_encrypted": r[2],
                "token_expires_at": r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]
    except Exception:
        return []


def update_connection_token(connection_id: str, access_token_encrypted: str, token_expires_at) -> tuple[bool, str]:
    """Atualiza o token (e nova expiração) de uma conexão já existente — usado pela renovação proativa."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE meta_connections SET access_token_encrypted = %s, token_expires_at = %s, updated_at = now() "
            "WHERE id = %s AND status = 'active'",
            (access_token_encrypted, token_expires_at, connection_id),
        )
        updated = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if not updated:
            return False, "connection_not_found_or_inactive"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def disconnect_user(user_email: str) -> tuple[bool, str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE meta_connections SET status = 'disconnected', updated_at = now() WHERE user_email = %s AND status = 'active'",
            (user_email,),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def disconnect_instance(instance_id: str) -> tuple[bool, str]:
    """Desativa só a conexão OAuth desta instância e remove seu vínculo."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT meta_connection_id FROM instance_meta_assets WHERE instance_id = %s",
            (instance_id,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "SELECT count(*) FROM instance_meta_assets WHERE meta_connection_id = %s AND instance_id <> %s",
                (row[0], instance_id),
            )
            other_links = int((cur.fetchone() or (0,))[0] or 0)
            if not other_links:
                cur.execute(
                    "UPDATE meta_connections SET status = 'disconnected', updated_at = now() WHERE id = %s",
                    (row[0],),
                )
        cur.execute("DELETE FROM instance_meta_assets WHERE instance_id = %s", (instance_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def upsert_instance_assets(instance_id: str, meta_connection_id: str, assets: dict) -> tuple[bool, str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    fields = (
        "business_id", "business_name", "ad_account_id", "ad_account_name",
        "page_id", "page_name", "instagram_id", "waba_id",
        "phone_number_id", "phone_display_number", "pixel_id", "pixel_name",
        "whatsapp_enabled", "lead_ads_enabled",
    )
    values = [bool(assets.get(f)) if f in ("whatsapp_enabled", "lead_ads_enabled") else (assets.get(f, "") or "") for f in fields]
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO instance_meta_assets (
                id, instance_id, meta_connection_id, {", ".join(fields)}, status, created_at, updated_at
            ) VALUES (%s, %s, %s, {", ".join(["%s"] * len(fields))}, 'active', now(), now())
            ON CONFLICT (instance_id) DO UPDATE SET
                meta_connection_id = EXCLUDED.meta_connection_id,
                {", ".join(f"{f} = EXCLUDED.{f}" for f in fields)},
                status = 'active',
                updated_at = now()
            """,
            [str(uuid.uuid4()), instance_id, meta_connection_id] + values,
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_instance_assets(instance_id: str) -> dict | None:
    ok, detail = ensure_schema()
    if not ok:
        return None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT business_id, business_name, ad_account_id, ad_account_name,
                   page_id, page_name, instagram_id, waba_id,
                   phone_number_id, phone_display_number, pixel_id, pixel_name,
                   whatsapp_enabled, lead_ads_enabled,
                   status, meta_connection_id
            FROM instance_meta_assets WHERE instance_id = %s
            """,
            (instance_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        keys = (
            "business_id", "business_name", "ad_account_id", "ad_account_name",
            "page_id", "page_name", "instagram_id", "waba_id",
            "phone_number_id", "phone_display_number", "pixel_id", "pixel_name",
            "whatsapp_enabled", "lead_ads_enabled",
            "status", "meta_connection_id",
        )
        result = {k: (str(v) if v is not None else "") for k, v in zip(keys, row)}
        whatsapp_mode = row[keys.index("whatsapp_enabled")]
        lead_ads_mode = row[keys.index("lead_ads_enabled")]
        result["whatsapp_enabled"] = bool(whatsapp_mode) if whatsapp_mode is not None else bool(result["waba_id"] and result["phone_number_id"])
        result["lead_ads_enabled"] = bool(lead_ads_mode) if lead_ads_mode is not None else bool(result["page_id"])
        return result
    except Exception:
        return None


def remove_instance_assets(instance_id: str) -> tuple[bool, str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM instance_meta_assets WHERE instance_id = %s", (instance_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_lead_routes(page_id: str = "") -> dict:
    """Retorna destinos sem expor credenciais OAuth."""
    ok, _ = ensure_schema()
    if not ok:
        return {"page_instance_id": "", "forms": {}}
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT instance_id FROM meta_lead_page_routes WHERE page_id = %s", (page_id,))
        row = cur.fetchone()
        cur.execute(
            "SELECT form_id, instance_id FROM meta_lead_form_routes WHERE page_id = %s",
            (page_id,),
        )
        forms = {str(form_id): instance_id for form_id, instance_id in cur.fetchall()}
        cur.close()
        conn.close()
        return {"page_instance_id": row[0] if row else "", "forms": forms}
    except Exception:
        return {"page_instance_id": "", "forms": {}}


def resolve_lead_instance(page_id: str, form_id: str = "") -> str:
    """Prioridade de roteamento: formulário específico, depois Página padrão."""
    ok, _ = ensure_schema()
    if not ok:
        return ""
    try:
        conn = _connect()
        cur = conn.cursor()
        if form_id:
            cur.execute("SELECT instance_id FROM meta_lead_form_routes WHERE form_id = %s", (form_id,))
            row = cur.fetchone()
            if row:
                cur.close(); conn.close()
                return row[0]
        cur.execute("SELECT instance_id FROM meta_lead_page_routes WHERE page_id = %s", (page_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def save_lead_routes(instance_id: str, page_id: str, default_for_page: bool,
                     forms: list[dict], confirm_transfer: bool = False) -> tuple[bool, str]:
    """Salva rotas exclusivas; conflitos exigem confirmação explícita."""
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    form_ids = [str(f.get("id", "")) for f in forms if f.get("id")]
    try:
        conn = _connect()
        cur = conn.cursor()
        conflicts = []
        if default_for_page:
            cur.execute("SELECT instance_id FROM meta_lead_page_routes WHERE page_id = %s", (page_id,))
            row = cur.fetchone()
            if row and row[0] != instance_id:
                conflicts.append(f"Página: {row[0]}")
        if form_ids:
            cur.execute(
                "SELECT form_id, instance_id FROM meta_lead_form_routes WHERE form_id = ANY(%s) AND instance_id <> %s",
                (form_ids, instance_id),
            )
            conflicts += [f"Formulário {fid}: {owner}" for fid, owner in cur.fetchall()]
        if conflicts and not confirm_transfer:
            cur.close(); conn.close()
            return False, "transfer_confirmation_required:" + " | ".join(conflicts)

        # Uma instância seleciona uma Página por conexão; ao trocar de Página,
        # remova suas regras antigas sem afetar rotas pertencentes a terceiros.
        cur.execute("DELETE FROM meta_lead_form_routes WHERE instance_id = %s", (instance_id,))
        cur.execute("DELETE FROM meta_lead_page_routes WHERE instance_id = %s", (instance_id,))
        if default_for_page:
            cur.execute(
                "INSERT INTO meta_lead_page_routes (page_id, instance_id) VALUES (%s,%s) "
                "ON CONFLICT (page_id) DO UPDATE SET instance_id=EXCLUDED.instance_id, updated_at=now()",
                (page_id, instance_id),
            )
        else:
            cur.execute("DELETE FROM meta_lead_page_routes WHERE page_id = %s AND instance_id = %s", (page_id, instance_id))
        for form in forms:
            if not form.get("id"):
                continue
            cur.execute(
                "INSERT INTO meta_lead_form_routes (form_id,page_id,form_name,instance_id) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (form_id) DO UPDATE SET page_id=EXCLUDED.page_id, form_name=EXCLUDED.form_name, "
                "instance_id=EXCLUDED.instance_id, updated_at=now()",
                (str(form["id"]), page_id, str(form.get("name", "")), instance_id),
            )
        conn.commit(); cur.close(); conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def clear_instance_lead_routes(instance_id: str) -> tuple[bool, str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM meta_lead_form_routes WHERE instance_id = %s", (instance_id,))
        cur.execute("DELETE FROM meta_lead_page_routes WHERE instance_id = %s", (instance_id,))
        conn.commit(); cur.close(); conn.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)
