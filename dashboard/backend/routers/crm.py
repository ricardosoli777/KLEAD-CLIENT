"""
Router CRM — /crm/*, /config/*, /conversations/{id}/{phone}/send-to-crm
Extraído de main.py em 2026-06-12.

Globals de main.py acessados via request.app.state.*:
  instances, conversations, instances_lock, conversations_lock
  now_iso, conversation_phone_key, resolve_conversation_key,
  get_or_create_conversation, mark_needs_human, clear_needs_human,
  save_conversations, sse_emit, maybe_queue_meta_capi_event,
  meta_stage_event_for_lead, meta_stage_event_for_column
"""

import base64
import copy
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, Security, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import services.auth_service as auth_svc
import services.crm_service as crm_svc
import services.audit_log_service as audit_log_svc
import services.media_service as media_svc
from services import meta_pg_service
from services import confirmation_pg_service
from services.confirmation_service import emphasize_whatsapp_details, normalize_options, render_message, render_template_text, validate_message_text
from services.meta_capi_service import (
    get_meta_capi_audit,
    preview_meta_capi_event,
    send_meta_capi_event,
    capi_already_sent_recently,
    is_meta_capi_enabled,
)
from services.outbound_routing import send_outbound_poll, send_outbound_text
from services.assignment_service import apply_assignment_rule_to_lead, tenant_key_for_user
from utils.cleaner import normalize_phone
from core.security import require_api_key

router = APIRouter(tags=["crm"])
logger = logging.getLogger("klead.crm")

_VALID_CAPI_EVENTS = {"Lead", "Contact", "QualifiedLead", "SubmitApplication", "Schedule", "Purchase", "none", ""}


# ── Modelos ───────────────────────────────────────────────────────────────────

class CrmColumnPayload(BaseModel):
    name: str
    color: str = "#8696A0"
    order: int | None = None
    capi_event: str | None = None
    instancia_id: str = ""

class CrmLeadPayload(BaseModel):
    nome: str
    telefone: str
    origem: str = "manual"
    instancia_id: str = ""
    coluna_id: str | None = None
    score: int | None = None
    score_reason: str | None = None
    lead_status: str | None = None
    email: str = ""
    empresa: str = ""
    campanha: str = ""
    cidade: str = ""
    estado: str = ""
    cep: str = ""
    pais: str = ""
    valor_estimado: str = ""
    followup_date: str = ""
    segmento: str = ""
    poder_aquisitivo: str = ""
    tags: list[str] = Field(default_factory=list)
    observacao: str = ""

class CrmMovePayload(BaseModel):
    coluna_id: str | None = None

class CrmNotePayload(BaseModel):
    text: str

class CrmTagsPayload(BaseModel):
    tags: list[str]

class CrmMessagePayload(BaseModel):
    message: str

class ConfirmationOptionPayload(BaseModel):
    id: str
    label: str
    emoji: str = ""
    synonyms: list[str] = Field(default_factory=list)
    auto_reply: str = ""

class ConfirmationTemplateCreatePayload(BaseModel):
    name: str
    message_text: str
    options: list[ConfirmationOptionPayload]

class ConfirmationTemplateUpdatePayload(BaseModel):
    name: str | None = None
    message_text: str | None = None
    options: list[ConfirmationOptionPayload] | None = None
    active: bool | None = None

class ConfirmationSendPayload(BaseModel):
    template_id: str | None = None
    message_text: str | None = None
    options: list[ConfirmationOptionPayload] | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    include_public_link: bool = False
    delivery_format: str = "text"


def _send_confirmation_outbound(inst: dict, instance_id: str, conv_key: str,
                                rendered: str, options: list[dict],
                                delivery_format: str) -> tuple[bool, str, str, str]:
    """Envia confirmação e sempre devolve provider_message_id, inclusive em texto."""
    if delivery_format == "poll":
        intro_ok, intro_err, intro_channel = send_outbound_text(
            inst, instance_id, conv_key, rendered
        )
        if not intro_ok:
            return False, intro_err, intro_channel, ""
        return send_outbound_poll(
            inst, instance_id, conv_key, "Escolha somente uma opção:",
            [option["label"] for option in options],
        )
    ok, err, channel = send_outbound_text(inst, instance_id, conv_key, rendered)
    return ok, err, channel, ""

class PublicConfirmationResponsePayload(BaseModel):
    option_id: str

class PublicConfirmationSchedulePayload(BaseModel):
    scheduled_for: datetime

class CrmFieldsPayload(BaseModel):
    nome:             str | None = None
    telefone:         str | None = None
    email:            str | None = None
    empresa:          str | None = None
    cidade:           str | None = None
    estado:           str | None = None
    cep:              str | None = None
    pais:             str | None = None
    valor_estimado:   str | None = None
    followup_date:    str | None = None
    segmento:         str | None = None
    poder_aquisitivo: str | None = None
    score:            int | None = None
    score_reason:     str | None = None
    lead_status:      str | None = None
    instancia_id:     str | None = None
    leadgen_id:       str | None = None

class MetaCapiSendPayload(BaseModel):
    event_name: str = "Lead"
    origem: str | None = None

class CrmContactLogPayload(BaseModel):
    tipo:            str = "ligação"
    nota:            str
    proximo_contato: str | None = None

class CrmListaPayload(BaseModel):
    name: str
    filtros: dict = {}

class CrmListaFollowupPayload(BaseModel):
    followup_date: str = ""

class ConfigPayload(BaseModel):
    value: str
    description: str = ""


# ── Helpers locais ────────────────────────────────────────────────────────────

def _get_allowed_instances_for_request(request: Request) -> list | None:
    """
    Retorna lista de instancias permitidas, ou None se superadmin (acesso total
    e intencional). Sem sessao valida (sem token ou token invalido) falha
    fechado — retorna lista vazia, nunca None, para nao vazar dados de todos
    os tenants pra uma requisicao nao autenticada.
    """
    token = request.cookies.get("klead_token") or request.headers.get("X-Auth-Token", "")
    if not token:
        return []
    user = auth_svc.verify_token(token)
    if not user:
        return []
    if user.get("funcao") == "superadmin":
        return None
    return user.get("allowed_instances") or []


def _col_has_explicit_none(lead: dict) -> bool:
    """Retorna True quando a coluna do lead tem capi_event='none' configurado explicitamente."""
    col_id = (lead.get("coluna_id") or "").strip()
    if not col_id:
        return False
    try:
        col = next((c for c in crm_svc.get_columns() if c["id"] == col_id), None)
        return col is not None and (col.get("capi_event") or "").strip() == "none"
    except Exception:
        return False


def _filter_leads_by_user(leads: list, allowed: list | None) -> list:
    if allowed is None:
        return leads
    return [l for l in leads if l.get("instancia_id") in allowed]


def _lead_or_404_with_access(lead_id: str, request: Request) -> dict:
    """
    Busca o lead pelo id, 404 se nao existir, e exige que o usuario
    autenticado tenha acesso a instancia dona do lead (auth_svc.require_instance_access).
    Ponto unico usado por todo endpoint /crm/leads/{lead_id}/... — evita repetir
    (e esquecer) a checagem em cada rota.
    """
    lead = crm_svc.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead não encontrado.")
    instancia_id = lead.get("instancia_id", "")
    if instancia_id:
        auth_svc.require_instance_access(instancia_id, request)
    return lead


def _confirmation_tenant(request: Request) -> tuple[str, dict]:
    user, _ = auth_svc.get_auth_user(request)
    return tenant_key_for_user(user), user


def _confirmation_error(exc: Exception):
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc))
    raise HTTPException(503, "Persistência de confirmações indisponível.")


def _pg_connect():
    from services import pg_pool
    host = os.getenv("EVO_PG_HOST", "postgres")
    user = os.getenv("EVO_PG_USER", "postgres")
    pwd  = os.getenv("EVO_PG_PASSWORD", "")
    # klead_config vive no banco do app ("klead"), que está no backup diário —
    # o banco "postgres" não é coberto pelo klead-pg-backup.sh.
    dbname = os.getenv("INST_PG_DB") or "klead"
    # pg_pool.get_connection retorna um proxy compativel com psycopg2.connect()
    # (mesmo .cursor()/.commit()/.close()) — .close() devolve ao pool em vez de
    # abrir/fechar uma conexao TCP nova a cada chamada destes endpoints de config.
    return pg_pool.get_connection({"host": host, "port": 5432, "user": user, "password": pwd, "dbname": dbname})


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/crm/confirmation-templates", dependencies=[Security(require_api_key)])
def crm_confirmation_templates(request: Request, include_inactive: bool = False):
    tenant, _ = _confirmation_tenant(request)
    try:
        return confirmation_pg_service.list_templates(tenant, include_inactive)
    except Exception as exc:
        logger.exception("Falha ao listar modelos de confirmação")
        _confirmation_error(exc)


@router.post("/crm/confirmation-templates", dependencies=[Security(require_api_key)])
def crm_create_confirmation_template(payload: ConfirmationTemplateCreatePayload, request: Request):
    tenant, user = _confirmation_tenant(request)
    try:
        return confirmation_pg_service.create_template(
            tenant,
            payload.name,
            payload.message_text,
            [item.model_dump() for item in payload.options],
            user.get("email", ""),
        )
    except Exception as exc:
        logger.exception("Falha ao criar modelo de confirmação")
        _confirmation_error(exc)


@router.patch("/crm/confirmation-templates/{template_id}", dependencies=[Security(require_api_key)])
def crm_update_confirmation_template(template_id: str, payload: ConfirmationTemplateUpdatePayload, request: Request):
    tenant, _ = _confirmation_tenant(request)
    fields = payload.model_dump(exclude_none=True)
    if "options" in fields:
        fields["options"] = [item.model_dump() if hasattr(item, "model_dump") else item for item in payload.options or []]
    try:
        result = confirmation_pg_service.update_template(template_id, tenant, fields)
        if not result:
            raise HTTPException(404, "Modelo não encontrado.")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Falha ao atualizar modelo de confirmação")
        _confirmation_error(exc)


@router.delete("/crm/confirmation-templates/{template_id}", dependencies=[Security(require_api_key)])
def crm_delete_confirmation_template(template_id: str, request: Request):
    tenant, _ = _confirmation_tenant(request)
    try:
        if not confirmation_pg_service.deactivate_template(template_id, tenant):
            raise HTTPException(404, "Modelo não encontrado.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Falha ao desativar modelo de confirmação")
        _confirmation_error(exc)


@router.get("/crm/leads/{lead_id}/confirmations", dependencies=[Security(require_api_key)])
def crm_list_lead_confirmations(lead_id: str, request: Request, limit: int = 50):
    _lead_or_404_with_access(lead_id, request)
    tenant, _ = _confirmation_tenant(request)
    try:
        return confirmation_pg_service.list_confirmations(lead_id, tenant, limit)
    except Exception as exc:
        logger.exception("Falha ao listar confirmações do lead")
        _confirmation_error(exc)


@router.post("/crm/leads/{lead_id}/confirmations", dependencies=[Security(require_api_key)])
def crm_send_confirmation(lead_id: str, payload: ConfirmationSendPayload, request: Request):
    delivery_format = payload.delivery_format.strip().lower()
    if delivery_format not in {"text", "poll"}:
        raise HTTPException(400, "Formato deve ser text ou poll.")
    lead = _lead_or_404_with_access(lead_id, request)
    tenant, user = _confirmation_tenant(request)
    template = None
    if payload.template_id:
        template = confirmation_pg_service.get_template(payload.template_id, tenant)
        if not template or not template.get("active"):
            raise HTTPException(404, "Modelo de confirmação não encontrado.")
    raw_text = payload.message_text if payload.message_text is not None else (template or {}).get("message_text")
    raw_options = (
        [item.model_dump() for item in payload.options]
        if payload.options is not None else (template or {}).get("options_json")
    )
    if not raw_text or not raw_options:
        raise HTTPException(400, "Informe um modelo ou texto e opções válidos.")
    try:
        message_text = validate_message_text(raw_text)
        options = normalize_options(raw_options)
        variables = {
            "nome": lead.get("nome", ""),
            "data": lead.get("followup_date", ""),
            "hora": "",
            "servico": lead.get("segmento", ""),
            "atendente": lead.get("assigned_to_name", ""),
        }
        variables.update({key: str(value) for key, value in payload.variables.items()})
        public_token = None
        base_text = (
            render_template_text(message_text, variables)
            if delivery_format == "poll"
            else render_message(message_text, options, variables)
        )
        rendered = emphasize_whatsapp_details(base_text, lead.get("nome", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    instances = request.app.state.instances
    with request.app.state.instances_lock:
        instance_id = lead.get("instancia_id", "")
        if not instance_id or instance_id not in instances:
            raise HTTPException(400, "Instância do lead não encontrada.")
        inst = dict(instances[instance_id])
    phone = request.app.state.conversation_phone_key(lead.get("telefone", ""))
    bsuid = request.app.state.conversation_phone_key(lead.get("bsuid", ""))
    conv_key = request.app.state.resolve_conversation_key(instance_id, bsuid or phone)
    if not conv_key:
        raise HTTPException(400, "Lead sem identificador de conversa.")
    provider = "meta" if inst.get("provider") == "meta" else "evolution"
    try:
        confirmation = confirmation_pg_service.create_confirmation(
            owner_key=tenant, lead_id=lead_id, instancia_id=instance_id,
            conversation_key=conv_key, provider=provider, message_text=message_text,
            rendered_text=rendered, options=options, created_by=user.get("email", ""),
            public_link_enabled=False, public_token=public_token, delivery_format=delivery_format,
        )
    except Exception as exc:
        logger.exception("Falha ao persistir confirmação antes do envio")
        _confirmation_error(exc)

    ok, err, channel, provider_message_id = _send_confirmation_outbound(
        inst, instance_id, conv_key, rendered, options, delivery_format
    )
    if not ok:
        confirmation_pg_service.mark_confirmation_failed(confirmation["id"], tenant, channel, err)
        raise HTTPException(502, f"Falha ao enviar via {channel}: {err}")
    confirmation = confirmation_pg_service.mark_confirmation_sent(confirmation["id"], tenant, provider_message_id)
    confirmation_id = confirmation["id"]
    kind = "Enquete" if delivery_format == "poll" else "Mensagem de confirmação"
    crm_svc.add_note(lead_id, f"[{kind} enviada — sem resposta automática]: {rendered}")
    confirmation = confirmation_pg_service.get_confirmation(confirmation_id, tenant)
    if not confirmation:
        logger.critical("Confirmação %s desapareceu após sincronização do CRM", confirmation_id)
        raise HTTPException(503, "Mensagem enviada, mas o registro de confirmação não foi preservado.")

    with request.app.state.conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead.get("nome", conv_key))
        previous_ai_paused = bool(conv.get("ai_paused"))
        conv["unread"] = False
        conv["messages"].append({
            "role": "human", "content": rendered,
            "timestamp": request.app.state.now_iso(),
            "confirmation_id": confirmation["id"],
        })
        conv["last_activity"] = request.app.state.now_iso()
        conv["ai_paused"] = previous_ai_paused
    request.app.state.save_conversation(instance_id, conv_key)
    return {"ok": True, "confirmation": confirmation, "channel": channel}


@router.get("/public/confirmations/{public_token}")
def public_confirmation(public_token: str):
    try:
        result = confirmation_pg_service.get_public_confirmation(public_token)
    except Exception:
        logger.exception("Falha ao abrir confirmação pública")
        raise HTTPException(503, "Confirmação temporariamente indisponível.")
    if not result or result.get("expired"):
        raise HTTPException(404, "Confirmação inválida ou expirada.")
    return {
        "status": result["status"],
        "options": result["options_snapshot_json"],
        "response_option_id": result.get("response_option_id", ""),
        "scheduled_for": result.get("scheduled_for"),
    }


@router.post("/public/confirmations/{public_token}/respond")
def public_confirmation_respond(public_token: str, payload: PublicConfirmationResponsePayload, request: Request):
    try:
        result = confirmation_pg_service.resolve_public_reply(public_token, payload.option_id)
    except ValueError:
        raise HTTPException(409, "Uma resposta diferente já foi registrada.")
    except Exception:
        logger.exception("Falha ao responder confirmação pública")
        raise HTTPException(503, "Não foi possível registrar a resposta.")
    if not result:
        raise HTTPException(404, "Confirmação inválida, expirada ou indisponível.")
    if result.get("_response_created"):
        try:
            crm_svc.add_note(str(result["lead_id"]), f"[Confirmação respondida pelo link]: {result['response_option_id']}")
        except Exception:
            logger.exception("Falha ao registrar resposta pública no CRM")
    option = next((item for item in result.get("options_snapshot_json", []) if item.get("id") == result["response_option_id"]), {})
    if result.get("_response_created") and result["response_option_id"] != "reschedule":
        auto_reply = str(option.get("auto_reply") or "").strip()
        if auto_reply:
            with request.app.state.instances_lock:
                inst = dict(request.app.state.instances.get(result["instancia_id"], {}))
            sent, detail, _ = send_outbound_text(inst, result["instancia_id"], result["conversation_key"], auto_reply)
            confirmation_pg_service.mark_auto_reply_result(result["id"], sent, detail)
    return {"ok": True, "option_id": result["response_option_id"], "requires_schedule": result["response_option_id"] == "reschedule"}


@router.post("/public/confirmations/{public_token}/schedule")
def public_confirmation_schedule(public_token: str, payload: PublicConfirmationSchedulePayload, request: Request):
    try:
        result = confirmation_pg_service.schedule_public_confirmation(public_token, payload.scheduled_for)
    except ValueError as exc:
        if str(exc) == "slot_unavailable":
            raise HTTPException(409, "Esse horário acabou de ser ocupado. Escolha outro.")
        raise HTTPException(400, str(exc))
    if not result:
        raise HTTPException(404, "Reagendamento inválido, expirado ou indisponível.")
    local_value = result["scheduled_for"].astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M")
    reply = f"✅ Visita reagendada com sucesso para {local_value}."
    if result.get("_schedule_created"):
        crm_svc.update_fields(str(result["lead_id"]), followup_date=result["scheduled_for"].isoformat())
        crm_svc.add_note(str(result["lead_id"]), f"[Visita reagendada pelo link]: {local_value}")
        with request.app.state.instances_lock:
            inst = dict(request.app.state.instances.get(result["instancia_id"], {}))
        sent, detail, _ = send_outbound_text(inst, result["instancia_id"], result["conversation_key"], reply)
        confirmation_pg_service.mark_auto_reply_result(result["id"], sent, detail)
    return {"ok": True, "scheduled_for": result["scheduled_for"], "message": reply}


@router.get("/crm/stats", dependencies=[Security(require_api_key)])
def crm_stats(request: Request, instancia_id: str = ""):
    """Estatísticas agregadas do CRM."""
    allowed = _get_allowed_instances_for_request(request)
    cols   = crm_svc.get_columns(instancia_id) if instancia_id else crm_svc.get_columns()
    leads  = _filter_leads_by_user(crm_svc.get_leads(), allowed)
    if instancia_id:
        leads = [l for l in leads if l.get("instancia_id") == instancia_id]
    total  = len(leads)

    by_col = {c["id"]: {"name": c["name"], "color": c["color"], "count": 0} for c in cols}
    by_origem: dict = {}
    scored = [l for l in leads if l.get("score") is not None]
    avg_score = round(sum(l["score"] for l in scored) / len(scored)) if scored else None

    for lead in leads:
        cid = lead.get("coluna_id", "")
        if cid in by_col:
            by_col[cid]["count"] += 1
        orig = lead.get("origem", "manual")
        by_origem[orig] = by_origem.get(orig, 0) + 1

    last_col = cols[-1]["id"] if cols else None
    fechados = by_col.get(last_col, {}).get("count", 0) if last_col else 0
    conversion = round(fechados / total * 100) if total > 0 else 0

    return {
        "total": total,
        "avg_score": avg_score,
        "conversion_pct": conversion,
        "by_col": list(by_col.values()),
        "by_origem": by_origem,
    }


@router.get("/crm/columns", dependencies=[Security(require_api_key)])
def crm_list_columns(request: Request, instancia_id: str = ""):
    """
    Sem `instancia_id`: retorna colunas de todas as instâncias que o usuário
    tem acesso (visão agregada, ex: tela /crm sem filtro — superadmin vê tudo).
    Com `instancia_id`: só as colunas daquela instância, e exige posse dela —
    é o que a tela de edição/criação de coluna deve sempre usar.
    """
    if instancia_id:
        auth_svc.require_instance_access(instancia_id, request)
        return crm_svc.get_columns(instancia_id)
    allowed = _get_allowed_instances_for_request(request)
    cols = crm_svc.get_columns()
    if allowed is None:
        return cols
    return [c for c in cols if (c.get("instancia_id") or "") in allowed]


@router.post("/crm/columns", dependencies=[Security(require_api_key)])
def crm_create_column(payload: CrmColumnPayload, request: Request):
    if not payload.instancia_id.strip():
        raise HTTPException(400, "instancia_id é obrigatório para criar uma coluna.")
    auth_svc.require_instance_access(payload.instancia_id, request)
    capi = payload.capi_event if payload.capi_event in _VALID_CAPI_EVENTS else None
    return crm_svc.create_column(payload.instancia_id, payload.name, payload.color, capi_event=capi)


@router.patch("/crm/columns/{col_id}", dependencies=[Security(require_api_key)])
def crm_update_column(col_id: str, payload: CrmColumnPayload, request: Request):
    if not payload.instancia_id.strip():
        raise HTTPException(400, "instancia_id é obrigatório para editar uma coluna.")
    auth_svc.require_instance_access(payload.instancia_id, request)
    capi = payload.capi_event if payload.capi_event in _VALID_CAPI_EVENTS else None
    col = crm_svc.update_column(payload.instancia_id, col_id, payload.name, payload.color, payload.order, capi_event=capi)
    if not col:
        raise HTTPException(404, "Coluna não encontrada ou não pertence a essa instância.")
    return col


@router.delete("/crm/columns/{col_id}", dependencies=[Security(require_api_key)])
def crm_delete_column(col_id: str, request: Request, instancia_id: str = ""):
    if not instancia_id.strip():
        raise HTTPException(400, "instancia_id é obrigatório para apagar uma coluna.")
    auth_svc.require_instance_access(instancia_id, request)
    if not crm_svc.delete_column(instancia_id, col_id):
        raise HTTPException(404, "Coluna não encontrada ou não pertence a essa instância.")
    return {"ok": True}


@router.post("/crm/backfill-leadgen", dependencies=[Security(require_api_key)])
def crm_backfill_leadgen(request: Request):
    """
    Percorre leads sem leadgen_id e tenta preencher a partir de:
    1. source_meta já armazenado no lead (leadgen_id aninhado)
    2. meta_form_submissions via PostgreSQL, cruzado por telefone
    Executado manualmente. Não sobrescreve leadgen_id já existente.
    Opera sobre TODOS os leads do sistema — restrito ao superadmin.
    """
    auth_svc.require_superadmin(request)
    leads = crm_svc.get_leads()

    phone_to_leadgen: dict[str, str] = {}
    if meta_pg_service.pg_configured():
        try:
            for phone, lgid in meta_pg_service.get_form_submissions_with_phone():
                if phone and lgid and phone not in phone_to_leadgen:
                    phone_to_leadgen[phone] = lgid
        except Exception as exc:
            logger.info(f"[BACKFILL] Erro ao consultar meta_form_submissions: {exc}")

    updated, skipped = 0, 0
    for lead in leads:
        if lead.get("leadgen_id"):
            skipped += 1
            continue
        source_meta = lead.get("source_meta") or {}
        leadgen_id = (
            str(source_meta.get("leadgen_id") or "").strip()
            or str(source_meta.get("lead_id") or "").strip()
            or str((source_meta.get("lead_fields") or {}).get("leadgen_id") or "").strip()
        )
        if not leadgen_id and phone_to_leadgen:
            leadgen_id = phone_to_leadgen.get(lead.get("telefone", ""), "")
        if not leadgen_id:
            continue
        merged_meta = dict(source_meta)
        merged_meta["leadgen_id"] = leadgen_id
        crm_svc.upsert_lead(
            telefone=lead.get("telefone", ""),
            nome=lead.get("nome", ""),
            origem=lead.get("origem", ""),
            instancia_id=lead.get("instancia_id", ""),
            source_meta=merged_meta,
        )
        logger.info(f"[BACKFILL] leadgen_id={leadgen_id} → lead {lead.get('id')}")
        updated += 1

    return {"ok": True, "updated": updated, "skipped_already_have": skipped, "total": len(leads)}


@router.get("/crm/leads", dependencies=[Security(require_api_key)])
def crm_list_leads(request: Request, coluna_id: str | None = None, origem: str | None = None, search: str | None = None, instancia_id: str = ""):
    allowed = _get_allowed_instances_for_request(request)
    leads = crm_svc.get_leads(coluna_id, origem, search)
    leads = _filter_leads_by_user(leads, allowed)
    if instancia_id:
        leads = [l for l in leads if l.get("instancia_id") == instancia_id]
        user = auth_svc.try_get_auth_user(request)
        if user and user.get("funcao") == "superadmin":
            audit_log_svc.log_admin_access(user.get("email", ""), "superadmin", instancia_id, "view_crm_leads")
    return leads


@router.post("/crm/leads", dependencies=[Security(require_api_key)])
def crm_create_lead(payload: CrmLeadPayload, request: Request):
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    if payload.instancia_id:
        with instances_lock:
            valid_ids = set(instances.keys())
        if payload.instancia_id not in valid_ids:
            raise HTTPException(422, f"instancia_id '{payload.instancia_id}' não encontrada.")
        auth_svc.require_instance_access(payload.instancia_id, request)
    lead = crm_svc.upsert_lead(
        # Normaliza celular BR quando possível (evita card com número sem o
        # nono dígito, impossível de contatar); fora do padrão BR mantém como veio.
        telefone=normalize_phone(payload.telefone) or payload.telefone,
        nome=payload.nome,
        origem=payload.origem,
        instancia_id=payload.instancia_id,
        score=payload.score,
        score_reason=payload.score_reason,
        coluna_id=payload.coluna_id,
        email=payload.email,
        empresa=payload.empresa,
        campanha=payload.campanha,
        cidade=payload.cidade,
        estado=payload.estado,
        cep=payload.cep,
        pais=payload.pais,
        assignment_status="unassigned",
    )
    lead = crm_svc.update_fields(
        lead["id"],
        email=payload.email or None,
        empresa=payload.empresa or None,
        cidade=payload.cidade or None,
        estado=payload.estado or None,
        cep=payload.cep or None,
        pais=payload.pais or None,
        valor_estimado=payload.valor_estimado or None,
        followup_date=payload.followup_date or None,
        segmento=payload.segmento or None,
        poder_aquisitivo=payload.poder_aquisitivo or None,
        score=payload.score,
        score_reason=payload.score_reason,
        lead_status=payload.lead_status,
    ) or lead
    if payload.tags:
        lead = crm_svc.set_tags(lead["id"], payload.tags) or lead
    if payload.observacao.strip():
        lead = crm_svc.add_note(lead["id"], payload.observacao.strip()) or lead
    with instances_lock:
        inst = dict(instances.get(payload.instancia_id, {}))
    if is_meta_capi_enabled(inst, lead):
        event_name = request.app.state.meta_stage_event_for_lead(lead)
        if event_name is None and not _col_has_explicit_none(lead):
            normalized_status = (lead.get("lead_status") or "").strip().lower().replace(" ", "_")
            event_name = None if normalized_status == "desqualificado" else "Lead"
        if event_name:
            request.app.state.maybe_queue_meta_capi_event(inst, lead, payload.origem or "crm", event_name=event_name)
    return apply_assignment_rule_to_lead(lead, "crm_create")


@router.post("/crm/leads/{lead_id}/seed-conversation", dependencies=[Security(require_api_key)])
def crm_seed_conversation(lead_id: str, request: Request):
    instances = request.app.state.instances
    conversations_lock = request.app.state.conversations_lock
    lead = _lead_or_404_with_access(lead_id, request)
    instance_id = lead.get("instancia_id", "")
    phone_raw   = lead.get("telefone", "")
    if not instance_id or instance_id not in instances:
        raise HTTPException(400, "Lead sem instância vinculada válida.")
    phone = request.app.state.conversation_phone_key(phone_raw)
    if not phone:
        raise HTTPException(400, "Lead sem telefone.")
    timestamp = request.app.state.now_iso()
    already_existed = False
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, phone, lead.get("nome", phone))
        already_existed = bool(conv.get("messages"))
        if not already_existed:
            conv["ai_paused"]  = True
            conv["unread"]     = True
            request.app.state.mark_needs_human(conv, "Lead vinculado manualmente pelo CRM", "crm_manual")
            conv["messages"].append({
                "role":      "system",
                "content":   f"Conversa iniciada pelo CRM — lead: {lead.get('nome', phone)}",
                "timestamp": timestamp,
            })
            conv["last_activity"] = timestamp
    request.app.state.save_conversation(instance_id, phone)
    request.app.state.sse_emit(instance_id, "new_message", {"phone": phone, "role": "system", "content": "Conversa iniciada pelo CRM"})
    return {"ok": True, "instance_id": instance_id, "phone": phone, "already_existed": already_existed}


@router.get("/crm/leads/{lead_id}", dependencies=[Security(require_api_key)])
def crm_get_lead(lead_id: str, request: Request):
    return _lead_or_404_with_access(lead_id, request)


@router.get("/crm/meta-capi/audit", dependencies=[Security(require_api_key)])
def crm_meta_capi_audit_instance(request: Request, instance_id: str = "", limit: int = 30):
    """Retorna os últimos eventos CAPI enviados para uma instância."""
    # instance_id obrigatório: sem ele a query voltaria eventos (com PII de
    # leads) de TODAS as instâncias — vazamento cross-tenant.
    if not instance_id:
        raise HTTPException(400, "instance_id é obrigatório.")
    auth_svc.require_instance_access(instance_id, request)
    limit = max(1, min(limit, 200))
    items = get_meta_capi_audit(instance_id=instance_id, limit=limit)
    return {"instance_id": instance_id, "total": len(items), "items": items}


@router.get("/crm/leads/{lead_id}/meta-capi/preview", dependencies=[Security(require_api_key)])
def crm_preview_meta_capi(lead_id: str, request: Request):
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    lead = _lead_or_404_with_access(lead_id, request)
    with instances_lock:
        inst = dict(instances.get(lead.get("instancia_id", ""), {}))
    origem = lead.get("origem", "meta")
    ok, payload, detail = preview_meta_capi_event(inst, lead, origem=origem, event_name="Lead")
    return {
        "ok": ok,
        "detail": detail,
        "origem": origem,
        "event_name": "Lead",
        "payload": payload or {},
    }


@router.get("/crm/leads/{lead_id}/meta-capi/audit", dependencies=[Security(require_api_key)])
def crm_meta_capi_audit(lead_id: str, request: Request, limit: int = 10):
    lead = _lead_or_404_with_access(lead_id, request)
    limit = max(1, min(limit, 100))
    return {
        "lead_id": lead_id,
        "items": get_meta_capi_audit(
            lead_id=lead_id,
            instance_id=lead.get("instancia_id", ""),
            limit=limit,
        ),
    }


@router.post("/crm/leads/{lead_id}/meta-capi/send", dependencies=[Security(require_api_key)])
def crm_send_meta_capi(lead_id: str, payload: MetaCapiSendPayload, request: Request):
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    lead = _lead_or_404_with_access(lead_id, request)
    with instances_lock:
        inst = dict(instances.get(lead.get("instancia_id", ""), {}))
    origem = payload.origem or lead.get("origem", "meta")
    preview_ok, preview_payload, preview_detail = preview_meta_capi_event(
        inst,
        lead,
        origem=origem,
        event_name=payload.event_name,
    )
    send_ok, send_detail = send_meta_capi_event(
        inst,
        lead,
        origem=origem,
        event_name=payload.event_name,
    )
    return {
        "ok": send_ok,
        "detail": send_detail,
        "preview_ok": preview_ok,
        "preview_detail": preview_detail,
        "payload": preview_payload or {},
        "audit": get_meta_capi_audit(
            lead_id=lead_id,
            instance_id=lead.get("instancia_id", ""),
            limit=1,
        ),
    }


@router.patch("/crm/leads/{lead_id}/move", dependencies=[Security(require_api_key)])
def crm_move_lead(lead_id: str, payload: CrmMovePayload, request: Request):
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    _lead_or_404_with_access(lead_id, request)
    try:
        lead = crm_svc.move_lead(lead_id, payload.coluna_id)
    except ValueError:
        raise HTTPException(400, "Coluna não pertence à instância deste lead.")
    if not lead:
        raise HTTPException(404, "Lead não encontrado.")
    with instances_lock:
        inst = dict(instances.get(lead.get("instancia_id", ""), {}))
    if is_meta_capi_enabled(inst, lead):
        event_name = request.app.state.meta_stage_event_for_column(lead)
        if event_name:
            lead_id = lead.get("id", "")
            if capi_already_sent_recently(lead_id, event_name):
                logger.info(f"[META-CAPI] Dedup: '{event_name}' já enviado para lead={lead_id} nas últimas 24h — ignorado")
            else:
                request.app.state.maybe_queue_meta_capi_event(inst, lead, lead.get("origem") or "crm", event_name=event_name)
    return apply_assignment_rule_to_lead(lead, "sdr_inbox")


@router.post("/crm/leads/{lead_id}/note", dependencies=[Security(require_api_key)])
def crm_add_note(lead_id: str, payload: CrmNotePayload, request: Request):
    _lead_or_404_with_access(lead_id, request)
    lead = crm_svc.add_note(lead_id, payload.text)
    if not lead:
        raise HTTPException(404, "Lead não encontrado.")
    return lead


@router.patch("/crm/leads/{lead_id}/tags", dependencies=[Security(require_api_key)])
def crm_set_tags(lead_id: str, payload: CrmTagsPayload, request: Request):
    _lead_or_404_with_access(lead_id, request)
    lead = crm_svc.set_tags(lead_id, payload.tags)
    if not lead:
        raise HTTPException(404, "Lead não encontrado.")
    return lead


@router.delete("/crm/leads/{lead_id}", dependencies=[Security(require_api_key)])
def crm_delete_lead(lead_id: str, request: Request):
    lead = _lead_or_404_with_access(lead_id, request)
    if not crm_svc.delete_lead(lead_id):
        raise HTTPException(404, "Lead não encontrado.")
    # Snapshot completo do lead ANTES de sumir. A crm_lead_timeline cai por
    # ON DELETE CASCADE junto com o lead, então esta trilha é o único rastro
    # que sobra — e é dela que sai a restauração de um lead apagado sem querer.
    actor_email, actor_funcao, ip = audit_log_svc.actor_from_request(request)
    audit_log_svc.record_event(
        actor_email=actor_email,
        actor_funcao=actor_funcao,
        action="lead_deleted",
        target_type="lead",
        target_id=lead_id,
        instancia_id=lead.get("instancia_id", ""),
        detail=f"Lead excluído: {lead.get('nome') or 'sem nome'} ({lead.get('telefone') or 'sem telefone'})",
        meta=lead,
        ip=ip,
    )
    return {"ok": True}


@router.patch("/crm/leads/{lead_id}/fields", dependencies=[Security(require_api_key)])
def crm_update_fields(lead_id: str, payload: CrmFieldsPayload, request: Request):
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    before = _lead_or_404_with_access(lead_id, request)
    if payload.instancia_id and payload.instancia_id != before.get("instancia_id"):
        # Reatribuir o lead pra outra instancia exige posse dela tambem —
        # senao um admin poderia "roubar" um lead pra dentro do proprio tenant.
        auth_svc.require_instance_access(payload.instancia_id, request)
    lead = crm_svc.update_fields(
        lead_id,
        nome=payload.nome,
        telefone=(normalize_phone(payload.telefone) or payload.telefone) if payload.telefone else payload.telefone,
        email=payload.email,
        empresa=payload.empresa,
        cidade=payload.cidade,
        estado=payload.estado,
        cep=payload.cep,
        pais=payload.pais,
        valor_estimado=payload.valor_estimado,
        followup_date=payload.followup_date,
        segmento=payload.segmento,
        poder_aquisitivo=payload.poder_aquisitivo,
        score=payload.score,
        score_reason=payload.score_reason,
        lead_status=payload.lead_status,
        instancia_id=payload.instancia_id,
        leadgen_id=payload.leadgen_id,
    )
    if not lead:
        raise HTTPException(404, "Lead não encontrado.")
    with instances_lock:
        inst = dict(instances.get(lead.get("instancia_id", ""), {}))
    if is_meta_capi_enabled(inst, lead):
        event_name = None
        status_changed = payload.lead_status is not None and (before.get("lead_status") or "") != (lead.get("lead_status") or "")
        followup_changed = payload.followup_date is not None and (before.get("followup_date") or "") != (lead.get("followup_date") or "")
        match_fields_changed = any(
            getattr(payload, field) is not None and (before.get(field) or "") != (lead.get(field) or "")
            for field in ("email", "cidade", "estado", "cep", "pais")
        )
        if status_changed:
            event_name = request.app.state.meta_stage_event_for_lead(lead)
        elif followup_changed:
            event_name = "Contact"
        elif match_fields_changed:
            event_name = "Contact"
        if event_name:
            lead_id_capi = lead.get("id", "")
            if capi_already_sent_recently(lead_id_capi, event_name):
                logger.info(f"[META-CAPI] Dedup: '{event_name}' já enviado para lead={lead_id_capi} nas últimas 24h — ignorado")
            else:
                request.app.state.maybe_queue_meta_capi_event(inst, lead, lead.get("origem", "meta"), event_name=event_name)
    return lead


@router.post("/crm/leads/{lead_id}/contatos", dependencies=[Security(require_api_key)])
def crm_add_contact_log(lead_id: str, payload: CrmContactLogPayload, request: Request):
    """Registra um contato manual no acompanhamento do lead."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    _lead_or_404_with_access(lead_id, request)
    lead = crm_svc.add_contact_log(lead_id, payload.tipo, payload.nota, payload.proximo_contato)
    if not lead:
        raise HTTPException(404, "Lead não encontrado.")
    with instances_lock:
        inst = dict(instances.get(lead.get("instancia_id", ""), {}))
    if is_meta_capi_enabled(inst, lead):
        lead_id_capi = lead.get("id", "")
        if capi_already_sent_recently(lead_id_capi, "Contact"):
            logger.info(f"[META-CAPI] Dedup: 'Contact' já enviado para lead={lead_id_capi} nas últimas 24h — ignorado")
        else:
            request.app.state.maybe_queue_meta_capi_event(inst, lead, lead.get("origem") or "crm", event_name="Contact")
    return lead


@router.get("/crm/export", dependencies=[Security(require_api_key)])
def crm_export_csv(request: Request, coluna_id: str | None = None, origem: str | None = None, search: str | None = None):
    allowed = _get_allowed_instances_for_request(request)
    csv_text = crm_svc.export_csv(coluna_id, origem, search, allowed_instances=allowed)
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=crm_leads.csv"},
    )


@router.get("/crm/listas", dependencies=[Security(require_api_key)])
def crm_list_listas(request: Request):
    user, _ = auth_svc.get_auth_user(request)
    listas = crm_svc.get_lists()
    if user.get("funcao") == "superadmin":
        return listas
    # Cada tenant vê só as próprias listas; owner_key vazio = legado (visível
    # a todos até a migração de adoção — ver _lista_or_404_with_tenant).
    tenant = tenant_key_for_user(user)
    return [l for l in listas if l.get("owner_key", "") in ("", tenant)]


# ── Configuração via banco de dados ───────────────────────────────────────────

@router.get("/config", dependencies=[Security(require_api_key)])
def config_list(request: Request):
    """Lista todas as chaves da tabela klead_config (sem expor valores sensíveis completos)."""
    auth_svc.require_superadmin(request)
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute("SELECT key, value, description FROM klead_config ORDER BY key")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [{"key": r[0], "value": r[1][:4] + "***" if len(r[1]) > 8 else "***", "description": r[2]} for r in rows]
    except Exception as e:
        raise HTTPException(500, f"Erro ao conectar ao banco: {e}")


# Chaves que definem a identidade/segurança do processo — nunca sobrescrever
# em runtime via API, nem por superadmin (defense-in-depth: um token de
# superadmin vazado não pode trocar segredos-mestre nem redirecionar o banco).
_PROTECTED_CONFIG_PREFIXES = (
    "DASHBOARD_", "WEBHOOK_", "META_OAUTH_", "META_APP_",
    "EVO_PG_", "INST_PG_", "CRM_PG_", "CONV_PG_",
    "STRIPE_", "SENTRY_", "PATH", "LD_", "PYTHON",
)


@router.put("/config/{key}", dependencies=[Security(require_api_key)])
def config_set(key: str, payload: ConfigPayload, request: Request):
    """Insere ou atualiza uma chave em klead_config e aplica imediatamente no processo."""
    auth_svc.require_superadmin(request)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", key):
        raise HTTPException(400, "Chave inválida — use MAIUSCULAS_COM_UNDERSCORE (2-64 caracteres).")
    if any(key.startswith(p) for p in _PROTECTED_CONFIG_PREFIXES):
        raise HTTPException(403, f"Chave protegida — '{key}' só pode ser alterada no .env do servidor.")
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO klead_config (key, value, description) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, description = EXCLUDED.description",
            (key, payload.value, payload.description),
        )
        conn.commit()
        cur.close(); conn.close()
        os.environ[key] = payload.value
        return {"ok": True, "key": key}
    except Exception as e:
        raise HTTPException(500, f"Erro ao salvar: {e}")


@router.delete("/config/{key}", dependencies=[Security(require_api_key)])
def config_delete(key: str, request: Request):
    """Remove uma chave de klead_config."""
    auth_svc.require_superadmin(request)
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM klead_config WHERE key = %s", (key,))
        conn.commit()
        cur.close(); conn.close()
        return {"ok": True, "key": key}
    except Exception as e:
        raise HTTPException(500, f"Erro ao remover: {e}")


# ── CRM Listas ────────────────────────────────────────────────────────────────

def _lista_or_404_with_tenant(lista_id: str, request: Request) -> dict:
    """
    Carrega a lista e valida o escopo de tenant: listas com owner_key só são
    acessíveis pelo tenant dono (ou superadmin). Listas legadas (owner_key
    vazio) permanecem acessíveis como antes — serão adotadas via migração.
    """
    user, _ = auth_svc.get_auth_user(request)
    lista = crm_svc.get_list(lista_id)
    if lista is None:
        raise HTTPException(404, "Lista não encontrada")
    owner = lista.get("owner_key", "")
    if owner and user.get("funcao") != "superadmin" and owner != tenant_key_for_user(user):
        raise HTTPException(403, "Sem acesso a esta lista")
    return lista


@router.post("/crm/listas", dependencies=[Security(require_api_key)])
def crm_create_lista(payload: CrmListaPayload, request: Request):
    user, _ = auth_svc.get_auth_user(request)
    return crm_svc.create_list(payload.name, payload.filtros, owner_key=tenant_key_for_user(user))


@router.delete("/crm/listas/{lista_id}", dependencies=[Security(require_api_key)])
def crm_delete_lista(lista_id: str, request: Request):
    _lista_or_404_with_tenant(lista_id, request)
    if not crm_svc.delete_list(lista_id):
        raise HTTPException(404, "Lista não encontrada")
    return {"ok": True}


@router.patch("/crm/listas/{lista_id}/followup", dependencies=[Security(require_api_key)])
def crm_update_lista_followup(lista_id: str, payload: CrmListaFollowupPayload, request: Request):
    _lista_or_404_with_tenant(lista_id, request)
    lista = crm_svc.update_list_followup(lista_id, payload.followup_date)
    if not lista:
        raise HTTPException(404, "Lista não encontrada")
    return lista


@router.get("/crm/listas/{lista_id}/leads", dependencies=[Security(require_api_key)])
def crm_lista_leads(lista_id: str, request: Request):
    _lista_or_404_with_tenant(lista_id, request)
    leads = crm_svc.get_list_leads(lista_id)
    if leads is None:
        raise HTTPException(404, "Lista não encontrada")
    allowed = _get_allowed_instances_for_request(request)
    leads = _filter_leads_by_user(leads, allowed)
    return {"leads": leads, "total": len(leads)}


@router.get("/crm/listas/{lista_id}/contatos", dependencies=[Security(require_api_key)])
def crm_lista_contatos(lista_id: str, request: Request):
    _lista_or_404_with_tenant(lista_id, request)
    leads = crm_svc.get_list_leads(lista_id)
    if leads is None:
        raise HTTPException(404, "Lista não encontrada")
    allowed = _get_allowed_instances_for_request(request)
    leads = _filter_leads_by_user(leads, allowed)
    contacts = [{"phone": l["telefone"], "name": l.get("nome", l["telefone"])} for l in leads if l.get("telefone")]
    return {"contacts": contacts, "total": len(contacts)}


@router.post("/crm/listas/{lista_id}/leads/{lead_id}", dependencies=[Security(require_api_key)])
def crm_add_lead_to_lista(lista_id: str, lead_id: str, request: Request):
    _lead_or_404_with_access(lead_id, request)
    _lista_or_404_with_tenant(lista_id, request)
    lista = crm_svc.add_lead_to_list(lista_id, lead_id)
    if not lista:
        raise HTTPException(404, "Lista não encontrada")
    return lista


@router.delete("/crm/listas/{lista_id}/leads/{lead_id}", dependencies=[Security(require_api_key)])
def crm_remove_lead_from_lista(lista_id: str, lead_id: str, request: Request):
    _lista_or_404_with_tenant(lista_id, request)
    lista = crm_svc.remove_lead_from_list(lista_id, lead_id)
    if not lista:
        raise HTTPException(404, "Lista não encontrada")
    return lista


@router.post("/crm/leads/{lead_id}/message", dependencies=[Security(require_api_key)])
def crm_send_message(lead_id: str, payload: CrmMessagePayload, request: Request):
    """Envia mensagem WhatsApp para o lead a partir do CRM."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    lead = _lead_or_404_with_access(lead_id, request)
    instance_id = lead.get("instancia_id", "")
    with instances_lock:
        if not instance_id or instance_id not in instances:
            raise HTTPException(400, "Instância do lead não encontrada. Vincule o lead a uma instância válida.")
        inst = dict(instances[instance_id])
    phone = request.app.state.conversation_phone_key(lead.get("telefone", ""))
    bsuid = request.app.state.conversation_phone_key(lead.get("bsuid", ""))
    conv_key = bsuid or phone
    if not conv_key:
        raise HTTPException(400, "Lead sem identificador de conversa.")
    ok, err, channel = send_outbound_text(inst, instance_id, conv_key, payload.message)
    if not ok:
        raise HTTPException(500, f"Falha ao enviar via {channel}: {err}")
    crm_svc.add_note(lead_id, f"[Mensagem enviada]: {payload.message}")

    conv_key = request.app.state.resolve_conversation_key(instance_id, conv_key)
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead.get("nome", conv_key))
        conv["ai_paused"] = True
        conv["unread"] = False
        request.app.state.clear_needs_human(conv)
        conv.pop("ai_resume_after", None)
        conv["messages"].append({
            "role": "human",
            "content": payload.message,
            "timestamp": request.app.state.now_iso(),
        })
        conv["last_activity"] = request.app.state.now_iso()
    request.app.state.save_conversation(instance_id, conv_key)
    return {"ok": True}


@router.post("/crm/leads/{lead_id}/send-media", dependencies=[Security(require_api_key)])
async def crm_send_media(
    lead_id: str,
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    """Envia imagem ou documento para o lead a partir do CRM. Só Evolution GO."""
    _MEDIA_MIME_MAP = {
        "image/jpeg": ("image", "🖼"), "image/jpg": ("image", "🖼"),
        "image/png":  ("image", "🖼"), "image/webp": ("image", "🖼"),
        "image/gif":  ("image", "🖼"),
        "application/pdf": ("document", "📄"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("document", "📄"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("document", "📄"),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("document", "📄"),
        "audio/mpeg": ("audio", "🎵"), "audio/mp4": ("audio", "🎵"),
        "audio/ogg":  ("audio", "🎵"), "audio/x-m4a": ("audio", "🎵"),
    }
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    lead = _lead_or_404_with_access(lead_id, request)
    instance_id = lead.get("instancia_id", "")
    with instances_lock:
        if not instance_id or instance_id not in instances:
            raise HTTPException(400, "Instância do lead não encontrada.")
        inst = dict(instances[instance_id])
    phone = request.app.state.conversation_phone_key(lead.get("telefone", ""))
    bsuid = request.app.state.conversation_phone_key(lead.get("bsuid", ""))
    conv_key = bsuid or phone
    if not conv_key:
        raise HTTPException(400, "Lead sem identificador de conversa.")

    mime = (file.content_type or "").lower()
    if mime not in _MEDIA_MIME_MAP:
        raise HTTPException(415, f"Tipo não suportado: {mime}")
    mediatype, icon = _MEDIA_MIME_MAP[mime]
    max_bytes = 5 * 1024 * 1024 if mediatype == "image" else 10 * 1024 * 1024
    raw = await file.read(max_bytes + 1)
    if not raw:
        raise HTTPException(400, "Arquivo vazio.")
    if len(raw) > max_bytes:
        raise HTTPException(413, f"Arquivo excede o limite de {max_bytes // (1024*1024)} MB.")

    filename = file.filename or f"arquivo.{mime.split('/')[-1]}"

    # run_in_threadpool: send_media faz chamada HTTP síncrona — não pode
    # rodar direto no event loop de uma rota async (trava o servidor inteiro).
    ok, err = await run_in_threadpool(
        media_svc.send_media,
        api_url=inst.get("api_url", ""),
        api_key=inst.get("api_key", ""),
        instance_id=instance_id,
        phone=conv_key,
        raw=raw,
        mediatype=mediatype,
        mimetype=mime,
        filename=filename,
        caption=caption.strip(),
    )
    if not ok:
        raise HTTPException(502, f"Falha ao enviar arquivo: {err}")

    crm_svc.add_note(lead_id, f"[Arquivo enviado]: {icon} {filename}" + (f" — {caption.strip()}" if caption.strip() else ""))
    resolved_key = request.app.state.resolve_conversation_key(instance_id, conv_key)
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, resolved_key, lead.get("nome", conv_key))
        conv["ai_paused"] = True
        conv["unread"] = False
        request.app.state.clear_needs_human(conv)
        conv.pop("ai_resume_after", None)
        display = f"{icon} {filename}" + (f" — {caption.strip()}" if caption.strip() else "")
        conv["messages"].append({
            "role": "human", "content": display,
            "timestamp": request.app.state.now_iso(),
            "is_media": True, "media_type": mediatype,
        })
        conv["last_activity"] = request.app.state.now_iso()
    request.app.state.save_conversation(instance_id, resolved_key)
    return {"ok": True, "filename": filename, "mediatype": mediatype}


# ── Enviar lead do Inbox SDR para o CRM ──────────────────────────────────────

@router.post("/conversations/{instance_id}/{phone}/send-to-crm", dependencies=[Security(require_api_key)])
def inbox_send_to_crm(instance_id: str, phone: str, request: Request, coluna_id: str | None = None):
    """Cria ou atualiza um lead no CRM com dados da conversa do Inbox SDR."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    auth_svc.require_instance_access(instance_id, request)
    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    with instances_lock:
        inst = dict(instances.get(instance_id, {}))
    with conversations_lock:
        conv_map = conversations.get(instance_id, {})
        conv = conv_map.get(conv_key)
        if not conv:
            raise HTTPException(404, "Conversa não encontrada.")
        conv = copy.deepcopy(conv)
    historico = [
        {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", "")}
        for m in conv.get("messages", [])
    ]
    identity_meta = copy.deepcopy(conv.get("source_meta") or {})
    meta_bsuid = str(identity_meta.get("user_id") or "").strip()
    if not meta_bsuid and str(conv_key).startswith("bsuid:"):
        meta_bsuid = str(conv_key).split(":", 1)[1]
    legacy_bsuid = conv_key if re.search(r"[A-Za-z]", str(conv_key or "")) else ""
    bsuid = meta_bsuid or legacy_bsuid
    phone_available = bool(identity_meta.get("phone_available", not bool(bsuid)))
    existing_lead = next(
        (
            l for l in crm_svc.get_leads()
            if l.get("instancia_id") == instance_id
            and (
                (bsuid and l.get("bsuid") == bsuid)
                or (conv_key and l.get("telefone") == conv_key)
            )
        ),
        None,
    )
    origem = (
        conv.get("lead_origem")
        or (existing_lead or {}).get("origem")
        or "sdr_inbox"
    )
    email = conv.get("lead_email") or (existing_lead or {}).get("email", "")
    campanha = conv.get("lead_campanha") or (existing_lead or {}).get("campanha", "")
    source_meta = copy.deepcopy(
        conv.get("source_meta")
        or (existing_lead or {}).get("source_meta")
        or {}
    )
    if conv.get("form_answers"):
        source_meta.setdefault("form_answers", {}).update(conv["form_answers"])

    # Extrair campos do source_meta para popular o card diretamente
    def _sm_get(*keys: str) -> str:
        for d in (source_meta.get("lead_fields") or {}, source_meta.get("form_answers") or {}, source_meta):
            if not isinstance(d, dict):
                continue
            for k in keys:
                v = d.get(k, "")
                if v:
                    return str(v)
        return ""

    cidade   = _sm_get("cidade", "city", "ct")
    estado   = _sm_get("estado", "state", "uf", "st")
    cep      = _sm_get("cep", "postal_code", "zip", "zp", "postcode")
    pais     = _sm_get("pais", "country", "co") or "BR"
    ctwa_clid = source_meta.get("ctwa_clid", "")

    lead = crm_svc.upsert_lead(
        telefone=(source_meta.get("wa_id", "") if phone_available else "") or ("" if bsuid else conv_key),
        bsuid=bsuid,
        phone_available=phone_available,
        nome=conv.get("name", conv_key),
        origem=origem,
        instancia_id=instance_id,
        score=conv.get("score"),
        score_reason=conv.get("score_reason", ""),
        historico=historico,
        coluna_id=coluna_id,
        email=email,
        campanha=campanha,
        cidade=cidade,
        estado=estado,
        cep=cep,
        pais=pais,
        source_meta=source_meta,
    )
    if is_meta_capi_enabled(inst, lead):
        event_name = request.app.state.meta_stage_event_for_lead(lead)
        if event_name is None and not _col_has_explicit_none(lead):
            normalized_status = (lead.get("lead_status") or "").strip().lower().replace(" ", "_")
            event_name = None if normalized_status == "desqualificado" else "Lead"
        if event_name:
            request.app.state.maybe_queue_meta_capi_event(inst, lead, origem or "crm", event_name=event_name)
    return lead
