"""
pg_pool — pool de conexões Postgres compartilhado.

Hoje cada módulo abre e fecha uma conexão nova a cada chamada
(psycopg2.connect(...) -> ... -> conn.close()). Isso funciona mas paga um
custo de latência (handshake TCP + auth) em toda query. Este módulo mantém
um pool por combinação de credenciais (host/port/user/password/dbname),
criado sob demanda e reaproveitado entre chamadas.

Uso:
    from services import pg_pool

    cfg = _pg_settings()
    with pg_pool.pooled_connection(cfg) as conn:
        cur = conn.cursor()
        cur.execute(...)
        conn.commit()
        cur.close()
    # a conexão volta pro pool automaticamente ao sair do "with",
    # mesmo se uma exceção for lançada — não feche `conn` manualmente.
"""

from __future__ import annotations

import logging
import os
import threading

import psycopg2
from psycopg2 import pool as _pgpool

logger = logging.getLogger("klead.pg_pool")

_MINCONN = 1
_MAXCONN = int(os.getenv("PG_POOL_MAXCONN", "40"))

_pools: dict[tuple, "_pgpool.ThreadedConnectionPool"] = {}
_pools_lock = threading.Lock()


def _pool_key(cfg: dict) -> tuple:
    return (
        cfg.get("host"),
        cfg.get("port"),
        cfg.get("user"),
        cfg.get("password"),
        cfg.get("dbname"),
    )


def _pool_for(cfg: dict) -> "_pgpool.ThreadedConnectionPool":
    key = _pool_key(cfg)
    existing = _pools.get(key)
    if existing is not None:
        return existing
    with _pools_lock:
        existing = _pools.get(key)
        if existing is not None:
            return existing
        pool = _pgpool.ThreadedConnectionPool(
            _MINCONN,
            _MAXCONN,
            host=cfg.get("host"),
            port=cfg.get("port"),
            user=cfg.get("user"),
            password=cfg.get("password"),
            dbname=cfg.get("dbname"),
            connect_timeout=5,
            # Defesa no servidor: query presa ou lock esperando não segura mais
            # o app inteiro — Postgres cancela sozinho. Foi um DDL esperando
            # lock (sem esses timeouts) que congelou o klead em produção.
            options=(
                f"-c statement_timeout={os.getenv('PG_STATEMENT_TIMEOUT_MS', '30000')} "
                f"-c lock_timeout={os.getenv('PG_LOCK_TIMEOUT_MS', '10000')} "
                f"-c idle_in_transaction_session_timeout={os.getenv('PG_IDLE_TX_TIMEOUT_MS', '60000')}"
            ),
        )
        _pools[key] = pool
        logger.info(f"[PG-POOL] Pool criado para dbname={cfg.get('dbname')} (min={_MINCONN}, max={_MAXCONN})")
        return pool


class _PooledConnProxy:
    """
    Proxy que se comporta como uma conexão psycopg2 normal (cursor(), commit(),
    rollback(), etc.), mas onde `.close()` devolve a conexão para o pool em vez
    de fechar de verdade.

    Existe para permitir migrar módulos legados trocando só a função `_connect()`
    de cada um — todos os `conn.close()` já espalhados pelo código continuam
    funcionando sem precisar editar cada ocorrência.
    """

    __slots__ = ("_pool", "_conn", "_released")

    def __init__(self, pool: "_pgpool.ThreadedConnectionPool", conn):
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_released", False)

    def close(self):
        if self._released:
            return
        object.__setattr__(self, "_released", True)
        try:
            if getattr(self._conn, "closed", 0) == 0:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                self._pool.putconn(self._conn)
            else:
                # Conexão já morreu (erro fatal) — descarta em vez de devolver ao pool.
                self._pool.putconn(self._conn, close=True)
        except Exception as exc:
            # Se o próprio putconn falhar, a conexão pode ficar "presa" como
            # em uso na contabilidade interna do pool (nunca volta a ficar
            # disponível) — fechar aqui evita ao menos vazar o socket TCP, mas
            # o slot do pool pode ficar perdido. Loga para isso não passar em
            # silêncio caso vire um padrão recorrente (sinal de esgotamento).
            logger.warning(f"[PG-POOL] putconn falhou ao devolver conexão — pode reduzir a capacidade efetiva do pool: {exc}")
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self):
        # Rede de segurança: os módulos legados fazem `conn = _connect()` e, em
        # caminho de erro, retornam sem chamar close() — sem isto a conexão nunca
        # voltaria ao pool e ele esgotaria após _MAXCONN erros acumulados.
        # close() é idempotente (_released), então quem já fechou não é afetado.
        try:
            self.close()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def get_connection(cfg: dict) -> "_PooledConnProxy":
    """
    Retorna uma conexão do pool que se comporta como psycopg2.connect() normal.
    Chamar `.close()` nela devolve a conexão ao pool em vez de fechar de verdade.

    Uso (substitui 1 para 1 uma chamada psycopg2.connect() existente):
        def _connect():
            cfg = _pg_settings()
            return pg_pool.get_connection(cfg)
    """
    pool = _pool_for(cfg)
    conn = pool.getconn()
    return _PooledConnProxy(pool, conn)


class pooled_connection:
    """Context manager: empresta uma conexão do pool e devolve ao sair (mesmo em erro)."""

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._pool: "_pgpool.ThreadedConnectionPool | None" = None
        self._conn = None

    def __enter__(self):
        self._pool = _pool_for(self._cfg)
        self._conn = self._pool.getconn()
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if self._conn is None or self._pool is None:
            return False
        try:
            if exc_type is not None:
                # Se a transação ficou aberta/abortada por causa da exceção,
                # devolver sem rollback deixaria a próxima query nessa conexão falhar.
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            self._pool.putconn(self._conn)
        except Exception as exc:
            logger.warning(f"[PG-POOL] putconn falhou ao devolver conexão — pode reduzir a capacidade efetiva do pool: {exc}")
            # Conexão pode estar em estado ruim — descarta em vez de devolver ao pool.
            try:
                self._conn.close()
            except Exception:
                pass
        return False
