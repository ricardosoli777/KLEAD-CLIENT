"""
audit_log_service — duas trilhas complementares:

1. admin_access_log — registra quando um superadmin *acessa* dados de uma
   instância de cliente (CRM, Inbox, conversas). Existe pra dar transparência:
   se um cliente perguntar "quem viu meus dados", tem como responder com
   precisão. Throttle de 1h por (ator, instância, ação) pra não virar ruído com
   o polling normal da tela (atualiza a cada 10s).

2. audit_events — registra *ações* (quem criou, mudou, apagou). Vive numa
   tabela própria de propósito: a crm_lead_timeline morre junto com o lead
   (ON DELETE CASCADE), então a exclusão de um lead não deixava rastro nenhum.
   Aqui o evento sobrevive ao alvo — e, em exclusões, guardamos um snapshot
   completo do objeto em `meta`, o que torna o registro restaurável.
   Sem throttle: ação é evento raro e cada uma importa.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid as _uuid
from typing import Any

from services import pg_pool
from services import settings_pg_service as _svc_pg

logger = logging.getLogger("klead.audit_log")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS admin_access_log (
    id varchar(64) PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    actor_email varchar(255) NOT NULL DEFAULT '',
    actor_funcao varchar(32) NOT NULL DEFAULT '',
    instancia_id varchar(128) NOT NULL DEFAULT '',
    action varchar(64) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS admin_access_log_instancia_idx ON admin_access_log (instancia_id, at DESC);
CREATE INDEX IF NOT EXISTS admin_access_log_actor_idx ON admin_access_log (actor_email, at DESC);

CREATE TABLE IF NOT EXISTS audit_events (
    id varchar(64) PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    actor_email varchar(255) NOT NULL DEFAULT '',
    actor_funcao varchar(32) NOT NULL DEFAULT '',
    action varchar(64) NOT NULL DEFAULT '',
    target_type varchar(32) NOT NULL DEFAULT '',
    target_id varchar(255) NOT NULL DEFAULT '',
    instancia_id varchar(128) NOT NULL DEFAULT '',
    detail text NOT NULL DEFAULT '',
    meta jsonb,
    ip varchar(64) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_events_at_idx ON audit_events (at DESC);
CREATE INDEX IF NOT EXISTS audit_events_actor_idx ON audit_events (actor_email, at DESC);
CREATE INDEX IF NOT EXISTS audit_events_action_idx ON audit_events (action, at DESC);
CREATE INDEX IF NOT EXISTS audit_events_target_idx ON audit_events (target_type, target_id, at DESC);
CREATE INDEX IF NOT EXISTS audit_events_instancia_idx ON audit_events (instancia_id, at DESC);
"""

_schema_ensured = False
_schema_lock = threading.Lock()

_THROTTLE_SECONDS = 3600  # 1 log por (ator, instancia, acao) por hora
_last_logged: dict[tuple, float] = {}
_throttle_lock = threading.Lock()


def _ensure_schema() -> bool:
    global _schema_ensured
    if _schema_ensured:
        return True
    with _schema_lock:
        if _schema_ensured:
            return True
        try:
            cfg = _svc_pg._pg_settings()
            with pg_pool.pooled_connection(cfg) as conn:
                cur = conn.cursor()
                cur.execute(_SCHEMA_SQL)
                conn.commit()
                cur.close()
            _schema_ensured = True
            return True
        except Exception as exc:
            logger.warning(f"[AUDIT-LOG] Falha ao garantir schema: {exc}")
            return False


def log_admin_access(actor_email: str, actor_funcao: str, instancia_id: str, action: str) -> None:
    """Registra acesso de um superadmin a dados de uma instância — com throttle de 1h."""
    if not actor_email or not instancia_id:
        return
    key = (actor_email, instancia_id, action)
    now = time.time()
    with _throttle_lock:
        last = _last_logged.get(key, 0)
        if now - last < _THROTTLE_SECONDS:
            return
        _last_logged[key] = now

    try:
        if not _ensure_schema():
            return
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO admin_access_log (id, actor_email, actor_funcao, instancia_id, action)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(_uuid.uuid4()), actor_email, actor_funcao, instancia_id, action),
            )
            conn.commit()
            cur.close()
    except Exception as exc:
        logger.warning(f"[AUDIT-LOG] Falha ao gravar acesso: {exc}")


def actor_from_request(request) -> tuple[str, str, str]:
    """
    Extrai (email, funcao, ip) do request. Nunca levanta — auditoria não pode
    derrubar a operação que ela está registrando. Ator vazio = anônimo
    (ex.: tentativa de login com senha errada, que ainda assim queremos logar).
    """
    email, funcao, ip = "", "", ""
    try:
        # Import local: auth_service já importa este módulo em outros pontos.
        from services.auth_service import try_get_auth_user

        user = try_get_auth_user(request)
        if isinstance(user, dict):
            email = user.get("email", "") or ""
            funcao = user.get("funcao", "") or ""
    except Exception:
        logger.warning("Falha ao registrar evento de auditoria", exc_info=True)
    try:
        fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = fwd or (getattr(request.client, "host", "") or "")
    except Exception:
        pass
    return email, funcao, ip


def record_event(
    *,
    actor_email: str = "",
    actor_funcao: str = "",
    action: str,
    target_type: str = "",
    target_id: str = "",
    instancia_id: str = "",
    detail: str = "",
    meta: Any = None,
    ip: str = "",
) -> None:
    """
    Grava uma ação na trilha de auditoria. Best-effort: qualquer falha aqui é
    logada e engolida — auditoria nunca quebra a operação de negócio.

    Em exclusões, passe o objeto inteiro em `meta`: é o que permite restaurar
    depois (a timeline do CRM some junto com o lead, essa trilha não).
    """
    if not action:
        return
    try:
        if not _ensure_schema():
            return
        meta_json = None
        if meta is not None:
            try:
                meta_json = json.dumps(meta, ensure_ascii=False, default=str)
            except Exception:
                meta_json = json.dumps({"_erro": "meta não serializável"}, ensure_ascii=False)
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO audit_events
                    (id, actor_email, actor_funcao, action, target_type,
                     target_id, instancia_id, detail, meta, ip)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(_uuid.uuid4()), actor_email, actor_funcao, action,
                    target_type, str(target_id), instancia_id, detail[:2000],
                    meta_json, ip,
                ),
            )
            conn.commit()
            cur.close()
    except Exception as exc:
        logger.warning(f"[AUDIT] Falha ao gravar evento {action!r}: {exc}")


def get_events(
    *,
    actor_email: str = "",
    action: str = "",
    target_id: str = "",
    instancia_id: str = "",
    limit: int = 100,
) -> list[dict]:
    """Lê a trilha de ações, do mais recente pro mais antigo, com filtros opcionais."""
    try:
        # _ensure_schema dentro do try: se o PG estiver fora do ar ele levanta,
        # e a tela de auditoria não pode derrubar a request por causa disso.
        if not _ensure_schema():
            return []
        where, params = [], []
        if actor_email:
            where.append("actor_email = %s")
            params.append(actor_email)
        if action:
            where.append("action = %s")
            params.append(action)
        if target_id:
            where.append("target_id = %s")
            params.append(target_id)
        if instancia_id:
            where.append("instancia_id = %s")
            params.append(instancia_id)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)

        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT at, actor_email, actor_funcao, action, target_type,
                       target_id, instancia_id, detail, meta, ip
                FROM audit_events {clause} ORDER BY at DESC LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            cur.close()
        return [
            {
                "at": r[0].isoformat() if r[0] else "",
                "actor_email": r[1],
                "actor_funcao": r[2],
                "action": r[3],
                "target_type": r[4],
                "target_id": r[5],
                "instancia_id": r[6],
                "detail": r[7],
                "meta": r[8],
                "ip": r[9],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning(f"[AUDIT] Falha ao ler eventos: {exc}")
        return []


def get_recent_access(*, instancia_id: str = "", limit: int = 100) -> list[dict]:
    if not _ensure_schema():
        return []
    try:
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            if instancia_id:
                cur.execute(
                    """
                    SELECT at, actor_email, actor_funcao, instancia_id, action
                    FROM admin_access_log WHERE instancia_id = %s
                    ORDER BY at DESC LIMIT %s
                    """,
                    (instancia_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT at, actor_email, actor_funcao, instancia_id, action
                    FROM admin_access_log ORDER BY at DESC LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            cur.close()
        return [
            {
                "at": row[0].isoformat() if row[0] else "",
                "actor_email": row[1],
                "actor_funcao": row[2],
                "instancia_id": row[3],
                "action": row[4],
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning(f"[AUDIT-LOG] Falha ao ler histórico: {exc}")
        return []
