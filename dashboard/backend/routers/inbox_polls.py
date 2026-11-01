"""Modelos reutilizáveis e envio de enquetes no Inbox."""

import logging
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, Field

from core.security import require_api_key
from services import inbox_poll_pg_service as polls
from services import inbox_poll_results_service as poll_results
from services.assignment_service import tenant_key_for_user
from services.auth_service import get_auth_user, require_instance_access
from services.outbound_routing import has_evolution_outbound, preferred_outbound_channel, send_outbound_poll


router = APIRouter(prefix="/inbox-polls", tags=["inbox-polls"], dependencies=[Security(require_api_key)])
logger = logging.getLogger("klead.inbox_polls")


class PollQuestionPayload(BaseModel):
    question: str = Field(..., max_length=255)
    options: list[str] = Field(..., min_length=2, max_length=12)


class PollCreatePayload(BaseModel):
    name: str = Field(..., max_length=255)
    question: str | None = Field(None, max_length=255)
    options: list[str] | None = Field(None, min_length=2, max_length=12)
    questions: list[PollQuestionPayload] | None = Field(None, min_length=1)
    allow_multiple_answers: bool = False
    is_default: bool = False


class PollUpdatePayload(BaseModel):
    name: str | None = Field(None, max_length=255)
    question: str | None = Field(None, max_length=255)
    options: list[str] | None = Field(None, min_length=2, max_length=12)
    questions: list[PollQuestionPayload] | None = Field(None, min_length=1)
    allow_multiple_answers: bool | None = None
    is_default: bool | None = None


class PollSendPayload(BaseModel):
    template_id: UUID
    request_id: UUID


def _tenant(request: Request) -> tuple[str, str]:
    user, _ = get_auth_user(request)
    owner_key = tenant_key_for_user(user)
    if not owner_key:
        raise HTTPException(403, "Usuário sem tenant.")
    return owner_key, user.get("email", "")


def _ready():
    from services import crm_pg_service
    if not crm_pg_service.pg_configured():
        raise HTTPException(503, "Persistência de enquetes indisponível.")


def _db_error(exc: Exception):
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    logger.exception("Falha na persistência de enquetes")
    raise HTTPException(503, "Persistência de enquetes indisponível.") from exc


@router.get("/templates")
def list_poll_templates(request: Request):
    owner_key, _ = _tenant(request)
    _ready()
    try:
        return polls.list_templates(owner_key)
    except Exception as exc:
        _db_error(exc)


@router.post("/templates")
def create_poll_template(payload: PollCreatePayload, request: Request):
    owner_key, email = _tenant(request)
    _ready()
    try:
        questions = [item.model_dump() for item in payload.questions] if payload.questions is not None else None
        first = questions[0] if questions else None
        question = payload.question or (first["question"] if first else "")
        options = payload.options or (first["options"] if first else [])
        return polls.create_template(owner_key, payload.name, question, options,
                                     payload.allow_multiple_answers, payload.is_default, email, questions)
    except Exception as exc:
        _db_error(exc)


@router.patch("/templates/{template_id}")
def update_poll_template(template_id: UUID, payload: PollUpdatePayload, request: Request):
    owner_key, _ = _tenant(request)
    _ready()
    try:
        result = polls.update_template(str(template_id), owner_key, payload.model_dump(exclude_none=True))
    except Exception as exc:
        _db_error(exc)
    if not result:
        raise HTTPException(404, "Enquete não encontrada.")
    return result


@router.post("/templates/{template_id}/duplicate")
def duplicate_poll_template(template_id: UUID, request: Request):
    owner_key, email = _tenant(request)
    _ready()
    try:
        result = polls.duplicate_template(str(template_id), owner_key, email)
    except Exception as exc:
        _db_error(exc)
    if not result:
        raise HTTPException(404, "Enquete não encontrada.")
    return result


@router.delete("/templates/{template_id}")
def delete_poll_template(template_id: UUID, request: Request):
    owner_key, _ = _tenant(request)
    _ready()
    try:
        changed = polls.deactivate_template(str(template_id), owner_key)
    except Exception as exc:
        _db_error(exc)
    if not changed:
        raise HTTPException(404, "Enquete não encontrada.")
    return {"ok": True}


@router.get("/{instance_id}/capabilities")
def inbox_poll_capabilities(instance_id: str, request: Request):
    require_instance_access(instance_id, request)
    with request.app.state.instances_lock:
        inst = dict(request.app.state.instances.get(instance_id) or {})
    if not inst:
        raise HTTPException(404, "Instância não encontrada.")
    channel = preferred_outbound_channel(inst, instance_id)
    return {"channel": channel, "native_poll": channel == "evolution" and has_evolution_outbound(inst)}


def _record_sent_poll(request: Request, instance_id: str, conv_key: str, sent: dict) -> None:
    """Reconstitui o histórico em uma repetição segura, sem reenviar ao WhatsApp."""
    added = False
    with request.app.state.conversations_lock:
        conv = request.app.state.conversations.get(instance_id, {}).get(conv_key)
        if conv is None:
            raise RuntimeError("Conversa indisponível após o envio.")
        if not any(m.get("poll_send_id") == sent["id"] for m in conv.get("messages", [])):
            conv["ai_paused"] = True
            conv["unread"] = False
            request.app.state.clear_needs_human(conv)
            conv.pop("ai_resume_after", None)
            conv["messages"].append({
                "role": "human", "content": sent["question_snapshot"],
                "timestamp": request.app.state.now_iso(), "is_poll": True,
                "poll_send_id": sent["id"], "poll_options": sent["options_snapshot_json"],
                "poll_allow_multiple_answers": sent["allow_multiple_snapshot"],
                "poll_question_index": sent.get("question_index", 1),
                "poll_question_count": sent.get("question_count", 1),
                "poll_batch_id": sent.get("batch_id"),
                "provider_message_id": sent["provider_message_id"],
            })
            conv["last_activity"] = request.app.state.now_iso()
            added = True
    if added:
        request.app.state.save_conversation(instance_id, conv_key)
        request.app.state.sse_emit(instance_id, "new_message", {"phone": conv_key, "role": "human"})


@router.post("/{instance_id}/{phone}/send")
def send_inbox_poll(instance_id: str, phone: str, payload: PollSendPayload, request: Request):
    instances = request.app.state.instances
    with request.app.state.instances_lock:
        inst = dict(instances.get(instance_id) or {})
    if not inst:
        raise HTTPException(404, "Instância não encontrada.")
    require_instance_access(instance_id, request)
    owner_key, email = _tenant(request)
    _ready()
    channel = preferred_outbound_channel(inst, instance_id)
    if channel != "evolution" or not has_evolution_outbound(inst):
        raise HTTPException(400, "Enquete nativa indisponível para o canal desta instância.")
    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    with request.app.state.conversations_lock:
        if not request.app.state.conversations.get(instance_id, {}).get(conv_key):
            raise HTTPException(404, "Conversa não encontrada.")
    try:
        template = polls.get_template(str(payload.template_id), owner_key)
    except Exception as exc:
        _db_error(exc)
    if not template:
        raise HTTPException(404, "Enquete não encontrada.")
    try:
        batch, batch_created = polls.create_batch(owner_key, template, instance_id, conv_key,
                                                 str(payload.request_id), email)
    except Exception as exc:
        _db_error(exc)
    child_request_id = str(uuid5(payload.request_id, "poll"))
    if not batch_created:
        try:
            legacy = polls.has_legacy_batch_sends(str(batch["id"]), owner_key, child_request_id)
        except Exception as exc:
            _db_error(exc)
        if legacy:
            raise HTTPException(409, "Esta sequência foi criada no formato anterior. Crie um novo envio.")
    if not batch_created and batch["status"] == "sending":
        raise HTTPException(409, "Esta sequência já está sendo enviada. Confira o histórico antes de tentar novamente.")
    questions = batch["questions_snapshot_json"]
    try:
        poll = polls.compose_poll(template["name"], questions)
    except ValueError as exc:
        _db_error(exc)
    template_snapshot = {**template, "allow_multiple_answers": batch["allow_multiple_snapshot"]}
    try:
        sent, created = polls.create_send(
            owner_key, template_snapshot, instance_id, conv_key, channel, child_request_id, email,
            question=poll, batch_id=str(batch["id"]),
        )
    except Exception as exc:
        _db_error(exc)
    if not created and (sent.get("batch_id") != str(batch["id"])
            or sent.get("instance_id") != instance_id or sent.get("conversation_key") != conv_key
            or sent.get("template_id") != str(payload.template_id)):
        raise HTTPException(409, "Identificador de envio já utilizado em outra enquete ou conversa.")
    already_sent = not created and sent["status"] == "sent"
    if not already_sent:
        if not created:
            if sent["status"] == "failed":
                sent = polls.retry_failed_send(sent["id"], owner_key)
                if not sent:
                    raise HTTPException(409, "Esta enquete já está sendo retomada. Atualize o histórico antes de tentar novamente.")
            else:
                raise HTTPException(409, "Este envio está em andamento. Verifique o histórico antes de tentar novamente.")
        try:
            ok, detail, _, message_id = send_outbound_poll(
                inst, instance_id, conv_key, sent["question_snapshot"], sent["options_snapshot_json"],
                max_answers=len(sent["options_snapshot_json"]) if sent["allow_multiple_snapshot"] else 1,
            )
            if not ok:
                polls.mark_send(sent["id"], owner_key, "failed", error_detail=detail)
                polls.mark_batch(str(batch["id"]), owner_key, "failed")
                raise HTTPException(502, f"Falha ao enviar enquete: {detail}")
            sent = polls.mark_send(sent["id"], owner_key, "sent", provider_message_id=message_id)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Falha no envio da enquete %s", sent["id"])
            polls.mark_batch(str(batch["id"]), owner_key, "failed")
            raise HTTPException(503, "O estado da enquete precisa ser conferido antes de reenviar.") from exc
        if not sent:
            polls.mark_batch(str(batch["id"]), owner_key, "failed")
            raise HTTPException(503, "O histórico da enquete não foi confirmado.")
    try:
        _record_sent_poll(request, instance_id, conv_key, sent)
    except Exception as exc:
        logger.exception("Enquete enviada, mas falhou o histórico %s", sent["id"])
        polls.mark_batch(str(batch["id"]), owner_key, "partial")
        raise HTTPException(503, "Enquete enviada, mas o histórico está indisponível. Não reenvie.") from exc

    try:
        polls.mark_batch(str(batch["id"]), owner_key, "sent")
    except Exception as exc:
        logger.exception("Enquete enviada, mas falhou a confirmação do grupo %s", batch["id"])
        raise HTTPException(503, "A enquete foi enviada, mas a confirmação não foi registrada.") from exc
    return {"ok": True, "sends": [sent], "already_sent": already_sent}


@router.get("/{instance_id}/{phone}/sends/{send_id}/results")
def get_inbox_poll_results(instance_id: str, phone: str, send_id: UUID, request: Request):
    require_instance_access(instance_id, request)
    owner_key, _ = _tenant(request)
    _ready()
    with request.app.state.instances_lock:
        inst = dict(request.app.state.instances.get(instance_id) or {})
    if not inst:
        raise HTTPException(404, "Instância não encontrada.")
    conv_key = request.app.state.resolve_conversation_key(instance_id, phone)
    with request.app.state.conversations_lock:
        if not request.app.state.conversations.get(instance_id, {}).get(conv_key):
            raise HTTPException(404, "Conversa não encontrada.")
    try:
        sent = polls.get_send(str(send_id), owner_key, instance_id, conv_key)
    except Exception as exc:
        _db_error(exc)
    if not sent:
        raise HTTPException(404, "Enquete enviada não encontrada.")
    if sent["provider"] != "evolution" or not sent["provider_message_id"] or not has_evolution_outbound(inst):
        raise HTTPException(400, "Resultados indisponíveis para esta enquete.")
    try:
        result = poll_results.fetch_results(inst["api_url"], inst["api_key"], sent["provider_message_id"])
        if result is not None:
            votes = poll_results.parse_votes(result, sent["options_snapshot_json"])
            polls.save_votes(sent["id"], votes)
        return {"votes": polls.list_votes(sent["id"]), "provider_available": result is not None}
    except Exception as exc:
        logger.warning("Falha na consulta de resultados da enquete %s: %s", send_id, type(exc).__name__)
        raise HTTPException(503, "Não foi possível consultar os resultados da enquete agora.") from exc
