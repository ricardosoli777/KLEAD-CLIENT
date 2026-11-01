import os
import json
import logging

from services import pg_pool

logger = logging.getLogger(__name__)

CHUNK_SIZE    = 400   # palavras por chunk
CHUNK_OVERLAP = 60    # palavras de sobreposição entre chunks
EMBED_MODEL   = "text-embedding-3-small"


# ── Conexão PostgreSQL ────────────────────────────────────────────────────────

def _pg_conn():
    cfg = {
        "host": os.getenv("EVO_PG_HOST", "postgres"),
        "port": int(os.getenv("EVO_PG_PORT", "5432")),
        "user": os.getenv("EVO_PG_USER", "postgres"),
        "password": os.getenv("EVO_PG_PASSWORD", ""),
        # Fallback fixo em "klead" — nunca cair no banco do Evolution GO (EVO_PG_DB).
        "dbname": os.getenv("CRM_PG_DB") or "klead",
    }
    return pg_pool.get_connection(cfg)


def ensure_table():
    """Cria a tabela de embeddings se não existir. Chamado no startup."""
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inbox_kb_chunks (
                id           SERIAL PRIMARY KEY,
                instance_id  TEXT NOT NULL,
                filename     TEXT NOT NULL,
                chunk_index  INTEGER NOT NULL,
                chunk_text   TEXT NOT NULL,
                embedding    JSONB NOT NULL,
                created_at   TIMESTAMP DEFAULT NOW(),
                UNIQUE (instance_id, filename, chunk_index)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_chunks_instance "
            "ON inbox_kb_chunks(instance_id)"
        )
        conn.commit()
        conn.close()
        logger.info("[KB-EMBED] Tabela inbox_kb_chunks pronta.")
    except Exception as e:
        logger.warning(f"[KB-EMBED] Não foi possível criar tabela (PG indisponível?): {e}")


# ── Chave de embedding ────────────────────────────────────────────────────────

def resolve_embed_key(inst: dict) -> str:
    """
    Ordem de prioridade para a chave OpenAI usada em embeddings:
    1. Variável de ambiente EMBEDDING_OPENAI_KEY (chave dedicada do sistema)
    2. openai_key da instância (chave de campanha — sempre OpenAI)
    3. llm_api_key da instância se o provider for openai
    """
    key = os.getenv("EMBEDDING_OPENAI_KEY", "").strip()
    if key:
        return key
    key = (inst.get("openai_key") or "").strip()
    if key:
        return key
    if inst.get("llm_provider") == "openai":
        key = (inst.get("llm_api_key") or "").strip()
        if key:
            return key
    return ""


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end = start + CHUNK_SIZE
        chunks.append(" ".join(words[start:end]))
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


# ── Embeddings via OpenAI ─────────────────────────────────────────────────────

def _embed(texts: list[str], api_key: str) -> list[list[float]]:
    import openai
    client = openai.OpenAI(api_key=api_key, timeout=60, max_retries=1)
    resp   = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]


# ── Indexação ─────────────────────────────────────────────────────────────────

def index_file(instance_id: str, filename: str, text: str, api_key: str) -> int:
    """
    Chunka, embeda e salva no PostgreSQL. Deleta índice anterior do mesmo arquivo.
    Retorna número de chunks indexados, ou 0 em caso de erro/sem chave.
    """
    if not api_key or not text.strip():
        return 0

    chunks = chunk_text(text)
    if not chunks:
        return 0

    try:
        embeddings = _embed(chunks, api_key)
    except Exception as e:
        logger.warning(f"[KB-EMBED] Erro ao gerar embeddings para '{filename}': {e}")
        return 0

    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM inbox_kb_chunks WHERE instance_id=%s AND filename=%s",
            (instance_id, filename),
        )
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                """INSERT INTO inbox_kb_chunks
                       (instance_id, filename, chunk_index, chunk_text, embedding)
                   VALUES (%s, %s, %s, %s, %s)""",
                (instance_id, filename, i, chunk, json.dumps(emb)),
            )
        conn.commit()
        conn.close()
        logger.info(f"[KB-EMBED] {len(chunks)} chunks indexados — {instance_id}/{filename}")
        return len(chunks)
    except Exception as e:
        logger.warning(f"[KB-EMBED] Erro ao salvar no PG: {e}")
        return 0


def delete_file(instance_id: str, filename: str):
    """Remove todos os chunks de um arquivo da KB."""
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM inbox_kb_chunks WHERE instance_id=%s AND filename=%s",
            (instance_id, filename),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[KB-EMBED] Erro ao deletar chunks de '{filename}': {e}")


def delete_instance(instance_id: str):
    """Remove todos os chunks de KB de uma instância (chamado ao deletar instância)."""
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM inbox_kb_chunks WHERE instance_id=%s", (instance_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[KB-EMBED] Erro ao deletar chunks da instância '{instance_id}': {e}")


# ── Busca semântica ───────────────────────────────────────────────────────────

def search(instance_id: str, query: str, api_key: str, top_k: int = 5) -> str:
    """
    Retorna os top_k chunks mais relevantes para a query como texto único.
    Fallback silencioso: retorna "" se PG indisponível ou sem chave.
    """
    if not api_key or not query.strip():
        return ""

    try:
        import numpy as np

        query_emb = _embed([query], api_key)[0]
        q = np.array(query_emb, dtype=float)

        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT chunk_text, embedding FROM inbox_kb_chunks WHERE instance_id=%s",
            (instance_id,),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return ""

        scored = []
        for chunk_text, emb_raw in rows:
            emb = np.array(
                json.loads(emb_raw) if isinstance(emb_raw, str) else emb_raw,
                dtype=float,
            )
            norm = np.linalg.norm(q) * np.linalg.norm(emb)
            sim  = float(np.dot(q, emb) / norm) if norm > 0 else 0.0
            scored.append((sim, chunk_text))

        scored.sort(reverse=True)
        return "\n\n".join(chunk for _, chunk in scored[:top_k])

    except Exception as e:
        logger.warning(f"[KB-EMBED] Erro na busca semântica: {e}")
        return ""
