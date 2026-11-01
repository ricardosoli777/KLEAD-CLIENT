"""
Router de roleta de leads (assignments) — /assignments/*
Extraído de main.py em 2026-06-12.

`instances` é acessado via request.app.state.instances (exposto no startup de main.py).

Todo o estado de assignment_service é particionado por tenant (dono das
instancias) — cada admin/superadmin so ve e opera na propria fila/fila de
distribuicao, nunca a de outro. Ver tenant_key_for_user() em
services/assignment_service.py.
"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

import services.assignment_service as assignment_svc
import services.crm_service as crm_svc
from services.auth_service import require_manager, require_feature

router = APIRouter(tags=["assignments"])


def _tenant_key(request: Request) -> str:
    # Roleta é feature bloqueada no plano Lite: require_manager valida o cargo,
    # require_feature valida o plano (superadmin passa; time de cliente lite não).
    user = require_manager(request)
    require_feature(request, "roleta")
    return assignment_svc.tenant_key_for_user(user)


# ── Modelos ───────────────────────────────────────────────────────────────────

class AssignmentSettingsPayload(BaseModel):
    enabled: bool | None = None
    mode: str | None = None


class AssignmentAttendantPayload(BaseModel):
    active: bool | None = None
    status: str | None = None
    weight: int | None = None
    order: int | None = None


class AssignmentReorderPayload(BaseModel):
    emails: list[str]


class AssignmentDistributePayload(BaseModel):
    limit: int | None = None


class AssignmentRulePayload(BaseModel):
    id: str = ""
    name: str = ""
    enabled: bool = True
    origem: str = ""
    campanha: str = ""
    instancia_id: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/assignments/settings")
def assignment_settings(request: Request):
    tenant_key = _tenant_key(request)
    return assignment_svc.get_settings(tenant_key)


@router.patch("/assignments/settings")
def assignment_update_settings(payload: AssignmentSettingsPayload, request: Request):
    tenant_key = _tenant_key(request)
    return assignment_svc.update_settings(tenant_key, enabled=payload.enabled, mode=payload.mode)


@router.get("/assignments/attendants")
def assignment_list_attendants(request: Request):
    tenant_key = _tenant_key(request)
    with request.app.state.instances_lock:
        instances_snap = dict(request.app.state.instances)
    return assignment_svc.sync_instances(tenant_key, instances_snap)


@router.patch("/assignments/attendants/{email}")
def assignment_update_attendant(email: str, payload: AssignmentAttendantPayload, request: Request):
    tenant_key = _tenant_key(request)
    with request.app.state.instances_lock:
        instances_snap = dict(request.app.state.instances)
    assignment_svc.sync_instances(tenant_key, instances_snap)
    attendant = assignment_svc.update_attendant(
        tenant_key,
        email=email,
        active=payload.active,
        status=payload.status,
        weight=payload.weight,
        order=payload.order,
    )
    if not attendant:
        raise HTTPException(404, "Atendente não encontrado")
    return attendant


@router.post("/assignments/reorder")
def assignment_reorder(payload: AssignmentReorderPayload, request: Request):
    tenant_key = _tenant_key(request)
    with request.app.state.instances_lock:
        instances_snap = dict(request.app.state.instances)
    assignment_svc.sync_instances(tenant_key, instances_snap)
    return assignment_svc.reorder_attendants(tenant_key, payload.emails)


@router.post("/assignments/generate-queue")
def assignment_generate_queue(request: Request):
    tenant_key = _tenant_key(request)
    with request.app.state.instances_lock:
        instances_snap = dict(request.app.state.instances)
    assignment_svc.sync_instances(tenant_key, instances_snap)
    return assignment_svc.generate_queue(tenant_key)


@router.post("/assignments/shuffle-queue")
def assignment_shuffle_queue(request: Request):
    tenant_key = _tenant_key(request)
    with request.app.state.instances_lock:
        instances_snap = dict(request.app.state.instances)
    assignment_svc.sync_instances(tenant_key, instances_snap)
    return assignment_svc.shuffle_queue(tenant_key)


@router.post("/assignments/start")
def assignment_start_distribution(request: Request):
    tenant_key = _tenant_key(request)
    return assignment_svc.start_distribution(tenant_key)


@router.post("/assignments/stop")
def assignment_stop_distribution(request: Request):
    tenant_key = _tenant_key(request)
    return assignment_svc.stop_distribution(tenant_key)


@router.get("/assignments/queue")
def assignment_queue(request: Request):
    tenant_key = _tenant_key(request)
    return {"queue": assignment_svc.preview_queue(tenant_key)}


@router.get("/assignments/next")
def assignment_preview_next(request: Request):
    tenant_key = _tenant_key(request)
    return {"next": assignment_svc.preview_next_attendant(tenant_key)}


@router.get("/assignments/unassigned-count")
def assignment_unassigned_count(request: Request):
    tenant_key = _tenant_key(request)
    own_ids = assignment_svc.instance_ids_for_tenant(tenant_key)
    count = sum(1 for lead in crm_svc.get_unassigned_leads() if lead.get("instancia_id") in own_ids)
    return {"count": count}


@router.post("/assignments/distribute-unassigned")
def assignment_distribute_unassigned(payload: AssignmentDistributePayload, request: Request):
    tenant_key = _tenant_key(request)
    if not assignment_svc.preview_queue(tenant_key):
        raise HTTPException(400, "Gere a fila antes de distribuir leads.")

    own_ids = assignment_svc.instance_ids_for_tenant(tenant_key)
    leads = [lead for lead in crm_svc.get_unassigned_leads() if lead.get("instancia_id") in own_ids]
    if payload.limit is not None:
        leads = leads[:max(0, payload.limit)]

    distributed = []
    for lead in leads:
        result = assignment_svc.assign_next(tenant_key, lead_id=lead.get("id", ""), reason="manual_distribution")
        attendant = result.get("assigned")
        if not attendant:
            break
        updated = crm_svc.assign_lead(
            lead_id=lead.get("id", ""),
            assigned_to=attendant.get("email", ""),
            assigned_to_name=attendant.get("nome", ""),
            assigned_at=attendant.get("last_assigned_at", ""),
            instancia_id=attendant.get("instance_id", ""),
        )
        if updated:
            distributed.append({"lead": updated, "attendant": attendant})

    remaining = sum(1 for lead in crm_svc.get_unassigned_leads() if lead.get("instancia_id") in own_ids)
    return {
        "ok": True,
        "total": len(distributed),
        "remaining": remaining,
        "distributed": distributed,
        "next": assignment_svc.preview_next_attendant(tenant_key),
    }


@router.get("/assignments/rules")
def assignment_list_rules(request: Request):
    tenant_key = _tenant_key(request)
    return {"rules": assignment_svc.list_rules(tenant_key)}


@router.post("/assignments/rules")
def assignment_save_rule(payload: AssignmentRulePayload, request: Request):
    tenant_key = _tenant_key(request)
    rule = assignment_svc.upsert_rule(tenant_key, payload.dict())
    return rule


@router.delete("/assignments/rules/{rule_id}")
def assignment_delete_rule(rule_id: str, request: Request):
    tenant_key = _tenant_key(request)
    if not assignment_svc.delete_rule(tenant_key, rule_id):
        raise HTTPException(404, "Regra nao encontrada")
    return {"ok": True}


@router.post("/assignments/retroactive-assign")
def assignment_retroactive_assign(request: Request):
    """Atribui leads sem responsável ao atendente da instância correspondente (dentro do proprio tenant)."""
    tenant_key = _tenant_key(request)
    from datetime import datetime, timezone

    attendants = assignment_svc.list_attendants(tenant_key)
    attendant_by_instance = {a["instance_id"]: a for a in attendants if a.get("instance_id")}

    # Nao precisa filtrar leads aqui: attendant_by_instance ja vem de
    # list_attendants(tenant_key), entao so instancias deste tenant tem
    # correspondencia — leads de outros tenants sao pulados no "skipped".
    leads = crm_svc.get_leads()
    assigned = 0
    skipped = 0

    for lead in leads:
        if lead.get("assigned_to"):
            skipped += 1
            continue
        instance_id = lead.get("instancia_id", "")
        attendant = attendant_by_instance.get(instance_id)
        if not attendant:
            skipped += 1
            continue
        now = datetime.now(timezone.utc).isoformat()
        crm_svc.assign_lead(
            lead_id=lead["id"],
            assigned_to=attendant["email"],
            assigned_to_name=attendant["nome"],
            assigned_at=now,
            instancia_id=instance_id,
        )
        assigned += 1

    return {"ok": True, "assigned": assigned, "skipped": skipped}


class AssignConversationPayload(BaseModel):
    instance_id: str
    phone: str
    name: str = ""
    assigned_to: str
    assigned_to_name: str = ""


@router.post("/assignments/assign-conversation")
def assignment_assign_conversation(payload: AssignConversationPayload, request: Request):
    """Atribui (ou reatribui) um lead de uma conversa a um atendente específico."""
    import services.auth_service as auth_svc
    user = require_manager(request)
    require_feature(request, "roleta")  # feature bloqueada no plano Lite
    if not auth_svc.can_access_instance(user, payload.instance_id):
        raise HTTPException(403, "Você não tem acesso a esta instância.")
    tenant_key = assignment_svc.tenant_key_for_user(user)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    attendants = assignment_svc.list_attendants(tenant_key)
    attendant = next((a for a in attendants if a["email"] == payload.assigned_to), None)
    assigned_to_name = attendant["nome"] if attendant else payload.assigned_to_name or payload.assigned_to

    lead = crm_svc.upsert_lead(
        telefone=payload.phone,
        nome=payload.name or payload.phone,
        origem="inbox",
        instancia_id=payload.instance_id,
        assigned_to=payload.assigned_to,
        assigned_to_name=assigned_to_name,
        assigned_at=now,
        assignment_status="assigned",
    )
    return lead
