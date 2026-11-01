"""
Router de conversas SDR — /conversations/*, /instance/{id}/sdr
Extraído de main.py em 2026-06-12.

Globals de main.py acessados via request.app.state.*:
  instances, conversations, instances_lock, conversations_lock
  resolve_conversation_key, get_or_create_conversation, clear_needs_human,
  save_conversations, save_configs, send_reply, resolve_ai_settings, now_iso
"""

import copy
import io
import json
import logging
import os
import threading
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, Security, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import services.conversation_pg_service as conversation_pg_svc
import services.crm_service as crm_svc
import services.kb_embedding_service as kb_emb
import services.audit_log_service as audit_log_svc
import services.media_service as media_svc
import services.transcription_service as transcription_svc
from services.auth_service import get_auth_user, try_get_auth_user, require_instance_access
from core.security import require_api_key

router = APIRouter(tags=["conversations"])
logger = logging.getLogger("klead.conversations")


# ── Modelos ───────────────────────────────────────────────────────────────────

class HumanSendPayload(BaseModel):
    message: str = Field(..., max_length=4096)


class NewLeadPayload(BaseModel):
    name: str       = Field(..., min_length=1, max_length=128)
    phone: str      = Field(..., min_length=8, max_length=20)
    message: str    = Field("", max_length=4096)
    campanha: str   = Field("", max_length=256)
    email: str      = Field("", max_length=254)
    leadgen_id: str = Field("", max_length=64)
    cep: str        = Field("", max_length=16)


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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/conversations/{instance_id}")
def list_conversations(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Lista todas as conversas de uma instância, ordenadas por última atividade."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")

    user = require_instance_access(instance_id, request)
    if user.get("funcao") == "superadmin":
        audit_log_svc.log_admin_access(user.get("email", ""), "superadmin", instance_id, "view_conversations")

    with conversations_lock:
        convs = copy.deepcopy(conversations.get(instance_id, {}))
        result = []
        for phone, conv in convs.items():
            if conv.get("lead_dismissed"):
                continue
            campanha = conv.get("lead_campanha", "").strip()
            result.append({
                "phone":          phone,
                "name":           conv.get("name", "") or phone,
                "stage":          conv.get("stage", "qualifying"),
                "ai_paused":      conv.get("ai_paused", False),
                "unread":         conv.get("unread", False),
                "score":          conv.get("score"),
                "score_reason":   conv.get("score_reason", ""),
                "turn_count":     conv.get("turn_count", 0),
                "needs_human":    conv.get("needs_human", False),
                "handoff_reason": conv.get("handoff_reason", ""),
                "handoff_at":     conv.get("handoff_at"),
                "handoff_source": conv.get("handoff_source", ""),
                "handoff_handled_at": conv.get("handoff_handled_at"),
                "msg_count":      len(conv.get("messages", [])),
                "last_message":   conv["messages"][-1]["content"] if conv.get("messages") else "",
                "last_activity":  conv.get("last_activity", ""),
                "created_at":     conv.get("created_at", ""),
                "lead_campanha":  campanha,
                "lead_origem":    conv.get("lead_origem", ""),
                "roleta_active":  bool((conv.get("source_meta") or {}).get("roleta_active")),
            })

    result.sort(key=lambda x: x["last_activity"], reverse=True)
    return {"instance_id": instance_id, "total": len(result), "conversations": result}


@router.get("/conversations/{instance_id}/{phone}")
def get_conversation(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Retorna conversa completa com histórico de mensagens."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    with conversations_lock:
        conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
        conv = conversations.get(instance_id, {}).get(conv_key)
        if not conv:
            raise HTTPException(404, "Conversa não encontrada.")
        conv = copy.deepcopy(conv)

    return {"phone": conv_key, **conv}


@router.get("/conversations/{instance_id}/_pg-preview")
def preview_conversations_from_pg(
    instance_id: str,
    request: Request,
    cursor: str | None = None,
    limit: int = 50,
    _: str = Security(require_api_key),
):
    """
    PREVIEW/DEBUG — lê a lista de conversas paginada direto do Postgres
    (services.conversation_pg_service.load_conversations_page), em paralelo
    ao caminho real (GET /conversations/{instance_id}, que lê da RAM).

    Não é usado pelo frontend. Existe só para comparação manual de paridade
    antes de qualquer mudança no caminho de leitura em produção (Sessão 1 do
    plano de escala — item 2). Não substitui nem afeta o endpoint real.
    """
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    ok, result = conversation_pg_svc.load_conversations_page(instance_id, cursor, limit)
    if not ok:
        raise HTTPException(500, f"Falha ao ler do Postgres: {result}")
    return {"instance_id": instance_id, **result}


@router.patch("/conversations/{instance_id}/{phone}/takeover")
def takeover_conversation(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Humano assume a conversa — IA para de responder. Cria conversa se não existir."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    lead_name = conv_key
    crm_lead = crm_svc.find_lead_by_phone(instance_id, conv_key)
    if crm_lead:
        lead_name = crm_lead.get("nome", conv_key)
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead_name)
        conv["ai_paused"] = True
        request.app.state.clear_needs_human(conv)
    request.app.state.save_conversation(instance_id, conv_key)
    return {"ok": True, "phone": conv_key, "ai_paused": True}


@router.patch("/conversations/{instance_id}/{phone}/release")
def release_conversation(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Devolve conversa para a IA."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    with conversations_lock:
        conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
        conv = conversations.get(instance_id, {}).get(conv_key)
        if not conv:
            raise HTTPException(404, "Conversa não encontrada.")

        conv["ai_paused"] = False
        conv["stage"]     = "qualifying"
        request.app.state.clear_needs_human(conv)
    request.app.state.save_conversation(instance_id, conv_key)
    return {"ok": True, "phone": conv_key, "ai_paused": False}


class CampanhaPayload(BaseModel):
    campanha: str = Field("", max_length=512)
    dismiss: bool = False


@router.patch("/conversations/{instance_id}/{phone}/campanha")
def update_campanha(instance_id: str, phone: str, payload: CampanhaPayload, request: Request, _: str = Security(require_api_key)):
    """Define a campanha de origem da conversa. Se dismiss=True, remove do Inbox SDR."""
    conversations      = request.app.state.conversations
    conversations_lock = request.app.state.conversations_lock
    require_instance_access(instance_id, request)
    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    with conversations_lock:
        inst_convs = conversations.get(instance_id, {})
        if conv_key not in inst_convs:
            raise HTTPException(404, "Conversa não encontrada.")
        conv = inst_convs[conv_key]
        if payload.dismiss:
            conv["lead_dismissed"] = True
            conv["lead_campanha"]  = ""
        else:
            conv["lead_dismissed"] = False
            conv["lead_campanha"]  = payload.campanha.strip()
    request.app.state.save_conversation(instance_id, conv_key)
    return {"ok": True, "phone": conv_key, "lead_campanha": conv.get("lead_campanha", ""), "lead_dismissed": conv.get("lead_dismissed", False)}


class LeadMetaPayload(BaseModel):
    nome:       str | None = Field(None, max_length=256)
    email:      str | None = Field(None, max_length=256)
    leadgen_id: str | None = Field(None, max_length=256)
    cidade:     str | None = Field(None, max_length=128)
    estado:     str | None = Field(None, max_length=128)
    pais:       str | None = Field(None, max_length=128)
    cep:        str | None = Field(None, max_length=32)


@router.patch("/conversations/{instance_id}/{phone}/metadata")
def update_lead_metadata(instance_id: str, phone: str, payload: LeadMetaPayload, request: Request, _: str = Security(require_api_key)):
    """Atualiza campos do lead na conversa (nome, email, leadgen_id, cidade, estado, pais, cep)."""
    conversations      = request.app.state.conversations
    conversations_lock = request.app.state.conversations_lock
    require_instance_access(instance_id, request)
    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    with conversations_lock:
        inst_convs = conversations.get(instance_id, {})
        if conv_key not in inst_convs:
            raise HTTPException(404, "Conversa não encontrada.")
        conv = inst_convs[conv_key]
        if payload.nome      is not None: conv["name"]       = payload.nome.strip() or conv.get("name", conv_key)
        if payload.email     is not None: conv["lead_email"] = payload.email.strip()
        if payload.leadgen_id is not None:
            conv.setdefault("source_meta", {})["leadgen_id"] = payload.leadgen_id.strip()
        lf = conv.setdefault("source_meta", {}).setdefault("lead_fields", {})
        if payload.cep    is not None: lf["cep"]    = payload.cep.strip()
        if payload.cidade is not None: lf["cidade"] = payload.cidade.strip()
        if payload.estado is not None: lf["estado"] = payload.estado.strip()
        if payload.pais   is not None: lf["pais"]   = payload.pais.strip()
    request.app.state.save_conversation(instance_id, conv_key)
    # Sync to CRM lead if it exists
    crm_lead = crm_svc.find_lead_by_phone(instance_id, conv_key)
    if crm_lead:
        crm_svc.update_fields(
            crm_lead["id"],
            nome=payload.nome.strip() if payload.nome is not None else None,
            email=payload.email.strip() if payload.email is not None else None,
            leadgen_id=payload.leadgen_id.strip() if payload.leadgen_id is not None else None,
            cidade=payload.cidade.strip() if payload.cidade is not None else None,
            estado=payload.estado.strip() if payload.estado is not None else None,
            pais=payload.pais.strip() if payload.pais is not None else None,
            cep=payload.cep.strip() if payload.cep is not None else None,
        )
    return {"ok": True}


@router.post("/conversations/{instance_id}/{phone}/send")
def human_send_message(instance_id: str, phone: str, payload: HumanSendPayload, request: Request, _: str = Security(require_api_key)):
    """Humano envia mensagem manualmente em uma conversa. Cria conversa se não existir."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    require_instance_access(instance_id, request)

    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    lead_name = conv_key
    crm_lead = crm_svc.find_lead_by_phone(instance_id, conv_key)
    if crm_lead:
        lead_name = crm_lead.get("nome", conv_key)
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead_name)

    if not payload.message.strip():
        raise HTTPException(400, "Mensagem não pode ser vazia.")

    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    ok = request.app.state.send_reply(inst, instance_id, conv_key, payload.message.strip())
    if not ok:
        # Erro genérico confunde quando a causa é número BR digitado sem o nono
        # dígito (WhatsApp responde "not registered"). Não bloqueia antes do
        # envio porque números BR antigos de 12 dígitos podem ser legítimos.
        digits = "".join(ch for ch in str(conv_key) if ch.isdigit())
        hint = (
            " O número parece estar sem o nono dígito — o formato esperado é 55 + DDD + 9XXXXXXXX (13 dígitos)."
            if digits.startswith("55") and len(digits) == 12 else ""
        )
        raise HTTPException(502, f"Falha ao enviar a mensagem para a instância.{hint}")

    with conversations_lock:
        conv["ai_paused"] = True
        conv["unread"]    = False
        request.app.state.clear_needs_human(conv)
        conv.pop("ai_resume_after", None)
        conv["messages"].append({
            "role":      "human",
            "content":   payload.message.strip(),
            "timestamp": request.app.state.now_iso(),
        })
        conv["last_activity"] = request.app.state.now_iso()
    request.app.state.save_conversation(instance_id, conv_key)
    request.app.state.sse_emit(instance_id, "new_message", {"phone": conv_key, "role": "human", "content": payload.message.strip()})

    return {"ok": True, "phone": conv_key, "message": payload.message.strip()}


@router.post("/conversations/{instance_id}/new-lead")
def create_new_lead_conversation(instance_id: str, payload: NewLeadPayload, request: Request, _: str = Security(require_api_key)):
    """Cria um novo lead manualmente no Inbox SDR com nome e telefone. Envia mensagem inicial se informada."""
    from utils.cleaner import normalize_phone, normalize_name

    instances      = request.app.state.instances
    instances_lock = request.app.state.instances_lock

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    require_instance_access(instance_id, request)

    phone_norm = normalize_phone(payload.phone.strip())
    if not phone_norm:
        raise HTTPException(400, "Telefone inválido. Use DDD + número (ex: 11999998888).")

    name_norm = normalize_name(payload.name.strip())
    conv_key  = request.app.state.resolve_conversation_key(instance_id, phone_norm)

    conversations_lock = request.app.state.conversations_lock
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, name_norm)
        conv["name"]          = name_norm
        conv["ai_paused"]     = True
        conv["unread"]        = False
        conv["lead_origem"]   = "manual"
        conv["lead_campanha"] = payload.campanha.strip()
        if payload.email.strip():
            conv["lead_email"] = payload.email.strip()
        if payload.leadgen_id.strip() or payload.cep.strip():
            sm = conv.setdefault("source_meta", {})
            if payload.leadgen_id.strip():
                sm["leadgen_id"] = payload.leadgen_id.strip()
            if payload.cep.strip():
                sm.setdefault("lead_fields", {})["cep"] = payload.cep.strip()

    first_message = payload.message.strip()
    if first_message:
        ok = request.app.state.send_reply(inst, instance_id, conv_key, first_message)
        if not ok:
            raise HTTPException(502, "Lead criado mas falha ao enviar a mensagem inicial.")
        with conversations_lock:
            conv["messages"].append({
                "role":      "human",
                "content":   first_message,
                "timestamp": request.app.state.now_iso(),
            })
            conv["last_activity"] = request.app.state.now_iso()

    request.app.state.save_conversation(instance_id, conv_key)
    logger.info(f"[INBOX] Novo lead criado manualmente: {instance_id}/{conv_key} ({name_norm})")
    return {"ok": True, "phone": conv_key, "name": name_norm}


@router.post("/conversations/{instance_id}/{phone}/send-audio")
async def human_send_audio(
    instance_id: str,
    phone: str,
    request: Request,
    file: UploadFile = File(...),
    _: str = Security(require_api_key),
):
    """Humano envia áudio manualmente via Evolution GO."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    require_instance_access(instance_id, request)

    MAX_AUDIO_BYTES = 10 * 1024 * 1024
    audio_bytes = await file.read(MAX_AUDIO_BYTES + 1)
    if not audio_bytes:
        raise HTTPException(400, "Arquivo de áudio vazio.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Arquivo de áudio excede o limite de 10MB.")

    _NON_AUDIO_MAGIC = [b"PK\x03\x04", b"MZ", b"%PDF", b"\x89PNG", b"\xff\xd8\xff"]
    if any(audio_bytes[:4].startswith(m) for m in _NON_AUDIO_MAGIC):
        raise HTTPException(415, "Tipo de arquivo não permitido. Envie apenas áudio.")

    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    mime = (file.content_type or "audio/ogg").lower()
    # run_in_threadpool: send_media faz uma chamada HTTP síncrona (até 60s) —
    # sem isso, ela bloqueia o event loop inteiro e trava o app pra todo mundo
    # enquanto um único áudio está sendo enviado (servidor roda 1 worker).
    ok, err = await run_in_threadpool(
        media_svc.send_media,
        api_url=inst.get("api_url", ""),
        api_key=inst.get("api_key", ""),
        instance_id=instance_id,
        phone=conv_key,
        raw=audio_bytes,
        mediatype="audio",
        mimetype=mime,
        filename=file.filename or "audio.ogg",
    )
    if not ok:
        raise HTTPException(502, f"Falha ao enviar áudio: {err}")

    ai = request.app.state.resolve_ai_settings(inst)
    ai_config = getattr(request.app.state, "ai_config", {}) or {}
    groq_key = (inst.get("key_groq") or ai_config.get("key_groq") or "").strip()
    openai_key = (inst.get("openai_key", "") or (ai["api_key"] if ai["provider"] == "openai" else "")).strip()
    transcribed = await run_in_threadpool(
        transcription_svc.transcribe_audio,
        audio_bytes=audio_bytes,
        filename=file.filename or "audio.ogg",
        llm_provider="groq" if groq_key else ai["provider"],
        llm_api_key=groq_key or ai["api_key"],
        openai_key=openai_key,
    )

    audio_ext = media_svc.ext_from_mimetype(mime, "audio")
    audio_filename = media_svc.save_temp_file(audio_bytes, audio_ext, delete_after=24 * 3600.0)
    audio_meta = {
        "is_audio":   True,
        "is_media":   True,
        "media_type": "audio",
        "media_url":  media_svc.signed_media_path(audio_filename),
        "filename":   file.filename or f"audio.{audio_ext}",
    }

    lead_name = conv_key
    crm_lead = crm_svc.find_lead_by_phone(instance_id, conv_key)
    if crm_lead:
        lead_name = crm_lead.get("nome", conv_key)
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead_name)
        conv["ai_paused"] = True
        conv["unread"]    = False
        request.app.state.clear_needs_human(conv)
        conv.pop("ai_resume_after", None)
        message = {
            "role":      "human",
            "content":   "🎤 [áudio enviado]",
            "timestamp": request.app.state.now_iso(),
        }
        message.update(audio_meta)
        conv["messages"].append(message)
        conv["last_activity"] = request.app.state.now_iso()
    request.app.state.save_conversation(instance_id, conv_key)
    event = {"phone": conv_key, "role": "human", "content": "🎤 [áudio enviado]"}
    event.update(audio_meta)
    request.app.state.sse_emit(instance_id, "new_message", event)

    if transcribed:
        transcript_text = f"📝 Transcrição do áudio:\n{transcribed}"
        transcript_ok = await run_in_threadpool(
            request.app.state.send_reply,
            inst,
            instance_id,
            conv_key,
            transcript_text,
        )
        if transcript_ok:
            with conversations_lock:
                conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead_name)
                conv["messages"].append({
                    "role": "human",
                    "content": transcript_text,
                    "timestamp": request.app.state.now_iso(),
                })
                conv["last_activity"] = request.app.state.now_iso()
            request.app.state.save_conversation(instance_id, conv_key)
            request.app.state.sse_emit(instance_id, "new_message", {"phone": conv_key, "role": "human", "content": transcript_text})
        else:
            logger.warning(f"[INBOX] Falha ao enviar transcricao do audio para {instance_id}/{conv_key}")
    else:
        logger.info(f"[INBOX] Audio sem transcricao para {instance_id}/{conv_key}")

    return {"ok": True}


@router.post("/conversations/{instance_id}/{phone}/send-media")
async def human_send_media(
    instance_id: str,
    phone: str,
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    _: str = Security(require_api_key),
):
    """Humano envia imagem, vídeo, PDF ou documento via Evolution GO."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    require_instance_access(instance_id, request)

    mime = (file.content_type or "").lower()
    classified = media_svc.classify_mime(mime)
    if not classified:
        raise HTTPException(415, f"Tipo de arquivo não suportado: {mime}. Use imagem, vídeo, PDF, DOCX ou XLSX.")
    mediatype, icon = classified
    if mediatype == "audio":
        raise HTTPException(400, "Para áudio, use /send-audio.")

    max_bytes = media_svc.max_bytes_for(mediatype)
    raw = await file.read(max_bytes + 1)
    if not raw:
        raise HTTPException(400, "Arquivo vazio.")
    if len(raw) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise HTTPException(413, f"Arquivo excede o limite de {limit_mb} MB.")

    filename = file.filename or f"arquivo.{media_svc.ext_from_mimetype(mime, mediatype)}"
    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)

    # run_in_threadpool: mesmo motivo do send-audio — chamada HTTP síncrona
    # não pode rodar direto no event loop de uma rota async.
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

    lead_name = conv_key
    crm_lead = crm_svc.find_lead_by_phone(instance_id, conv_key)
    if crm_lead:
        lead_name = crm_lead.get("nome", conv_key)
    with conversations_lock:
        conv = request.app.state.get_or_create_conversation(instance_id, conv_key, lead_name)
        conv["ai_paused"] = True
        conv["unread"]    = False
        request.app.state.clear_needs_human(conv)
        conv.pop("ai_resume_after", None)
        display = f"{icon} {filename}" + (f" — {caption.strip()}" if caption.strip() else "")
        conv["messages"].append({
            "role":       "human",
            "content":    display,
            "timestamp":  request.app.state.now_iso(),
            "is_media":   True,
            "media_type": mediatype,
        })
        conv["last_activity"] = request.app.state.now_iso()
    request.app.state.save_conversation(instance_id, conv_key)
    request.app.state.sse_emit(instance_id, "new_message", {"phone": conv_key, "role": "human", "content": display, "is_media": True, "media_type": mediatype})
    return {"ok": True, "filename": filename, "mediatype": mediatype}


@router.delete("/conversations/{instance_id}/{phone}/messages/{index}")
def delete_conversation_message(instance_id: str, phone: str, index: int, request: Request, _: str = Security(require_api_key)):
    """Remove uma mensagem individual de uma conversa pelo índice."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    with conversations_lock:
        conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
        conv = conversations.get(instance_id, {}).get(conv_key)
        if conv is None:
            raise HTTPException(404, "Conversa não encontrada.")
        msgs = conv.get("messages", [])
        if index < 0 or index >= len(msgs):
            raise HTTPException(400, f"Índice {index} fora do intervalo (0–{len(msgs)-1}).")
        msgs.pop(index)

    # Upsert regrava as mensagens da conversa — remove a mensagem também no Postgres
    request.app.state.save_conversation(instance_id, conv_key)
    return {"ok": True, "removed_index": index}


@router.delete("/conversations/{instance_id}/{phone}")
def delete_conversation(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Remove uma conversa."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    with conversations_lock:
        conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
        conv = conversations.get(instance_id, {}).pop(conv_key, None)
        if conv is None:
            raise HTTPException(404, "Conversa não encontrada.")
        messages = copy.deepcopy(conv.get("messages", []))

    request.app.state.delete_conversation_pg(instance_id, conv_key)
    # Lead qualificado (tem card no CRM) mantém o histórico da conversa no
    # card mesmo depois de apagada do inbox. Best-effort: falha aqui não pode
    # impedir a exclusão, que já aconteceu.
    try:
        crm_svc.archive_conversation_history(instance_id, conv_key, messages)
    except Exception as exc:
        logger.warning(f"[CRM] Falha ao arquivar histórico de {instance_id}/{conv_key}: {exc}")
    return {"ok": True, "phone": conv_key}


@router.get("/conversations/{instance_id}/trash")
def list_deleted_conversations(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Lista conversas apagadas nos últimos dias, ainda recuperáveis (lixeira)."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)
    return {"items": request.app.state.list_deleted_conversations_pg(instance_id)}


@router.post("/conversations/{instance_id}/{phone}/restore")
def restore_conversation(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Restaura uma conversa apagada por engano — some da lixeira e volta ao InboxSDR."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    conv_key = request.app.state.conversation_phone_key(phone)
    ok, detail = request.app.state.restore_conversation_pg(instance_id, conv_key)
    if not ok:
        if detail == "not_found_or_not_deleted":
            raise HTTPException(404, "Conversa não encontrada na lixeira.")
        raise HTTPException(500, f"Erro ao restaurar: {detail}")
    return {"ok": True, "phone": conv_key}


@router.delete("/conversations/{instance_id}")
def clear_conversations(instance_id: str, request: Request, stage: str | None = None, _: str = Security(require_api_key)):
    """Remove todas as conversas de uma instância (ou de um estágio específico)."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    with conversations_lock:
        conv_map = conversations.get(instance_id, {})
        if stage:
            to_delete = [p for p, c in conv_map.items() if c.get("stage") == stage]
            to_archive = {p: copy.deepcopy(conv_map[p].get("messages", [])) for p in to_delete}
            for p in to_delete:
                conv_map.pop(p)
            removed = len(to_delete)
        else:
            to_delete = None
            to_archive = {p: copy.deepcopy(c.get("messages", [])) for p, c in conv_map.items()}
            removed = len(conv_map)
            conversations[instance_id] = {}

    if to_delete is None:
        request.app.state.delete_instance_conversations_pg(instance_id)
    else:
        for p in to_delete:
            request.app.state.delete_conversation_pg(instance_id, p)

    # Conversas com card no CRM preservam o histórico no card (best-effort)
    for p, msgs in to_archive.items():
        try:
            crm_svc.archive_conversation_history(instance_id, p, msgs)
        except Exception as exc:
            logger.warning(f"[CRM] Falha ao arquivar histórico de {instance_id}/{p}: {exc}")
    return {"ok": True, "removed": removed}


@router.patch("/instance/{instance_id}/sdr")
def update_sdr_config(instance_id: str, payload: UpdateSdrPayload, request: Request, _: str = Security(require_api_key)):
    """Atualiza apenas as configurações de SDR da instância."""
    instances = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    # Resolve usuario/permissao ANTES de segurar o lock — get_auth_user pode
    # fazer I/O (verificacao de token, consulta de usuario), e segurar o lock
    # de instancias durante isso bloqueia toda outra rota que precise dele
    # enquanto essa chamada estiver em andamento.
    user = require_instance_access(instance_id, request)
    can_manage_credentials = user.get("funcao") in ("superadmin", "admin")
    with instances_lock:
        inst = instances[instance_id]
        inst["inbound_ai_enabled"]    = payload.inbound_ai_enabled
        inst["qualification_prompt"]  = payload.qualification_prompt
        inst["max_ai_turns"]          = payload.max_ai_turns
        inst["ai_response_delay"]     = payload.ai_response_delay
        inst["auto_handoff_keywords"] = payload.auto_handoff_keywords
        inst["model"]                 = payload.model or "openai/gpt-oss-120b"
        if can_manage_credentials:
            inst["llm_provider"] = payload.llm_provider or "groq"
            _is_masked = request.app.state.is_masked_secret
            if payload.key_openai and not _is_masked(payload.key_openai):
                inst["key_openai"] = payload.key_openai
            if payload.key_anthropic and not _is_masked(payload.key_anthropic):
                inst["key_anthropic"] = payload.key_anthropic
            if payload.key_groq and not _is_masked(payload.key_groq):
                inst["key_groq"] = payload.key_groq
            provider_key_map = {"openai": "key_openai", "anthropic": "key_anthropic", "groq": "key_groq"}
            active_key_field = provider_key_map.get(inst["llm_provider"], "key_openai")
            inst["llm_api_key"] = inst.get(active_key_field) or payload.llm_api_key
        inst["followup_enabled"]      = payload.followup_enabled
        inst["followup_delay_hours"]  = payload.followup_delay_hours
        inst["followup_max"]          = payload.followup_max
        inst["scoring_criteria"]      = payload.scoring_criteria
        inst["auto_release_minutes"]  = payload.auto_release_minutes
        inst["tts_enabled"]           = payload.tts_enabled
    request.app.state.save_instance_config(instance_id)
    return {"ok": True, "instance_id": instance_id}


@router.get("/conversations/{instance_id}/{phone}/summary")
def get_conversation_summary(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Gera um resumo estruturado da conversa usando IA."""
    instances = request.app.state.instances
    conversations = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    require_instance_access(instance_id, request)
    with conversations_lock:
        conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
        conv = conversations.get(instance_id, {}).get(conv_key)
        if not conv:
            raise HTTPException(404, "Conversa não encontrada.")
        conv = copy.deepcopy(conv)
    ai = request.app.state.resolve_ai_settings(inst)
    if not ai["api_key"]:
        raise HTTPException(400, "API Key não configurada.")

    msgs = conv.get("messages", [])
    if len(msgs) < 2:
        return {
            "nome": conv.get("name", conv_key),
            "interesse": "Conversa muito curta para analisar",
            "principais_temas": [],
            "perguntas_feitas": [],
            "sentimento": "neutro",
            "proxima_acao": "Aguardar mais interações",
            "score_atual": conv.get("score"),
        }

    history_text = "\n".join(
        f"{'LEAD' if m['role']=='user' else 'IA' if m['role']=='assistant' else 'HUMANO'}: {m['content']}"
        for m in msgs
    )

    from openai import OpenAI
    from services.openai_service import create_chat_completion
    is_groq = ai["provider"] == "groq"
    kwargs  = {"base_url": "https://api.groq.com/openai/v1"} if is_groq else {}
    client  = OpenAI(api_key=ai["api_key"], timeout=60, max_retries=1, **kwargs)
    resp = create_chat_completion(
        client, is_groq, tokens=400,
        model=ai["model"],
        messages=[
            {"role": "system", "content": "Analise conversas de vendas e retorne JSON estruturado, conciso e objetivo."},
            {"role": "user", "content": (
                "Analise esta conversa e retorne JSON com:\n"
                "- \"interesse\": nível de interesse do lead (ex: 'Alto — perguntou sobre preço')\n"
                "- \"principais_temas\": lista de até 4 temas discutidos\n"
                "- \"perguntas_feitas\": perguntas que o lead fez\n"
                "- \"sentimento\": \"positivo\", \"neutro\" ou \"negativo\"\n"
                "- \"proxima_acao\": o que o vendedor deve fazer agora (1 frase)\n"
                "- \"score_estimado\": potencial de compra 0-100\n\n"
                f"Conversa:\n{history_text}"
            )},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    try:
        data = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, AttributeError, IndexError) as exc:
        raise HTTPException(502, f"IA retornou resposta em formato inesperado: {exc}")
    data["nome"]       = conv.get("name", conv_key)
    data["score_atual"] = conv.get("score")
    return data


# ── Export ZIP de conversas ───────────────────────────────────────────────────

@router.get("/conversations/{instance_id}/export")
def export_conversations(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Exporta todas as conversas como ZIP com um .txt por lead."""
    instances      = request.app.state.instances
    conversations  = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)

    with conversations_lock:
        convs = copy.deepcopy(conversations.get(instance_id, {}))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for phone, conv in convs.items():
            name      = conv.get("name", phone)
            campanha  = conv.get("lead_campanha", "")
            score     = conv.get("score")
            stage     = conv.get("stage", "")
            msgs      = conv.get("messages", [])
            lines = [
                f"Lead: {name}",
                f"Telefone: {phone}",
                f"Campanha: {campanha or '—'}",
                f"Score: {score if score is not None else '—'}",
                f"Estágio: {stage}",
                f"Mensagens: {len(msgs)}",
                "",
                "── Histórico ──────────────────────────────────",
            ]
            for m in msgs:
                role = "LEAD" if m["role"] == "user" else ("IA" if m["role"] == "assistant" else "VOCÊ")
                ts   = m.get("timestamp", "")[:16].replace("T", " ")
                lines.append(f"[{ts}] {role}: {m['content']}")
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{name}_{phone}")
            zf.writestr(f"{safe_name}.txt", "\n".join(lines))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=inbox_{instance_id}.zip"},
    )


# ── Inbox Knowledge Base ──────────────────────────────────────────────────────

DATA_DIR      = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
INBOX_KB_DIR  = DATA_DIR / "inbox_knowledge"


def _parse_llm_json(raw: str) -> dict:
    """
    Parseia JSON retornado por LLM de forma tolerante a dois erros comuns:
    1. Blocos markdown (```json ... ```) ao redor do objeto.
    2. Quebras de linha literais dentro de strings — inválidas em JSON, mas
       alguns modelos (ex: modelos open-weight via Groq) geram assim mesmo
       com response_format=json_object. Escapa \\n apenas quando ocorre
       dentro de uma string (fora de string, newline é whitespace válido).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    sanitized = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch == "\n":
                sanitized.append("\\n")
                continue
            elif ch == "\r":
                continue
        elif ch == '"':
            in_string = True
        sanitized.append(ch)
    return json.loads("".join(sanitized))


def _read_file_text(path: Path) -> str:
    """Extrai texto puro de um arquivo (PDF, DOCX ou texto)."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            import fitz
            doc = fitz.open(str(path))
            return "\n".join(page.get_text() for page in doc)
        if suffix == ".docx":
            import docx
            doc = docx.Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _load_inbox_knowledge(instance_id: str) -> str:
    """Carrega e concatena arquivos da KB do agente de chat do Inbox."""
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    kb_dir  = INBOX_KB_DIR / safe_id
    if not kb_dir.exists():
        return ""
    texts = []
    for f in sorted(kb_dir.iterdir()):
        if not f.is_file():
            continue
        text = _read_file_text(f)
        if text.strip():
            texts.append(f"[{f.name}]\n{text.strip()}")
    return "\n\n".join(texts)


@router.get("/instance/{instance_id}/inbox-knowledge")
def list_inbox_knowledge(instance_id: str, request: Request, _: str = Security(require_api_key)):
    instances      = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    kb_dir  = INBOX_KB_DIR / safe_id
    if not kb_dir.exists():
        return {"files": []}
    return {"files": [{"filename": f.name, "size": f.stat().st_size} for f in sorted(kb_dir.iterdir()) if f.is_file()]}


@router.post("/instance/{instance_id}/inbox-knowledge")
async def upload_inbox_knowledge(instance_id: str, request: Request, file: UploadFile = File(...), _: str = Security(require_api_key)):
    instances      = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)
    allowed  = {".pdf", ".txt", ".md", ".docx"}
    safe_id  = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    raw_name = file.filename or "file"
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw_name)
    if Path(safe_name).suffix.lower() not in allowed:
        raise HTTPException(400, f"Tipo não suportado. Use: {', '.join(allowed)}")
    kb_dir = INBOX_KB_DIR / safe_id
    kb_dir.mkdir(parents=True, exist_ok=True)
    dest = kb_dir / safe_name
    dest.write_bytes(await file.read())

    # Indexa embeddings em background (não bloqueia o upload)
    with instances_lock:
        inst = dict(request.app.state.instances.get(instance_id, {}))
    embed_key = kb_emb.resolve_embed_key(inst)
    if embed_key:
        file_text = _read_file_text(dest)
        threading.Thread(
            target=kb_emb.index_file,
            args=(instance_id, dest.name, file_text, embed_key),
            daemon=True,
        ).start()

    return {"ok": True, "filename": dest.name, "size": dest.stat().st_size}


@router.delete("/instance/{instance_id}/inbox-knowledge/{filename}")
def delete_inbox_knowledge(instance_id: str, filename: str, request: Request, _: str = Security(require_api_key)):
    instances      = request.app.state.instances
    instances_lock = request.app.state.instances_lock
    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
    require_instance_access(instance_id, request)
    safe_id   = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    target    = INBOX_KB_DIR / safe_id / safe_name
    if not target.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    target.unlink()
    kb_emb.delete_file(instance_id, safe_name)
    return {"ok": True, "filename": safe_name}


# ── Auto-geração de padrões — lógica central (reutilizável) ──────────────────

_MIN_CONVS_FOR_PATTERNS = int(os.getenv("AUTO_PATTERNS_MIN_CONVS", "5"))
_AUTO_PATTERNS_INTERVAL = int(os.getenv("AUTO_PATTERNS_INTERVAL_HOURS", "6")) * 3600

# timestamp do último run por instância (em memória — reseta no restart, ok)
_patterns_last_run: dict = {}


def _generate_patterns_core(instance_id: str, inst: dict, convs: dict, ai: dict) -> dict:
    """
    Lógica central de geração de padrões.
    Retorna dict com {ok, ...} em vez de HTTPException — seguro para chamar em background.
    """
    if not ai.get("api_key"):
        return {"ok": False, "error": "API Key não configurada"}

    knowledge = _load_inbox_knowledge(instance_id)

    def _is_converted(c):
        return (c.get("score") or 0) >= 60 or c.get("stage") == "handoff"

    all_convs     = list(convs.values())
    converted_set = {id(c) for c in all_convs if _is_converted(c)}

    summaries = []
    for conv in all_convs[:30]:
        msgs = conv.get("messages", [])
        if len(msgs) < 2:
            continue
        label   = "CONVERTIDA" if id(conv) in converted_set else "NÃO CONVERTIDA"
        history = "\n".join(
            f"{'LEAD' if m['role']=='user' else 'VENDEDOR'}: {m['content']}"
            for m in msgs[-30:]
        )
        summaries.append(
            f"--- Conversa: {conv.get('name', '?')} | Score: {conv.get('score')} "
            f"| Estágio: {conv.get('stage')} | {label} ---\n{history}"
        )

    if not summaries:
        return {"ok": False, "error": "Sem conversas com mensagens suficientes"}

    n_converted = sum(1 for c in all_convs if _is_converted(c))
    all_text    = "\n\n".join(summaries[:20])

    kb_section = f"\n\n## Base de Conhecimento da Instância\n{knowledge}" if knowledge else ""
    kb_hint    = (" Use também a base de conhecimento disponível no contexto do sistema "
                  "para alinhar os padrões ao produto/serviço oferecido.") if knowledge else ""

    system = (
        "Você é um especialista em vendas e análise de conversas. "
        "Analise o conjunto de conversas — CONVERTIDAS (score alto ou handoff) e NÃO CONVERTIDAS — "
        "e extraia padrões de comunicação eficazes. Priorize as CONVERTIDAS; "
        "use as NÃO CONVERTIDAS para identificar o que evitar. Seja específico e prático."
        + kb_section
    )
    prompt = (
        f"Analise estas {len(summaries)} conversas "
        f"({n_converted} convertidas, {len(summaries) - n_converted} não convertidas):\n\n"
        f"{all_text}\n\n"
        "Com base nessas conversas, crie um documento de 'Padrões de Resposta Eficazes' contendo:\n"
        "1. Frases e abordagens que funcionaram para engajar o lead (das CONVERTIDAS)\n"
        "2. Como responder às objeções mais comuns\n"
        "3. Perguntas que avançaram a conversa\n"
        "4. Sinais de interesse e como responder\n"
        "5. Tom e estilo que converteu\n"
        "6. O que evitar (das NÃO CONVERTIDAS, se houver)\n\n"
        f"Escreva em texto corrido, em português, como guia prático para o atendente. "
        f"Seja direto e use exemplos reais das conversas.{kb_hint}"
    )

    try:
        import openai
        from services.openai_service import create_chat_completion
        is_groq  = ai["provider"] == "groq"
        client   = openai.OpenAI(
            api_key=ai["api_key"],
            timeout=60,
            max_retries=1,
            base_url="https://api.groq.com/openai/v1" if is_groq else None,
        )
        resp = create_chat_completion(
            client, is_groq, tokens=1500,
            model=ai["model"], temperature=0.4,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        patterns_text = resp.choices[0].message.content
    except Exception as e:
        logger.error(f"[AUTO-PATTERNS] Erro ao gerar padrões para {instance_id}: {e}")
        logger.warning("Falha na operação de conversas", exc_info=True)
        return {"ok": False, "error": "Erro interno ao processar a operação."}

    import datetime
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    kb_dir  = INBOX_KB_DIR / safe_id
    kb_dir.mkdir(parents=True, exist_ok=True)

    header = (
        f"# Padrões de Resposta Eficazes — gerado automaticamente\n"
        f"# Data: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"# Baseado em {len(summaries)} conversas ({n_converted} convertidas)\n\n"
    )
    content = header + patterns_text
    dest    = kb_dir / "_auto_patterns.txt"
    dest.write_text(content, encoding="utf-8")

    embed_key = kb_emb.resolve_embed_key(inst)
    if embed_key:
        threading.Thread(
            target=kb_emb.index_file,
            args=(instance_id, dest.name, content, embed_key),
            daemon=True,
        ).start()

    _patterns_last_run[instance_id] = __import__("time").time()
    logger.info(f"[AUTO-PATTERNS] {instance_id}: {len(summaries)} conversas → padrões atualizados.")
    return {"ok": True, "filename": dest.name, "size": dest.stat().st_size,
            "conversas_analisadas": len(summaries), "convertidas": n_converted}


def _auto_patterns_tick(app):
    import time
    try:
        with app.state.instances_lock:
            instance_ids = list(app.state.instances.keys())

        for iid in instance_ids:
            try:
                # Verifica cooldown
                last = _patterns_last_run.get(iid, 0)
                if time.time() - last < _AUTO_PATTERNS_INTERVAL:
                    continue

                with app.state.instances_lock:
                    inst = dict(app.state.instances.get(iid, {}))
                if not inst:
                    continue

                with app.state.conversations_lock:
                    convs = copy.deepcopy(app.state.conversations.get(iid, {}))

                # Mínimo de conversas com 2+ mensagens
                eligible = [c for c in convs.values() if len(c.get("messages", [])) >= 2]
                if len(eligible) < _MIN_CONVS_FOR_PATTERNS:
                    continue

                ai = app.state.resolve_ai_settings(inst)
                if not ai.get("api_key"):
                    continue

                _generate_patterns_core(iid, inst, convs, ai)

            except Exception as exc:
                logger.warning(f"[AUTO-PATTERNS] Erro na instância {iid}: {exc}")

    except Exception as exc:
        logger.warning(f"[AUTO-PATTERNS] Erro no loop: {exc}")


def start_auto_patterns_loop(app):
    """Inicia o ciclo de geração automática de padrões. Chamar no lifespan."""
    from services.scheduler import scheduler
    scheduler.schedule_recurring("auto-patterns", _AUTO_PATTERNS_INTERVAL, lambda: _auto_patterns_tick(app), initial_delay=300)
    logger.info(f"[AUTO-PATTERNS] Ciclo agendado — intervalo: {_AUTO_PATTERNS_INTERVAL // 3600}h, mínimo: {_MIN_CONVS_FOR_PATTERNS} conversas.")


# ── Endpoint manual (mantido para forçar geração quando quiser) ───────────────

@router.post("/instance/{instance_id}/inbox-knowledge/auto-generate")
def auto_generate_patterns(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """Força a geração imediata de padrões (o loop já faz isso automaticamente)."""
    instances_lock     = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock

    with instances_lock:
        if instance_id not in request.app.state.instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(request.app.state.instances[instance_id])
    require_instance_access(instance_id, request)

    ai = request.app.state.resolve_ai_settings(inst)

    with conversations_lock:
        convs = copy.deepcopy(request.app.state.conversations.get(instance_id, {}))

    if not convs:
        raise HTTPException(400, "Nenhuma conversa encontrada para esta instância.")

    result = _generate_patterns_core(instance_id, inst, convs, ai)
    if not result["ok"]:
        raise HTTPException(400, result.get("error", "Erro ao gerar padrões."))
    return result


# ── Base de conhecimento sintetizada pelo agente ─────────────────────────────

def _synthesize_agent_kb_core(instance_id: str, inst: dict, convs: dict, ai: dict) -> dict:
    """
    Lógica central de síntese da memória operacional do agente.
    Retorna dict com {ok, ...} em vez de HTTPException — seguro para chamar em background.
    """
    if not ai.get("api_key"):
        return {"ok": False, "error": "API Key de IA não configurada."}

    # Carrega toda a KB do inbox (qualquer coisa já salva)
    full_kb = _load_inbox_knowledge(instance_id)

    # Prepara resumo das conversas recentes (últimas 20, max 400 chars cada)
    conv_summaries = []
    for phone, conv in list(convs.items())[:20]:
        msgs = conv.get("messages", [])
        if not msgs:
            continue
        score = conv.get("score")
        stage = conv.get("stage", "qualifying")
        snippet = " | ".join(
            f"{'L' if m['role']=='user' else 'A'}: {m['content'][:100]}"
            for m in msgs[-6:]
        )
        conv_summaries.append(f"[score={score}, stage={stage}] {snippet}")

    if not full_kb and not conv_summaries:
        return {"ok": False, "error": "Sem KB nem conversas para sintetizar."}

    kb_section  = f"BASE DE CONHECIMENTO ATUAL:\n{full_kb[:8000]}\n\n" if full_kb else ""
    conv_section = ("AMOSTRA DE CONVERSAS REAIS:\n" + "\n".join(conv_summaries)) if conv_summaries else ""

    system = (
        "Você é o agente SDR deste produto. Sua tarefa é criar sua PRÓPRIA base de conhecimento interna — "
        "um documento que você mesmo usará para sugerir respostas certeiras no WhatsApp.\n\n"
        "Analise a base de conhecimento do produto e as conversas reais. "
        "Destile em um documento prático com:\n"
        "1. Os 5-10 fatos mais importantes sobre o produto que aparecem nas conversas\n"
        "2. As objeções mais comuns e como rebatê-las de forma natural\n"
        "3. Perguntas que avançam o lead (use exemplos reais das conversas)\n"
        "4. Frases que funcionaram / tom que converte\n"
        "5. O que o lead precisa ouvir em cada etapa (interesse → qualificação → decisão)\n\n"
        "Escreva de forma direta, sem introduções. Use exemplos concretos. "
        "Este documento é para você — não para humanos lerem.\n"
        "Máximo 800 palavras. Português."
    )

    prompt = f"{kb_section}{conv_section}\n\nCrie sua base de conhecimento operacional."

    try:
        import openai
        from services.openai_service import create_chat_completion
        is_groq  = ai["provider"] == "groq"
        client   = openai.OpenAI(
            api_key=ai["api_key"],
            timeout=60,
            max_retries=1,
            base_url="https://api.groq.com/openai/v1" if is_groq else None,
        )
        resp = create_chat_completion(
            client, is_groq, tokens=1200,
            model=ai["model"], temperature=0.3,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        synth_text = resp.choices[0].message.content
    except Exception as e:
        logger.error(f"[SYNTHESIZE-KB] Erro para {instance_id}: {e}")
        return {"ok": False, "error": f"Erro ao sintetizar KB: {e}"}

    import datetime
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    kb_dir  = INBOX_KB_DIR / safe_id
    kb_dir.mkdir(parents=True, exist_ok=True)

    header = (
        f"# Memória Operacional do Agente — sintetizada automaticamente\n"
        f"# Data: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"# Conversas analisadas: {len(conv_summaries)} | KB de origem: {'sim' if full_kb else 'não'}\n\n"
    )
    content = header + synth_text
    dest = kb_dir / "_agent_memory.txt"
    dest.write_text(content, encoding="utf-8")

    embed_key = kb_emb.resolve_embed_key(inst)
    if embed_key:
        threading.Thread(
            target=kb_emb.index_file,
            args=(instance_id, dest.name, content, embed_key),
            daemon=True,
        ).start()

    logger.info(f"[SYNTHESIZE-KB] {instance_id}: memória operacional gerada ({dest.stat().st_size} bytes).")
    return {"ok": True, "filename": dest.name, "size": dest.stat().st_size,
            "conversas_analisadas": len(conv_summaries), "kb_usada": bool(full_kb)}


@router.post("/instance/{instance_id}/inbox-knowledge/synthesize")
def synthesize_agent_kb(instance_id: str, request: Request, _: str = Security(require_api_key)):
    """
    O agente lê toda a KB + conversas e gera uma base de conhecimento própria,
    destilada e acionável, salva como _agent_memory.txt na inbox KB.
    """
    instances_lock     = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock

    with instances_lock:
        if instance_id not in request.app.state.instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(request.app.state.instances[instance_id])
    require_instance_access(instance_id, request)

    ai = request.app.state.resolve_ai_settings(inst)
    if not ai["api_key"]:
        raise HTTPException(400, "API Key de IA não configurada.")

    with conversations_lock:
        convs = copy.deepcopy(request.app.state.conversations.get(instance_id, {}))

    result = _synthesize_agent_kb_core(instance_id, inst, convs, ai)
    if not result["ok"]:
        raise HTTPException(400, result.get("error", "Erro ao sintetizar KB."))
    return result


# ── Auto-síntese da memória operacional — loop em background ─────────────────

_MIN_CONVS_FOR_AGENT_MEMORY = int(os.getenv("AUTO_AGENT_MEMORY_MIN_CONVS", "5"))
_AUTO_AGENT_MEMORY_INTERVAL = int(os.getenv("AUTO_AGENT_MEMORY_INTERVAL_HOURS", "6")) * 3600

# timestamp do último run por instância (em memória — reseta no restart, ok)
_agent_memory_last_run: dict = {}


def _auto_agent_memory_tick(app):
    import time
    try:
        with app.state.instances_lock:
            instance_ids = list(app.state.instances.keys())

        for iid in instance_ids:
            try:
                last = _agent_memory_last_run.get(iid, 0)
                if time.time() - last < _AUTO_AGENT_MEMORY_INTERVAL:
                    continue

                with app.state.instances_lock:
                    inst = dict(app.state.instances.get(iid, {}))
                if not inst:
                    continue

                with app.state.conversations_lock:
                    convs = copy.deepcopy(app.state.conversations.get(iid, {}))

                eligible = [c for c in convs.values() if len(c.get("messages", [])) >= 2]
                if len(eligible) < _MIN_CONVS_FOR_AGENT_MEMORY:
                    continue

                ai = app.state.resolve_ai_settings(inst)
                if not ai.get("api_key"):
                    continue

                _synthesize_agent_kb_core(iid, inst, convs, ai)
                _agent_memory_last_run[iid] = time.time()

            except Exception as exc:
                logger.warning(f"[AUTO-AGENT-MEMORY] Erro na instância {iid}: {exc}")

    except Exception as exc:
        logger.warning(f"[AUTO-AGENT-MEMORY] Erro no loop: {exc}")


def start_auto_agent_memory_loop(app):
    """
    Sintetiza a memória operacional do agente (_agent_memory.txt) periodicamente,
    sem precisar clicar no botão manual. Mesmo padrão de start_auto_patterns_loop.
    """
    from services.scheduler import scheduler
    scheduler.schedule_recurring("auto-agent-memory", _AUTO_AGENT_MEMORY_INTERVAL, lambda: _auto_agent_memory_tick(app), initial_delay=300)
    logger.info(f"[AUTO-AGENT-MEMORY] Ciclo agendado — intervalo: {_AUTO_AGENT_MEMORY_INTERVAL // 3600}h, mínimo: {_MIN_CONVS_FOR_AGENT_MEMORY} conversas.")


# ── Agente de sugestões de resposta ──────────────────────────────────────────

@router.post("/conversations/{instance_id}/{phone}/suggest")
def suggest_replies(instance_id: str, phone: str, request: Request, _: str = Security(require_api_key)):
    """Gera 3 sugestões de resposta para a última mensagem do lead."""
    instances      = request.app.state.instances
    conversations  = request.app.state.conversations
    instances_lock = request.app.state.instances_lock
    conversations_lock = request.app.state.conversations_lock

    with instances_lock:
        if instance_id not in instances:
            raise HTTPException(404, f"Instância '{instance_id}' não encontrada.")
        inst = dict(instances[instance_id])
    require_instance_access(instance_id, request)

    ai = request.app.state.resolve_ai_settings(inst)
    if not ai["api_key"]:
        raise HTTPException(400, "API Key de IA não configurada.")

    with conversations_lock:
        conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
        conv     = conversations.get(instance_id, {}).get(conv_key)
        if not conv:
            raise HTTPException(404, "Conversa não encontrada.")
        msgs = copy.deepcopy(conv.get("messages", []))
        lead_name = (conv.get("name") or "").strip()

    if not msgs:
        raise HTTPException(400, "Sem mensagens na conversa.")

    # Nome só é usável se não for o próprio telefone/fallback (evita "Oi 5511999999999")
    lead_first_name = lead_name.split()[0] if lead_name and not lead_name.replace("+", "").isdigit() else ""

    # Busca semântica se disponível; senão, KB completa (o agente decide o que usar)
    last_lead_msgs = [m["content"] for m in msgs[-6:] if m.get("role") == "user"]
    semantic_query = " ".join(last_lead_msgs) if last_lead_msgs else ""

    embed_key = kb_emb.resolve_embed_key(inst)
    knowledge = (
        kb_emb.search(instance_id, semantic_query, embed_key, top_k=8)
        if embed_key and semantic_query
        else _load_inbox_knowledge(instance_id)
    )

    # Histórico completo recente — o agente precisa do contexto real para analisar
    history_text = "\n".join(
        f"{'LEAD' if m['role']=='user' else 'VOCÊ'}: {m['content']}"
        for m in msgs[-15:]
    )

    qual_ctx = inst.get("qualification_prompt", "").strip()

    system = (
        "Você é um SDR experiente. Antes de sugerir qualquer resposta, você ANALISA a conversa "
        "com atenção — a última mensagem do lead é o que importa mais, não o histórico genérico.\n\n"
        "PROCESSO OBRIGATÓRIO:\n"
        "1. Releia a ÚLTIMA mensagem do lead palavra por palavra. O que ele disse, perguntou ou "
        "insinuou, literalmente? Cite mentalmente o trecho antes de responder.\n"
        "2. Identifique: em que etapa ele está, o que essa mensagem específica exige como resposta?\n"
        "3. Consulte o roteiro e a base de conhecimento apenas pelo que for diretamente relevante "
        "para ESSA mensagem — não injete informação genérica de produto que ele não pediu.\n"
        "4. Só então gere as sugestões, cada uma respondendo de fato ao que o lead disse.\n\n"
        "PROIBIDO (torna a sugestão genérica e inútil):\n"
        "- Perguntas que servem pra qualquer conversa, tipo 'Como posso te ajudar?', 'Tem alguma "
        "dúvida?', 'Posso te passar mais informações?', 'Fico à disposição'\n"
        "- Repetir a mesma pergunta que o lead acabou de responder\n"
        "- Ignorar um dado concreto que o lead deu (valor, prazo, objeção, nome de bairro/produto "
        "específico) e responder de forma genérica mesmo assim\n"
        "- Sugestões que dariam pra colar em qualquer outra conversa sem editar nada\n\n"
        "TESTE ANTES DE SUGERIR: se a resposta não muda dependendo do que o lead disse, ela está "
        "errada — refaça.\n\n"
        "FORMATO DAS SUGESTÕES:\n"
        "- 1 a 2 frases cada, direto ao ponto\n"
        "- Tom natural de WhatsApp — como pessoa real, não robô\n"
        "- Sem saudações, sem formalidade, sem 'prezado', 'segue', 'conforme'\n"
        "- Cite ou reaja a algo específico que o lead disse, sempre que possível\n"
        "- Varie a abordagem: 1 que qualifica, 1 que avança, 1 que entrega valor — mas todas "
        "ancoradas na última mensagem do lead\n\n"
        "Retorne JSON com dois campos:\n"
        "{\"analise\": \"o que o lead disse/quer AGORA, especificamente, em 1 linha\", "
        "\"sugestoes\": [\"...\", \"...\", \"...\"]}"
    )
    if lead_first_name:
        system += (
            f"\n\nNOME DO LEAD: {lead_first_name}. Use o primeiro nome dele naturalmente quando fizer "
            "sentido (ex: numa saudação inicial ou pra dar um toque pessoal) — não force em toda frase."
        )
    if qual_ctx:
        system += f"\n\nROTEIRO DO SDR (internalize e aplique cirurgicamente):\n{qual_ctx}"
    if knowledge:
        system += f"\n\nBASE DE CONHECIMENTO (use só o trecho diretamente relevante para o momento):\n{knowledge}"

    ultima_msg_lead = last_lead_msgs[-1] if last_lead_msgs else ""

    prompt = (
        f"Conversa:\n{history_text}\n\n"
        f"ÚLTIMA MENSAGEM DO LEAD (a que você precisa responder de verdade): \"{ultima_msg_lead}\"\n\n"
        "Analise o momento do lead e gere as 3 sugestões, todas respondendo a essa última mensagem."
    )

    try:
        import openai
        from services.openai_service import create_chat_completion
        is_groq = ai["provider"] == "groq"
        client  = openai.OpenAI(
            api_key=ai["api_key"],
            timeout=60,
            max_retries=1,
            base_url="https://api.groq.com/openai/v1" if is_groq else None,
        )
        resp = create_chat_completion(
            client, is_groq, tokens=400,
            model=ai["model"], temperature=0.6,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content

        try:
            data = _parse_llm_json(raw)
        except Exception:
            logger.error(f"[SUGGEST] JSON inválido do modelo — raw: {raw[:500]!r}")
            raise
        sugestoes = data.get("sugestoes") or data.get("suggestions") or []
        analise   = data.get("analise", "")
        return {"sugestoes": sugestoes[:3], "analise": analise}
    except Exception as e:
        logger.error(f"[SUGGEST] Erro: {e}")
        raise HTTPException(500, f"Erro ao gerar sugestões: {e}")
