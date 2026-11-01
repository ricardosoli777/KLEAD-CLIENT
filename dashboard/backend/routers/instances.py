"""
Router de instâncias e campanha — /instances, /instance/*, /contacts/*, /queue/*, etc.
Todos os helpers de estado são acessados via request.app.state.* (definidos em main.py).
"""
import copy
import csv
import io
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Security, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import requests as _requests

from core.state import conversations, conversations_lock, instances, instances_lock
from core.security import require_api_key
from core.limiter import limiter

import services.auth_service as auth_svc
import services.audit_log_service as audit_log_svc
import services.meta_oauth_pg_service as meta_oauth_pg_svc
from services.auth_service import require_admin as _require_admin
from services.campaign_service import (
    HISTORY_FILE,
    clear_queue,
    clear_sent_numbers,
    generate_queue_thread,
    load_queue,
    load_sent_numbers,
    purge_instance_data,
    run_campaign,
    run_campaign_from_queue,
    save_history,
    save_queue,
)
from services.evolution_service import (
    check_connection_state, send_whatsapp_message,
    get_whatsapp_settings, set_whatsapp_settings,
    get_all_instances, get_instance_info, disconnect_instance, reconnect_instance,
    invalidate_instance_cache, update_webhook_events, delete_evolution_instance,
)
from services.meta_capi_service import get_meta_capi_audit
from services.outbound_routing import has_evolution_outbound, resolve_meta_outbound_credentials
from services.openai_service import generate_message
from utils.cleaner import clean_contacts, normalize_name, normalize_phone
from utils.file_parser import _parse_lines, parse_file

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
ALERTS_FILE = DATA_DIR / "alerts.json"
_PROVIDER_KEY_FIELD = {"openai": "key_openai", "anthropic": "key_anthropic", "groq": "key_groq"}
_META_HEALTH_TTL = 300  # segundos

router = APIRouter()


# ── Modelos ────────────────────────────────────────────────────────────────────

class CreateInstancePayload(BaseModel):
    instance_id: str
    api_url: str = ""
    api_key: str = ""
    openai_key: str
    prompt: str
    provider: str = "evolution"
    meta_token: str = ""
    meta_app_secret: str = ""
    phone_number_id: str = ""
    template_name: str = ""
    meta_page_id: str = ""
    meta_page_access_token: str = ""
    meta_ad_account_id: str = ""
    meta_capi_enabled: bool = False
    meta_capi_access_token: str = ""
    meta_capi_event_source_id: str = ""
    meta_capi_test_event_code: str = ""
    inbound_ai_enabled: bool = True
    qualification_prompt: str = ""
    max_ai_turns: int = 8
    ai_response_delay: int = 3
    auto_handoff_keywords: list = []


class CreateChannelPayload(BaseModel):
    instance_id: str
    global_api_key: str
    api_url: str = ""


class UpdateInstancePayload(BaseModel):
    api_url: str = ""
    api_key: str = ""
    global_api_key: str = ""
    openai_key: str = ""
    provider: str = "evolution"
    meta_token: str = ""
    meta_app_secret: str = ""
    phone_number_id: str = ""
    template_name: str = ""
    meta_capi_enabled: bool = False
    meta_capi_access_token: str = ""
    meta_capi_event_source_id: str = ""
    meta_capi_test_event_code: str = ""
    llm_provider: str = "groq"
    llm_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    evo_instance_name: str = ""
    meta_page_id: str = ""
    meta_page_access_token: str = ""
    meta_ad_account_id: str = ""


class ValidateEvolutionPayload(BaseModel):
    reconnect: bool = True


class UpdateSdrPayload(BaseModel):
    inbound_ai_enabled: bool
    qualification_prompt: str = Field("", max_length=20000)
    max_ai_turns: int = 8
    ai_response_delay: int = 3
    auto_handoff_keywords: list = []
    model: str = "openai/gpt-oss-120b"
    llm_provider: str = "groq"
    llm_api_key: str = ""
    key_openai: str = ""
    key_anthropic: str = ""
    key_groq: str = ""
    followup_enabled: bool = True
    followup_delay_hours: int = 24
    followup_max: int = 3
    scoring_criteria: str = Field("", max_length=5000)
    auto_release_minutes: int = 5
    tts_enabled: bool = False


class UpdateAiConfigPayload(BaseModel):
    llm_provider: str = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    llm_api_key: str = ""
    key_openai: str = ""
    key_anthropic: str = ""
    key_groq: str = ""
    evo_global_api_key: str = ""


class HumanSendPayload(BaseModel):
    message: str = Field(..., max_length=4096)


class UpdatePromptPayload(BaseModel):
    prompt: str = Field(..., max_length=20000)


class UpdateCampaignContextPayload(BaseModel):
    campaign_context: str = Field("", max_length=50000)
    campaign_timing: dict = Field(default_factory=dict)


class StartCampaignPayload(BaseModel):
    instance_id: str
    context: str = Field("", max_length=50000)
    resume: bool = False
    daily_limit: int = 0
    hourly_limit: int = 0
    delay_min: int = 0
    delay_max: int = 0
    long_pause_every: int = 0
    long_pause_min: int = 0
    long_pause_max: int = 0


class TestMessagePayload(BaseModel):
    instance_id: str
    phone: str
    context: str
    name: str = ""
    message: str = ""


class SendDirectPayload(BaseModel):
    instance_id: str
    phone: str
    message: str


class PreviewPromptPayload(BaseModel):
    instance_id: str
    context: str
    count: int = 3
    name: str = ""
    phone: str = ""


class RetryFailedPayload(BaseModel):
    instance_id: str
    context: str = Field("", max_length=50000)


class GenerateQueuePayload(BaseModel):
    context: str = Field("", max_length=50000)


class UpdateQueueItemPayload(BaseModel):
    message: str = Field(..., max_length=4096)


class SendQueuePayload(BaseModel):
    daily_limit: int = 0
    hourly_limit: int = 0
    delay_min: int = 0
    delay_max: int = 0
    long_pause_every: int = 0
    long_pause_min: int = 0
    long_pause_max: int = 0


class ImportContactsTextPayload(BaseModel):
    raw_text: str


class ProcessContactsPayload(BaseModel):
    raw_text: str


def _team_tenant_admin_or_raise(actor: dict | None) -> dict | None:
    """
    Gerente e SDR NÃO criam instâncias — eles só enxergam a própria instância
    (e, no caso do gerente, as liberadas pelo admin) com Inbox SDR e CRM.
    Criação é exclusiva de admin/superadmin. Retorna None quando não se
    aplica (actor é None, admin ou superadmin).
    """
    if not actor or actor.get("funcao") not in ("gerente", "sdr"):
        return None
    raise HTTPException(
        403,
        "Seu perfil não pode criar instâncias. "
        "Peça para o administrador da sua conta conectar o WhatsApp.",
    )


def _grant_to_tenant_admin(tenant_admin: dict | None, iid: str) -> None:
    """Replica o acesso à instância recém-criada pro admin do tenant, pra
    ele enxergar as conversas do time sem precisar liberar manualmente."""
    if not tenant_admin:
        return
    owned = list(tenant_admin.get("allowed_instances") or [])
    if iid not in owned:
        owned.append(iid)
        auth_svc.set_allowed_instances(tenant_admin["email"], owned)


def _existing_instance_id(iid: str) -> str | None:
    """Retorna o ID já existente sem diferenciar maiúsculas/minúsculas."""
    wanted = iid.casefold()
    with instances_lock:
        return next((current for current in instances if current.casefold() == wanted), None)


def _register_instance_owner_or_raise(actor: dict, iid: str) -> None:
    if not auth_svc.register_created_instance(actor.get("email", ""), iid):
        raise HTTPException(
            409,
            "A conta autenticada deixou de existir durante a criação. Tente entrar novamente.",
        )


def _audit_instance(request: Request, actor: dict, action: str, iid: str, detail: str) -> None:
    _, _, ip = audit_log_svc.actor_from_request(request)
    audit_log_svc.record_event(
        actor_email=actor.get("email", ""),
        actor_funcao=actor.get("funcao", ""),
        action=action,
        target_type="instance",
        target_id=iid,
        instancia_id=iid,
        detail=detail,
        ip=ip,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/")
def root():
    return {"status": "KLEAD API online"}


@router.post("/create-instance")
def create_instance(payload: CreateInstancePayload, request: Request, _: str = Security(require_api_key)):
    actor = _require_admin(request)
    _safe_instance_path_part = request.app.state.safe_instance_path_part
    _validate_api_url        = request.app.state.validate_api_url
    _make_instance           = request.app.state.make_instance
    _save_instance_config            = request.app.state.save_instance_config

    iid = _safe_instance_path_part(payload.instance_id.strip())
    existing_iid = _existing_instance_id(iid)
    if existing_iid:
        raise HTTPException(400, f"Instância '{existing_iid}' já existe.")
    _validate_api_url(payload.api_url)

    # Limite de instâncias por usuário (plano) — superadmin não tem limite.
    if not auth_svc.can_create_more_instances(actor):
        raise HTTPException(
            403,
            f"Limite de instâncias do seu plano atingido ({actor.get('max_instances', 1)}). "
            "Fale com o suporte para aumentar seu plano.",
        )
    tenant_admin = _team_tenant_admin_or_raise(actor)

    with instances_lock:
        instances[iid] = _make_instance(
            payload.api_url, payload.api_key, payload.openai_key, payload.prompt,
            provider=payload.provider,
            meta_token=payload.meta_token,
            meta_app_secret=payload.meta_app_secret,
            meta_page_id=payload.meta_page_id,
            meta_capi_enabled=payload.meta_capi_enabled,
            meta_capi_access_token=payload.meta_capi_access_token,
            meta_capi_event_source_id=payload.meta_capi_event_source_id,
            meta_capi_test_event_code=payload.meta_capi_test_event_code,
            meta_ad_account_id=payload.meta_ad_account_id,
            phone_number_id=payload.phone_number_id,
            template_name=payload.template_name,
            inbound_ai_enabled=payload.inbound_ai_enabled,
            qualification_prompt=payload.qualification_prompt,
            max_ai_turns=payload.max_ai_turns,
            ai_response_delay=payload.ai_response_delay,
            auto_handoff_keywords=payload.auto_handoff_keywords,
        )
    try:
        _register_instance_owner_or_raise(actor, iid)
    except Exception:
        with instances_lock:
            instances.pop(iid, None)
        raise
    _save_instance_config(iid)
    import services.crm_service as crm_svc
    crm_svc.ensure_default_columns(iid)
    _grant_to_tenant_admin(tenant_admin, iid)
    _audit_instance(request, actor, "instance_created", iid, f"Criou a instância {iid}")
    return {"ok": True, "instance_id": iid}


@router.post("/create-channel")
def create_channel(payload: CreateChannelPayload, request: Request, _: str = Security(require_api_key)):
    """
    Cria instância no Evolution GO e configura webhook automaticamente.
    Recebe a global API key do servidor (não o token da instância).
    Retorna o QR code para o usuário escanear.
    """
    from services.evolution_service import create_evolution_instance, get_qrcode, set_whatsapp_settings

    _make_instance              = request.app.state.make_instance
    _save_instance_config               = request.app.state.save_instance_config
    _register_webhook_postgres  = request.app.state.register_webhook_postgres
    _register_webhook_evolution = request.app.state.register_webhook_evolution
    _save_ai_config             = request.app.state.save_ai_config
    ai_config                   = request.app.state.ai_config

    actor = _require_admin(request)
    iid = payload.instance_id.strip()
    if not iid:
        raise HTTPException(400, "instance_id não pode ser vazio.")
    existing_iid = _existing_instance_id(iid)
    if existing_iid:
        raise HTTPException(400, f"Instância '{existing_iid}' já existe.")

    if not auth_svc.can_create_more_instances(actor):
        raise HTTPException(
            403,
            f"Limite de instâncias do seu plano atingido ({actor.get('max_instances', 1)}). "
            "Fale com o suporte para aumentar seu plano.",
        )
    tenant_admin = _team_tenant_admin_or_raise(actor)

    glob_key = payload.global_api_key or ai_config.get("evo_global_api_key", "")
    if not glob_key:
        raise HTTPException(400, "Global API Key do Evolution GO é obrigatória. Configure em Configurações ou informe ao criar a instância.")
    result = create_evolution_instance(payload.api_url, glob_key, iid)
    if not result.get("ok"):
        raise HTTPException(502, f"Erro ao criar instância no Evolution GO: {result.get('detail', '')}")
    if payload.global_api_key and payload.global_api_key != ai_config.get("evo_global_api_key"):
        ai_config["evo_global_api_key"] = payload.global_api_key
        _save_ai_config()

    instance_api_key = result["api_key"]
    qr_code = result.get("qr_code", "")

    if not qr_code:
        qr_result = get_qrcode(payload.api_url, instance_api_key, iid)
        qr_code = qr_result.get("qr_code", "")

    default_prompt = (
        "Você cria mensagens de WhatsApp curtas, calorosas e naturais. "
        "Use o nome do contato. Máximo 5 linhas. 1 a 2 emojis. Sem linguagem corporativa."
    )
    with instances_lock:
        instances[iid] = _make_instance(
            api_url=payload.api_url,
            api_key=instance_api_key,
            openai_key="",
            prompt=default_prompt,
            provider="evolution",
        )
        instances[iid]["global_api_key"] = payload.global_api_key
    try:
        _register_instance_owner_or_raise(actor, iid)
    except Exception:
        with instances_lock:
            instances.pop(iid, None)
        delete_evolution_instance(payload.api_url, glob_key, iid)
        raise
    _save_instance_config(iid)
    import services.crm_service as crm_svc
    crm_svc.ensure_default_columns(iid)
    _grant_to_tenant_admin(tenant_admin, iid)
    _audit_instance(request, actor, "instance_created", iid, f"Criou o canal Evolution {iid}")

    # Aplica configurações avançadas padrão do WhatsApp — facilita a vida do usuário,
    # já cria a instância com o comportamento mais comum já ligado.
    default_wa_settings = {
        "alwaysOnline":    True,
        "rejectCall":      True,
        "msgCall":         "",
        "readMessages":    True,
        "readStatus":      True,
        "groupsIgnore":    True,
        "syncFullHistory": True,
    }
    wa_ok, wa_detail = set_whatsapp_settings(
        payload.api_url, instance_api_key, iid, default_wa_settings, global_api_key=payload.global_api_key,
    )
    if wa_ok:
        with instances_lock:
            instances[iid]["wa_settings"] = default_wa_settings
        _save_instance_config(iid)
    else:
        logger.warning(f"[WA-SETTINGS] Falha ao aplicar padrões na criação de {iid}: {wa_detail}")

    # Tenta registrar webhook via API HTTP do Evolution GO (método primário)
    webhook_result = _register_webhook_evolution(iid, instances[iid])
    webhook_ok = webhook_result.get("ok", False)
    if webhook_ok:
        logger.info(f"[WEBHOOK] Registrado via API: {iid}")
    else:
        logger.warning(f"[WEBHOOK] API falhou ({webhook_result.get('detail', '')}), tentando via SQL...")
        # Fallback: tenta via SQL direto no banco do Evolution GO
        sql_result = _register_webhook_postgres(iid, instances[iid])
        if sql_result.get("ok"):
            webhook_ok = True
            logger.info(f"[WEBHOOK] Registrado via SQL: {iid}")
        else:
            logger.warning(f"[WEBHOOK] SQL também falhou: {sql_result.get('detail', '')}")

    return {
        "ok": True,
        "instance_id": iid,
        "webhook_ok": webhook_ok,
        "qr_code": qr_code,
    }


@router.get("/instance/{instance_id}/qrcode")
def get_instance_qrcode(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Busca QR code atualizado para uma instância Evolution GO."""
    from services.evolution_service import get_qrcode

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    auth_svc.require_instance_access(instance_id, request)
    if not has_evolution_outbound(inst):
        raise HTTPException(400, "QR code disponível apenas quando api_url/api_key do Evolution GO estão configurados.")

    api_url = inst.get("api_url", "")
    api_key = inst.get("api_key", "") or inst.get("global_api_key", "")
    evo_name = inst.get("evo_instance_name", "").strip() or instance_id

    result = get_qrcode(api_url, api_key, evo_name)
    if not result.get("qr_code") and inst.get("global_api_key"):
        result = get_qrcode(api_url, inst["global_api_key"], evo_name)
    return result


@router.get("/instance/{instance_id}/qrcode/reconnect")
def reconnect_instance_qrcode(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Força nova tentativa de pareamento. Se a instância não existir no Evolution GO, recria automaticamente."""
    from services.evolution_service import get_qrcode, create_evolution_instance

    _save_instance_config              = request.app.state.save_instance_config
    _register_webhook_postgres = request.app.state.register_webhook_postgres
    ai_config                  = request.app.state.ai_config

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    auth_svc.require_instance_access(instance_id, request)
    if not has_evolution_outbound(inst):
        raise HTTPException(400, "QR code disponível apenas quando api_url/api_key do Evolution GO estão configurados.")

    api_url  = inst.get("api_url", "")
    api_key  = inst.get("api_key", "") or inst.get("global_api_key", "")
    glob_key = inst.get("global_api_key", "") or ai_config.get("evo_global_api_key", "")
    evo_name = inst.get("evo_instance_name", "").strip() or instance_id

    result = get_qrcode(api_url, api_key, evo_name)

    # Sessão já ativa — Evolution GO reconectou sozinho (sessão Baileys salva em disco)
    if result.get("already_connected"):
        return {"ok": True, "qr_code": "", "already_connected": True,
                "detail": "Sessão já ativa — a instância está reconectando automaticamente."}

    if not result.get("qr_code") and glob_key:
        result = get_qrcode(api_url, glob_key, evo_name)
        if result.get("already_connected"):
            return {"ok": True, "qr_code": "", "already_connected": True,
                    "detail": "Sessão já ativa — a instância está reconectando automaticamente."}

    if not result.get("qr_code") and glob_key:
        created = create_evolution_instance(api_url, glob_key, evo_name)
        if created.get("ok"):
            with instances_lock:
                instances[instance_id]["api_key"] = created["api_key"]
            _save_instance_config(instance_id)
            _register_webhook_postgres(instance_id, instances[instance_id])
            result = {"ok": True, "qr_code": created.get("qr_code", ""), "recreated": True}
        else:
            result = {"ok": False, "detail": f"Instância não existe e não foi possível recriar: {created.get('detail','')}"}

    return result


@router.post("/instance/{instance_id}/evolution/validate")
def validate_evolution_instance(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Valida a conexão Evolution da instância e tenta renovar o QR quando estiver offline."""
    from services.evolution_service import get_qrcode, create_evolution_instance

    admin_user = _require_admin(request)
    _save_instance_config = request.app.state.save_instance_config
    ai_config     = request.app.state.ai_config

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")

    if not has_evolution_outbound(inst):
        raise HTTPException(400, "Validação disponível apenas quando api_url/api_key do Evolution GO estão configurados.")

    api_url  = (inst.get("api_url", "") or "").strip()
    api_key  = (inst.get("api_key", "") or "").strip() or (inst.get("global_api_key", "") or "").strip()
    glob_key = (inst.get("global_api_key", "") or "").strip() or ai_config.get("evo_global_api_key", "")
    evo_name = (inst.get("evo_instance_name", "") or "").strip() or instance_id

    if not api_url:
        raise HTTPException(400, "api_url não configurada para esta instância.")
    if not api_key:
        raise HTTPException(400, "Token da instância não configurado para esta instância.")

    state = check_connection_state(api_url, api_key)
    response = {
        "ok": True,
        "instance_id": instance_id,
        "api_url": api_url,
        "evo_instance_name": evo_name,
        "state": state,
        "connected": state == "open",
        "reconnect_ok": False,
        "qr_code": "",
    }

    if state != "open":
        result = get_qrcode(api_url, api_key, evo_name)
        if not result.get("qr_code") and glob_key:
            result = get_qrcode(api_url, glob_key, evo_name)
        if not result.get("qr_code") and glob_key:
            created = create_evolution_instance(api_url, glob_key, evo_name)
            if created.get("ok"):
                with instances_lock:
                    instances[instance_id]["api_key"] = created["api_key"]
                _save_instance_config(instance_id)
                result = {"qr_code": created.get("qr_code", ""), "reconnect_ok": True}
        response["qr_code"] = result.get("qr_code", "") or ""
        response["reconnect_ok"] = bool(response["qr_code"])

    return response


@router.get("/instance/{instance_id}/config")
def get_instance_config(instance_id: str, request: Request, _: str = Security(require_api_key)):
    _instance_public_response = request.app.state.instance_public_response
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    auth_svc.require_instance_access(instance_id, request)
    return _instance_public_response(inst, request)


class UpdateAutoSuggestPayload(BaseModel):
    enabled: bool


@router.patch("/instance/{instance_id}/auto-suggest")
def update_auto_suggest(instance_id: str, payload: UpdateAutoSuggestPayload, request: Request, _: str = Security(require_api_key)):
    """Liga/desliga a geração automática de sugestões de resposta no Inbox SDR desta instância."""
    _save_instance_config = request.app.state.save_instance_config
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        instances[instance_id]["auto_suggest_enabled"] = payload.enabled
    _save_instance_config(instance_id)
    return {"ok": True, "auto_suggest_enabled": payload.enabled}


@router.put("/instance/{instance_id}/credentials")
def update_credentials(instance_id: str, payload: UpdateInstancePayload, request: Request, _: str = Security(require_api_key)):
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    _save_instance_config                = request.app.state.save_instance_config
    _validate_api_url            = request.app.state.validate_api_url
    _is_masked_secret            = request.app.state.is_masked_secret
    _normalize_meta_ad_account_id = request.app.state.normalize_meta_ad_account_id
    _validate_meta_token         = request.app.state.validate_meta_token
    _subscribe_meta_page_leadgen = request.app.state.subscribe_meta_page_leadgen
    _meta_health_cache           = request.app.state.meta_health_cache

    oauth_assets = meta_oauth_pg_svc.get_instance_assets(instance_id)
    oauth_connection = meta_oauth_pg_svc.get_connection_for_instance(instance_id)
    oauth_capi_active = bool(
        oauth_assets
        and oauth_assets.get("status") == "active"
        and oauth_assets.get("pixel_id")
    )

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if instances[instance_id]["status"] == "running":
            raise HTTPException(400, "Pare a campanha antes de alterar as credenciais.")

        inst = instances[instance_id]
        def _set_secret(field: str, value: str):
            if not _is_masked_secret(value):
                inst[field] = value

        inst["api_url"]              = _validate_api_url(payload.api_url)
        _set_secret("api_key", payload.api_key)
        if payload.global_api_key and not _is_masked_secret(payload.global_api_key):
            inst["global_api_key"]   = payload.global_api_key
        _set_secret("openai_key", payload.openai_key)
        inst["provider"]             = payload.provider
        _set_secret("meta_token", payload.meta_token)
        _set_secret("meta_app_secret", payload.meta_app_secret)
        inst["phone_number_id"]      = payload.phone_number_id
        inst["template_name"]        = payload.template_name
        # Salvar/validar a Evolution não pode desligar uma CAPI configurada
        # pelo card OAuth, cujo token vive fora dos campos manuais.
        inst["meta_capi_enabled"]    = True if oauth_capi_active else payload.meta_capi_enabled
        _set_secret("meta_capi_access_token", payload.meta_capi_access_token)
        if payload.meta_capi_event_source_id or not oauth_capi_active:
            inst["meta_capi_event_source_id"] = payload.meta_capi_event_source_id
        inst["meta_capi_test_event_code"] = payload.meta_capi_test_event_code
        inst["llm_provider"]         = payload.llm_provider
        _set_secret("llm_api_key", payload.llm_api_key)
        inst["llm_model"]            = payload.llm_model
        inst["model"]                = payload.llm_model
        inst["evo_instance_name"]    = payload.evo_instance_name
        inst["meta_page_id"]             = payload.meta_page_id
        _set_secret("meta_page_access_token", payload.meta_page_access_token)
        inst["meta_ad_account_id"]       = _normalize_meta_ad_account_id(payload.meta_ad_account_id)
    # inbound_ai_enabled, qualification_prompt e SDR settings são gerenciados pelo /sdr endpoint
    _save_instance_config(instance_id)

    activation: dict = {}

    if payload.provider == "meta":
        with instances_lock:
            real_token = instances[instance_id].get("meta_token", "")
            # meta_page_access_token é um Page Token dedicado (opcional); quando
            # preenchido, tem prioridade sobre meta_token para operações de página
            # (assinatura de leadgen). Fallback preserva o comportamento anterior
            # para instâncias que nunca preencheram esse campo.
            real_page_token = instances[instance_id].get("meta_page_access_token", "") or real_token
        token_result = {"ok": False, "reason": "token não informado"}
        if real_token:
            token_result = _validate_meta_token(real_token, payload.phone_number_id or "")
            if not token_result["ok"]:
                logger.warning(f"[META] Token inválido para instância={instance_id}: {token_result.get('reason')}")
        elif oauth_connection:
            token_result = {"ok": True, "name": "conexão automática (OAuth)"}

        capi_auto_enabled = oauth_capi_active
        capi_token_valid = oauth_capi_active
        capi_token_error = ""
        with instances_lock:
            real_capi_token = instances[instance_id].get("meta_capi_access_token", "")
        if real_capi_token and payload.meta_capi_event_source_id:
            try:
                rc = _requests.get(
                    "https://graph.facebook.com/v25.0/me",
                    params={"access_token": real_capi_token},
                    timeout=10,
                )
                rc_data = rc.json()
                if rc.status_code == 200 and rc_data.get("id"):
                    capi_token_valid = True
                else:
                    err = (rc_data.get("error") or {})
                    capi_token_error = err.get("message", f"HTTP {rc.status_code}")
                    logger.warning(f"[META] CAPI token inválido para instância={instance_id}: {capi_token_error}")
            except Exception as e:
                capi_token_error = str(e)
                logger.warning(f"[META] Erro ao validar CAPI token para instância={instance_id}: {e}")

            if capi_token_valid:
                with instances_lock:
                    instances[instance_id]["meta_capi_enabled"] = True
                _save_instance_config(instance_id)
                capi_auto_enabled = True
                logger.info(f"[META] CAPI ativado automaticamente — instância={instance_id}")
            else:
                with instances_lock:
                    instances[instance_id]["meta_capi_enabled"] = False
                _save_instance_config(instance_id)

        sub_result = {
            "ok": bool(instances[instance_id].get("meta_page_subscribed")),
            "reason": "" if instances[instance_id].get("meta_page_subscribed") else "Página ainda não inscrita em leadgen",
        }
        if payload.meta_page_id and real_page_token:
            sub_result = _subscribe_meta_page_leadgen(real_page_token, payload.meta_page_id)
            if sub_result["ok"]:
                logger.info(f"[META] Página {payload.meta_page_id} assinada para leadgen — instância={instance_id}")
            else:
                logger.warning(f"[META] Falha ao assinar página {payload.meta_page_id}: {sub_result.get('reason')} — instância={instance_id}")

        activation = {
            "token_valid":          token_result.get("ok", False),
            "token_name":           token_result.get("name", ""),
            "token_error":          token_result.get("reason", "") if not token_result.get("ok") else "",
            "page_subscribed":      sub_result.get("ok", False),
            "page_subscribed_error": sub_result.get("reason", "") if not sub_result.get("ok") else "",
            "capi_auto_enabled":    capi_auto_enabled,
            "capi_token_valid":     capi_token_valid,
            "capi_token_error":     capi_token_error,
            "webhook_waba_pending": True,
        }
        with instances_lock:
            instances[instance_id]["meta_page_subscribed"] = sub_result.get("ok", False)
        _save_instance_config(instance_id)
        _meta_health_cache.pop(instance_id, None)

    return {"ok": True, "instance_id": instance_id, "activation": activation}


@router.patch("/instance/{instance_id}/prompt")
def update_prompt(instance_id: str, payload: UpdatePromptPayload, request: Request, _: str = Security(require_api_key)):
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    _save_instance_config = request.app.state.save_instance_config
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        instances[instance_id]["prompt"] = payload.prompt
    _save_instance_config(instance_id)
    return {"ok": True, "instance_id": instance_id}


@router.patch("/instance/{instance_id}/campaign-context")
def update_campaign_context(instance_id: str, payload: UpdateCampaignContextPayload, request: Request, _: str = Security(require_api_key)):
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    auth_svc.require_feature(request, "disparador")
    _save_instance_config = request.app.state.save_instance_config
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        instances[instance_id]["campaign_context"] = payload.campaign_context
        if payload.campaign_timing:
            instances[instance_id]["campaign_timing"] = payload.campaign_timing
    _save_instance_config(instance_id)
    return {"ok": True}


@router.get("/instance/{instance_id}/credentials")
def get_credentials(instance_id: str, request: Request, _: str = Security(require_api_key)):
    admin_user = _require_admin(request)
    if not auth_svc.can_access_instance(admin_user, instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    _mask_secret = request.app.state.mask_secret
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    oauth_assets = meta_oauth_pg_svc.get_instance_assets(instance_id)
    oauth_capi_active = bool(
        oauth_assets
        and oauth_assets.get("status") == "active"
        and oauth_assets.get("pixel_id")
    )
    effective_capi_source_id = (
        inst.get("meta_capi_event_source_id", "")
        or ((oauth_assets or {}).get("pixel_id", "") if oauth_capi_active else "")
    )
    return {
        "api_url":               inst["api_url"],
        "api_key":               _mask_secret(inst.get("api_key", "")),
        "openai_key":            _mask_secret(inst.get("openai_key", "")),
        "prompt":                inst["prompt"],
        "provider":              inst.get("provider", "evolution"),
        "meta_token":            _mask_secret(inst.get("meta_token", "")),
        "meta_app_secret":       _mask_secret(inst.get("meta_app_secret", "")),
        "meta_page_id":              inst.get("meta_page_id", ""),
        "meta_page_access_token":    _mask_secret(inst.get("meta_page_access_token", "")),
        "phone_number_id":           inst.get("phone_number_id", ""),
        "template_name":         inst.get("template_name", ""),
        "meta_capi_enabled":     bool(inst.get("meta_capi_enabled") or oauth_capi_active),
        "meta_capi_access_token": _mask_secret(inst.get("meta_capi_access_token", "")),
        "meta_capi_event_source_id": effective_capi_source_id,
        "meta_capi_test_event_code": inst.get("meta_capi_test_event_code", ""),
        "meta_ad_account_id":    inst.get("meta_ad_account_id", ""),
        "inbound_ai_enabled":    inst.get("inbound_ai_enabled", True),
        "qualification_prompt":  inst.get("qualification_prompt", ""),
        "max_ai_turns":          inst.get("max_ai_turns", 8),
        "ai_response_delay":     inst.get("ai_response_delay", 3),
        "auto_handoff_keywords": inst.get("auto_handoff_keywords", []),
        "model":                 inst.get("model", inst.get("llm_model", "openai/gpt-oss-120b")),
        "llm_model":             inst.get("llm_model", inst.get("model", "openai/gpt-oss-120b")),
        "llm_provider":          inst.get("llm_provider", "groq"),
        "llm_api_key":           _mask_secret(inst.get("llm_api_key", "")),
        "key_openai":            _mask_secret(inst.get("key_openai", "")),
        "key_anthropic":         _mask_secret(inst.get("key_anthropic", "")),
        "key_groq":              _mask_secret(inst.get("key_groq", "")),
        "followup_enabled":      inst.get("followup_enabled", True),
        "followup_delay_hours":  inst.get("followup_delay_hours", 24),
        "followup_max":          inst.get("followup_max", 3),
        "scoring_criteria":      inst.get("scoring_criteria", ""),
        "evo_instance_name":     inst.get("evo_instance_name", ""),
        "global_api_key":        _mask_secret(inst.get("global_api_key", "")),
        "auto_release_minutes":  inst.get("auto_release_minutes", 3),
        "campaign_context":      inst.get("campaign_context", ""),
        "campaign_timing":       inst.get("campaign_timing", {}),
    }


@router.get("/settings/ai")
def get_ai_config(request: Request, _: str = Security(require_api_key)):
    _require_admin(request)
    _ai_config_response = request.app.state.ai_config_response
    return _ai_config_response()


@router.put("/settings/ai")
def update_ai_config(payload: UpdateAiConfigPayload, request: Request, _: str = Security(require_api_key)):
    # Config global (chaves mestras de IA + evo_global_api_key) é da conta mestre:
    # admin de tenant configura a chave dele NA INSTÂNCIA (_resolve_ai_settings dá
    # prioridade a ela) — só superadmin pode alterar o fallback global.
    auth_svc.require_superadmin(request)
    _is_masked_secret   = request.app.state.is_masked_secret
    _save_ai_config     = request.app.state.save_ai_config
    _ai_config_response = request.app.state.ai_config_response
    ai_config           = request.app.state.ai_config

    provider = payload.llm_provider if payload.llm_provider in _PROVIDER_KEY_FIELD else "groq"
    ai_config["llm_provider"] = provider
    ai_config["llm_model"] = payload.llm_model or "openai/gpt-oss-120b"
    if payload.key_openai and not _is_masked_secret(payload.key_openai):
        ai_config["key_openai"] = payload.key_openai
    if payload.key_anthropic and not _is_masked_secret(payload.key_anthropic):
        ai_config["key_anthropic"] = payload.key_anthropic
    if payload.key_groq and not _is_masked_secret(payload.key_groq):
        ai_config["key_groq"] = payload.key_groq
    if payload.llm_api_key and not _is_masked_secret(payload.llm_api_key):
        ai_config["llm_api_key"] = payload.llm_api_key
        ai_config[_PROVIDER_KEY_FIELD.get(provider, "key_openai")] = payload.llm_api_key
    else:
        ai_config["llm_api_key"] = ai_config.get(_PROVIDER_KEY_FIELD.get(provider, "key_openai"), "")
    if payload.evo_global_api_key and not _is_masked_secret(payload.evo_global_api_key):
        ai_config["evo_global_api_key"] = payload.evo_global_api_key
    _save_ai_config()
    return {"ok": True, **_ai_config_response()}


@router.get("/instance/{instance_id}/meta-health", dependencies=[Security(require_api_key)])
def get_meta_health(instance_id: str, request: Request):
    """Retorna saúde da integração Meta: token válido, página subscrita, último evento CAPI."""
    _validate_meta_token = request.app.state.validate_meta_token
    _meta_health_cache   = request.app.state.meta_health_cache
    auth_svc.require_instance_access(instance_id, request)

    now = time.time()
    cached = _meta_health_cache.get(instance_id)
    if cached and cached.get("expires_at", 0) > now:
        return {**cached["result"], "cached": True}

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])

    if inst.get("provider") != "meta":
        return {"ok": False, "reason": "not_meta", "cached": False}

    phone_id, token, credential_source = resolve_meta_outbound_credentials(inst, instance_id)
    token_result = _validate_meta_token(token, phone_id) if token else {"ok": False, "reason": "token não configurado"}

    audit = get_meta_capi_audit(instance_id=instance_id, limit=1)
    last = audit[0] if audit else None

    result = {
        "ok": True,
        "token_valid":    token_result.get("ok", False),
        "token_name":     token_result.get("name", ""),
        "token_error":    token_result.get("reason", "") if not token_result.get("ok") else "",
        "credential_source": credential_source,
        "page_subscribed": inst.get("meta_page_subscribed", False),
        "waba_subscribed": inst.get("meta_waba_subscribed", False),
        "capi_enabled":   inst.get("meta_capi_enabled", False),
        "last_capi_at":   last["timestamp"] if last else None,
        "last_capi_ok":   last["ok"] if last else None,
        "last_capi_event": last["event_name"] if last else None,
    }
    _meta_health_cache[instance_id] = {"result": result, "expires_at": now + _META_HEALTH_TTL}
    return {**result, "cached": False}


@router.get("/alerts", dependencies=[Security(require_api_key)])
def get_alerts(instance_id: str = "", limit: int = 50, request: Request = None):
    """Retorna os últimos alertas de descarte e falhas silenciosas."""
    _load_alerts = request.app.state.load_alerts
    if instance_id:
        auth_svc.require_instance_access(instance_id, request)
    else:
        auth_svc.require_superadmin(request)
    limit = max(1, min(limit, 200))
    alerts = _load_alerts(instance_id=instance_id, limit=limit)
    return {"total": len(alerts), "alerts": list(reversed(alerts))}


@router.delete("/alerts", dependencies=[Security(require_api_key)])
def clear_alerts(instance_id: str = "", request: Request = None):
    """Limpa alertas (todos ou de uma instância)."""
    if instance_id:
        auth_svc.require_instance_access(instance_id, request)
    else:
        auth_svc.require_superadmin(request)
    _alerts_lock  = request.app.state.alerts_lock
    _atomic_write = request.app.state.atomic_write

    with _alerts_lock:
        if not ALERTS_FILE.exists():
            return {"ok": True, "removed": 0}
        try:
            alerts = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            alerts = []
        before = len(alerts)
        if instance_id:
            alerts = [a for a in alerts if a.get("instance_id") != instance_id]
        else:
            alerts = []
        _atomic_write(ALERTS_FILE, json.dumps(alerts, ensure_ascii=False, indent=2))
    return {"ok": True, "removed": before - len(alerts)}


@router.get("/instances")
def list_instances(request: Request, _: str = Security(require_api_key)):
    _instance_public_response = request.app.state.instance_public_response
    token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
    user = auth_svc.verify_token(token) if token else None
    # Instância criada por gerente/sdr = "número de equipe": marcada aqui pra
    # sair da grade do dashboard e aparecer na aba Usuários (task #9).
    team_owners = auth_svc.team_instance_owners()
    with instances_lock:
        return {
            iid: {
                "status":         inst["status"],
                "envios_hoje":    inst["envios_hoje"],
                "total_contacts": len(inst["contacts"]),
                "logs_count":     len(inst["logs"]),
                "falhos_count":   sum(1 for l in inst["logs"] if l["status"].startswith(("FALHOU", "ERRO_OPENAI"))),
                "daily_limit":    inst.get("daily_limit", 0),
                "provider":       inst.get("provider", "evolution"),
                "team":           iid in team_owners,
                "owner":          team_owners.get(iid),
            }
            for iid, inst in instances.items()
            # Falha fechado: sem usuario valido, nao mostra nenhuma instancia
            # (antes caia num "mostra tudo" que vazava dados entre tenants).
            if user is not None and auth_svc.owns_instance(user, iid)
        }


@router.post("/upload-contacts/{instance_id}")
async def upload_contacts(instance_id: str, request: Request, file: UploadFile = File(...), _: str = Security(require_api_key)):
    _save_instance_config = request.app.state.save_instance_config
    if instance_id not in instances:
        raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instances[instance_id]["status"] == "running":
            raise HTTPException(400, "Não é possível trocar contatos com campanha em andamento.")

    content  = await file.read()
    filename = file.filename or ""

    try:
        raw = parse_file(filename, content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    novos, rejeitados = clean_contacts(raw)
    with instances_lock:
        existentes = instances[instance_id]["contacts"]
        numeros_existentes = {c["number"] for c in existentes}
        adicionados = [c for c in novos if c["number"] not in numeros_existentes]
        duplicados_existentes = len(novos) - len(adicionados)
        instances[instance_id]["contacts"] = existentes + adicionados
        total = len(instances[instance_id]["contacts"])
    _save_instance_config(instance_id)
    return {
        "ok": True,
        "instance_id": instance_id,
        "total": total,
        "adicionados": len(adicionados),
        "duplicados": duplicados_existentes,
        "removidos": len(rejeitados),
        "lidos": len(raw),
    }


@router.get("/contacts/{instance_id}")
def list_contacts(instance_id: str, request: Request, _: str = Security(require_api_key)):
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        contacts = list(instances[instance_id]["contacts"])
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    return {"contacts": contacts}


@router.delete("/contacts/{instance_id}")
def clear_contacts(instance_id: str, request: Request, _: str = Security(require_api_key)):
    _save_instance_config = request.app.state.save_instance_config
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if instances[instance_id]["status"] == "running":
            raise HTTPException(400, "Pare a campanha antes de limpar os contatos.")
        instances[instance_id]["contacts"] = []
        instances[instance_id]["logs"]     = []
        instances[instance_id]["envios_hoje"] = 0
        instances[instance_id]["envios_hora"] = []
    clear_sent_numbers(instance_id)
    clear_queue(instance_id)
    _save_instance_config(instance_id)
    return {"ok": True}


@router.get("/contacts/{instance_id}/download")
def download_contacts(instance_id: str, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        contacts = list(instances[instance_id]["contacts"])
    if not contacts:
        raise HTTPException(404, "Nenhum contato carregado nesta instância.")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["nome", "numero"])
    for c in contacts:
        writer.writerow([c["name"], c["number"]])

    filename = f"contatos_{instance_id}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/contacts/{instance_id}/invalid")
def get_invalid_contacts(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Retorna lista de números marcados como inválidos (não têm WhatsApp)."""
    from services.campaign_service import load_invalid_numbers
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    invalid = list(load_invalid_numbers(instance_id))
    return {"instance_id": instance_id, "invalid_count": len(invalid), "invalid_numbers": invalid}


@router.delete("/contacts/{instance_id}/invalid")
def clean_invalid_contacts(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Remove da lista de contatos todos os números marcados como inválidos."""
    from services.campaign_service import load_invalid_numbers, clear_invalid_numbers
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    _save_instance_config = request.app.state.save_instance_config
    invalid = load_invalid_numbers(instance_id)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if not invalid:
            return {"removed": 0, "remaining": len(instances[instance_id]["contacts"])}
        before = len(instances[instance_id]["contacts"])
        instances[instance_id]["contacts"] = [
            c for c in instances[instance_id]["contacts"]
            if c["number"] not in invalid
        ]
        removed = before - len(instances[instance_id]["contacts"])
        remaining = len(instances[instance_id]["contacts"])
    clear_invalid_numbers(instance_id)
    _save_instance_config(instance_id)
    return {"removed": removed, "remaining": remaining}


@router.post("/start-campaign")
@limiter.limit("5/minute")
def start_campaign(request: Request, payload: StartCampaignPayload, _: str = Security(require_api_key)):
    _apply_global_ai_defaults = request.app.state.apply_global_ai_defaults
    _save_conversation        = request.app.state.save_conversation

    iid = payload.instance_id
    with instances_lock:
        if iid not in instances:
            raise HTTPException(404, f"Instância '{iid}' não encontrada.")
    auth_svc.require_instance_access(iid, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        inst = instances[iid]
        if inst["status"] == "running":
            raise HTTPException(400, "Campanha já em andamento nesta instância.")
        if not inst["contacts"]:
            raise HTTPException(400, "Nenhum contato carregado nesta instância.")
        if not inst["api_url"] or not inst["api_key"]:
            raise HTTPException(400, "api_url e api_key são obrigatórios.")

        _apply_global_ai_defaults(iid)
        inst["status"]      = "running"
        inst["stop_flag"]   = False
        inst["logs"]        = []
        inst["envios_hora"] = []
        inst["daily_limit"] = 0

        if payload.resume:
            inst["envios_hoje"] = len(load_sent_numbers(iid))
        else:
            inst["envios_hoje"] = 0

    timing = {
        "hourly_limit":    payload.hourly_limit,
        "delay_min":       payload.delay_min,
        "delay_max":       payload.delay_max,
        "long_pause_every": payload.long_pause_every,
        "long_pause_min":  payload.long_pause_min,
        "long_pause_max":  payload.long_pause_max,
    }
    t = threading.Thread(
        target=run_campaign,
        args=(iid, instances, payload.context, payload.resume, payload.daily_limit, timing, conversations, _save_conversation),
        daemon=True,
        name=f"campaign-{iid}",
    )
    with instances_lock:
        instances[iid]["thread"] = t
    t.start()

    return {"ok": True, "instance_id": iid, "message": "Campanha iniciada.", "resume": payload.resume}


@router.post("/stop-campaign/{instance_id}")
def stop_campaign_endpoint(instance_id: str, request: Request, _: str = Security(require_api_key)):
    from services.campaign_service import stop_campaign
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if instances[instance_id]["status"] not in ("running", "stopping"):
            raise HTTPException(400, "Nenhuma campanha em andamento.")
    stop_campaign(instance_id, instances)
    return {"ok": True, "instance_id": instance_id, "message": "Sinal de parada enviado."}


@router.get("/status/{instance_id}")
def get_status(instance_id: str, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
        logs = copy.deepcopy(inst.get("logs", []))
        contacts = list(inst.get("contacts", []))
    total        = len(contacts)
    daily_limit  = inst.get("daily_limit", 0)
    progress_max = daily_limit if daily_limit > 0 else total
    progress     = round(inst["envios_hoje"] / progress_max * 100) if progress_max > 0 else 0
    sent_saved   = len(load_sent_numbers(instance_id))
    return {
        "instance_id":    instance_id,
        "status":         inst["status"],
        "envios_hoje":    inst["envios_hoje"],
        "total_contacts": total,
        "logs_count":     len(logs),
        "falhos_count":   sum(1 for l in logs if l["status"].startswith(("FALHOU", "ERRO_OPENAI"))),
        "daily_limit":    daily_limit,
        "progress":       min(progress, 100),
        "sent_saved":     sent_saved,
    }


@router.get("/logs/{instance_id}")
def get_logs(instance_id: str, request: Request, offset: int = 0, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        logs = copy.deepcopy(instances[instance_id]["logs"])
    return {"instance_id": instance_id, "total": len(logs), "logs": logs[offset:]}


@router.get("/sends/{instance_id}")
def get_sends(instance_id: str, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        logs = copy.deepcopy(instances[instance_id]["logs"])
    sends = [
        log for log in logs
        if log["status"].startswith(("ENVIADO", "FALHOU", "ERRO_OPENAI"))
        and log.get("phone")
    ]
    return {"instance_id": instance_id, "total": len(sends), "sends": sends}


@router.post("/test-message")
@limiter.limit("20/minute")
def test_message(request: Request, payload: TestMessagePayload, _: str = Security(require_api_key)):
    _resolve_ai_settings = request.app.state.resolve_ai_settings

    iid = payload.instance_id
    with instances_lock:
        if iid not in instances:
            raise HTTPException(404, f"Instância '{iid}' não encontrada.")
    auth_svc.require_instance_access(iid, request)
    with instances_lock:
        inst = dict(instances[iid])
        if not inst["api_url"] or not inst["api_key"]:
            raise HTTPException(400, "api_url e api_key são obrigatórios.")

    if payload.message.strip():
        message = payload.message.strip()
    else:
        ai = _resolve_ai_settings(inst)
        if not ai["api_key"]:
            raise HTTPException(400, "API Key da OpenAI não configurada.")
        message = generate_message(
            openai_key=ai["api_key"],
            prompt=inst["prompt"],
            name=payload.name,
            phone=payload.phone,
            contact={"name": payload.name, "number": payload.phone},
            context=payload.context,
            model=ai["model"],
            provider=ai["provider"],
        )
        if not message:
            raise HTTPException(500, "Erro ao gerar mensagem com OpenAI.")

    success, err = send_whatsapp_message(
        api_url=inst["api_url"],
        api_key=inst["api_key"],
        instance_id=iid,
        phone=payload.phone,
        text=message,
    )

    if not success:
        raise HTTPException(500, f"Mensagem gerada mas falhou ao enviar: {err}")

    return {"ok": True, "message": message, "phone": payload.phone}


@router.post("/send-direct")
def send_direct(payload: SendDirectPayload, request: Request, _: str = Security(require_api_key)):
    """Envia mensagem digitada manualmente, sem geração por IA."""
    iid = payload.instance_id
    with instances_lock:
        if iid not in instances:
            raise HTTPException(404, f"Instância '{iid}' não encontrada.")
    auth_svc.require_instance_access(iid, request)
    with instances_lock:
        inst = dict(instances[iid])
        if not inst["api_url"] or not inst["api_key"]:
            raise HTTPException(400, "api_url e api_key são obrigatórios.")
    if not payload.message.strip():
        raise HTTPException(400, "Mensagem não pode ser vazia.")

    success, err = send_whatsapp_message(
        api_url=inst["api_url"],
        api_key=inst["api_key"],
        instance_id=iid,
        phone=payload.phone,
        text=payload.message,
    )
    if not success:
        raise HTTPException(500, err or "Falha ao enviar mensagem via Evolution GO.")
    return {"ok": True, "phone": payload.phone, "message": payload.message}


@router.post("/preview-prompt/{instance_id}")
@limiter.limit("20/minute")
def preview_prompt(request: Request, instance_id: str, payload: PreviewPromptPayload, _: str = Security(require_api_key)):
    _resolve_ai_settings = request.app.state.resolve_ai_settings

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        inst = dict(instances[instance_id])
        contacts = list(inst.get("contacts", []))
    ai = _resolve_ai_settings(inst)
    if not ai["api_key"]:
        raise HTTPException(400, "API Key da OpenAI não configurada.")

    count = max(1, min(payload.count, 5))

    if payload.name.strip() or payload.phone.strip():
        samples = [{"name": payload.name.strip() or "Teste", "phone": payload.phone.strip()}]
    else:
        if contacts:
            sample = random.sample(contacts, min(count, len(contacts)))
            samples = [{"name": c["name"], "phone": c["number"]} for c in sample]
        else:
            fallback = ["João", "Maria", "Carlos", "Ana", "Pedro"]
            samples  = [{"name": fallback[i % len(fallback)], "phone": ""} for i in range(count)]

    def _gen(s):
        msg = generate_message(
            openai_key=ai["api_key"],
            prompt=inst["prompt"],
            name=s["name"],
            phone=s.get("phone", ""),
            contact=s,
            context=payload.context,
            model=ai["model"],
            provider=ai["provider"],
        )
        return {"name": s["name"], "phone": s["phone"], "message": msg or "(erro ao gerar)"}

    messages = [None] * len(samples)
    with ThreadPoolExecutor(max_workers=len(samples)) as ex:
        futures = {ex.submit(_gen, s): i for i, s in enumerate(samples)}
        for future in as_completed(futures):
            messages[futures[future]] = future.result()

    return {"ok": True, "previews": messages}


@router.post("/retry-failed/{instance_id}")
def retry_failed(instance_id: str, payload: RetryFailedPayload, request: Request, _: str = Security(require_api_key)):
    _save_conversation = request.app.state.save_conversation
    auth_svc.require_instance_access(instance_id, request)

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

        inst = instances[instance_id]
        if inst["status"] == "running":
            raise HTTPException(400, "Pare a campanha antes de tentar reenvio.")

        sent_ok  = {log["phone"] for log in inst["logs"] if log["status"].startswith("ENVIADO") and log.get("phone")}
        failed   = [
            log for log in inst["logs"]
            if log["status"].startswith("FALHOU") and log.get("phone") and log["phone"] not in sent_ok
        ]

        if not failed:
            raise HTTPException(400, "Nenhum contato com falha para reenviar.")

        contacts_map = {c["number"]: c for c in inst["contacts"]}
        retry_contacts = [
            contacts_map[log["phone"]]
            for log in failed
            if log["phone"] in contacts_map
        ]

        if not retry_contacts:
            raise HTTPException(400, "Contatos com falha não encontrados na lista atual.")

        inst["contacts"]    = retry_contacts
        inst["status"]      = "running"
        inst["stop_flag"]   = False
        inst["logs"]        = []
        inst["envios_hoje"] = 0
        inst["envios_hora"] = []
        inst["daily_limit"] = 0

    t = threading.Thread(
        target=run_campaign,
        args=(instance_id, instances, payload.context, False, 0, None, conversations, _save_conversation),
        daemon=True,
        name=f"retry-{instance_id}",
    )
    with instances_lock:
        instances[instance_id]["thread"] = t
    t.start()

    return {"ok": True, "instance_id": instance_id, "retry_count": len(retry_contacts)}


@router.get("/history")
def get_history(request: Request, _: str = Security(require_api_key)):
    """Historico de campanhas de TODAS as instancias — arquivo unico, sem
    separacao por tenant hoje. Restrito ao superadmin ate isolar por instancia."""
    auth_svc.require_superadmin(request)
    if not HISTORY_FILE.exists():
        return {"history": []}
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return {"history": list(reversed(history))}
    except Exception:
        return {"history": []}


def _delete_instance_core(instance_id: str, request: Request) -> dict:
    """
    Nucleo da delecao de instancia: Evolution GO, dados locais, acessos e KB.
    Usado tanto pelo endpoint DELETE /instance/{id} quanto pelo cascade de
    delecao de usuario (routers/auth.py) quando o usuario excluido e dono de
    instancias proprias — nao repete a checagem de posse do chamador, isso
    ja deve ter sido validado antes (ou o dono e o proprio alvo da exclusao).
    """
    _safe_instance_path_part    = request.app.state.safe_instance_path_part
    _delete_instance_config_pg = request.app.state.delete_instance_config_pg
    _delete_instance_local_data = request.app.state.delete_instance_local_data
    ai_config                   = request.app.state.ai_config

    safe_instance_id = _safe_instance_path_part(instance_id)
    with instances_lock:
        if safe_instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if instances[safe_instance_id]["status"] == "running":
            raise HTTPException(400, "Pare a campanha antes de deletar a instância.")
        inst = instances[safe_instance_id]
        api_url        = inst.get("api_url", "")
        evo_name       = inst.get("evo_instance_name") or safe_instance_id
        global_api_key = ai_config.get("evo_global_api_key", "")
        del instances[safe_instance_id]

    # Remove do Evolution GO (não bloqueia se falhar)
    if api_url and global_api_key:
        ok, err = delete_evolution_instance(api_url, global_api_key, evo_name)
        if not ok:
            logger.warning(f"[Delete] Evolution GO não removeu '{evo_name}': {err}")

    _delete_instance_config_pg(safe_instance_id)
    _delete_instance_local_data(safe_instance_id)
    removed_users = auth_svc.remove_instance_from_all_users(safe_instance_id)
    try:
        import services.kb_embedding_service as kb_emb
        kb_emb.delete_instance(safe_instance_id)
    except Exception:
        logger.warning(f"[Delete] Falha ao purgar embeddings de KB de '{safe_instance_id}'", exc_info=True)
    return {"ok": True, "instance_id": safe_instance_id, "removed_users": removed_users}


@router.delete("/instance/{instance_id}")
def delete_instance(instance_id: str, request: Request, _: str = Security(require_api_key)):
    admin_user = _require_admin(request)
    safe_instance_id = request.app.state.safe_instance_path_part(instance_id)
    if not auth_svc.can_access_instance(admin_user, safe_instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    result = _delete_instance_core(instance_id, request)
    _audit_instance(
        request, admin_user, "instance_deleted", safe_instance_id,
        f"Excluiu a instância {safe_instance_id}",
    )
    return result


# ── Fila de mensagens pré-geradas ──────────────────────────────────────────────

@router.post("/generate-queue/{instance_id}")
@limiter.limit("10/minute")
def start_generate_queue(request: Request, instance_id: str, payload: GenerateQueuePayload, _: str = Security(require_api_key)):
    _resolve_openai_key       = request.app.state.resolve_openai_key
    _apply_global_ai_defaults = request.app.state.apply_global_ai_defaults
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = instances[instance_id]
        if not inst["contacts"]:
            raise HTTPException(400, "Nenhum contato carregado.")
        if not _resolve_openai_key(inst):
            raise HTTPException(400, "API Key da OpenAI não configurada.")
        _apply_global_ai_defaults(instance_id)
        if inst["queue_status"] == "generating":
            raise HTTPException(400, "Geração já em andamento.")

        inst["queue_status"]    = "generating"
        inst["queue_stop_flag"] = False
        inst["queue"]           = []
        inst["queue_progress"]  = 0
        total_contacts = len(inst["contacts"])

    t = threading.Thread(
        target=generate_queue_thread,
        args=(instance_id, instances, payload.context),
        daemon=True,
        name=f"queue-{instance_id}",
    )
    t.start()
    return {"ok": True, "total": total_contacts}


@router.get("/queue/{instance_id}")
def get_queue(instance_id: str, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = instances[instance_id]
        return {
            "queue_status":   inst["queue_status"],
            "queue_progress": inst["queue_progress"],
            "total_contacts": len(inst["contacts"]),
            "queue":          copy.deepcopy(inst["queue"]),
        }


@router.put("/queue/{instance_id}/{index}")
def update_queue_item(instance_id: str, index: int, payload: UpdateQueueItemPayload, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        queue = instances[instance_id]["queue"]
        if index < 0 or index >= len(queue):
            raise HTTPException(404, "Item não encontrado na fila.")
        queue[index]["message"] = payload.message
    save_queue(instance_id, instances[instance_id]["queue"])
    return {"ok": True}


@router.patch("/queue/{instance_id}/{index}/skip")
def toggle_skip_queue_item(instance_id: str, index: int, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        queue = instances[instance_id]["queue"]
        if index < 0 or index >= len(queue):
            raise HTTPException(404, "Item não encontrado na fila.")
        queue[index]["skipped"] = not queue[index]["skipped"]
        skipped = queue[index]["skipped"]
    save_queue(instance_id, instances[instance_id]["queue"])
    return {"ok": True, "skipped": skipped}


@router.post("/stop-queue-generation/{instance_id}")
def stop_queue_generation(instance_id: str, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        instances[instance_id]["queue_stop_flag"] = True
    return {"ok": True}


# Cache local do endpoint batch de estados — limita chamadas externas por
# ciclo de polling quando não há chave global (o /instance/all já tem cache
# próprio no service).
_conn_state_cache: dict[str, tuple[float, str]] = {}
_state_cache_lock = threading.Lock()
_CONN_STATE_TTL = 10.0


@router.get("/instances/wa-states")
def get_wa_states(request: Request, _: str = Security(require_api_key)):
    """
    Estado da sessão WhatsApp no Evolution GO para todas as instâncias do
    usuário em uma chamada. O campo provider não interfere nesta checagem:
    integrações Meta possuem indicadores de saúde próprios.
    Substitui N chamadas de /instance/{id}/whatsapp-status no polling da home —
    para instâncias Evolution com global key, resolve tudo com 1 fetch (cacheado)
    de /instance/all por servidor, em vez de N fetches da lista completa (O(N²)).
    """
    ai_config = request.app.state.ai_config
    token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
    user = auth_svc.verify_token(token) if token else None
    with instances_lock:
        items = [
            (iid, dict(inst)) for iid, inst in instances.items()
            # Falha fechado, como /instances: sem usuário válido, nada é retornado
            if user is not None and auth_svc.owns_instance(user, iid)
        ]

    now = time.monotonic()
    states: dict[str, str] = {}
    for iid, inst in items:
        api_url  = inst.get("api_url", "")
        evo_name = (inst.get("evo_instance_name") or "").strip() or iid
        glob_key = (inst.get("global_api_key") or "").strip() or ai_config.get("evo_global_api_key", "")
        if glob_key:
            # get_all_instances é cacheado por servidor — N instâncias no mesmo
            # Evolution GO compartilham o mesmo fetch.
            info = next(
                (i for i in get_all_instances(api_url, glob_key)
                 if i.get("name", "").lower() == evo_name.lower()),
                {},
            )
            states[iid] = "open" if info.get("connected", False) else "close"
        else:
            # Sem global key só dá pra checar se o servidor responde — cacheado
            # por api_url para não repetir a mesma checagem por instância.
            cache_key = api_url
            with _state_cache_lock:
                cached = _conn_state_cache.get(cache_key)
            if cached and now - cached[0] < _CONN_STATE_TTL:
                states[iid] = cached[1]
                continue
            state = check_connection_state(api_url, inst.get("api_key", ""))
            with _state_cache_lock:
                _conn_state_cache[cache_key] = (now, state)
            states[iid] = state

    return {"states": states}


@router.get("/instance/{instance_id}/whatsapp-status")
def get_whatsapp_status(instance_id: str, request: Request, _: str = Security(require_api_key)):
    ai_config = request.app.state.ai_config
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    api_url  = inst.get("api_url", "")
    evo_name = (inst.get("evo_instance_name") or "").strip() or instance_id
    glob_key = (inst.get("global_api_key") or "").strip() or ai_config.get("evo_global_api_key", "")
    if glob_key:
        # O status WA representa sempre a sessão do Evolution GO, inclusive
        # quando a instância também possui integração Meta.
        data      = get_instance_info(api_url, glob_key, evo_name)
        connected = data.get("connected", False)
        state     = "open" if connected else "close"
    else:
        # Fallback: só verifica se o servidor Evolution GO responde
        state = check_connection_state(api_url, inst.get("api_key", ""))
    return {
        "instance_id": instance_id,
        "state": state,
        "connected": state == "open",
        "detail": "",
    }


@router.post("/reset-instance/{instance_id}")
def reset_instance(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Limpa tudo da instância: contatos, logs, fila e progresso salvo."""
    _save_instance_config = request.app.state.save_instance_config
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if instances[instance_id]["status"] == "running":
            raise HTTPException(400, "Pare a campanha antes de fazer reset.")
        inst = instances[instance_id]
        inst["contacts"]       = []
        inst["logs"]           = []
        inst["envios_hoje"]    = 0
        inst["envios_hora"]    = []
        inst["daily_limit"]    = 0
        inst["status"]         = "idle"
        inst["queue"]          = []
        inst["queue_status"]   = "idle"
        inst["queue_progress"] = 0
        inst["stop_flag"]      = False
    clear_sent_numbers(instance_id)
    _save_instance_config(instance_id)
    return {"ok": True, "instance_id": instance_id}


@router.post("/preview-contacts-import")
def preview_contacts_import(payload: ProcessContactsPayload, _: str = Security(require_api_key)):
    """Processa texto bruto de contatos e retorna resultado validado sem salvar."""
    raw = _parse_lines(payload.raw_text)
    seen: set = set()
    entries = []

    for item in raw:
        orig_name   = item.get("name", "")
        orig_number = item.get("number", "")
        original    = f"{orig_name}, {orig_number}" if orig_name else orig_number

        number = normalize_phone(orig_number)
        if not number:
            entries.append({"original": original, "name": orig_name, "number": orig_number, "valid": False, "reason": "número inválido"})
        elif number in seen:
            entries.append({"original": original, "name": orig_name, "number": number, "valid": False, "reason": "duplicado"})
        else:
            seen.add(number)
            entries.append({"original": original, "name": normalize_name(orig_name), "number": number, "valid": True, "reason": None})

    total_valid    = sum(1 for e in entries if e["valid"])
    total_rejected = sum(1 for e in entries if not e["valid"])
    return {"entries": entries, "total_valid": total_valid, "total_rejected": total_rejected}


@router.post("/import-contacts-text/{instance_id}")
async def import_contacts_text(instance_id: str, payload: ImportContactsTextPayload, request: Request, _: str = Security(require_api_key)):
    """Importa contatos colados como texto puro para uma instância."""
    _save_instance_config = request.app.state.save_instance_config
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if instances[instance_id]["status"] == "running":
            raise HTTPException(400, "Não é possível trocar contatos com campanha em andamento.")

    raw = _parse_lines(payload.raw_text)
    novos, rejeitados = clean_contacts(raw)
    with instances_lock:
        existentes = instances[instance_id]["contacts"]
        numeros_existentes = {c["number"] for c in existentes}
        adicionados = [c for c in novos if c["number"] not in numeros_existentes]
        duplicados_existentes = len(novos) - len(adicionados)
        instances[instance_id]["contacts"] = existentes + adicionados
        total = len(instances[instance_id]["contacts"])
    _save_instance_config(instance_id)
    return {
        "ok": True,
        "instance_id": instance_id,
        "total": total,
        "adicionados": len(adicionados),
        "duplicados": duplicados_existentes,
        "removidos": len(rejeitados),
        "lidos": len(raw),
    }


@router.post("/send-queue/{instance_id}")
@limiter.limit("5/minute")
def send_queue(request: Request, instance_id: str, payload: SendQueuePayload = SendQueuePayload(), _: str = Security(require_api_key)):
    _apply_global_ai_defaults = request.app.state.apply_global_ai_defaults
    _save_conversation        = request.app.state.save_conversation
    auth_svc.require_instance_access(instance_id, request)
    auth_svc.require_feature(request, "disparador")

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = instances[instance_id]
        if inst["status"] == "running":
            raise HTTPException(400, "Campanha já em andamento.")

        queue_snapshot = copy.deepcopy(inst["queue"])
        active = [item for item in queue_snapshot if not item["skipped"] and item["message"].strip()]
        if not active:
            raise HTTPException(400, "Nenhuma mensagem aprovada na fila.")

        _apply_global_ai_defaults(instance_id)
        inst["status"]      = "running"
        inst["stop_flag"]   = False
        inst["logs"]        = []
        inst["envios_hoje"] = 0
        inst["envios_hora"] = []
        inst["daily_limit"] = 0

    timing = {
        "hourly_limit":    payload.hourly_limit,
        "delay_min":       payload.delay_min,
        "delay_max":       payload.delay_max,
        "long_pause_every": payload.long_pause_every,
        "long_pause_min":  payload.long_pause_min,
        "long_pause_max":  payload.long_pause_max,
    }
    t = threading.Thread(
        target=run_campaign_from_queue,
        args=(instance_id, instances, active, payload.daily_limit, timing, conversations, _save_conversation),
        daemon=True,
        name=f"send-queue-{instance_id}",
    )
    with instances_lock:
        instances[instance_id]["thread"] = t
    t.start()
    return {"ok": True, "total": len(active)}


# ── Base de Conhecimento ────────────────────────────────────────────────────────

@router.post("/instance/{instance_id}/knowledge")
async def upload_knowledge(
    instance_id: str,
    request: Request,
    file: UploadFile = File(...),
    _: str = Security(require_api_key),
):
    """Faz upload de um arquivo para a base de conhecimento da instância."""
    _safe_instance_path_part = request.app.state.safe_instance_path_part
    _safe_filename           = request.app.state.safe_filename
    auth_svc.require_instance_access(instance_id, request)

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

    allowed = {".pdf", ".txt", ".md", ".docx"}
    safe_instance_id = _safe_instance_path_part(instance_id)
    safe_name = _safe_filename(file.filename or "")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"Tipo não suportado. Use: {', '.join(allowed)}")

    kb_dir = KNOWLEDGE_DIR / safe_instance_id
    kb_dir.mkdir(parents=True, exist_ok=True)

    dest = kb_dir / safe_name
    dest.write_bytes(await file.read())
    return {"ok": True, "filename": dest.name, "size": dest.stat().st_size}


@router.get("/instance/{instance_id}/knowledge")
def list_knowledge(instance_id: str, request: Request, _: str = Security(require_api_key)):
    _safe_instance_path_part = request.app.state.safe_instance_path_part
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

    safe_instance_id = _safe_instance_path_part(instance_id)
    kb_dir = KNOWLEDGE_DIR / safe_instance_id
    if not kb_dir.exists():
        return {"files": []}

    files = [
        {"filename": f.name, "size": f.stat().st_size}
        for f in sorted(kb_dir.iterdir())
        if f.is_file()
    ]
    return {"files": files}


@router.delete("/instance/{instance_id}/knowledge/{filename}")
def delete_knowledge(instance_id: str, filename: str, request: Request, _: str = Security(require_api_key)):
    """Remove um arquivo da base de conhecimento."""
    _safe_instance_path_part = request.app.state.safe_instance_path_part
    _safe_filename           = request.app.state.safe_filename
    auth_svc.require_instance_access(instance_id, request)

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

    safe_instance_id = _safe_instance_path_part(instance_id)
    safe_name = _safe_filename(filename)
    target = KNOWLEDGE_DIR / safe_instance_id / safe_name
    if not target.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    target.unlink()
    return {"ok": True, "filename": safe_name}


# ── WhatsApp Advanced Settings (Evolution GO) ─────────────────────────────────

class WhatsAppSettingsPayload(BaseModel):
    alwaysOnline:    bool = True
    rejectCall:      bool = True
    msgCall:         str  = ""
    readMessages:    bool = True
    readStatus:      bool = True
    groupsIgnore:    bool = True
    syncFullHistory: bool = True


@router.get("/instance/{instance_id}/whatsapp-settings")
def get_whatsapp_settings_endpoint(instance_id: str, request: Request, _: str = Security(require_api_key)):
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        stored = inst.get("wa_settings", {})
    return {"ok": True, "settings": stored}


@router.put("/instance/{instance_id}/whatsapp-settings")
def update_whatsapp_settings_endpoint(instance_id: str, payload: WhatsAppSettingsPayload, request: Request, _: str = Security(require_api_key)):
    _save_instance_config = request.app.state.save_instance_config
    ai_config     = request.app.state.ai_config
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        api_url          = inst.get("api_url", "")
        evo_name         = inst.get("evo_instance_name", "").strip() or instance_id
        instance_api_key = inst.get("api_key", "")
        inst["wa_settings"] = payload.model_dump()

    _save_instance_config(instance_id)

    if api_url and instance_api_key:
        global_api_key = ai_config.get("evo_global_api_key", "")
        set_whatsapp_settings(api_url, instance_api_key, evo_name, payload.model_dump(), global_api_key)

    return {"ok": True}


# ── Informações da instância (número + perfil) ────────────────────────────────

@router.get("/instance/{instance_id}/whatsapp-info")
def get_whatsapp_info(instance_id: str, request: Request, _: str = Security(require_api_key)):
    ai_config = request.app.state.ai_config
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        api_url  = inst.get("api_url", "")
        evo_name = inst.get("evo_instance_name", "").strip() or instance_id

    global_api_key = ai_config.get("evo_global_api_key", "")
    if not api_url or not global_api_key:
        return {"ok": True, "connected": False, "number": "", "profile_name": ""}

    data = get_instance_info(api_url, global_api_key, evo_name)
    connected    = data.get("connected", False)
    profile_name = data.get("name", "") or ""
    jid          = data.get("jid", "") or ""
    number       = jid.split(":")[0].split("@")[0] if jid else ""
    return {"ok": True, "connected": connected, "number": number, "profile_name": profile_name}


# ── Desconectar instância (sem deletar) ───────────────────────────────────────

@router.post("/instance/{instance_id}/disconnect")
def disconnect_whatsapp(instance_id: str, request: Request, _: str = Security(require_api_key)):
    ai_config = request.app.state.ai_config
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        if inst.get("status") == "running":
            raise HTTPException(400, "Pare a campanha antes de desconectar.")
        api_url  = inst.get("api_url", "")
        api_key  = inst.get("api_key", "")

    global_api_key = ai_config.get("evo_global_api_key", "")
    if not api_url or not api_key:
        raise HTTPException(400, "Instância sem credenciais Evolution GO configuradas.")

    ok, err = disconnect_instance(api_url, api_key, instance_id)
    if not ok:
        raise HTTPException(502, f"Evolution GO recusou a desconexão: {err}")
    return {"ok": True}


# ── Reconectar instância (sessão existente, sem QR Code) ──────────────────────

@router.post("/instance/{instance_id}/reconnect")
def reconnect_whatsapp(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    Reconecta uma sessão de WhatsApp já pareada, sem precisar de QR Code novo.
    Só funciona se a sessão ainda existir no Evolution GO (não foi deslogada de
    verdade pelo celular) — nesse caso, o usuário precisa gerar um QR Code novo.
    """
    auth_svc.require_instance_access(instance_id, request)
    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        api_url = inst.get("api_url", "")
        api_key = inst.get("api_key", "")

    if not api_url or not api_key:
        raise HTTPException(400, "Instância sem credenciais Evolution GO configuradas.")

    ok, err = reconnect_instance(api_url, api_key, instance_id)
    if not ok:
        raise HTTPException(
            502,
            f"Não foi possível reconectar automaticamente ({err}). "
            "Se o WhatsApp foi desconectado pelo celular, gere um novo QR Code.",
        )
    invalidate_instance_cache(api_url)
    return {"ok": True}


# ── Eventos do Webhook ────────────────────────────────────────────────────────

VALID_WEBHOOK_EVENTS = {
    "ALL", "MESSAGE", "READ_RECEIPT", "PRESENCE", "HISTORY_SYNC",
    "CHAT_PRESENCE", "CALL", "CONNECTION", "QRCODE", "LABEL",
    "CONTACT", "GROUP", "NEWSLETTER",
}

class WebhookEventsPayload(BaseModel):
    events: list[str]


@router.put("/instance/{instance_id}/webhook-events")
def update_whatsapp_webhook_events(instance_id: str, payload: WebhookEventsPayload, request: Request, _: str = Security(require_api_key)):
    _build_webhook_url = request.app.state.build_webhook_url
    ai_config          = request.app.state.ai_config
    auth_svc.require_instance_access(instance_id, request)

    invalid = [e for e in payload.events if e not in VALID_WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(400, f"Eventos inválidos: {invalid}")

    with instances_lock:
        inst = instances.get(instance_id)
        if not inst:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        api_url  = inst.get("api_url", "")
        api_key  = inst.get("api_key", "")
        evo_name = inst.get("evo_instance_name", "").strip() or instance_id

    global_api_key = ai_config.get("evo_global_api_key", "")
    if not api_url or not api_key:
        raise HTTPException(400, "Instância sem credenciais Evolution GO configuradas.")

    webhook_url = _build_webhook_url(instance_id)
    ok, err = update_webhook_events(api_url, api_key, evo_name, webhook_url, payload.events)
    if not ok:
        raise HTTPException(502, f"Evolution GO recusou a atualização: {err}")
    return {"ok": True, "webhook_url": webhook_url, "events": payload.events}
