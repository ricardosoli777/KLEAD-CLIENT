"""
routers/admin.py — Painel administrativo multi-cliente (só superadmin).

Visão agregada de uso por instância: leads, atividade recente, IA on/off.
Status de conexão do WhatsApp fica de fora da carga automática (é uma
chamada de rede por instância) — endpoint separado, sob demanda.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, Security

from core.security import require_api_key
from core.state import instances, conversations, instances_lock, conversations_lock
from services.auth_service import require_superadmin, list_users
import services.crm_service as crm_svc
import services.audit_log_service as audit_log_svc


def _owner_by_instance() -> dict[str, str]:
    """
    Mapeia instance_id -> nome/email do dono (admin ou superadmin dono da
    instancia). Cada instancia pertence a um unico admin/superadmin — quem
    tem acesso via gerente/sdr nao conta como dono, so quem administra.
    """
    owners: dict[str, str] = {}
    for user in list_users():
        if user.get("funcao") not in ("superadmin", "admin"):
            continue
        for iid in (user.get("allowed_instances") or []):
            if iid not in owners:
                owners[iid] = user.get("nome") or user.get("email", "")
    return owners

router = APIRouter(tags=["admin"])


@router.get("/admin/overview", dependencies=[Security(require_api_key)])
def admin_overview(request: Request):
    require_superadmin(request)

    week_ago_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    with instances_lock:
        inst_snapshot = {iid: dict(inst) for iid, inst in instances.items()}
    with conversations_lock:
        conv_snapshot = {iid: dict(convs) for iid, convs in conversations.items()}

    leads_by_instance: dict[str, int] = {}
    for lead in crm_svc.get_leads():
        iid = lead.get("instancia_id", "")
        leads_by_instance[iid] = leads_by_instance.get(iid, 0) + 1

    owners = _owner_by_instance()

    overview = []
    for iid, inst in inst_snapshot.items():
        convs = conv_snapshot.get(iid, {})
        last_activity = max((c.get("last_activity", "") for c in convs.values()), default="")
        messages_7d = sum(
            1
            for c in convs.values()
            for m in c.get("messages", [])
            if (m.get("timestamp") or "") >= week_ago_iso
        )
        overview.append({
            "instance_id": iid,
            "owner": owners.get(iid, ""),
            "provider": inst.get("provider", "evolution"),
            "inbound_ai_enabled": inst.get("inbound_ai_enabled", True),
            "lead_count": leads_by_instance.get(iid, 0),
            "conversation_count": len(convs),
            "messages_7d": messages_7d,
            "last_activity": last_activity,
        })

    overview.sort(key=lambda x: x["last_activity"], reverse=True)
    return {
        "instances": overview,
        "totals": {
            "instance_count": len(overview),
            "lead_count": sum(o["lead_count"] for o in overview),
            "messages_7d": sum(o["messages_7d"] for o in overview),
        },
    }


@router.get("/admin/access-log", dependencies=[Security(require_api_key)])
def admin_access_log(request: Request, instancia_id: str = "", limit: int = 100):
    """Histórico de quando o superadmin acessou dados de cada cliente — transparência/auditoria."""
    require_superadmin(request)
    return {"entries": audit_log_svc.get_recent_access(instancia_id=instancia_id, limit=min(limit, 500))}


@router.get("/admin/audit", dependencies=[Security(require_api_key)])
def admin_audit(
    request: Request,
    actor_email: str = "",
    action: str = "",
    target_id: str = "",
    instancia_id: str = "",
    limit: int = 100,
):
    """
    Trilha de AÇÕES: quem apagou, mudou ou criou o quê. Complementa o
    /admin/access-log (que registra só visualizações).

    Eventos de exclusão trazem o snapshot do objeto em `meta` — é daí que sai a
    restauração de um lead/usuário apagado sem querer.
    """
    require_superadmin(request)
    return {
        "entries": audit_log_svc.get_events(
            actor_email=actor_email,
            action=action,
            target_id=target_id,
            instancia_id=instancia_id,
            limit=min(limit, 500),
        )
    }
