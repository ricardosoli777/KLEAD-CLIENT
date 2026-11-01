"""
Persistencia das conversas em PostgreSQL — unica fonte de verdade.

Caminho quente: upsert_conversation (grava UMA conversa por vez, incremental).
sync_snapshot (delete + regrava tudo) fica como reparo de deriva periodico e
carga inicial; por isso o startup e fail-fast: nunca subir com estado vazio
se o PG estiver configurado mas indisponivel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import pg_pool

logger = logging.getLogger("klead.conversation_pg")

# Sync incremental: cache do hash de cada conversa já persistida.
# Custo do sync passa a ser proporcional ao que MUDOU (tipicamente 1 conversa
# por mensagem recebida) em vez de regravar a tabela inteira a cada save.
_sync_lock = threading.Lock()
_synced_hashes: dict[tuple[str, str], str] = {}


def _conv_hash(conv: dict) -> str:
    return hashlib.sha256(
        json.dumps(conv, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _invalidate_sync_cache() -> None:
    _synced_hashes.clear()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY,
    instancia_id varchar(128) NOT NULL,
    conversation_key varchar(180) NOT NULL,
    lead_id uuid NULL,
    display_name varchar(255) NOT NULL DEFAULT '',
    stage varchar(64) NOT NULL DEFAULT 'qualifying',
    ai_paused boolean NOT NULL DEFAULT false,
    unread boolean NOT NULL DEFAULT false,
    score integer NULL,
    score_reason text NOT NULL DEFAULT '',
    turn_count integer NOT NULL DEFAULT 0,
    followup_count integer NOT NULL DEFAULT 0,
    last_followup_at timestamptz NULL,
    needs_human boolean NOT NULL DEFAULT false,
    handoff_reason text NOT NULL DEFAULT '',
    handoff_at timestamptz NULL,
    handoff_source varchar(64) NOT NULL DEFAULT '',
    handoff_handled_at timestamptz NULL,
    lead_campanha text NOT NULL DEFAULT '',
    lead_email varchar(255) NOT NULL DEFAULT '',
    lead_origem varchar(64) NOT NULL DEFAULT '',
    lead_dismissed boolean NOT NULL DEFAULT false,
    source_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    last_activity timestamptz NOT NULL,
    deleted_at timestamptz NULL,
    UNIQUE (instancia_id, conversation_key)
);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_campanha text NOT NULL DEFAULT '';
-- Soft delete: "apagar conversa" no InboxSDR passa a marcar deleted_at em vez
-- de remover a linha — evita perda permanente por exclusao acidental. Uma
-- rotina periodica (ver purge_deleted_conversations) remove de vez depois de
-- alguns dias. load_snapshot ja filtra deleted_at IS NULL.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at timestamptz NULL;
CREATE INDEX IF NOT EXISTS conversations_deleted_at_idx ON conversations (deleted_at) WHERE deleted_at IS NOT NULL;
-- Formularios Meta com varias perguntas concatenadas facilmente passam de 255
-- caracteres — varchar(255) fazia upsert_conversation falhar silenciosamente
-- (retry infinito, nunca persistia) para esses leads. Instalacoes antigas
-- ainda tem a coluna como varchar(255); amplia para nao perder leads assim.
ALTER TABLE conversations ALTER COLUMN lead_campanha TYPE text;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_email varchar(255) NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_origem varchar(64) NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_dismissed boolean NOT NULL DEFAULT false;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS source_meta jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS conversation_messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role varchar(32) NOT NULL,
    content text NOT NULL,
    timestamp timestamptz NOT NULL,
    message_type varchar(32) NOT NULL DEFAULT 'text',
    provider_message_id varchar(180) NOT NULL DEFAULT '',
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS conversations_instance_activity_idx
ON conversations (instancia_id, last_activity DESC);

CREATE INDEX IF NOT EXISTS conversation_messages_conversation_time_idx
ON conversation_messages (conversation_id, timestamp ASC);
"""


def _pg_settings() -> dict:
    host = os.getenv("CONV_PG_HOST") or os.getenv("CRM_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port = int(os.getenv("CONV_PG_PORT") or os.getenv("CRM_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user = os.getenv("CONV_PG_USER") or os.getenv("CRM_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("CONV_PG_PASSWORD") or os.getenv("CRM_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    # Fallback fixo em "klead": EVO_PG_DB aponta pro banco do Evolution GO e já
    # causou dados do app gravados lá quando CRM_PG_DB não estava definida.
    dbname = os.getenv("CONV_PG_DB") or os.getenv("CRM_PG_DB") or "klead"
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


def backup_conversations_file(conv_file: Path) -> tuple[bool, str]:
    if not conv_file.exists():
        return False, "conversations_file_not_found"
    backup_dir = conv_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"conversations.{stamp}.json"
    shutil.copy2(conv_file, backup_path)
    return True, str(backup_path)


def _clean(v: str) -> str:
    return v.replace("\x00", "") if isinstance(v, str) else v


def _truncate_all(cur):
    """
    Limpa só as conversas ATIVAS (deleted_at IS NULL) antes de regravar o
    snapshot em memória — preserva a "lixeira" de soft-deletes intacta.
    Sem esse filtro, o sync_snapshot periódico (reparo de deriva) apagaria
    de vez qualquer conversa apagada há menos de purge_deleted_conversations.
    """
    cur.execute(
        "DELETE FROM conversation_messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE deleted_at IS NULL)"
    )
    cur.execute("DELETE FROM conversations WHERE deleted_at IS NULL")


# Colunas na ordem usada por _conv_row (sem o id, que cada caminho gera/preserva).
_CONV_COLS = (
    "instancia_id, conversation_key, lead_id, display_name, stage, "
    "ai_paused, unread, score, score_reason, turn_count, followup_count, "
    "last_followup_at, needs_human, handoff_reason, handoff_at, "
    "handoff_source, handoff_handled_at, "
    "lead_campanha, lead_email, lead_origem, lead_dismissed, source_meta, "
    "created_at, last_activity"
)
_CONV_PLACEHOLDERS = ", ".join(["%s"] * 24)

# Colunas atualizáveis num upsert (exclui id, instancia_id, conversation_key,
# lead_id, created_at — chave/identidade não deve ser sobrescrita por upsert).
# Compartilhada por upsert_conversation e sync_snapshot — nunca deixar divergir.
_CONV_UPDATE_COLS = (
    "display_name", "stage", "ai_paused", "unread", "score", "score_reason",
    "turn_count", "followup_count", "last_followup_at", "needs_human",
    "handoff_reason", "handoff_at", "handoff_source", "handoff_handled_at",
    "lead_campanha", "lead_email", "lead_origem", "lead_dismissed",
    "source_meta", "last_activity",
)
_CONV_UPDATE_SET = ", ".join(f"{c} = EXCLUDED.{c}" for c in _CONV_UPDATE_COLS)


def _conv_row(instance_id: str, conv_key: str, conv: dict) -> tuple:
    """Tupla de valores de uma conversa na ordem de _CONV_COLS.
    Compartilhada por sync_snapshot e upsert_conversation — nunca deixar divergir."""
    from psycopg2.extras import Json

    now_iso = datetime.now(timezone.utc).isoformat()
    return (
        instance_id,
        conv_key,
        None,
        _clean(conv.get("name", conv_key)),
        conv.get("stage", "qualifying"),
        bool(conv.get("ai_paused", False)),
        bool(conv.get("unread", False)),
        conv.get("score"),
        conv.get("score_reason", ""),
        int(conv.get("turn_count", 0) or 0),
        int(conv.get("followup_count", 0) or 0),
        conv.get("last_followup_at") or None,
        bool(conv.get("needs_human", False)),
        conv.get("handoff_reason", ""),
        conv.get("handoff_at") or None,
        conv.get("handoff_source", ""),
        conv.get("handoff_handled_at") or None,
        _clean(conv.get("lead_campanha", "")),
        _clean(conv.get("lead_email", "")),
        _clean(conv.get("lead_origem", "")),
        bool(conv.get("lead_dismissed", False)),
        Json(conv.get("source_meta") or {}),
        conv.get("created_at") or now_iso,
        conv.get("last_activity") or now_iso,
    )


def _msg_row(conv_id: str, msg: dict) -> tuple:
    from psycopg2.extras import Json

    return (
        str(uuid.uuid4()),
        conv_id,
        _clean(msg.get("role", "user")),
        _clean(msg.get("content", "")),
        msg.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        _clean(msg.get("message_type", "text")),
        _clean(msg.get("provider_message_id", "")),
        Json(msg.get("payload", {})),
    )


# Evita rodar o DDL de ensure_schema a cada mensagem no caminho quente.
_schema_ready = False


def _ensure_schema_once() -> tuple[bool, str]:
    global _schema_ready
    if _schema_ready:
        return True, ""
    ok, detail = ensure_schema()
    if ok:
        _schema_ready = True
    return ok, detail


def upsert_conversation(instance_id: str, conv_key: str, conv: dict) -> tuple[bool, str]:
    """
    Persiste UMA conversa: upsert da linha em `conversations` e regrava apenas
    as mensagens dela (delete + insert em lote, na mesma transação).
    Caminho quente — substitui o sync_snapshot completo por evento.
    Não sobrescreve lead_id nem created_at de linha existente.
    """
    from psycopg2.extras import execute_values

    ok, detail = _ensure_schema_once()
    if not ok:
        return False, detail

    # Pula write se a conversa não mudou desde o último upsert bem-sucedido.
    new_hash = _conv_hash(conv)
    cache_key = (instance_id, conv_key)
    with _sync_lock:
        if _synced_hashes.get(cache_key) == new_hash:
            return True, "no-op"

    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO conversations (id, {_CONV_COLS})
            VALUES (%s, {_CONV_PLACEHOLDERS})
            ON CONFLICT (instancia_id, conversation_key) DO UPDATE SET {_CONV_UPDATE_SET}
            RETURNING id
            """,
            (str(uuid.uuid4()),) + _conv_row(instance_id, conv_key, conv),
        )
        conv_id = str(cur.fetchone()[0])
        cur.execute("DELETE FROM conversation_messages WHERE conversation_id = %s", (conv_id,))
        msgs = conv.get("messages", []) or []
        if msgs:
            execute_values(
                cur,
                """
                INSERT INTO conversation_messages (
                    id, conversation_id, role, content, timestamp,
                    message_type, provider_message_id, payload_json
                ) VALUES %s
                """,
                [_msg_row(conv_id, m) for m in msgs],
            )
        conn.commit()
        cur.close()
        conn.close()
        with _sync_lock:
            _synced_hashes[cache_key] = new_hash
        return True, ""
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        return False, str(exc)


def delete_conversation(instance_id: str, conv_key: str) -> tuple[bool, str]:
    """
    Soft delete: marca deleted_at em vez de remover a linha. A conversa some
    do InboxSDR (load_snapshot filtra deleted_at IS NULL) mas fica recuperável
    via restore_conversation até ser purgada (ver purge_deleted_conversations).
    """
    ok, detail = _ensure_schema_once()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE conversations SET deleted_at = now() "
            "WHERE instancia_id = %s AND conversation_key = %s AND deleted_at IS NULL",
            (instance_id, conv_key),
        )
        conn.commit()
        cur.close()
        conn.close()
        with _sync_lock:
            _synced_hashes.pop((instance_id, conv_key), None)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def list_deleted_conversations(instance_id: str = "") -> list[dict]:
    """Lista conversas apagadas (deleted_at IS NOT NULL) ainda não purgadas — a "lixeira"."""
    ok, detail = _ensure_schema_once()
    if not ok:
        return []
    try:
        conn = _connect()
        cur = conn.cursor()
        if instance_id:
            cur.execute(
                "SELECT instancia_id, conversation_key, display_name, lead_email, "
                "last_activity, deleted_at FROM conversations "
                "WHERE deleted_at IS NOT NULL AND instancia_id = %s ORDER BY deleted_at DESC",
                (instance_id,),
            )
        else:
            cur.execute(
                "SELECT instancia_id, conversation_key, display_name, lead_email, "
                "last_activity, deleted_at FROM conversations "
                "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
            )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {
                "instancia_id": r[0],
                "conversation_key": r[1],
                "display_name": r[2] or r[1],
                "lead_email": r[3] or "",
                "last_activity": r[4].isoformat() if r[4] else "",
                "deleted_at": r[5].isoformat() if r[5] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning(f"[Conversations] Falha ao listar lixeira: {exc}")
        return []


def restore_conversation(instance_id: str, conv_key: str) -> tuple[bool, dict | str]:
    """
    Reverte o soft delete e retorna a conversa completa (com mensagens) pronta
    para reinserir na memória do processo — sem precisar reiniciar o backend.
    """
    ok, detail = _ensure_schema_once()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE conversations SET deleted_at = NULL "
            "WHERE instancia_id = %s AND conversation_key = %s AND deleted_at IS NOT NULL "
            "RETURNING id",
            (instance_id, conv_key),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            conn.close()
            return False, "not_found_or_not_deleted"
        conv_id = str(row[0])
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        return False, str(exc)

    ok, snapshot = load_snapshot()
    if not ok or not isinstance(snapshot, dict):
        return False, "restored_but_reload_failed"
    conv = (snapshot.get(instance_id) or {}).get(conv_key)
    if conv is None:
        return False, "restored_but_not_found_in_snapshot"
    with _sync_lock:
        _synced_hashes.pop((instance_id, conv_key), None)
    return True, conv


def purge_deleted_conversations(older_than_days: int = 30) -> tuple[bool, int, str]:
    """Remove definitivamente conversas apagadas há mais de N dias (retenção da lixeira)."""
    ok, detail = _ensure_schema_once()
    if not ok:
        return False, 0, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM conversations WHERE deleted_at IS NOT NULL "
            "AND deleted_at < now() - (%s * interval '1 day')",
            (older_than_days,),
        )
        purged = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return True, purged, ""
    except Exception as exc:
        return False, 0, str(exc)


def delete_instance_conversations(instance_id: str) -> tuple[bool, str]:
    """Remove todas as conversas de uma instância do Postgres."""
    ok, detail = _ensure_schema_once()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM conversations WHERE instancia_id = %s", (instance_id,))
        conn.commit()
        cur.close()
        conn.close()
        with _sync_lock:
            for k in [k for k in _synced_hashes if k[0] == instance_id]:
                del _synced_hashes[k]
        return True, ""
    except Exception as exc:
        return False, str(exc)


def sync_snapshot(conv_data: dict, allow_empty: bool = False) -> tuple[bool, str]:
    """
    Reparo de deriva periódico: reconcilia o snapshot em RAM com o Postgres.
    Diff por hash (via _synced_hashes, o mesmo cache que upsert_conversation
    mantém no caminho quente) — só regrava conversas cujo conteúdo mudou desde
    o último sync bem-sucedido, e só apaga do Postgres o que saiu da RAM sem
    passar pelo delete explícito (deriva). Substituiu o TRUNCATE + regrava tudo
    anterior, que virava uma transação O(todas as conversas) a cada ciclo.
    """
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    from psycopg2.extras import execute_values

    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        # Guarda anti-wipe: um snapshot totalmente vazio quase sempre indica erro
        # transitório (ex: memória ainda não carregada no startup), não intenção
        # de apagar tudo. Se o banco já tem conversas ativas, preserva e avisa.
        total_convs = sum(len(conv_map or {}) for conv_map in conv_data.values())
        if total_convs == 0:
            if not allow_empty:
                cur.execute("SELECT COUNT(*) FROM conversations WHERE deleted_at IS NULL")
                existing = int(cur.fetchone()[0])
                if existing > 0:
                    cur.close()
                    conn.close()
                    logger.warning(
                        f"[Conversations] Snapshot vazio com {existing} conversas no Postgres — "
                        "sync ignorado para evitar wipe (use allow_empty=True para forçar)."
                    )
                    return True, "skip-empty-guard"
            else:
                # allow_empty=True com RAM vazia é reset explícito e intencional —
                # mesmo comportamento do wipe anterior, não um diff.
                _truncate_all(cur)
                conn.commit()
                cur.close()
                conn.close()
                _invalidate_sync_cache()
                return True, ""

        incoming: dict[tuple[str, str], tuple[dict, str]] = {}
        for instance_id, conv_map in conv_data.items():
            for raw_key, conv in (conv_map or {}).items():
                incoming[(instance_id, raw_key)] = (conv, _conv_hash(conv))

        with _sync_lock:
            cached_hashes = dict(_synced_hashes)
        to_write = {k: v for k, v in incoming.items() if cached_hashes.get(k) != v[1]}

        cur.execute("SELECT instancia_id, conversation_key FROM conversations WHERE deleted_at IS NULL")
        existing_keys = {(row[0], row[1]) for row in cur.fetchall()}
        stale_keys = existing_keys - set(incoming.keys())

        # Guarda anti-deriva-em-massa: um snapshot parcial (ex: falha de leitura
        # de algumas instâncias) faria o diff tratar conversas legítimas como
        # "saíram da RAM" e apagá-las. Se a fração a apagar for grande demais,
        # aborta a limpeza (mas ainda grava o que mudou) e avisa, em vez de
        # confiar cegamente no diff.
        if existing_keys and len(stale_keys) / len(existing_keys) > 0.05 and len(stale_keys) > 5:
            logger.warning(
                f"[Conversations] sync_snapshot: {len(stale_keys)}/{len(existing_keys)} "
                "conversas seriam apagadas por deriva — fração grande demais, "
                "pulando limpeza (possível snapshot parcial). Upserts do que mudou seguem normalmente."
            )
            stale_keys = set()

        for (instance_id, conv_key), (conv, _h) in to_write.items():
            cur.execute(
                f"""
                INSERT INTO conversations (id, {_CONV_COLS})
                VALUES (%s, {_CONV_PLACEHOLDERS})
                ON CONFLICT (instancia_id, conversation_key) DO UPDATE SET {_CONV_UPDATE_SET}
                RETURNING id
                """,
                (str(uuid.uuid4()),) + _conv_row(instance_id, conv_key, conv),
            )
            conv_id = str(cur.fetchone()[0])
            cur.execute("DELETE FROM conversation_messages WHERE conversation_id = %s", (conv_id,))
            msgs = conv.get("messages", []) or []
            if msgs:
                execute_values(
                    cur,
                    """
                    INSERT INTO conversation_messages (
                        id, conversation_id, role, content, timestamp,
                        message_type, provider_message_id, payload_json
                    ) VALUES %s
                    """,
                    [_msg_row(conv_id, m) for m in msgs],
                )

        for instance_id, conv_key in stale_keys:
            cur.execute(
                "DELETE FROM conversations WHERE instancia_id = %s AND conversation_key = %s",
                (instance_id, conv_key),
            )

        conn.commit()
        cur.close()
        conn.close()
        # Cache só reflete o que foi de fato commitado — atualiza depois do commit,
        # nunca antes (senão um rollback deixaria o cache mentindo sobre o Postgres).
        with _sync_lock:
            for k in stale_keys:
                _synced_hashes.pop(k, None)
            for k, (_conv, h) in to_write.items():
                _synced_hashes[k] = h
        return True, ""
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        return False, str(exc)


def validate_snapshot(conv_data: dict) -> tuple[bool, dict]:
    ok, detail = ensure_schema()
    if not ok:
        return False, {"reason": detail}
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM conversations")
        conversations_count = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM conversation_messages")
        messages_count = int(cur.fetchone()[0])
        cur.close()
        conn.close()

        expected_conversations = sum(len(conv_map or {}) for conv_map in conv_data.values())
        expected_messages = sum(
            len((conv or {}).get("messages", []) or [])
            for conv_map in conv_data.values()
            for conv in (conv_map or {}).values()
        )
        actual = {
            "conversations": conversations_count,
            "messages": messages_count,
        }
        expected = {
            "conversations": expected_conversations,
            "messages": expected_messages,
        }
        return actual == expected, {"expected": expected, "actual": actual}
    except Exception as exc:
        return False, {"reason": str(exc)}


def load_conversations_page(
    instance_id: str, cursor: str | None = None, limit: int = 50
) -> tuple[bool, dict | str]:
    """
    Lista resumida e paginada de conversas de uma instância, mais recentes
    primeiro — mesmo formato usado por GET /conversations/{instance_id}, mas
    sem carregar tudo em RAM. Usa o índice conversations_instance_activity_idx.

    Cursor = "<last_activity_iso>|<conversation_key>" da última linha da
    página anterior (tie-breaker por conversation_key evita pular/repetir
    linhas com last_activity idêntico).

    PREVIEW: não é usado por nenhum caminho de produção ainda — existe para
    comparação lado a lado com o caminho em RAM antes de qualquer substituição.
    """
    ok, detail = _ensure_schema_once()
    if not ok:
        return False, detail

    cursor_activity, cursor_key = None, None
    if cursor:
        try:
            cursor_activity, cursor_key = cursor.split("|", 1)
        except ValueError:
            return False, "invalid_cursor"

    try:
        conn = _connect()
        cur = conn.cursor()
        query = (
            "SELECT conversation_key, display_name, stage, ai_paused, unread, "
            "score, score_reason, turn_count, needs_human, handoff_reason, "
            "handoff_at, handoff_source, handoff_handled_at, lead_campanha, "
            "lead_origem, lead_dismissed, source_meta, created_at, last_activity "
            "FROM conversations "
            "WHERE instancia_id = %s AND deleted_at IS NULL"
        )
        params: list = [instance_id]
        if cursor_activity is not None:
            query += " AND (last_activity, conversation_key) < (%s, %s)"
            params += [cursor_activity, cursor_key]
        query += " ORDER BY last_activity DESC, conversation_key DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        items = []
        for r in rows:
            items.append({
                "phone": r[0],
                "name": r[1] or r[0],
                "stage": r[2] or "qualifying",
                "ai_paused": bool(r[3]),
                "unread": bool(r[4]),
                "score": r[5],
                "score_reason": r[6] or "",
                "turn_count": int(r[7] or 0),
                "needs_human": bool(r[8]),
                "handoff_reason": r[9] or "",
                "handoff_at": r[10].isoformat() if r[10] else None,
                "handoff_source": r[11] or "",
                "handoff_handled_at": r[12].isoformat() if r[12] else None,
                "lead_campanha": r[13] or "",
                "lead_origem": r[14] or "",
                "lead_dismissed": bool(r[15]),
                "roleta_active": bool((r[16] or {}).get("roleta_active")),
                "created_at": r[17].isoformat() if r[17] else "",
                "last_activity": r[18].isoformat() if r[18] else "",
            })

        next_cursor = None
        if len(rows) == limit:
            last = rows[-1]
            last_activity_iso = last[18].isoformat() if last[18] else ""
            next_cursor = f"{last_activity_iso}|{last[0]}"

        return True, {"conversations": items, "next_cursor": next_cursor}
    except Exception as exc:
        return False, str(exc)


def load_conversation_messages_page(
    instance_id: str, conv_key: str, before: str | None = None, limit: int = 100
) -> tuple[bool, list | str]:
    """
    Mensagens de UMA conversa, paginadas, mais recentes primeiro — usa o
    índice conversation_messages_conversation_time_idx. `before` é o
    timestamp ISO da mensagem mais antiga já carregada (para "carregar mais
    antigas" ao rolar o inbox para cima).

    PREVIEW: mesma ressalva de load_conversations_page — não usado em produção ainda.
    """
    ok, detail = _ensure_schema_once()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM conversations WHERE instancia_id = %s AND conversation_key = %s "
            "AND deleted_at IS NULL",
            (instance_id, conv_key),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return False, "conversation_not_found"
        conv_id = row[0]

        query = (
            "SELECT role, content, timestamp, message_type, provider_message_id, payload_json "
            "FROM conversation_messages WHERE conversation_id = %s"
        )
        params: list = [conv_id]
        if before:
            query += " AND timestamp < %s"
            params.append(before)
        query += " ORDER BY timestamp DESC, id DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return True, [
            {
                "role": r[0] or "user",
                "content": r[1] or "",
                "timestamp": r[2].isoformat() if r[2] else "",
                "message_type": r[3] or "text",
                "provider_message_id": r[4] or "",
                "payload": r[5] or {},
            }
            for r in rows
        ]
    except Exception as exc:
        return False, str(exc)


def load_snapshot() -> tuple[bool, dict | str]:
    ok, detail = ensure_schema()
    if not ok:
        return False, detail
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id, instancia_id, conversation_key, display_name, stage, ai_paused, unread,
                score, score_reason, turn_count, followup_count, last_followup_at,
                needs_human, handoff_reason, handoff_at, handoff_source,
                handoff_handled_at,
                lead_campanha, lead_email, lead_origem, lead_dismissed, source_meta,
                created_at, last_activity
            FROM conversations
            WHERE deleted_at IS NULL
            ORDER BY instancia_id ASC, last_activity ASC, conversation_key ASC
            """
        )
        snapshot: dict[str, dict] = {}
        conversation_ids: dict[str, tuple[str, str]] = {}
        for row in cur.fetchall():
            conversation_id = str(row[0])
            instance_id = row[1]
            conv_key = row[2]
            snapshot.setdefault(instance_id, {})
            snapshot[instance_id][conv_key] = {
                "name": row[3] or conv_key,
                "stage": row[4] or "qualifying",
                "messages": [],
                "ai_paused": bool(row[5]),
                "unread": bool(row[6]),
                "score": row[7],
                "score_reason": row[8] or "",
                "turn_count": int(row[9] or 0),
                "followup_count": int(row[10] or 0),
                "last_followup_at": row[11].isoformat() if row[11] else None,
                "needs_human": bool(row[12]),
                "handoff_reason": row[13] or "",
                "handoff_at": row[14].isoformat() if row[14] else None,
                "handoff_source": row[15] or "",
                "handoff_handled_at": row[16].isoformat() if row[16] else None,
                "lead_campanha": row[17] or "",
                "lead_email": row[18] or "",
                "lead_origem": row[19] or "",
                "lead_dismissed": bool(row[20]),
                "source_meta": row[21] or {},
                "created_at": row[22].isoformat() if row[22] else "",
                "last_activity": row[23].isoformat() if row[23] else "",
            }
            conversation_ids[conversation_id] = (instance_id, conv_key)

        cur.execute(
            """
            SELECT conversation_id, role, content, timestamp, message_type, provider_message_id, payload_json
            FROM conversation_messages
            ORDER BY timestamp ASC, id ASC
            """
        )
        for row in cur.fetchall():
            owner = conversation_ids.get(str(row[0]))
            if not owner:
                continue
            instance_id, conv_key = owner
            snapshot[instance_id][conv_key]["messages"].append({
                "role": row[1] or "user",
                "content": row[2] or "",
                "timestamp": row[3].isoformat() if row[3] else "",
                "message_type": row[4] or "text",
                "provider_message_id": row[5] or "",
                "payload": row[6] or {},
            })

        cur.close()
        conn.close()
        return True, snapshot
    except Exception as exc:
        return False, str(exc)
