"""
routers/webhook.py — Webhooks: Meta Lead Ads, Meta Forms, Evolution GO, SSE, Métricas.

Helpers de negócio (process_meta_webhook_body, handle_inbound, etc.) permanecem em
main.py e são acessados via request.app.state.* para evitar importação circular.
Estado mutável (instances, conversations, locks) importado de core.state.
SSE importado de core.sse.
"""
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shlex
import threading
import queue as _queue_mod
from concurrent.futures import ThreadPoolExecutor

import requests
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from core.limiter import limiter
from core.security import require_api_key
from core.state import instances, conversations, instances_lock, conversations_lock
from core.sse import _sse_listeners, _sse_lock
import services.auth_service as auth_svc
from services.auth_service import require_admin as _require_admin
from services.ads_analyzer_service import analyze_meta_ads, chat_meta_ads, fetch_pixel_quality
from services.meta_ads_history_service import save_snapshot, get_history, get_weekly_comparison
import services.meta_oauth_pg_service as meta_oauth_pg
from services.outbound_routing import has_evolution_outbound

logger = logging.getLogger("klead")

router = APIRouter(tags=["webhook"])

_INTERNAL_WEBHOOK_BASE = os.getenv("WEBHOOK_BASE_URL", "http://klead_backend:8000")
_PUBLIC_WEBHOOK_BASE = (os.getenv("PUBLIC_WEBHOOK_BASE", "") or "https://api.example.com").strip()

# Pool limitado de threads para processar mensagens inbound (texto e áudio).
# Antes cada mensagem criava uma threading.Thread() sem limite — com muitas
# instâncias recebendo mensagens na mesma janela de tempo (ex: várias campanhas
# disparando junto), isso podia gerar centenas de threads simultâneas sem controle.
# Um pool limitado enfileira o excedente em vez de estourar recursos do processo.
_INBOUND_MAX_WORKERS = int(os.getenv("INBOUND_MAX_WORKERS", "64"))
_inbound_executor = ThreadPoolExecutor(max_workers=_INBOUND_MAX_WORKERS, thread_name_prefix="inbound-worker")


def _submit_inbound(func, *args) -> None:
    _inbound_executor.submit(func, *args)


# ── Helpers locais (funções puras sem dependência de estado global) ─────────────

_EVOLUTION_JID_KEYS = frozenset({
    "chat", "chatalt", "sender", "senderalt", "senderjid", "senderlid",
    "senderpn", "remotejid", "remotejidalt", "participant", "participantalt",
})


def _build_evolution_identity_meta(data: dict, primary_jid: str, parse_jid, channel: str) -> dict:
    """Extrai PN/LID presentes no payload sem inferir um a partir do outro."""
    candidates: list[str] = []

    def _walk(value, depth: int = 0) -> None:
        if depth > 4 or not isinstance(value, dict):
            return
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized_key in _EVOLUTION_JID_KEYS and isinstance(item, str) and "@" in item:
                candidates.append(item.strip())
            elif isinstance(item, dict):
                _walk(item, depth + 1)

    if primary_jid:
        candidates.append(primary_jid.strip())
    _walk(data)
    candidates = list(dict.fromkeys(item for item in candidates if item))

    pn_jid = ""
    lid_jid = ""
    typed_aliases: list[str] = []
    primary_type = ""
    for candidate in candidates:
        parsed = parse_jid(candidate)
        jid_type = str(parsed.get("type", ""))
        user = str(parsed.get("user", ""))
        server = str(parsed.get("server", ""))
        canonical = f"{user}@{server}" if user and server else candidate
        if jid_type in ("pn", "lid"):
            typed_aliases.append(canonical)
        if jid_type == "pn" and not pn_jid:
            pn_jid = canonical
        elif jid_type == "lid" and not lid_jid:
            lid_jid = canonical
        if candidate == primary_jid:
            primary_type = jid_type

    identity = {
        "raw_jid": primary_jid,
        "jid_type": primary_type,
        "pn_jid": pn_jid,
        "lid_jid": lid_jid,
        "jid_aliases": list(dict.fromkeys(typed_aliases)),
        "mapping_observed": bool(pn_jid and lid_jid),
    }
    identity = {k: v for k, v in identity.items() if v not in (None, "", [], {})}
    return {
        "provider": "evolution",
        "channel": channel,
        "evolution_identity": identity,
    }


def _existing_meta_conversation_key(instance_id: str, values: list[str], normalize) -> tuple[str, bool]:
    """Retorna uma chave existente única; conflito nunca escolhe contato."""
    normalized = {normalize(value) for value in values if value}
    normalized.discard("")
    with conversations_lock:
        existing = set(conversations.get(instance_id, {}))
    matches = sorted(normalized & existing)
    if len(matches) == 1:
        return matches[0], False
    return "", len(matches) > 1


def _meta_phone_value(*values: str) -> str:
    """Aceita telefone real, mas nunca extrai dígitos de BSUID/username."""
    for value in values:
        raw = str(value or "").strip()
        if not raw or re.search(r"[A-Za-z@]", raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 7:
            return digits
    return ""


def _meta_conversation_key(
    instance_id: str,
    *,
    phone: str,
    bsuid: str,
    parent_bsuid: str,
    username: str,
    waba_id: str,
) -> str:
    """Chave nova tipada sem renomear formatos legados já existentes."""
    candidates: list[str] = []
    if phone:
        # Compatibilidade: mensagens com telefone continuam no formato atual.
        candidates.extend([bsuid, phone])
    else:
        if bsuid:
            candidates.extend([bsuid, f"bsuid:{bsuid}"])
        if parent_bsuid:
            candidates.extend([parent_bsuid, f"parent_bsuid:{parent_bsuid}"])
        if username:
            scoped = username.removeprefix("@").strip().lower()
            candidates.append(f"username:{waba_id or instance_id}:{scoped}")

    with conversations_lock:
        existing = conversations.get(instance_id, {})
        for candidate in candidates:
            if candidate and candidate in existing:
                return candidate
    if phone:
        return bsuid or phone
    if bsuid:
        return f"bsuid:{bsuid}"
    if parent_bsuid:
        return f"parent_bsuid:{parent_bsuid}"
    if username:
        return f"username:{waba_id or instance_id}:{username.removeprefix('@').strip().lower()}"
    return ""

def _verify_meta_signature(body_bytes: bytes, signature_header: str, app_secret: str) -> bool:
    """Valida assinatura X-Hub-Signature-256 enviada pelo Meta."""
    if not app_secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def _valid_leadgen_token(token: str) -> bool:
    """
    Token válido para webhooks de Lead Ads (multi-tenant):
    - webhook_secret de qualquer instância (cada cliente usa o token da própria
      instância ao cadastrar o webhook no painel Meta dele); ou
    - WEBHOOK_SECRET global (legado — configurações antigas continuam valendo).
    """
    if not token:
        return False
    global_secret = os.getenv("WEBHOOK_SECRET", "")
    if global_secret and hmac.compare_digest(token, global_secret):
        logger.warning("[DEPRECATED] Webhook Lead Ads autenticado via WEBHOOK_SECRET global; migre para webhook_secret por instância.")
        return True
    with instances_lock:
        for inst in instances.values():
            secret = inst.get("webhook_secret", "")
            if secret and hmac.compare_digest(token, secret):
                return True
    return False


def _normalize_meta_ad_account_id(ad_account_id: str) -> str:
    """Garante o formato `act_<id>` esperado pela Graph API de Ads."""
    value = (ad_account_id or "").strip()
    if not value:
        return ""
    if value.startswith("act_"):
        return value
    return f"act_{value}"


# Períodos aceitos pela Graph API que o explorador expõe — qualquer valor fora
# da lista cai no padrão para não repassar input arbitrário ao Meta.
_ADS_DATE_PRESETS = {
    "today", "yesterday", "last_7d", "last_14d", "last_30d", "last_90d",
    "this_month", "last_month", "maximum",
}


def _safe_date_preset(value: str) -> str:
    return value if value in _ADS_DATE_PRESETS else "last_30d"


def _resolve_ads_token(request: Request, instance_id: str, inst: dict, prefer_oauth: bool) -> str:
    """
    Token de leitura de Ads. O token OAuth vinculado à instância enxerga as
    BMs/contas autorizadas; o meta_token manual normalmente só a conta
    configurada. prefer_oauth=True quando o usuário pediu uma conta explícita.
    """
    manual = inst.get("meta_token", "").strip()
    oauth = ""
    try:
        connection = meta_oauth_pg.get_connection_for_instance(instance_id)
        if connection:
            oauth = request.app.state.decrypt_field_for_user(connection["user_email"], connection["access_token_encrypted"]) or ""
    except Exception:
        oauth = ""
    return (oauth or manual) if prefer_oauth else (manual or oauth)


def _resolve_ads_account_and_token(request: Request, instance_id: str, inst: dict, requested_account: str) -> tuple[str, str]:
    """
    Resolve (ad_account, token) para os endpoints do explorador. Sem conexão
    OAuth própria da instância, só o meta_token manual está
    disponível — e esse token de sistema/agência pode enxergar contas de
    OUTROS clientes no mesmo Business Manager. Sem travar isso, um usuário
    desta instância poderia pedir o ad_account de outro tenant e puxar dados
    dele usando o token manual configurado aqui.
    """
    own_account = _normalize_meta_ad_account_id(inst.get("meta_ad_account_id", ""))
    account = _normalize_meta_ad_account_id(requested_account) if requested_account else ""

    oauth_token = ""
    try:
        connection = meta_oauth_pg.get_connection_for_instance(instance_id)
        if connection:
            oauth_token = request.app.state.decrypt_field_for_user(connection["user_email"], connection["access_token_encrypted"]) or ""
    except Exception:
        oauth_token = ""

    if oauth_token:
        return (account or own_account), oauth_token

    if account and account != own_account:
        raise HTTPException(403, "Sem conexão OAuth própria — só é possível explorar a conta de anúncio desta instância.")
    return own_account, inst.get("meta_token", "").strip()


# ── Pydantic models ────────────────────────────────────────────────────────────

class MetaAdsChatPayload(BaseModel):
    messages: list[dict]   # [{role: "user"|"assistant", content: str}]
    campaign: str = ""
    ad_account: str = ""   # conta alternativa do explorador; vazio = conta da instância
    date_preset: str = ""  # período alternativo; vazio = last_30d


# ── Meta Lead Ads & Forms webhooks ────────────────────────────────────────────

@router.get("/webhook/meta-leads")
async def verify_meta_leads(request: Request):
    q = request.query_params
    return request.app.state.meta_webhook_verify(
        q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", "")
    )


@router.post("/webhook/meta-leads")
@limiter.limit("60/minute")
async def receive_meta_leads(request: Request, token: str = ""):
    raw_body = await request.body()
    sig = request.headers.get("x-hub-signature-256", "")
    META_APP_SECRET = os.getenv("META_APP_SECRET", "") or os.getenv("META_OAUTH_APP_SECRET", "")
    # Aceita: assinatura do app Meta da KLEAD (fluxo OAuth) OU token por
    # instância/global (cliente com app Meta próprio). Fail-closed: sem
    # nenhuma credencial válida, rejeita — mesmo se os secrets não estiverem setados.
    signature_ok = bool(META_APP_SECRET and sig and _verify_meta_signature(raw_body, sig, META_APP_SECRET))
    if not signature_ok and not _valid_leadgen_token(token):
        logger.warning("[META-LEADS] Acesso negado — assinatura e token inválidos.")
        return Response(status_code=403)
    try:
        body = json.loads(raw_body)
    except Exception:
        return {"ok": False, "reason": "invalid json"}
    # Salva no CRM via Postgres síncrono — fora do event loop.
    saved = await run_in_threadpool(request.app.state.process_meta_webhook_body, body, "meta-leads")
    return {"ok": True, "saved": saved}


@router.get("/webhook/meta-forms")
async def verify_meta_forms(request: Request):
    q = request.query_params
    return request.app.state.meta_webhook_verify(
        q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", "")
    )


@router.post("/webhook/meta-forms")
@limiter.limit("60/minute")
async def receive_meta_forms(request: Request, token: str = ""):
    raw_body = await request.body()
    sig = request.headers.get("x-hub-signature-256", "")
    META_APP_SECRET = os.getenv("META_APP_SECRET", "") or os.getenv("META_OAUTH_APP_SECRET", "")
    # Mesma regra do meta-leads: assinatura KLEAD OU token por instância/global.
    signature_ok = bool(META_APP_SECRET and sig and _verify_meta_signature(raw_body, sig, META_APP_SECRET))
    if not signature_ok and not _valid_leadgen_token(token):
        logger.warning("[META-FORMS] Acesso negado — assinatura e token inválidos.")
        return Response(status_code=403)
    try:
        body = json.loads(raw_body)
    except Exception:
        return {"ok": False, "reason": "invalid json"}
    # Salva no CRM via Postgres síncrono — fora do event loop.
    saved = await run_in_threadpool(request.app.state.process_meta_webhook_body, body, "meta-forms")
    return {"ok": True, "saved": saved}


# ── Meta Ads métricas e análise ───────────────────────────────────────────────

def _fetch_campaign_metrics(
    ad_account: str,
    token: str,
    campaign_filter: str = "",
    date_preset: str = "last_30d",
    campaign_id: str = "",
) -> dict:
    """Busca métricas por campanha na Graph API (período configurável, padrão 30 dias). Reutilizada por múltiplos endpoints."""
    budgets_by_id: dict = {}
    status_by_id: dict = {}
    try:
        br = requests.get(
            f"https://graph.facebook.com/v25.0/{ad_account}/campaigns",
            params={"fields": "id,daily_budget,status,effective_status", "access_token": token, "limit": 200},
            timeout=10,
        )
        if br.status_code == 200:
            for camp in br.json().get("data", []):
                cid = camp["id"]
                raw = camp.get("daily_budget")
                if raw:
                    budgets_by_id[cid] = round(float(raw) / 100, 2)
                status_by_id[cid] = camp.get("effective_status", camp.get("status", "UNKNOWN"))
    except Exception:
        logger.debug("Exceção ignorada ao inspecionar credenciais", exc_info=True)

    r = requests.get(
        f"https://graph.facebook.com/v25.0/{ad_account}/insights",
        params={
            "fields": "campaign_name,campaign_id,impressions,clicks,spend,actions,cpm,cpc,reach,frequency,quality_ranking",
            "date_preset": _safe_date_preset(date_preset),
            "level": "campaign",
            "filtering": '[{"field":"campaign.effective_status","operator":"IN","value":["ACTIVE","PAUSED"]}]',
            "access_token": token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise HTTPException(502, f"Meta API: {r.text[:200]}")

    data = r.json().get("data", [])
    if campaign_id:
        data = [c for c in data if c.get("campaign_id") == campaign_id]
    if campaign_filter:
        data = [c for c in data if campaign_filter.lower() in c.get("campaign_name", "").lower()]

    campaigns = []
    for c in data:
        leads = next((int(a["value"]) for a in c.get("actions", []) if a["action_type"] == "lead"), 0)
        cid = c.get("campaign_id", "")
        campaigns.append({
            "campaign":        c.get("campaign_name", ""),
            "impressions":     int(c.get("impressions", 0)),
            "reach":           int(c.get("reach", 0)),
            "frequency":       round(float(c.get("frequency", 0)), 2),
            "quality_ranking": c.get("quality_ranking", ""),
            "clicks":          int(c.get("clicks", 0)),
            "spend":           float(c.get("spend", 0)),
            "leads":           leads,
            "cpm":             float(c.get("cpm", 0)),
            "cpc":             float(c.get("cpc", 0)),
            "cpl":             round(float(c.get("spend", 0)) / leads, 2) if leads > 0 else None,
            "daily_budget":    budgets_by_id.get(cid),
            "status":          status_by_id.get(cid, "UNKNOWN"),
        })

    total_spend  = sum(c["spend"]  for c in campaigns)
    total_leads  = sum(c["leads"]  for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_reach  = sum(c["reach"]  for c in campaigns)
    return {
        "campaigns":    campaigns,
        "total_spend":  round(total_spend, 2),
        "total_leads":  total_leads,
        "total_clicks": total_clicks,
        "total_reach":  total_reach,
        "avg_cpl":      round(total_spend / total_leads, 2) if total_leads > 0 else None,
    }


@router.get("/metrics/meta-ads/{instance_id}")
def get_meta_ads_metrics(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Busca métricas de anúncios Meta via Graph API (últimos 30 dias) e salva snapshot diário."""
    user = auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    ad_account = _normalize_meta_ad_account_id(inst.get("meta_ad_account_id", ""))
    token = _resolve_ads_token(request, instance_id, inst, prefer_oauth=False)
    if not ad_account or not token:
        raise HTTPException(400, "meta_ad_account_id e meta_token são obrigatórios.")
    try:
        result = _fetch_campaign_metrics(ad_account, token)
        # Snapshot diário — salva em background para não atrasar a resposta.
        # save_snapshot() sempre limpa e regrava o dia com os dados reais
        # buscados agora, evitando misturar campanhas de uma conta de anuncio
        # antiga (ja trocada nas credenciais) com os dados atuais.
        threading.Thread(
            target=save_snapshot, args=(instance_id, result), daemon=True
        ).start()
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Falha ao comunicar com a Meta API", exc_info=True)
        raise HTTPException(502, "Falha ao comunicar com a Meta API.")


@router.get("/metrics/meta-ads/{instance_id}/explorer")
def get_meta_ads_explorer_metrics(
    instance_id: str,
    request: Request,
    ad_account: str = "",
    campaign_id: str = "",
    date_preset: str = "last_30d",
    _: str = Security(require_api_key),
):
    """
    Métricas de campanha para o explorador do dashboard: qualquer conta de
    anúncio visível pelo token do usuário, período configurável e filtro
    opcional por campanha. Não grava snapshot — o histórico diário continua
    refletindo só a conta padrão da instância.
    """
    user = auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    account, token = _resolve_ads_account_and_token(request, instance_id, inst, ad_account)
    if not account or not token:
        raise HTTPException(400, "Conta de anúncio e credencial Meta são obrigatórias.")
    try:
        return _fetch_campaign_metrics(account, token, date_preset=date_preset, campaign_id=campaign_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Falha ao comunicar com a Meta API", exc_info=True)
        raise HTTPException(502, "Falha ao comunicar com a Meta API.")


@router.get("/metrics/meta-ads/{instance_id}/history")
def get_meta_ads_history(instance_id: str, request: Request, days: int = 35, _: str = Security(require_api_key)):
    """Retorna histórico diário de snapshots Meta Ads (padrão: últimos 35 dias)."""
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    rows = get_history(instance_id, min(days, 90))
    return {"history": rows, "days": days, "count": len(rows)}


@router.get("/metrics/meta-ads/{instance_id}/comparison")
def get_meta_ads_comparison(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Retorna comparativo esta semana vs. semana passada com variação percentual."""
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    data = get_weekly_comparison(instance_id)
    return data


@router.get("/metrics/meta-ads/{instance_id}/ads")
def get_meta_ads_by_ad(instance_id: str, request: Request, ad_account: str = "", date_preset: str = "last_30d", _: str = Security(require_api_key)):
    """Busca métricas por anúncio individual com dados de criativo (período configurável, padrão 30 dias)."""
    user = auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    ad_account, token = _resolve_ads_account_and_token(request, instance_id, inst, ad_account)
    if not ad_account or not token:
        raise HTTPException(400, "meta_ad_account_id e meta_token são obrigatórios.")
    try:
        r = requests.get(
            f"https://graph.facebook.com/v25.0/{ad_account}/insights",
            params={
                "fields": "ad_id,ad_name,adset_name,campaign_name,impressions,clicks,spend,actions,cpm,cpc,reach,frequency,quality_ranking",
                "date_preset": _safe_date_preset(date_preset),
                "level": "ad",
                "filtering": '[{"field":"campaign.effective_status","operator":"IN","value":["ACTIVE"]},{"field":"adset.effective_status","operator":"IN","value":["ACTIVE"]},{"field":"ad.effective_status","operator":"IN","value":["ACTIVE"]}]',
                "access_token": token,
                "limit": 200,
            },
            timeout=20,
        )
        if r.status_code != 200:
            raise HTTPException(502, f"Meta API: {r.text[:200]}")

        data = r.json().get("data", [])

        # Busca thumbnails dos criativos em batch
        ad_ids = [c.get("ad_id") for c in data if c.get("ad_id")]
        thumbnails: dict = {}
        if ad_ids:
            try:
                fields = "id,creative{thumbnail_url,body,title,image_url,object_type,video_id}"
                ids_param = ",".join(ad_ids[:50])  # limite de segurança
                cr = requests.get(
                    "https://graph.facebook.com/v25.0/",
                    params={"ids": ids_param, "fields": fields, "access_token": token},
                    timeout=15,
                )
                if cr.status_code == 200:
                    for ad_id, ad_data in cr.json().items():
                        creative = ad_data.get("creative", {})
                        obj_type = creative.get("object_type", "")
                        is_video = bool(creative.get("video_id")) or obj_type in ("VIDEO", "SHARE")
                        thumbnails[ad_id] = {
                            "thumbnail_url": creative.get("thumbnail_url") or creative.get("image_url"),
                            "body":          (creative.get("body") or "")[:120],
                            "title":         (creative.get("title") or "")[:80],
                            "creative_type": "video" if is_video else ("image" if obj_type in ("PHOTO", "IMAGE") else "unknown"),
                        }
            except Exception:
                logger.debug("Exceção ignorada ao montar métricas", exc_info=True)

        ads = []
        for c in data:
            leads  = next((int(a["value"]) for a in c.get("actions", []) if a["action_type"] == "lead"), 0)
            spend  = float(c.get("spend", 0))
            clicks = int(c.get("clicks", 0))
            ad_id  = c.get("ad_id", "")
            creative_info = thumbnails.get(ad_id, {})
            ads.append({
                "ad_id":           ad_id,
                "ad_name":         c.get("ad_name", ""),
                "adset_name":      c.get("adset_name", ""),
                "campaign_name":   c.get("campaign_name", ""),
                "impressions":     int(c.get("impressions", 0)),
                "reach":           int(c.get("reach", 0)),
                "frequency":       round(float(c.get("frequency", 0)), 2),
                "quality_ranking": c.get("quality_ranking", ""),
                "clicks":          clicks,
                "spend":           spend,
                "leads":           leads,
                "cpm":             float(c.get("cpm", 0)),
                "cpc":             float(c.get("cpc", 0)),
                "ctr":             round(clicks / int(c.get("impressions", 1)) * 100, 2) if c.get("impressions") else 0,
                "cpl":             round(spend / leads, 2) if leads > 0 else None,
                "thumbnail_url":   creative_info.get("thumbnail_url"),
                "creative_body":   creative_info.get("body", ""),
                "creative_title":  creative_info.get("title", ""),
                "creative_type":   creative_info.get("creative_type", "unknown"),
            })

        # Ordena por leads desc, depois por clicks
        ads.sort(key=lambda x: (x["leads"], x["clicks"]), reverse=True)

        total_spend = sum(a["spend"] for a in ads)
        total_leads = sum(a["leads"] for a in ads)
        return {
            "ads":         ads,
            "total_spend": round(total_spend, 2),
            "total_leads": total_leads,
            "avg_cpl":     round(total_spend / total_leads, 2) if total_leads > 0 else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Falha ao comunicar com a Meta API", exc_info=True)
        raise HTTPException(502, "Falha ao comunicar com a Meta API.")


@router.post("/dashboard/{instance_id}/analyze-meta-ads", dependencies=[Security(require_api_key)])
@limiter.limit("20/minute")
def analyze_meta_ads_endpoint(instance_id: str, request: Request, campaign: str = "", ad_account: str = "", date_preset: str = ""):
    """
    Analisa métricas Meta Ads usando LLM — diagnóstico de criativo, audiência e orçamento.
    Parâmetros opcionais: campaign (nome da campanha para análise focada),
    ad_account e date_preset (explorador — conta/período diferentes do padrão).
    Etapa 2: tools de busca de benchmarks e comparação histórica serão adicionados aqui.
    """
    user = auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])

    ai = request.app.state.resolve_ai_settings(inst)
    if not ai["api_key"]:
        raise HTTPException(400, "IA não configurada. Configure o provedor e a API key nas configurações.")

    ad_account, token = _resolve_ads_account_and_token(request, instance_id, inst, ad_account)
    if not ad_account or not token:
        raise HTTPException(400, "meta_ad_account_id e meta_token são obrigatórios para análise.")

    try:
        metrics_dict = _fetch_campaign_metrics(ad_account, token, campaign, date_preset=_safe_date_preset(date_preset))
        if campaign and not metrics_dict.get("campaigns"):
            safe_campaign = re.sub(r"[^\w\s\-.]", "", campaign)[:100]
            raise HTTPException(404, f"Campanha '{safe_campaign}' não encontrada nos dados do período.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[META-ADS] Falha ao buscar métricas: {exc}", exc_info=True)
        raise HTTPException(502, "Erro ao buscar métricas da conta de anúncios.")

    try:
        result = analyze_meta_ads(metrics_dict, ai)
        result["_meta"] = {
            "provider": ai["provider"],
            "model": ai["model"],
            "campaigns_analyzed": len(metrics_dict.get("campaigns", [])),
            "campaign_filter": campaign or None,
        }
        return result
    except ValueError as exc:
        if "api_key_missing" in str(exc):
            raise HTTPException(400, "API Key da IA não configurada.")
        raise HTTPException(500, str(exc))
    except Exception as exc:
        logger.warning(f"[META-ADS] Falha na análise: {exc}", exc_info=True)
        raise HTTPException(500, "Erro ao gerar a análise. Tente novamente em instantes.")


@router.post("/dashboard/{instance_id}/chat-meta-ads", dependencies=[Security(require_api_key)])
@limiter.limit("20/minute")
def chat_meta_ads_endpoint(instance_id: str, request: Request, payload: MetaAdsChatPayload):
    """Chat conversacional com o agente Meta Ads usando os dados reais da conta."""
    user = auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])

    ai = request.app.state.resolve_ai_settings(inst)
    if not ai["api_key"]:
        raise HTTPException(400, "IA não configurada. Configure o provedor e a API key.")

    ad_account, token = _resolve_ads_account_and_token(request, instance_id, inst, payload.ad_account)
    if not ad_account or not token:
        raise HTTPException(400, "meta_ad_account_id e meta_token são obrigatórios.")

    if not payload.messages or not any(m.get("role") == "user" for m in payload.messages):
        raise HTTPException(400, "Envie ao menos uma mensagem do usuário.")

    try:
        metrics_dict = _fetch_campaign_metrics(ad_account, token, payload.campaign or "", date_preset=_safe_date_preset(payload.date_preset))
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[META-ADS] Falha ao buscar métricas: {exc}", exc_info=True)
        raise HTTPException(502, "Erro ao buscar métricas da conta de anúncios.")

    try:
        # Comparativo semanal e pixel são artefatos da conta padrão da instância —
        # ao explorar outra conta, incluí-los misturaria dados de contas diferentes.
        is_default_account = ad_account == _normalize_meta_ad_account_id(inst.get("meta_ad_account_id", ""))
        comparison  = get_weekly_comparison(instance_id) if is_default_account else {}
        pixel_id    = inst.get("meta_capi_event_source_id", "").strip()
        pixel_token = inst.get("meta_capi_access_token", "").strip()
        pixel_data  = fetch_pixel_quality(pixel_id, pixel_token) if is_default_account and pixel_id and pixel_token else {}
        messages = payload.messages[-10:]
        reply = chat_meta_ads(messages, metrics_dict, ai, comparison=comparison, pixel_data=pixel_data)
        return {"reply": reply, "provider": ai["provider"], "model": ai["model"]}
    except ValueError as exc:
        if "api_key_missing" in str(exc):
            raise HTTPException(400, "API Key da IA não configurada.")
        raise HTTPException(500, str(exc))
    except Exception as exc:
        logger.warning(f"[META-ADS] Falha no chat: {exc}", exc_info=True)
        raise HTTPException(500, "Erro ao processar sua mensagem. Tente novamente em instantes.")


@router.get("/webhook/meta-urls")
def get_meta_webhook_urls(request: Request, instance_id: str = "", _: str = Security(require_api_key)):
    """
    URLs de webhook Lead Ads para colar no painel Meta.
    Com instance_id: token da própria instância (cada cliente vê só o seu).
    Sem instance_id: token global — restrito à conta mestre (superadmin),
    porque esse token autentica leads de todos os tenants.
    """
    _require_admin(request)
    base = _PUBLIC_WEBHOOK_BASE.rstrip("/")
    if instance_id:
        auth_svc.require_instance_access(instance_id, request)
        with instances_lock:
            token = instances.get(instance_id, {}).get("webhook_secret", "")
        if not token:
            raise HTTPException(404, "Instância sem webhook_secret configurado.")
    else:
        auth_svc.require_superadmin(request)
        token = os.getenv("WEBHOOK_SECRET", "")
    return {
        "verify_token": token,
        "webhooks": [
            {
                "tipo":     "Lead Ads",
                "descricao": "Formulários de Lead Ads do Facebook/Instagram",
                "url":      f"{base}/webhook/meta-leads?token={token}",
                "campo":    "leadgen",
            },
            {
                "tipo":     "Meta Forms / Click-to-WhatsApp",
                "descricao": "Formulários de cadastro e campanhas Click-to-WhatsApp",
                "url":      f"{base}/webhook/meta-forms?token={token}",
                "campo":    "leadgen",
            },
        ],
    }


# ── Webhook de entrada (Evolution GO + Meta Cloud API) ────────────────────────

@router.get("/webhook/meta")
@router.get("/webhook/meta-whatsapp")
async def verify_meta_whatsapp(request: Request):
    """Callback único do app KLEAD para WABAs e Páginas conectados via OAuth."""
    q = request.query_params
    return request.app.state.meta_webhook_verify(
        q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", "")
    )


@router.get("/webhook/{instance_id}")
async def verify_webhook_meta(
    instance_id: str,
    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = "",
):
    """Verificação do webhook exigida pelo Meta Cloud API (v21.0)."""
    with instances_lock:
        secret = instances.get(instance_id, {}).get("webhook_secret", "")
    if not secret:
        return PlainTextResponse("webhook secret not configured", status_code=503)
    if hub_mode == "subscribe" and hub_verify_token == secret:
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("forbidden", status_code=403)


@router.post("/webhook/meta")
@router.post("/webhook/meta-whatsapp")
@limiter.limit("120/minute")
async def receive_meta_whatsapp(request: Request):
    """Despacha WhatsApp por Phone Number ID e Lead Ads pelas regras form/page."""
    raw_body = await request.body()
    try:
        body = json.loads(raw_body)
    except Exception:
        return {"ok": False, "reason": "invalid json"}
    if body.get("object") == "page":
        sig = request.headers.get("x-hub-signature-256", "")
        app_secret = os.getenv("META_APP_SECRET", "") or os.getenv("META_OAUTH_APP_SECRET", "")
        if not app_secret or not sig or not _verify_meta_signature(raw_body, sig, app_secret):
            logger.warning("[META-LEADS] Callback global rejeitado — assinatura inválida.")
            return Response(status_code=403)
        saved = await run_in_threadpool(request.app.state.process_meta_webhook_body, body, "meta-leads")
        return {"ok": True, "saved": saved}
    if body.get("object") != "whatsapp_business_account":
        return {"ok": True}

    phone_number_id = ""
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            metadata = (change.get("value") or {}).get("metadata") or {}
            phone_number_id = (metadata.get("phone_number_id") or "").strip()
            if phone_number_id:
                break
        if phone_number_id:
            break
    if not phone_number_id:
        logger.warning("[WEBHOOK-META] Payload sem phone_number_id no callback OAuth global.")
        return {"ok": True}

    with instances_lock:
        instance_id = next(
            (iid for iid, inst in instances.items()
             if (inst.get("phone_number_id") or "").strip() == phone_number_id),
            "",
        )
    if not instance_id:
        logger.warning("[WEBHOOK-META] Nenhuma instância corresponde ao phone_number_id recebido.")
        return {"ok": True}

    return await run_in_threadpool(_receive_webhook_sync, instance_id, request, raw_body, "")


@router.post("/webhook/{instance_id}")
@limiter.limit("60/minute")
async def receive_webhook(instance_id: str, request: Request, token: str = ""):
    """
    Recebe eventos do Evolution GO e da Meta Cloud API (v21.0).
    Evolution: protegido por ?token=<webhook_secret da instância>
    Meta: protegido por X-Hub-Signature-256 (APP_SECRET)
    """
    raw_body = await request.body()
    # O processamento (braço Meta em especial) usa Postgres e locks síncronos —
    # roda no threadpool para não bloquear o event loop. A resposta continua
    # saindo só depois de concluir, preservando a semântica de retry do Meta.
    return await run_in_threadpool(_receive_webhook_sync, instance_id, request, raw_body, token)


def _receive_webhook_sync(instance_id: str, request: Request, raw_body: bytes, token: str):
    # Captura referências do app.state upfront — threads de background precisam de refs locais
    _conversation_phone_key      = request.app.state.conversation_phone_key
    _parse_whatsapp_jid          = request.app.state.parse_whatsapp_jid
    _record_webhook_event        = request.app.state.record_webhook_event
    _get_or_create_conversation  = request.app.state.get_or_create_conversation
    _save_conversation           = request.app.state.save_conversation
    _build_meta_message_source_meta = request.app.state.build_meta_message_source_meta
    _persist_meta_identity_aliases = request.app.state.persist_meta_identity_aliases
    _handle_inbound              = request.app.state.handle_inbound
    _handle_audio_inbound        = request.app.state.handle_audio_inbound
    _handle_media_inbound        = request.app.state.handle_media_inbound
    _extract_message_text        = request.app.state.extract_message_text
    _is_audio_message            = request.app.state.is_audio_message
    _detect_media_message        = request.app.state.detect_media_message
    META_APP_SECRET              = os.getenv("META_APP_SECRET", "") or os.getenv("META_OAUTH_APP_SECRET", "")

    # ── Meta Cloud API: identifica pelo campo "object" ─────────────────────────
    try:
        body = json.loads(raw_body)
    except Exception:
        return {"ok": False, "reason": "invalid json"}

    if body.get("object") == "whatsapp_business_account":
        # Valida assinatura do Meta usando o app_secret da instância
        with instances_lock:
            inst_for_sig = dict(instances.get(instance_id, {}))
        app_secret = inst_for_sig.get("meta_app_secret", "") or META_APP_SECRET
        sig = request.headers.get("x-hub-signature-256", "")
        if not app_secret:
            logger.info(f"[WEBHOOK-META] App secret não configurado — instância={instance_id}")
            return Response(status_code=403)
        if not _verify_meta_signature(raw_body, sig, app_secret):
            logger.info(f"[WEBHOOK-META] Assinatura inválida — instância={instance_id}")
            return Response(status_code=403)

        with instances_lock:
            if instance_id not in instances:
                return {"ok": True}  # 200 para o Meta não retentar

        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field", "")
                value = change.get("value", {})
                if field == "user_id_update":
                    update_meta = {
                        "provider": "meta",
                        "channel": "whatsapp_cloud_api",
                        "waba_id": entry.get("id", ""),
                        "wa_id": value.get("wa_id", ""),
                        "old_user_id": value.get("old_user_id", ""),
                        "new_user_id": value.get("new_user_id", "") or value.get("user_id", ""),
                        "parent_user_id": value.get("parent_user_id", ""),
                    }
                    dedupe = ":".join(str(update_meta.get(k, "")) for k in ("waba_id", "old_user_id", "new_user_id"))
                    _record_webhook_event(
                        provider="meta",
                        instancia_id=instance_id,
                        event_type="whatsapp_user_id_update",
                        payload=value,
                        dedupe_key=f"meta:user-id-update:{dedupe}" if dedupe.strip(":") else "",
                    )
                    existing_key, conflict = _existing_meta_conversation_key(
                        instance_id,
                        [
                            update_meta["wa_id"], update_meta["old_user_id"],
                            update_meta["new_user_id"], update_meta["parent_user_id"],
                        ],
                        _conversation_phone_key,
                    )
                    if existing_key:
                        _persist_meta_identity_aliases(instance_id, existing_key, update_meta, 100)
                    elif conflict:
                        logger.warning("[META-IDENTITY] user_id_update ambíguo; nenhum alias vinculado.")
                    continue
                if field == "business_username_updates":
                    # Username do próprio número comercial, não do lead.
                    _record_webhook_event(
                        provider="meta",
                        instancia_id=instance_id,
                        event_type="whatsapp_business_username_update",
                        payload=value,
                        dedupe_key=(
                            f"meta:business-username:{entry.get('id', '')}:"
                            f"{value.get('username', '')}:{value.get('event', '')}"
                        ),
                    )
                    continue
                if field != "messages":
                    continue
                messages = value.get("messages", [])
                contacts = value.get("contacts", [])
                metadata = value.get("metadata") or {}

                for status in value.get("statuses", []):
                    status_meta = {
                        "provider": "meta",
                        "channel": "whatsapp_cloud_api_status",
                        "waba_id": entry.get("id", ""),
                        "phone_number_id": metadata.get("phone_number_id", ""),
                        "wa_id": status.get("recipient_id", ""),
                        "user_id": status.get("recipient_user_id", ""),
                        "parent_user_id": status.get("recipient_parent_user_id", ""),
                        "username": status.get("username", ""),
                    }
                    existing_key, conflict = _existing_meta_conversation_key(
                        instance_id,
                        [status_meta["wa_id"], status_meta["user_id"], status_meta["parent_user_id"]],
                        _conversation_phone_key,
                    )
                    if existing_key:
                        _persist_meta_identity_aliases(instance_id, existing_key, status_meta, 90)
                    elif conflict:
                        logger.warning("[META-IDENTITY] status ambíguo; nenhum alias vinculado.")

                # Monta mapa wa_id → nome
                contact_map: dict[str, dict] = {}
                for contact in contacts:
                    keys = {
                        _conversation_phone_key(contact.get("wa_id", "")),
                        _conversation_phone_key(contact.get("user_id", "")),
                    }
                    for key in keys:
                        if key:
                            contact_map[key] = contact

                for msg in messages:
                    msg_type = msg.get("type", "")
                    raw_from = msg.get("from", "")
                    bsuid = (msg.get("from_user_id") or "").strip()
                    msg_id = msg.get("id", "")
                    _record_webhook_event(
                        provider="meta",
                        instancia_id=instance_id,
                        event_type="whatsapp_messages",
                        payload=msg,
                        dedupe_key=f"meta:waba:{msg_id}" if msg_id else "",
                    )
                    provisional_key = _conversation_phone_key(bsuid or raw_from)
                    raw_phone_key = _conversation_phone_key(raw_from)
                    contact = contact_map.get(provisional_key) or contact_map.get(raw_phone_key) or {}
                    bsuid = (contact.get("user_id") or bsuid).strip()
                    username = (
                        contact.get("username")
                        or (contact.get("profile") or {}).get("username")
                        or msg.get("username")
                        or ""
                    ).strip()
                    parent_bsuid = (contact.get("parent_user_id") or msg.get("parent_user_id") or "").strip()
                    phone_key = _meta_phone_value(contact.get("wa_id", ""), raw_from)
                    conv_key = _meta_conversation_key(
                        instance_id,
                        phone=phone_key,
                        bsuid=bsuid,
                        parent_bsuid=parent_bsuid,
                        username=username,
                        waba_id=entry.get("id", ""),
                    )
                    if not conv_key:
                        continue
                    name = (
                        (contact.get("profile") or {}).get("name", "")
                        or msg.get("profile_name", "")
                        or phone_key
                        or conv_key
                    )
                    phone_available = bool(phone_key)

                    # Extrai texto conforme tipo
                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                    elif msg_type == "button":
                        text = msg.get("button", {}).get("payload", "") or msg.get("button", {}).get("text", "")
                    elif msg_type == "interactive":
                        inter = msg.get("interactive", {})
                        text = (inter.get("button_reply", {}).get("title")
                                or inter.get("list_reply", {}).get("title", ""))
                    else:
                        # Áudio, imagem, vídeo, documento — avisa no inbox mas não processa IA
                        text = f"[{msg_type}]"

                    if not text.strip():
                        continue

                    referral = msg.get("referral") or {}
                    lead_origem = "meta_ads" if referral else "meta"
                    lead_campanha = (
                        referral.get("headline")
                        or referral.get("source_id")
                        or referral.get("source_url")
                        or ""
                    )
                    source_meta = _build_meta_message_source_meta(msg, contact, referral, lead_origem)
                    if phone_key:
                        source_meta["wa_id"] = phone_key
                    else:
                        source_meta.pop("wa_id", None)
                    source_meta["phone_available"] = bool(phone_key)
                    source_meta["outbound_available"] = bool(phone_key)
                    source_meta["waba_id"] = entry.get("id", "")
                    source_meta["phone_number_id"] = metadata.get("phone_number_id", "")
                    lead_fields = source_meta.get("lead_fields") or {}
                    parsed_name = (
                        lead_fields.get("nome_completo", "")
                        or name
                    )
                    parsed_email = lead_fields.get("email", "")
                    parsed_phone = _conversation_phone_key(
                        lead_fields.get("telefone", "") or phone_key
                    )
                    inbox_text = source_meta.get("message_text", "") or text
                    with conversations_lock:
                        conv = _get_or_create_conversation(instance_id, conv_key, parsed_name or name)
                        conv["lead_origem"] = lead_origem
                        conv["lead_campanha"] = lead_campanha
                        conv["lead_email"] = parsed_email or conv.get("lead_email", "")
                        # Merge source_meta: preserva campos de rastreamento da primeira mensagem
                        existing_sm = conv.get("source_meta") or {}
                        merged_sm = copy.deepcopy(source_meta)
                        for _pk in ("ctwa_clid", "referral", "leadgen_id", "form_id", "page_id", "ad_id", "adgroup_id"):
                            if existing_sm.get(_pk) and not merged_sm.get(_pk):
                                merged_sm[_pk] = existing_sm[_pk]
                        if existing_sm.get("form_answers") and isinstance(existing_sm["form_answers"], dict):
                            fa = dict(existing_sm["form_answers"])
                            fa.update(merged_sm.get("form_answers") or {})
                            merged_sm["form_answers"] = fa
                        conv["source_meta"] = merged_sm
                    _persist_meta_identity_aliases(instance_id, conv_key, source_meta, 90)
                    _save_conversation(instance_id, conv_key)
                    _submit_inbound(
                        _handle_inbound, instance_id, conv_key, parsed_name or name,
                        inbox_text, False, source_meta,
                    )
        return {"ok": True}

    # ── Evolution GO / Evolution API: requer ?token= ───────────────────────────
    with instances_lock:
        inst_for_token = instances.get(instance_id)
        if inst_for_token is None:
            return {"ok": False, "reason": "instance not found"}
        secret = inst_for_token.get("webhook_secret", "")

    if not secret:
        logger.warning(f"[WEBHOOK] Acesso negado — nenhum secret configurado (instância={instance_id}).")
        return Response(status_code=503)

    if token != secret:
        logger.warning(
            f"[WEBHOOK] ACESSO NEGADO — instância={instance_id} "
            "Execute o setup de webhook novamente para atualizar a URL com o token correto."
        )
        return Response(status_code=401)

    event = (body.get("event") or "").lower().strip()
    data  = body.get("data", {})
    logger.info(f"[WEBHOOK-EVO] instance={instance_id} event={event!r} keys={list(body.keys())}")

    # ── Evolution GO format: event="Message", data.Info.* ─────────────────────
    if event == "message":
        info = data.get("Info", {}) if isinstance(data, dict) else {}

        if info.get("IsFromMe", False) or info.get("IsGroup", False):
            return {"ok": True, "reason": "ignored"}

        chat = info.get("Chat", "")
        if not chat:
            return {"ok": True, "reason": "no phone"}

        phone_key = _conversation_phone_key(chat)
        if not phone_key:
            return {"ok": True, "reason": "no phone"}
        name = info.get("PushName") or phone_key
        source_meta = _build_evolution_identity_meta(data, chat, _parse_whatsapp_jid, "evolution_go")
        message_id = str(info.get("ID") or info.get("Id") or info.get("id") or "").strip()
        if message_id:
            source_meta["message_id"] = message_id

        media_info = _detect_media_message(data)
        if media_info:
            _submit_inbound(_handle_media_inbound, instance_id, phone_key, name, data, media_info, source_meta)
            return {"ok": True}

        text = _extract_message_text(data)
        if not text.strip():
            if _is_audio_message(data):
                _submit_inbound(_handle_audio_inbound, instance_id, phone_key, name, data, source_meta)
            return {"ok": True}

        _submit_inbound(_handle_inbound, instance_id, phone_key, name, text, False, source_meta)
        return {"ok": True}

    # ── Evolution API padrão: event="messages.upsert", data.key.* ─────────────
    event_norm = event.replace(".", "_").replace("-", "_")
    if event_norm == "messages_upsert":
        items = data if isinstance(data, list) else [data]
        for item in items:
            key = item.get("key", {})
            if key.get("fromMe"):
                continue
            remote_jid = key.get("remoteJid", "")
            if "@g.us" in remote_jid:
                continue
            if not remote_jid:
                continue
            phone_key = _conversation_phone_key(remote_jid)
            if not phone_key:
                continue
            name = item.get("pushName") or item.get("notifyName") or phone_key
            source_meta = _build_evolution_identity_meta(
                item, remote_jid, _parse_whatsapp_jid, "evolution_api",
            )
            message_id = str(key.get("id") or key.get("ID") or "").strip()
            if message_id:
                source_meta["message_id"] = message_id

            media_info = _detect_media_message(item)
            if media_info:
                _submit_inbound(_handle_media_inbound, instance_id, phone_key, name, item, media_info, source_meta)
                continue

            text = _extract_message_text(item)
            if not text.strip():
                if _is_audio_message(item):
                    _submit_inbound(_handle_audio_inbound, instance_id, phone_key, name, item, source_meta)
                continue
            _submit_inbound(_handle_inbound, instance_id, phone_key, name, text, False, source_meta)
        return {"ok": True}

    return {"ok": True, "reason": f"event ignored: {event}"}


# ── Webhook helpers (setup, URL, info, check) ─────────────────────────────────

@router.post("/webhook/setup/{instance_id}")
def setup_webhook(instance_id: str, request: Request, webhook_url: str = "", _: str = Security(require_api_key)):
    """
    Registra a URL de webhook na instância Evolution API.
    Usa sempre a URL interna com o token correto.
    O parâmetro webhook_url é ignorado — mantido por compatibilidade.
    """
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Sem acesso a esta instância.")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    if not has_evolution_outbound(inst):
        raise HTTPException(400, "Setup de webhook automático exige api_url e api_key do Evolution GO configurados.")

    result = request.app.state.register_webhook_evolution(instance_id, inst)
    if result.get("ok"):
        return result
    raise HTTPException(500, result.get("detail", "Erro ao registrar webhook"))


@router.post("/instance/{instance_id}/webhook-secret/rotate")
def rotate_webhook_secret(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    Gera um novo webhook_secret para a instância, invalidando o anterior.
    Para Evolution, registra o novo URL antes de efetivar a troca no KLEAD e
    restaura o webhook antigo caso a persistência falhe.
    Retorna o novo valor em texto plano uma única vez — não fica mascarado
    depois, já que não é persistido em nenhuma resposta de leitura completa.
    Para Meta, ainda é necessário reconfigurar a URL no painel com o novo valor.
    """
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Sem acesso a esta instância.")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        old_inst = dict(instances[instance_id])

    new_secret = secrets.token_urlsafe(32)
    new_inst = dict(old_inst)
    new_inst["webhook_secret"] = new_secret
    evolution_registered = False

    def register_evolution(inst: dict) -> dict:
        result = request.app.state.register_webhook_evolution(instance_id, inst)
        if not result.get("ok"):
            result = request.app.state.register_webhook_postgres(instance_id, inst)
        return result

    if has_evolution_outbound(new_inst):
        result = register_evolution(new_inst)
        if not result.get("ok"):
            raise HTTPException(502, result.get("detail", "Falha ao atualizar webhook no Evolution GO."))
        evolution_registered = True

    with instances_lock:
        if instance_id not in instances:
            if evolution_registered:
                register_evolution(old_inst)
            raise HTTPException(409, "Instância removida durante a rotação.")
        instances[instance_id]["webhook_secret"] = new_secret

    if request.app.state.save_instance_config(instance_id) is False:
        with instances_lock:
            if instance_id in instances:
                instances[instance_id]["webhook_secret"] = old_inst.get("webhook_secret", "")
        if evolution_registered:
            register_evolution(old_inst)
        raise HTTPException(503, "Não foi possível persistir o novo segredo; rotação revertida.")

    return {"ok": True, "webhook_secret": new_secret}


@router.get("/webhook/url/{instance_id}")
def get_webhook_url(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Retorna a URL de webhook correta para esta instância (com token)."""
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Sem acesso a esta instância.")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    evo_name   = inst.get("evo_instance_name", "").strip() or instance_id
    url        = request.app.state.build_webhook_url(instance_id, internal=True)
    evo_db     = os.getenv("EVO_PG_DB", "evogo_users")
    evo_user   = os.getenv("EVO_PG_USER", "postgres")
    # Escape SQL single quotes, then quote the whole -c argument for the shell
    safe_url  = url.replace("'", "''")
    safe_name = evo_name.replace("'", "''")
    psql_stmt = shlex.quote(f"UPDATE instances SET webhook='{safe_url}' WHERE name='{safe_name}';")
    sql = f"docker exec evolution-postgres psql -U {shlex.quote(evo_user)} -d {shlex.quote(evo_db)} -c {psql_stmt}"
    return {
        "webhook_url": url,
        "evo_instance_name": evo_name,
        "fix_command": sql,
        "fix_script": "python scripts/fix-webhook.py",
    }


@router.get("/webhook/meta-info/{instance_id}")
def get_meta_webhook_info(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Retorna URL pública do webhook (para configurar no Meta) e o Verify Token."""
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Sem acesso a esta instância.")
    base = _PUBLIC_WEBHOOK_BASE or _INTERNAL_WEBHOOK_BASE
    url  = f"{base}/webhook/{instance_id}"
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    return {
        "webhook_url":   url,
        "verify_token":  inst.get("webhook_secret", ""),
        "page_id":       inst.get("meta_page_id", ""),
    }


@router.get("/webhook/check/{instance_id}")
def check_webhook(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    Verifica se o webhook registrado na Evolution GO corresponde ao URL esperado
    (com o token correto). Retorna diagnóstico e auto-corrige se necessário.
    """
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Sem acesso a esta instância.")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    if not has_evolution_outbound(inst):
        return {"ok": True, "message": "sem api_url/api_key do Evolution GO configurados — webhook não é gerenciado aqui"}

    api_url = inst.get("api_url", "").rstrip("/")
    api_key = inst.get("api_key", "")
    evo_name = inst.get("evo_instance_name", "").strip() or instance_id
    secret = inst.get("webhook_secret", "")
    expected_url = request.app.state.build_webhook_url(instance_id)

    try:
        r = requests.get(f"{api_url}/webhook/find/{evo_name}", headers={"apikey": api_key}, timeout=10)
        current_url = ""
        if r.status_code == 200:
            data = r.json()
            current_url = (data.get("webhook") or {}).get("url", "")
    except Exception as e:
        logger.warning("Falha ao registrar webhook no Postgres", exc_info=True)
        return {"ok": False, "error": "Falha ao registrar webhook."}

    token_ok = (not secret) or (f"token={secret}" in current_url)

    if not token_ok or current_url != expected_url:
        fix = request.app.state.register_webhook_evolution(instance_id, inst)
        return {
            "ok": fix.get("ok", False),
            "status": "corrigido" if fix.get("ok") else "falha_ao_corrigir",
            "anterior": current_url,
            "esperado": expected_url,
            "detail": fix.get("detail"),
        }

    return {
        "ok": True,
        "status": "correto",
        "webhook_url": current_url,
    }


# ── SSE — notificações em tempo real ──────────────────────────────────────────

# Máximo de streams SSE simultâneos por instância — cada stream segura uma
# conexão do servidor aberta; sem teto, abas órfãs/reconexões agressivas
# acumulam e esgotam workers.
_SSE_MAX_PER_INSTANCE = int(os.getenv("SSE_MAX_PER_INSTANCE", "20"))


@router.get("/events/{instance_id}")
async def sse_events(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    SSE stream — emite eventos em tempo real para o inbox.
    Generator ASYNC de propósito: a versão síncrona bloqueava uma thread do
    pool do Starlette (40 no total) por conexão aberta em q.get(timeout=25) —
    com dezenas de inboxes abertos, o pool esgotava e a API inteira parava.
    Aqui a espera é cooperativa no event loop (asyncio.sleep), custo ~zero
    por conexão ociosa.
    """
    import asyncio

    # Auth no threadpool: require_instance_access pega o _lock do auth — se
    # alguém segurar esse lock (ex.: escrita no PG), rodar isso direto no
    # event loop congelava a API inteira (foi exatamente o que aconteceu).
    await run_in_threadpool(auth_svc.require_instance_access, instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

    q: _queue_mod.Queue = _queue_mod.Queue(maxsize=50)
    with _sse_lock:
        listeners = _sse_listeners.setdefault(instance_id, [])
        if len(listeners) >= _SSE_MAX_PER_INSTANCE:
            raise HTTPException(429, "Muitas conexões de eventos abertas para esta instância — feche abas antigas.")
        listeners.append(q)

    async def stream():
        try:
            yield "event: connected\ndata: {}\n\n"
            idle = 0.0
            while True:
                drained = False
                while True:
                    try:
                        msg = q.get_nowait()
                    except _queue_mod.Empty:
                        break
                    drained = True
                    yield msg
                if drained:
                    idle = 0.0
                    continue
                await asyncio.sleep(0.5)
                idle += 0.5
                if idle >= 25.0:
                    idle = 0.0
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                current = _sse_listeners.get(instance_id, [])
                if q in current:
                    current.remove(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ── Métricas SDR ───────────────────────────────────────────────────────────────

@router.get("/metrics/{instance_id}")
def get_metrics(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Retorna métricas agregadas de performance do SDR para uma instância."""
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    with conversations_lock:
        convs = [copy.deepcopy(conv) for conv in conversations.get(instance_id, {}).values()]
    total = len(convs)
    if total == 0:
        return {
            "total": 0, "qualifying": 0, "handoff": 0, "disqualified": 0, "initiated": 0,
            "conversion_rate": 0, "disqualify_rate": 0, "avg_score": None, "avg_turns": 0,
            "score_distribution": {"hot": 0, "warm": 0, "cold": 0},
            "unscored": 0, "needs_human": 0, "ai_paused": 0,
            "by_hour": [],
        }

    by_stage = {"qualifying": 0, "handoff": 0, "disqualified": 0, "initiated": 0}
    scores = []
    turns  = []
    needs_human = 0
    ai_paused   = 0
    by_hour: dict[int, int] = {}

    for conv in convs:
        stage = conv.get("stage", "qualifying")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        s = conv.get("score")
        if s is not None:
            scores.append(s)
        turns.append(conv.get("turn_count", 0))
        if conv.get("needs_human"):
            needs_human += 1
        if conv.get("ai_paused"):
            ai_paused += 1
        created = conv.get("created_at", "")
        if created:
            try:
                h = datetime.fromisoformat(created).hour
                by_hour[h] = by_hour.get(h, 0) + 1
            except Exception:
                logger.debug("Exceção ignorada no processamento do webhook", exc_info=True)

    hot  = sum(1 for s in scores if s >= 70)
    warm = sum(1 for s in scores if 40 <= s < 70)
    cold = sum(1 for s in scores if s < 40)

    return {
        "total":            total,
        "qualifying":       by_stage.get("qualifying", 0),
        "handoff":          by_stage.get("handoff", 0),
        "disqualified":     by_stage.get("disqualified", 0),
        "initiated":        by_stage.get("initiated", 0),
        "conversion_rate":  round(hot / total * 100, 1),
        "disqualify_rate":  round(by_stage.get("disqualified", 0) / total * 100, 1),
        "avg_score":        round(sum(scores) / len(scores), 1) if scores else None,
        "avg_turns":        round(sum(turns) / len(turns), 1) if turns else 0,
        "score_distribution": {"hot": hot, "warm": warm, "cold": cold},
        "unscored":         total - len(scores),
        "needs_human":      needs_human,
        "ai_paused":        ai_paused,
        "by_hour":          [{"hour": h, "count": by_hour[h]} for h in sorted(by_hour)],
    }
