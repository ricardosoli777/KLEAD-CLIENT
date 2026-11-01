"""
Helpers para enviar eventos do CRM para a Meta Conversions API.

A integração é opt-in: se a instância não estiver configurada, as funções
retornam silenciosamente sem interromper o fluxo principal.
"""

from __future__ import annotations

import hashlib
import logging
import json
import os
import re
import threading
import time
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("klead.capi")

import requests

META_CAPI_API_VERSION = "v25.0"
META_CAPI_BASE_URL = f"https://graph.facebook.com/{META_CAPI_API_VERSION}"
import uuid as _uuid

from services import settings_pg_service as _svc_pg
from services import pg_pool
from services import crypto_utils
from services import meta_oauth_pg_service as _meta_oauth_pg

_AUDIT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta_capi_audit (
    id varchar(64) PRIMARY KEY,
    sent_at timestamptz NOT NULL DEFAULT now(),
    ok boolean NOT NULL DEFAULT false,
    detail text NOT NULL DEFAULT '',
    status_code integer,
    instancia_id varchar(128) NOT NULL DEFAULT '',
    lead_id varchar(64) NOT NULL DEFAULT '',
    lead_nome varchar(255) NOT NULL DEFAULT '',
    lead_telefone varchar(32) NOT NULL DEFAULT '',
    origem varchar(64) NOT NULL DEFAULT '',
    event_name varchar(128) NOT NULL DEFAULT '',
    event_source_id varchar(128) NOT NULL DEFAULT '',
    credential_source varchar(16) NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE meta_capi_audit ADD COLUMN IF NOT EXISTS credential_source varchar(16) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS meta_capi_audit_lead_idx ON meta_capi_audit (lead_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS meta_capi_audit_instance_idx ON meta_capi_audit (instancia_id, sent_at DESC);
"""

_audit_schema_ensured = False
_audit_schema_lock = threading.Lock()

# HTTP codes que indicam falha transiente — vale retentar
_TRANSIENT_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
# Delays entre as 3 tentativas imediatas: 2s antes da 2ª, 4s antes da 3ª
_IMMEDIATE_RETRY_DELAYS = [2, 4]
# Falhas de configuração — não vale salvar na fila persistente
CAPI_PERMANENT_DETAILS = frozenset({
    "meta_instance_missing",
    "meta_capi_disabled",
    "meta_capi_missing_credentials",
    "meta_capi_missing_match_data",
})
# Prefixo usado para marcar falhas HTTP definitivas da própria Meta (400/401/403
# com resposta recebida) — para diferenciar de erro transiente (5xx/429/rede),
# que ainda vale a pena reenfileirar para retry.
_PERMANENT_HTTP_DETAIL_PREFIX = "[permanent:http"


def is_permanent_capi_failure(detail: str) -> bool:
    """True se a falha não vale a pena reenfileirar para retry.

    Cobre tanto problemas de configuração local (token/credencial ausente)
    quanto rejeições definitivas da própria Meta (ex: 400 payload inválido,
    401 token expirado) — nesses casos o retry só repetiria o mesmo erro.
    """
    return detail in CAPI_PERMANENT_DETAILS or (detail or "").startswith(_PERMANENT_HTTP_DETAIL_PREFIX)

_capi_retry_lock = threading.Lock()

_DIGITS_RE = re.compile(r"\D+")


def _parse_valor_brl(raw) -> float:
    """Parse Brazilian/US currency string to float.

    Handles formats like: 500000, R$ 500.000, 500.000,00, 500,000.50
    Returns 0.0 for empty or unparseable input.
    """
    clean = re.sub(r"[^\d.,]", "", str(raw or "")).strip()
    if not clean:
        return 0.0
    has_dot = "." in clean
    has_comma = "," in clean
    try:
        if has_dot and has_comma:
            # Whichever comes last is the decimal separator
            if clean.rfind(".") > clean.rfind(","):
                # US format: 1,000.50
                clean = clean.replace(",", "")
            else:
                # BR format: 1.000,50
                clean = clean.replace(".", "").replace(",", ".")
        elif has_dot:
            after = len(clean) - clean.rfind(".") - 1
            if after == 3 or clean.count(".") > 1:
                # Thousands separator: "500.000" or "1.000.000"
                clean = clean.replace(".", "")
            # else decimal separator, leave as-is
        elif has_comma:
            after = len(clean) - clean.rfind(",") - 1
            if after == 3 or clean.count(",") > 1:
                # Thousands separator: "500,000"
                clean = clean.replace(",", "")
            else:
                # Decimal separator: "500,50"
                clean = clean.replace(",", ".")
        return float(clean) if clean else 0.0
    except (ValueError, TypeError):
        return 0.0


def _normalize_text(value) -> str:
    return str(value or "").strip()


def _normalize_email(value: str) -> str:
    return _normalize_text(value).lower()


def _normalize_phone(value: str) -> str:
    digits = _DIGITS_RE.sub("", value or "")
    # Adiciona DDI 55 quando o número vier só com DDD + celular/fixo (10 ou 11 dígitos),
    # como a Meta exige o código do país no campo `ph` para o match funcionar.
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


# Nomes completos de estados brasileiros -> sigla UF, como a Meta espera no campo `st`.
_BR_STATE_TO_UF = {
    "acre": "ac", "alagoas": "al", "amapa": "ap", "amazonas": "am", "bahia": "ba",
    "ceara": "ce", "distrito federal": "df", "espirito santo": "es", "goias": "go",
    "maranhao": "ma", "mato grosso": "mt", "mato grosso do sul": "ms",
    "minas gerais": "mg", "para": "pa", "paraiba": "pb", "parana": "pr",
    "pernambuco": "pe", "piaui": "pi", "rio de janeiro": "rj",
    "rio grande do norte": "rn", "rio grande do sul": "rs", "rondonia": "ro",
    "roraima": "rr", "santa catarina": "sc", "sao paulo": "sp", "sergipe": "se",
    "tocantins": "to",
}

# Variações comuns de nome de país -> código ISO de 2 letras, como a Meta espera no campo `country`.
_COUNTRY_NAME_TO_ISO = {
    "brasil": "br", "brazil": "br",
}


def _normalize_address(value: str) -> str:
    """
    Normaliza campos de endereço genéricos (ex: cidade) antes do hash.

    A Meta recomenda valores limpos e consistentes para campos de correspondência.
    """
    text = _normalize_text(value).lower()
    text = _strip_accents(text)
    return _DIGITS_RE.sub("", text) if text.isdigit() else text


def _normalize_zip(value: str) -> str:
    """CEP/zip: a Meta exige apenas dígitos, sem hífen ou outros caracteres."""
    return _DIGITS_RE.sub("", _normalize_text(value))


def _normalize_state(value: str) -> str:
    """Estado: a Meta espera a sigla (UF) de 2 letras, minúscula."""
    text = _strip_accents(_normalize_text(value).lower())
    if len(text) == 2:
        return text
    return _BR_STATE_TO_UF.get(text, text)


def _normalize_country(value: str) -> str:
    """País: a Meta espera o código ISO 3166-1 alfa-2, minúsculo (ex: 'br')."""
    text = _strip_accents(_normalize_text(value).lower())
    if len(text) == 2:
        return text
    return _COUNTRY_NAME_TO_ISO.get(text, text)


def _hash_value(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _split_name(name: str) -> tuple[str, str]:
    clean = " ".join(_normalize_text(name).split())
    if not clean:
        return "", ""
    parts = clean.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _source_meta_value(lead: dict[str, Any], *aliases: str) -> str:
    source_meta = lead.get("source_meta") or {}
    candidates: list[dict[str, Any]] = []

    if isinstance(source_meta, dict):
        candidates.append(source_meta.get("lead_fields") or {})
        candidates.append(source_meta.get("field_data") or {})
        candidates.append(source_meta.get("raw_value") or {})
        candidates.append(source_meta)

    candidates.append(lead)

    for container in candidates:
        if not isinstance(container, dict):
            continue
        normalized = {
            re.sub(r"[^\w]+", "_", str(key).strip().lower(), flags=re.UNICODE).strip("_"): value
            for key, value in container.items()
        }
        for alias in aliases:
            value = normalized.get(re.sub(r"[^\w]+", "_", alias.strip().lower(), flags=re.UNICODE).strip("_"), "")
            if value not in (None, "", [], {}):
                return str(value)
    return ""


def _build_user_data(lead: dict[str, Any]) -> dict[str, Any]:
    user_data: dict[str, Any] = {}

    email = _normalize_email(lead.get("email", "") or _source_meta_value(lead, "email", "e-mail"))
    if email and "@" in email and "." in email.split("@")[-1]:
        user_data["em"] = [_hash_value(email)]

    phone = _normalize_phone(lead.get("telefone", "") or _source_meta_value(lead, "phone_number", "telefone", "phone", "whatsapp"))
    if phone:
        user_data["ph"] = [_hash_value(phone)]

    full_name = lead.get("nome", "") or _source_meta_value(lead, "full_name", "nome_completo", "nome", "name")
    first_name, last_name = _split_name(full_name)
    if not first_name:
        first_name = _source_meta_value(lead, "first_name")
    if not last_name:
        last_name = _source_meta_value(lead, "last_name")
    if first_name:
        user_data["fn"] = [_hash_value(first_name.lower())]
    if last_name:
        user_data["ln"] = [_hash_value(last_name.lower())]

    lead_id = _normalize_text(lead.get("id", ""))
    if lead_id:
        user_data["external_id"] = [_hash_value(lead_id)]

    meta_lead_id = _normalize_text(
        lead.get("leadgen_id", "")
        or _source_meta_value(lead, "lead_id", "leadgen_id")
    )
    if meta_lead_id.isdigit():
        try:
            user_data["lead_id"] = int(meta_lead_id)
        except Exception:
            user_data["lead_id"] = meta_lead_id
    elif meta_lead_id:
        user_data["lead_id"] = meta_lead_id

    fbc = _source_meta_value(lead, "fbc")
    if fbc:
        user_data["fbc"] = fbc

    fbp = _source_meta_value(lead, "fbp")
    if fbp:
        user_data["fbp"] = fbp

    ctwa_clid = _source_meta_value(lead, "ctwa_clid")
    if ctwa_clid:
        user_data["ctwa_clid"] = ctwa_clid

    client_ip_address = _source_meta_value(lead, "client_ip_address", "ip_address", "ip")
    if client_ip_address:
        user_data["client_ip_address"] = client_ip_address

    client_user_agent = _source_meta_value(lead, "client_user_agent", "user_agent", "useragent")
    if client_user_agent:
        user_data["client_user_agent"] = client_user_agent

    city = lead.get("cidade", "") or _source_meta_value(lead, "city", "cidade", "ct")
    if city:
        user_data["ct"] = [_hash_value(_normalize_address(city))]

    state = lead.get("estado", "") or _source_meta_value(lead, "state", "estado", "uf", "st")
    if state:
        user_data["st"] = [_hash_value(_normalize_state(state))]

    zip_code = lead.get("cep", "") or _source_meta_value(lead, "zip", "postal_code", "cep", "postcode", "postalcode", "zp")
    if zip_code:
        user_data["zp"] = [_hash_value(_normalize_zip(zip_code))]

    country = lead.get("pais", "") or _source_meta_value(lead, "country", "pais", "país")
    if country:
        user_data["country"] = [_hash_value(_normalize_country(country))]

    return user_data


def _build_event_id(lead: dict[str, Any], event_name: str) -> str:
    source_meta = lead.get("source_meta") or {}
    if event_name == "Lead":
        leadgen_id = _normalize_text(lead.get("leadgen_id", "") or source_meta.get("leadgen_id", ""))
        if leadgen_id:
            return f"{leadgen_id}:{event_name}"
        created_at = _normalize_text(lead.get("created_at", ""))
        if created_at:
            return f"{_normalize_text(lead.get('id', ''))}:{event_name}:{created_at}"

    timeline = lead.get("timeline") or []
    if timeline:
        try:
            anchor = _normalize_text(timeline[-1].get("at", ""))
            if anchor:
                return f"{_normalize_text(lead.get('id', ''))}:{event_name}:{anchor}"
        except Exception:
            pass

    updated_at = _normalize_text(lead.get("updated_at", ""))
    if updated_at:
        return f"{_normalize_text(lead.get('id', ''))}:{event_name}:{updated_at}"
    return f"{_normalize_text(lead.get('id', ''))}:{event_name}"


def _build_custom_data(lead: dict[str, Any], origem: str) -> dict[str, Any]:
    source_meta = lead.get("source_meta") or {}
    lead_event_source = (
        _normalize_text(lead.get("lead_event_source", ""))
        or _normalize_text(source_meta.get("lead_event_source", ""))
        or _normalize_text(source_meta.get("crm_name", ""))
        or "KLEAD"
    )
    custom_data = {
        "event_source": "crm",
        "lead_event_source": lead_event_source,
        "crm_stage": _normalize_text(lead.get("coluna_id", "")),
        "crm_status": _normalize_text(lead.get("lead_status", "")),
    }
    meta_lead_id = _normalize_text(
        lead.get("leadgen_id", "")
        or source_meta.get("lead_id", "")
        or source_meta.get("leadgen_id", "")
    )
    if meta_lead_id:
        custom_data["lead_id"] = meta_lead_id
    else:
        internal_lead_id = _normalize_text(lead.get("id", ""))
        if internal_lead_id:
            custom_data["lead_id"] = internal_lead_id
    custom_data.update({
        "lead_id_internal": _normalize_text(lead.get("id", "")),
        "instancia_id": _normalize_text(lead.get("instancia_id", "")),
        "origem": _normalize_text(origem),
        "campanha": _normalize_text(lead.get("campanha", "")),
        "lead_score": _normalize_text(lead.get("score", "")),
        "valor_estimado": _normalize_text(lead.get("valor_estimado", "")),
        "segmento": _normalize_text(lead.get("segmento", "")),
        "poder_aquisitivo": _normalize_text(lead.get("poder_aquisitivo", "")),
        "followup_date": _normalize_text(lead.get("followup_date", "")),
    })
    timeline = lead.get("timeline") or []
    if timeline:
        try:
            custom_data["crm_stage_detail"] = _normalize_text(timeline[-1].get("detail", ""))
        except Exception:
            pass
    for key in ("leadgen_id", "form_id", "page_id", "ad_id", "adgroup_id", "campaign_name", "form_name"):
        value = _normalize_text(lead.get(key, "") or source_meta.get(key, ""))
        if value:
            custom_data[key] = value
    referral = source_meta.get("referral") or {}
    if isinstance(referral, dict):
        for key in ("ctwa_clid", "source_id", "source_url", "headline"):
            value = _normalize_text(referral.get(key, ""))
            if value:
                custom_data[f"meta_referral_{key}"] = value
    form_answers = source_meta.get("form_answers") or {}
    if isinstance(form_answers, dict):
        for key, value in form_answers.items():
            normalized_key = re.sub(r"[^\w]+", "_", str(key).strip().lower(), flags=re.UNICODE).strip("_")
            normalized_value = _normalize_text(str(value))
            if normalized_key and normalized_value:
                custom_data[f"meta_form_{normalized_key}"] = normalized_value
    return {k: v for k, v in custom_data.items() if v}


def _action_source_for_origin(lead: dict[str, Any], origem: str) -> str:
    # Lead com ctwa_clid veio de um anúncio Click-to-WhatsApp — a Meta recomenda
    # "business_messaging" nesse caso para atribuir corretamente o evento ao
    # anúncio de origem (em vez do genérico "system_generated").
    if _source_meta_value(lead, "ctwa_clid"):
        return "business_messaging"
    return "system_generated"


def _audit_entry(
    *,
    inst: dict[str, Any],
    lead: dict[str, Any],
    origem: str,
    event_name: str,
    ok: bool,
    detail: str,
    payload: dict[str, Any] | None = None,
    status_code: int | None = None,
    event_source_id: str = "",
    credential_source: str = "",
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "detail": detail,
        "status_code": status_code,
        "instancia_id": _normalize_text(lead.get("instancia_id", "") or inst.get("id", "")),
        "lead_id": _normalize_text(lead.get("id", "")),
        "lead_nome": _normalize_text(lead.get("nome", "")),
        "lead_telefone": _normalize_text(lead.get("telefone", "")),
        "origem": _normalize_text(origem),
        "event_name": _normalize_text(event_name),
        "event_source_id": _normalize_text(event_source_id or inst.get("meta_capi_event_source_id", "")),
        "credential_source": credential_source,
        "payload": payload or {},
    }


def _ensure_audit_schema() -> bool:
    global _audit_schema_ensured
    if _audit_schema_ensured:
        return True
    with _audit_schema_lock:
        if _audit_schema_ensured:
            return True
        try:
            cfg = _svc_pg._pg_settings()
            with pg_pool.pooled_connection(cfg) as conn:
                cur = conn.cursor()
                cur.execute(_AUDIT_SCHEMA_SQL)
                conn.commit()
                cur.close()
            _audit_schema_ensured = True
            return True
        except Exception as exc:
            logger.warning(f"[META-CAPI] Falha ao garantir schema de auditoria: {exc}")
            return False


def _append_audit(entry: dict[str, Any]) -> None:
    try:
        if not _ensure_audit_schema():
            return
        from psycopg2.extras import Json
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO meta_capi_audit (
                    id, sent_at, ok, detail, status_code, instancia_id,
                    lead_id, lead_nome, lead_telefone, origem,
                    event_name, event_source_id, credential_source, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(_uuid.uuid4()),
                    entry.get("timestamp") or None,
                    bool(entry.get("ok", False)),
                    entry.get("detail", "") or "",
                    entry.get("status_code"),
                    entry.get("instancia_id", "") or "",
                    entry.get("lead_id", "") or "",
                    entry.get("lead_nome", "") or "",
                    entry.get("lead_telefone", "") or "",
                    entry.get("origem", "") or "",
                    entry.get("event_name", "") or "",
                    entry.get("event_source_id", "") or "",
                    entry.get("credential_source", "") or "",
                    Json(entry.get("payload") or {}),
                ),
            )
            conn.commit()
            cur.close()
    except Exception:
        pass


def capi_already_sent_recently(lead_id: str, event_name: str, *, within_hours: int = 24) -> bool:
    """Retorna True se o evento já foi enviado com sucesso para esse lead nas últimas `within_hours` horas."""
    if not lead_id or not event_name:
        return False
    if not _ensure_audit_schema():
        return False
    try:
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM meta_capi_audit
                WHERE lead_id = %s
                  AND event_name = %s
                  AND ok = true
                  AND sent_at >= now() - (%s * interval '1 hour')
                LIMIT 1
                """,
                (_normalize_text(lead_id), _normalize_text(event_name), within_hours),
            )
            found = cur.fetchone() is not None
            cur.close()
        return found
    except Exception as exc:
        logger.warning(f"[META-CAPI] Falha ao checar deduplicação: {exc}")
        return False  # em caso de erro, deixa passar para não bloquear o envio


def get_meta_capi_audit(*, lead_id: str = "", instance_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if not _ensure_audit_schema():
        return []
    try:
        cfg = _svc_pg._pg_settings()
        with pg_pool.pooled_connection(cfg) as conn:
            cur = conn.cursor()
            conditions = []
            params: list = []
            if lead_id:
                conditions.append("lead_id = %s")
                params.append(_normalize_text(lead_id))
            if instance_id:
                conditions.append("instancia_id = %s")
                params.append(_normalize_text(instance_id))
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            cur.execute(
                f"""
                SELECT sent_at, ok, detail, status_code, instancia_id, lead_id,
                       lead_nome, lead_telefone, origem, event_name, event_source_id,
                       credential_source, payload
                FROM meta_capi_audit
                {where}
                ORDER BY sent_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
            cur.close()
        items = []
        for row in rows:
            items.append({
                "timestamp": row[0].isoformat() if row[0] else "",
                "ok": bool(row[1]),
                "detail": row[2] or "",
                "status_code": row[3],
                "instancia_id": row[4] or "",
                "lead_id": row[5] or "",
                "lead_nome": row[6] or "",
                "lead_telefone": row[7] or "",
                "origem": row[8] or "",
                "event_name": row[9] or "",
                "event_source_id": row[10] or "",
                "credential_source": row[11] or "",
                "payload": row[12] or {},
            })
        return items
    except Exception as exc:
        logger.warning(f"[META-CAPI] Falha ao ler auditoria do Postgres: {exc}")
        return []




def _resolve_capi_oauth(instance_id: str) -> tuple[str, str]:
    """
    Resolve (event_source_id, access_token) a partir da conexão OAuth
    (Integração Meta) da instância, se houver um Pixel selecionado.
    A conexão OAuth tem prioridade sobre o token manual — ver
    preview_meta_capi_event/send_meta_capi_event. Retorna ("", "") se não
    houver conexão ativa ou pixel selecionado.
    """
    if not instance_id:
        return "", ""
    assets = _meta_oauth_pg.get_instance_assets(instance_id)
    if not assets or assets.get("status") != "active":
        return "", ""
    pixel_id = assets.get("pixel_id", "")
    if not pixel_id:
        return "", ""
    connection = _meta_oauth_pg.get_connection_by_id(assets.get("meta_connection_id", ""))
    if not connection:
        return "", ""
    access_token = crypto_utils.decrypt_field_for_user(connection["user_email"], connection["access_token_encrypted"])
    if not access_token:
        return "", ""
    return pixel_id, access_token


def _resolve_capi_credentials(inst: dict[str, Any], lead: dict[str, Any]) -> tuple[str, str, str]:
    """
    OAuth (Integração Meta) tem prioridade sobre o token manual quando há um
    Pixel selecionado via OAuth — reflete a intenção mais recente do usuário
    (conectar e escolher ativos) em vez de um token manual esquecido de uma
    configuração anterior. Cai para o manual só quando não há OAuth conectado.
    Retorna (event_source_id, access_token, credential_source) — credential_source
    é "oauth" ou "manual", registrado no log de auditoria para o usuário
    conseguir ver qual credencial disparou cada evento.
    """
    instance_id = _normalize_text(lead.get("instancia_id", "") or inst.get("id", ""))
    oauth_event_source_id, oauth_token = _resolve_capi_oauth(instance_id)
    if oauth_token:
        return oauth_event_source_id, oauth_token, "oauth"
    return (
        _normalize_text(inst.get("meta_capi_event_source_id", "")),
        _normalize_text(inst.get("meta_capi_access_token", "")),
        "manual",
    )


def is_meta_capi_enabled(inst: dict[str, Any], lead: dict[str, Any]) -> bool:
    """Estado efetivo da CAPI, independente do transporte do WhatsApp.

    Uma conexão OAuth ativa com Pixel/Dataset selecionado habilita a CAPI por
    si só. A flag ``meta_capi_enabled`` continua sendo o liga/desliga do modo
    manual legado.
    """
    if not inst:
        return False
    if inst.get("meta_capi_enabled"):
        return True
    instance_id = _normalize_text(lead.get("instancia_id", "") or inst.get("id", ""))
    event_source_id, access_token = _resolve_capi_oauth(instance_id)
    return bool(event_source_id and access_token)


def preview_meta_capi_event(
    inst: dict[str, Any],
    lead: dict[str, Any],
    *,
    origem: str,
    event_name: str = "Lead",
) -> tuple[bool, dict[str, Any] | None, str]:
    if not inst:
        return False, None, "meta_instance_missing"
    event_source_id, access_token, credential_source = _resolve_capi_credentials(inst, lead)
    # OAuth + pixel selecionado é uma configuração completa e não depende da
    # flag do formulário manual. Sem OAuth, a flag continua obrigatória.
    if credential_source != "oauth" and not inst.get("meta_capi_enabled"):
        return False, None, "meta_capi_disabled"
    if not event_source_id or not access_token:
        return False, None, "meta_capi_missing_credentials"

    user_data = _build_user_data(lead)
    if not user_data:
        return False, None, "meta_capi_missing_match_data"

    custom_data = _build_custom_data(lead, origem)
    # Purchase exige currency obrigatório (error_subcode 2804010 sem ele).
    # value=0 é aceito pela Meta quando currency está presente.
    if event_name == "Purchase":
        value = _parse_valor_brl(lead.get("valor_estimado"))
        custom_data["currency"] = "BRL"
        custom_data["value"] = value if value > 0 else 0

    payload = {
        "data": [
            {
                "event_name": event_name,
                "event_time": int(time.time()),
                "action_source": _action_source_for_origin(lead, origem),
                "event_id": _build_event_id(lead, event_name),
                "user_data": user_data,
                "custom_data": custom_data,
            }
        ]
    }
    test_event_code = _normalize_text(inst.get("meta_capi_test_event_code", ""))
    if test_event_code:
        payload["test_event_code"] = test_event_code
    return True, payload, ""


def send_meta_capi_event(
    inst: dict[str, Any],
    lead: dict[str, Any],
    *,
    origem: str,
    event_name: str = "Lead",
) -> tuple[bool, str]:
    """
    Envia um evento para a Meta Conversions API com retry automático.

    Tenta até 3 vezes para erros transientes (rede, 5xx, 429).
    Erros permanentes (token/payload inválido) falham imediatamente.
    Retorna (ok, detalhe). Falhas de configuração retornam False sem exceção.
    """
    ok, payload, detail = preview_meta_capi_event(inst, lead, origem=origem, event_name=event_name)
    if not ok:
        _append_audit(_audit_entry(
            inst=inst, lead=lead, origem=origem, event_name=event_name,
            ok=False, detail=detail,
        ))
        return False, detail

    event_source_id, access_token, credential_source = _resolve_capi_credentials(inst, lead)
    url = f"{META_CAPI_BASE_URL}/{event_source_id}/events"
    last_detail = ""
    last_status: int | None = None

    for attempt in range(3):
        try:
            response = requests.post(
                url,
                params={"access_token": access_token},
                json=payload,
                timeout=15,
            )
            last_status = response.status_code
            if response.status_code in (200, 201):
                _append_audit(_audit_entry(
                    inst=inst, lead=lead, origem=origem, event_name=event_name,
                    ok=True, detail="", payload=payload, status_code=last_status,
                    event_source_id=event_source_id, credential_source=credential_source,
                ))
                return True, ""
            last_detail = response.text[:200] or f"HTTP {response.status_code}"
            if response.status_code not in _TRANSIENT_HTTP_CODES:
                # Resposta definitiva da Meta (ex: 400/401/403) — marca como
                # permanente para não ser reenfileirada indefinidamente.
                last_detail = f"{_PERMANENT_HTTP_DETAIL_PREFIX}{response.status_code}] {last_detail}"
                break  # erro permanente — não adianta retentar
            if attempt < 2:
                time.sleep(_IMMEDIATE_RETRY_DELAYS[attempt])
        except requests.RequestException as exc:
            # Não usar str(exc): erros de rede do requests repetem a URL completa,
            # que carrega o access_token na query string — vazaria no audit/log.
            last_detail = f"erro de rede ({exc.__class__.__name__})"
            last_status = None
            if attempt < 2:
                time.sleep(_IMMEDIATE_RETRY_DELAYS[attempt])

    _append_audit(_audit_entry(
        inst=inst, lead=lead, origem=origem, event_name=event_name,
        ok=False, detail=last_detail, payload=payload, status_code=last_status,
        event_source_id=event_source_id, credential_source=credential_source,
    ))
    return False, last_detail


def queue_meta_capi_event(
    inst: dict[str, Any],
    lead: dict[str, Any],
    *,
    origem: str,
    event_name: str = "Lead",
) -> None:
    """
    Dispara o envio em background para não adicionar latência ao fluxo principal.
    """
    try:
        inst_copy = deepcopy(inst)
        lead_copy = deepcopy(lead)
    except Exception:
        inst_copy = inst
        lead_copy = lead

    def _worker():
        instance_id = inst_copy.get("id", inst_copy.get("instance_id", ""))
        try:
            ok, detail = send_meta_capi_event(inst_copy, lead_copy, origem=origem, event_name=event_name)
            if not ok:
                if is_permanent_capi_failure(detail):
                    logger.warning(f"[META-CAPI] Evento '{event_name}' ignorado (falha permanente): {detail}")
                else:
                    # Falha transiente — enfileirar para retry atrasado
                    logger.warning(f"[META-CAPI] Falha transiente '{event_name}': {detail}. Enfileirando retry.")
                    enqueue_capi_retry(instance_id, lead_copy, origem=origem, event_name=event_name, attempt=0)
        except Exception as exc:
            logger.warning(f"[META-CAPI] Erro inesperado '{event_name}': {exc}. Enfileirando retry.")
            try:
                enqueue_capi_retry(instance_id, lead_copy, origem=origem, event_name=event_name, attempt=0)
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True, name=f"meta-capi-{event_name.lower()}").start()


# ── Fila persistente de retry CAPI ────────────────────────────────────────────

_CAPI_RETRY_PG_KEY = "capi_retry_queue"


def enqueue_capi_retry(
    instance_id: str,
    lead: dict[str, Any],
    *,
    origem: str,
    event_name: str,
    attempt: int = 0,
) -> None:
    """Salva evento CAPI em fila persistente para retry atrasado."""
    _DELAYS = [300, 900, 3600]  # 5min, 15min, 1h
    entry = {
        "id": f"{instance_id}:{_normalize_text(lead.get('id', ''))}:{event_name}:{int(time.time())}",
        "instance_id": instance_id,
        "lead": lead,
        "origem": origem,
        "event_name": event_name,
        "attempt": attempt,
        "next_retry_at": time.time() + _DELAYS[min(attempt, len(_DELAYS) - 1)],
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    with _capi_retry_lock:
        ok, data = _svc_pg.load_setting(_CAPI_RETRY_PG_KEY)
        queue: list = data if (ok and isinstance(data, list)) else []
        queue.append(entry)
        _svc_pg.save_setting(_CAPI_RETRY_PG_KEY, queue)
    logger.warning(f"[META-CAPI-RETRY] Enfileirado '{event_name}' para instância={instance_id} tentativa={attempt+1}")


def load_capi_retry_queue() -> list[dict[str, Any]]:
    with _capi_retry_lock:
        ok, data = _svc_pg.load_setting(_CAPI_RETRY_PG_KEY)
        return data if (ok and isinstance(data, list)) else []


def remove_capi_retry_item(item_id: str) -> None:
    with _capi_retry_lock:
        ok, data = _svc_pg.load_setting(_CAPI_RETRY_PG_KEY)
        if not ok or not isinstance(data, list):
            return
        filtered = [x for x in data if x.get("id") != item_id]
        _svc_pg.save_setting(_CAPI_RETRY_PG_KEY, filtered)
