"""
Persistência PostgreSQL para histórico diário de métricas Meta Ads.
Salva um snapshot por dia por instância (campanhas + totais da conta).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone, timedelta
from typing import Any

from services import pg_pool

logger = logging.getLogger("klead.meta_ads_history")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta_ads_snapshots (
    id          SERIAL PRIMARY KEY,
    instance_id VARCHAR(128) NOT NULL,
    snapshot_date DATE NOT NULL,
    level       VARCHAR(32) NOT NULL DEFAULT 'campaign',
    name        TEXT NOT NULL DEFAULT '',
    impressions BIGINT NOT NULL DEFAULT 0,
    clicks      BIGINT NOT NULL DEFAULT 0,
    spend       NUMERIC(12,2) NOT NULL DEFAULT 0,
    leads       INT NOT NULL DEFAULT 0,
    cpm         NUMERIC(10,2) NOT NULL DEFAULT 0,
    cpc         NUMERIC(10,2) NOT NULL DEFAULT 0,
    ctr         NUMERIC(8,4) NOT NULL DEFAULT 0,
    cpl         NUMERIC(10,2) NULL,
    reach       BIGINT NOT NULL DEFAULT 0,
    frequency   NUMERIC(8,4) NOT NULL DEFAULT 0,
    quality_ranking TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS meta_ads_snapshots_unique_idx
ON meta_ads_snapshots (instance_id, snapshot_date, level, name);

CREATE INDEX IF NOT EXISTS meta_ads_snapshots_instance_date_idx
ON meta_ads_snapshots (instance_id, snapshot_date DESC);
"""


def _pg_settings() -> dict:
    host     = os.getenv("CRM_PG_HOST") or os.getenv("EVO_PG_HOST", "")
    port     = int(os.getenv("CRM_PG_PORT") or os.getenv("EVO_PG_PORT", "5432"))
    user     = os.getenv("CRM_PG_USER") or os.getenv("EVO_PG_USER", "postgres")
    password = os.getenv("CRM_PG_PASSWORD") or os.getenv("EVO_PG_PASSWORD", "")
    # Fallback fixo em "klead" — nunca cair no banco do Evolution GO (EVO_PG_DB).
    dbname   = os.getenv("CRM_PG_DB") or "klead"
    return {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}


def _pg_configured() -> bool:
    cfg = _pg_settings()
    return bool(cfg["host"] and cfg["password"] and cfg["dbname"])


def _connect():
    cfg = _pg_settings()
    return pg_pool.get_connection(cfg)


def _ensure_schema() -> bool:
    if not _pg_configured():
        return False
    try:
        conn = _connect()
        cur  = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as exc:
        logger.warning(f"[ADS-HISTORY] Falha ao criar schema: {exc}")
        return False


def save_snapshot(instance_id: str, metrics: dict[str, Any]) -> tuple[bool, str]:
    """
    Salva snapshot diário de métricas.
    metrics: retorno de _fetch_campaign_metrics()
    Ignora se já existe snapshot hoje (idempotente).
    """
    if not _ensure_schema():
        return False, "postgres_not_configured"

    today = date.today()
    rows: list[tuple] = []

    # Linha por campanha
    for c in metrics.get("campaigns", []):
        clicks = c.get("clicks", 0) or 0
        impr   = c.get("impressions", 0) or 1
        ctr    = round(clicks / impr * 100, 4)
        rows.append((
            instance_id, today, "campaign", c.get("campaign", ""),
            c.get("impressions", 0), clicks, c.get("spend", 0),
            c.get("leads", 0), c.get("cpm", 0), c.get("cpc", 0),
            ctr, c.get("cpl"), c.get("reach", 0), c.get("frequency", 0),
            c.get("quality_ranking", ""),
        ))

    # Linha de totais da conta
    total_clicks = metrics.get("total_clicks") or sum(c.get("clicks", 0) for c in metrics.get("campaigns", []))
    total_impr   = sum(c.get("impressions", 0) for c in metrics.get("campaigns", [])) or 1
    total_ctr    = round(total_clicks / total_impr * 100, 4)
    rows.append((
        instance_id, today, "account", "__total__",
        total_impr, total_clicks, metrics.get("total_spend", 0),
        metrics.get("total_leads", 0), 0, 0,
        total_ctr, metrics.get("avg_cpl"), metrics.get("total_reach", 0), 0, "",
    ))

    try:
        conn = _connect()
        cur  = conn.cursor()
        # Remove o snapshot do dia antes de regravar — evita misturar campanhas
        # de uma conta de anuncio antiga (ja trocada nas credenciais) com os
        # dados reais e atuais buscados agora na Graph API.
        cur.execute(
            "DELETE FROM meta_ads_snapshots WHERE instance_id = %s AND snapshot_date = %s",
            (instance_id, today),
        )
        cur.executemany(
            """
            INSERT INTO meta_ads_snapshots
                (instance_id, snapshot_date, level, name,
                 impressions, clicks, spend, leads, cpm, cpc,
                 ctr, cpl, reach, frequency, quality_ranking)
            VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s)
            ON CONFLICT (instance_id, snapshot_date, level, name) DO NOTHING
            """,
            rows,
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[ADS-HISTORY] Snapshot salvo: {instance_id} / {today} / {len(rows)-1} campanhas")
        return True, ""
    except Exception as exc:
        logger.warning(f"[ADS-HISTORY] Falha ao salvar snapshot: {exc}")
        return False, str(exc)


def get_history(instance_id: str, days: int = 35) -> list[dict[str, Any]]:
    """
    Retorna snapshots dos últimos N dias para a instância.
    Retorna lista de dicts ordenada por data desc.
    """
    if not _pg_configured():
        return []
    try:
        conn = _connect()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT snapshot_date, level, name,
                   impressions, clicks, spend, leads,
                   cpm, cpc, ctr, cpl, reach, frequency, quality_ranking
            FROM meta_ads_snapshots
            WHERE instance_id = %s
              AND snapshot_date >= %s
            ORDER BY snapshot_date DESC, level, name
            """,
            (instance_id, date.today() - timedelta(days=days)),
        )
        cols = ["snapshot_date","level","name","impressions","clicks","spend","leads",
                "cpm","cpc","ctr","cpl","reach","frequency","quality_ranking"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
        # Converter date para string
        for r in rows:
            r["snapshot_date"] = r["snapshot_date"].isoformat()
        return rows
    except Exception as exc:
        logger.warning(f"[ADS-HISTORY] Falha ao buscar histórico: {exc}")
        return []


def get_weekly_comparison(instance_id: str) -> dict[str, Any]:
    """
    Retorna comparativo últimos 7 dias vs 7 dias anteriores.

    Cada snapshot guarda dados ACUMULADOS dos últimos 30 dias da Meta API.
    Para calcular o que entrou num período de 7 dias, usamos o DELTA entre
    dois snapshots (mais recente − snapshot de 7 dias atrás).

    this_week  = snapshot_hoje  − snapshot_7d_atrás
    last_week  = snapshot_7d_atrás − snapshot_14d_atrás
    """
    if not _pg_configured():
        return {}
    try:
        conn = _connect()
        cur  = conn.cursor()
        today = date.today()
        cur.execute(
            """
            SELECT snapshot_date, leads, spend, clicks, impressions, reach, ctr, cpl, cpm
            FROM meta_ads_snapshots
            WHERE instance_id = %s
              AND level = 'account'
              AND name  = '__total__'
              AND snapshot_date >= %s
            ORDER BY snapshot_date DESC
            """,
            (instance_id, today - timedelta(days=16)),
        )
        cols = ["snapshot_date", "leads", "spend", "clicks", "impressions", "reach", "ctr", "cpl", "cpm"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()

        if not rows:
            return {}

        def _nearest(target: date) -> dict | None:
            before = [r for r in rows if r["snapshot_date"] <= target]
            after  = [r for r in rows if r["snapshot_date"] > target]
            if before:
                return before[0]
            if after:
                return after[-1]
            return None

        snap_now  = rows[0]
        snap_7d   = _nearest(today - timedelta(days=7))
        snap_14d  = _nearest(today - timedelta(days=14))

        def _delta(newer: dict, older: dict | None) -> dict | None:
            if not older:
                return None
            return {
                "leads":       max(0, int(newer["leads"] or 0)       - int(older["leads"] or 0)),
                "spend":       max(0.0, float(newer["spend"] or 0)   - float(older["spend"] or 0)),
                "clicks":      max(0, int(newer["clicks"] or 0)      - int(older["clicks"] or 0)),
                "impressions": max(0, int(newer["impressions"] or 0) - int(older["impressions"] or 0)),
                "reach":       max(0, int(newer["reach"] or 0)       - int(older["reach"] or 0)),
                "ctr":         float(newer["ctr"] or 0),
                "cpl":         float(newer["cpl"]) if newer["cpl"] else None,
                "cpm":         float(newer["cpm"] or 0),
            }

        this_week = _delta(snap_now, snap_7d)
        last_week = _delta(snap_7d, snap_14d) if snap_7d else None

        if not this_week:
            return {}

        result: dict[str, Any] = {
            "this_week": this_week,
            "last_week": last_week or {},
        }

        deltas: dict[str, Any] = {}
        for key in ("spend", "leads", "clicks", "ctr", "cpl", "cpm"):
            tv = this_week.get(key)
            lv = (last_week or {}).get(key)
            if tv is not None and lv and lv != 0:
                deltas[key] = round((tv - lv) / lv * 100, 1)
            else:
                deltas[key] = None
        result["delta_pct"] = deltas
        return result
    except Exception as exc:
        logger.warning(f"[ADS-HISTORY] Falha ao calcular comparativo: {exc}")
        return {}
