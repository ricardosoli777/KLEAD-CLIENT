"""
Assignment Service - KLEAD

Mantem a fila de atendentes para distribuicao de leads, particionada por
tenant (dono das instancias) — cada admin/superadmin tem sua propria fila,
fila, historico e regras, isolados dos demais. Gerente/sdr operam dentro
do tenant do admin que os convidou.

No modo automatico, a atribuicao so acontece depois que a fila foi gerada e
a distribuicao foi iniciada.
"""

import json
import logging
import os
import random
import threading
from datetime import datetime, timezone
from pathlib import Path

from services import settings_pg_service

logger = logging.getLogger("klead.assignment")
_lock = threading.Lock()

# Bucket de retencao para itens cujo dono nao foi possivel resolver na
# migracao (instancia ja deletada, usuario orfao) — nunca exposto a nenhum
# usuario real, mantido so para nao perder dado silenciosamente.
_ORPHAN_TENANT_KEY = "_orphan"


def _default_tenant_state() -> dict:
    return {
        "enabled": False,
        "mode": "manual",
        "pointer": 0,
        "queue": [],
        "queue_generated_at": "",
        "attendants": [],
        "history": [],
        "rules": [],
    }


_state: dict = {"tenants": {}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save():
    """Persiste assignments no Postgres — única fonte de verdade."""
    if not settings_pg_service.pg_configured():
        logger.warning("[Assignment] Postgres não configurado — estado NÃO persistido (modo dev/teste).")
        return
    try:
        ok, detail = settings_pg_service.save_setting("assignments", _state)
        if not ok:
            logger.warning(f"[Assignment] Falha ao sincronizar com Postgres: {detail}")
    except Exception as exc:
        logger.warning(f"[Assignment] Erro inesperado ao sincronizar: {exc}")


def _normalize_tenant_state(raw: dict) -> dict:
    base = _default_tenant_state()
    base.update(raw if isinstance(raw, dict) else {})
    base["attendants"] = base.get("attendants") or []
    base["history"] = base.get("history") or []
    base["queue"] = base.get("queue") or []
    base["rules"] = base.get("rules") or []
    base["mode"] = base.get("mode") if base.get("mode") in ("automatic", "manual") else "manual"
    base["pointer"] = max(0, int(base.get("pointer", 0) or 0))
    return base


def _owner_email_by_instance() -> dict[str, str]:
    """Mapeia instance_id -> email do admin/superadmin dono (mesma logica de routers/admin.py)."""
    import services.auth_service as auth_svc
    owners: dict[str, str] = {}
    for user in auth_svc.list_users():
        if user.get("funcao") not in ("superadmin", "admin"):
            continue
        for iid in (user.get("allowed_instances") or []):
            if iid not in owners:
                owners[iid] = user.get("email", "")
    return owners


def instance_ids_for_tenant(tenant_key: str) -> set[str]:
    """Retorna o conjunto de instance_id pertencentes a este tenant (dono)."""
    owners = _owner_email_by_instance()
    return {iid for iid, owner in owners.items() if owner == tenant_key}


def auto_assign_tenant_key_for_instance(instance_id: str) -> str:
    """
    Retorna o tenant_key do dono da instância SE a roleta automática estiver
    ativa para ele (enabled + modo automatic + fila com atendentes), ou ""
    caso contrário. Usado por fluxos sem request/usuário (ex: processamento
    de webhook em background) para decidir se um lead recém-chegado deve
    pular a espera manual no InboxSDR e ir direto pra fila de atribuição.
    """
    owners = _owner_email_by_instance()
    tenant_key = owners.get(instance_id, "")
    if not tenant_key or not should_auto_assign(tenant_key):
        return ""
    return tenant_key


def tenant_key_for_user(user: dict) -> str:
    """
    Resolve a chave de particionamento (tenant) para o usuario autenticado.
    Admin/superadmin sao donos diretos (chave = o proprio email). Gerente
    opera dentro do tenant do admin que o convidou — resolvido pelo dono de
    alguma instancia que ele tenha acesso.
    """
    funcao = user.get("funcao", "")
    email = user.get("email", "")
    if funcao in ("superadmin", "admin"):
        return email
    allowed = user.get("allowed_instances") or []
    if allowed:
        owners = _owner_email_by_instance()
        for iid in allowed:
            owner = owners.get(iid)
            if owner:
                return owner
    return email  # fallback: usuario sem instancia atribuida ainda


def _tenant_unlocked(tenant_key: str) -> dict:
    """Retorna (criando se necessario) o estado do tenant. Chamar sempre com _lock."""
    tenants = _state.setdefault("tenants", {})
    if tenant_key not in tenants:
        tenants[tenant_key] = _default_tenant_state()
    return tenants[tenant_key]


def _migrate_legacy_state(raw: dict) -> dict:
    """
    Converte o formato antigo (um unico blob global, sem particionamento por
    tenant) para o novo formato particionado. So roda uma vez — depois que o
    dado e salvo no formato novo, "tenants" ja existe e essa funcao nao e
    mais chamada. Reset deliberado de enabled/mode/queue/pointer por
    seguranca: o valor antigo era compartilhado entre tenants misturados,
    entao nenhum tenant migrado herda fila/distribuicao ativa — precisa
    gerar a fila de novo apos o upgrade. Attendants, historico e regras sao
    preservados integralmente, so redistribuidos por dono.
    """
    owners = _owner_email_by_instance()
    tenants: dict[str, dict] = {}

    def _bucket(owner_key: str) -> dict:
        if owner_key not in tenants:
            tenants[owner_key] = _default_tenant_state()
        return tenants[owner_key]

    for attendant in (raw.get("attendants") or []):
        instance_id = attendant.get("instance_id") or attendant.get("email", "")
        owner = owners.get(instance_id, _ORPHAN_TENANT_KEY)
        _bucket(owner)["attendants"].append(attendant)

    for rule in (raw.get("rules") or []):
        instance_id = rule.get("instancia_id", "")
        owner = owners.get(instance_id, _ORPHAN_TENANT_KEY) if instance_id else _ORPHAN_TENANT_KEY
        _bucket(owner)["rules"].append(rule)

    for item in (raw.get("history") or []):
        instance_id = item.get("to_instance_id") or item.get("instance_id", "")
        owner = owners.get(instance_id, _ORPHAN_TENANT_KEY) if instance_id else _ORPHAN_TENANT_KEY
        _bucket(owner)["history"].append(item)

    logger.info(
        f"[Assignment] Migracao para estado particionado por tenant: "
        f"{len(tenants)} tenant(s) — {list(tenants.keys())}"
    )
    return {"tenants": {key: _normalize_tenant_state(val) for key, val in tenants.items()}}


def load_assignments():
    """
    Carrega assignments do Postgres — única fonte de verdade.
    Fail-fast: PG configurado mas indisponível aborta o startup (subir vazio
    faria o próximo _save() apagar o estado real). Chave ausente = instalação nova.
    """
    global _state
    raw: dict | None = None
    if not settings_pg_service.pg_configured():
        logger.warning("[Assignment] Postgres não configurado — estado vazio (modo dev/teste).")
    else:
        ok, data = settings_pg_service.load_setting("assignments")
        if ok and isinstance(data, dict):
            raw = data
        elif not ok and "não encontrada" not in str(data):
            raise RuntimeError(f"[Assignment] Falha ao carregar do Postgres — abortando: {data}")

    if not isinstance(raw, dict):
        _state = {"tenants": {}}
        return

    if "tenants" in raw and isinstance(raw["tenants"], dict):
        _state = {
            "tenants": {
                key: _normalize_tenant_state(val)
                for key, val in raw["tenants"].items()
            }
        }
    else:
        # Formato antigo (blob global) — migra uma unica vez.
        _state = _migrate_legacy_state(raw)
        _save()


def _settings_unlocked(tenant: dict) -> dict:
    return {
        "enabled": bool(tenant.get("enabled", False)),
        "mode": tenant.get("mode", "manual"),
        "pointer": int(tenant.get("pointer", 0) or 0),
        "queue_generated_at": tenant.get("queue_generated_at", ""),
        "queue_count": len(_queue_attendants_unlocked(tenant)),
    }


def get_settings(tenant_key: str) -> dict:
    with _lock:
        return _settings_unlocked(_tenant_unlocked(tenant_key))


def update_settings(tenant_key: str, enabled: bool | None = None, mode: str | None = None) -> dict:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        if enabled is not None:
            tenant["enabled"] = bool(enabled) and bool(_queue_attendants_unlocked(tenant))
        if mode is not None and mode in ("automatic", "manual"):
            tenant["mode"] = mode
        _save()
        return _settings_unlocked(tenant)


def _normalize_attendant(attendant: dict) -> dict:
    instance_id = attendant.get("instance_id", "") or attendant.get("email", "")
    return {
        "instance_id": instance_id,
        "user_id": attendant.get("user_id", ""),
        "email": attendant.get("email", "") or instance_id,
        "nome": attendant.get("nome") or attendant.get("responsavel") or instance_id,
        "funcao": attendant.get("funcao", "") or "instancia",
        "active": bool(attendant.get("active", False)),
        "status": attendant.get("status") or "available",
        "weight": max(1, int(attendant.get("weight", 1) or 1)),
        "order": int(attendant.get("order", 0) or 0),
        "last_assigned_at": attendant.get("last_assigned_at", ""),
        "total_assigned": int(attendant.get("total_assigned", 0) or 0),
        "channel_status": attendant.get("channel_status", ""),
        "evo_instance_name": attendant.get("evo_instance_name", ""),
    }


def _normalize_rule(rule: dict) -> dict:
    rule_id = str(rule.get("id") or "").strip()
    name = str(rule.get("name") or "").strip()
    origem = str(rule.get("origem") or "").strip()
    campanha = str(rule.get("campanha") or "").strip()
    instancia_id = str(rule.get("instancia_id") or "").strip()
    return {
        "id": rule_id or f"rule-{rule.get('_seq', 1)}",
        "name": name or campanha or origem or "Regra de distribuicao",
        "enabled": bool(rule.get("enabled", True)),
        "origem": origem,
        "campanha": campanha,
        "instancia_id": instancia_id,
        "created_at": rule.get("created_at", ""),
        "updated_at": rule.get("updated_at", ""),
    }


def _rule_has_criteria(rule: dict) -> bool:
    return bool(rule.get("origem") or rule.get("campanha") or rule.get("instancia_id"))


def _field_matches(rule_value: str, lead_value: str) -> bool:
    if not rule_value:
        return True
    return rule_value.strip().lower() == (lead_value or "").strip().lower()


def _rule_matches_lead(rule: dict, lead: dict) -> bool:
    if not rule.get("enabled", True) or not _rule_has_criteria(rule):
        return False
    return (
        _field_matches(rule.get("origem", ""), lead.get("origem", ""))
        and _field_matches(rule.get("campanha", ""), lead.get("campanha", ""))
        and _field_matches(rule.get("instancia_id", ""), lead.get("instancia_id", ""))
    )


def _attendants_unlocked(tenant: dict) -> list[dict]:
    return sorted(
        [_normalize_attendant(a) for a in tenant.get("attendants", [])],
        key=lambda a: (a.get("order", 0), a.get("nome", "")),
    )


def sync_instances(tenant_key: str, instances: dict) -> list[dict]:
    """
    Sincroniza as instancias WhatsApp pertencentes a este tenant como
    participantes da roleta. `instances` pode conter instancias de todos os
    tenants (snapshot global do sistema) — o filtro por dono e feito aqui
    dentro, usando o mapa de posse (mesma logica de routers/admin.py), para
    que instancias de outro tenant nunca entrem na fila deste.
    """
    owners = _owner_email_by_instance()
    own_instance_ids = {iid for iid, owner in owners.items() if owner == tenant_key}
    # Fallback: se o tenant_key nao aparece no mapa de posse (ex: usuario
    # recem-criado ainda sem instancia proprio dono, ou tenant orfao), nao
    # inclui nenhuma instancia — fail closed, nunca mostra instancia alheia.
    scoped_instances = {
        iid: inst for iid, inst in (instances or {}).items()
        if iid in own_instance_ids
    }

    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        existing = {}
        for item in tenant.get("attendants", []):
            normalized = _normalize_attendant(item)
            key = normalized.get("instance_id") or normalized.get("email", "")
            if key:
                existing[key] = normalized

        max_order = max((item.get("order", 0) for item in existing.values()), default=-1)
        active_instance_ids = set()

        for instance_id, inst in sorted(scoped_instances.items()):
            if not instance_id:
                continue
            active_instance_ids.add(instance_id)
            if instance_id in existing:
                existing[instance_id]["instance_id"] = instance_id
                existing[instance_id]["email"] = instance_id
                existing[instance_id]["funcao"] = "instancia"
            else:
                max_order += 1
                existing[instance_id] = _normalize_attendant({
                    "instance_id": instance_id,
                    "email": instance_id,
                    "nome": instance_id,
                    "funcao": "instancia",
                    "active": False,
                    "status": "available",
                    "weight": 1,
                    "order": max_order,
                })
            existing[instance_id]["channel_status"] = inst.get("status", "")
            existing[instance_id]["evo_instance_name"] = inst.get("evo_instance_name", "")

        current = [
            participant
            for instance_id, participant in existing.items()
            if instance_id in active_instance_ids
        ]
        current_ids = {participant.get("instance_id", "") for participant in current}

        tenant["attendants"] = sorted(current, key=lambda a: (a.get("order", 0), a.get("nome", "")))
        tenant["queue"] = [
            item for item in (tenant.get("queue", []) or [])
            if item in current_ids
        ]
        if not tenant["queue"]:
            tenant["enabled"] = False
        _save()
        return _attendants_unlocked(tenant)


def purge_instance(instance_id: str) -> dict:
    """Remove residuos de uma instancia da roleta e historico de atribuicao, em TODOS os tenants."""
    with _lock:
        before_attendants = 0
        before_history = 0
        before_rules = 0
        after_attendants = 0
        after_history = 0
        after_rules = 0

        for tenant in _state.get("tenants", {}).values():
            before_attendants += len(tenant.get("attendants", []))
            before_history += len(tenant.get("history", []))
            before_rules += len(tenant.get("rules", []))

            tenant["attendants"] = [
                item for item in tenant.get("attendants", [])
                if _normalize_attendant(item).get("instance_id") != instance_id
            ]
            tenant["queue"] = [item for item in (tenant.get("queue", []) or []) if item != instance_id]
            tenant["rules"] = [
                rule for rule in (tenant.get("rules", []) or [])
                if _normalize_rule(rule).get("instancia_id", "") != instance_id
            ]
            tenant["history"] = [
                item for item in (tenant.get("history", []) or [])
                if item.get("to_instance_id") != instance_id and item.get("instance_id") != instance_id
            ]
            tenant["pointer"] = min(int(tenant.get("pointer", 0) or 0), max(0, len(tenant["queue"]) - 1))
            tenant["enabled"] = bool(tenant.get("enabled", False)) and bool(tenant["queue"])

            after_attendants += len(tenant["attendants"])
            after_history += len(tenant["history"])
            after_rules += len(tenant["rules"])

        _save()
        return {
            "attendants_removed": before_attendants - after_attendants,
            "history_removed": before_history - after_history,
            "rules_removed": before_rules - after_rules,
        }


def list_attendants(tenant_key: str) -> list[dict]:
    with _lock:
        return _attendants_unlocked(_tenant_unlocked(tenant_key))


def update_attendant(tenant_key: str, email: str, active: bool | None = None, status: str | None = None,
                     weight: int | None = None, order: int | None = None) -> dict | None:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        allowed_status = {"available", "paused", "absent"}
        for attendant in tenant.get("attendants", []):
            if attendant.get("email") == email:
                if active is not None:
                    attendant["active"] = bool(active)
                if status is not None and status in allowed_status:
                    attendant["status"] = status
                if weight is not None:
                    attendant["weight"] = max(1, int(weight))
                if order is not None:
                    attendant["order"] = int(order)
                _save()
                return _normalize_attendant(attendant)
        return None


def reorder_attendants(tenant_key: str, emails: list[str]) -> list[dict]:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        order_map = {email: idx for idx, email in enumerate(emails)}
        next_order = len(order_map)
        for attendant in tenant.get("attendants", []):
            email = attendant.get("email", "")
            if email in order_map:
                attendant["order"] = order_map[email]
            else:
                attendant["order"] = next_order
                next_order += 1
        _save()
        return _attendants_unlocked(tenant)


def _eligible_attendants_unlocked(tenant: dict) -> list[dict]:
    return [
        _normalize_attendant(a)
        for a in sorted(tenant.get("attendants", []), key=lambda item: item.get("order", 0))
        if a.get("active") and a.get("status", "available") == "available" and _normalize_attendant(a).get("instance_id")
    ]


def _queue_attendants_unlocked(tenant: dict) -> list[dict]:
    attendants_by_email = {a["email"]: a for a in _eligible_attendants_unlocked(tenant)}
    queued = []
    for email in tenant.get("queue", []) or []:
        attendant = attendants_by_email.get(email)
        if attendant:
            queued.append(attendant)
    return queued


def _build_queue(tenant: dict, shuffled: bool = False) -> list[str]:
    eligible = _eligible_attendants_unlocked(tenant)
    if shuffled:
        eligible = eligible[:]
        random.shuffle(eligible)
    queue: list[str] = []
    for attendant in eligible:
        queue.extend([attendant["email"]] * max(1, int(attendant.get("weight", 1) or 1)))
    return queue


def generate_queue(tenant_key: str) -> dict:
    """Gera a fila de distribuicao na ordem atual dos atendentes do tenant."""
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        queue = _build_queue(tenant, shuffled=False)
        tenant["queue"] = queue
        tenant["pointer"] = 0
        tenant["queue_generated_at"] = _now() if queue else ""
        if not queue:
            tenant["enabled"] = False
        _save()
        return {
            "queue": _queue_attendants_unlocked(tenant),
            "settings": _settings_unlocked(tenant),
        }


def shuffle_queue(tenant_key: str) -> dict:
    """Gera a fila em ordem sorteada a partir dos atendentes ativos e disponiveis do tenant."""
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        queue = _build_queue(tenant, shuffled=True)
        tenant["queue"] = queue
        tenant["pointer"] = 0
        tenant["queue_generated_at"] = _now() if queue else ""
        if not queue:
            tenant["enabled"] = False
        _save()
        return {
            "queue": _queue_attendants_unlocked(tenant),
            "settings": _settings_unlocked(tenant),
        }


def start_distribution(tenant_key: str) -> dict:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        if not _queue_attendants_unlocked(tenant):
            tenant["enabled"] = False
            _save()
            return {"ok": False, "reason": "queue_not_generated", "settings": _settings_unlocked(tenant)}
        tenant["enabled"] = True
        _save()
        return {"ok": True, "settings": _settings_unlocked(tenant)}


def stop_distribution(tenant_key: str) -> dict:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        tenant["enabled"] = False
        _save()
        return {"ok": True, "settings": _settings_unlocked(tenant)}


def preview_queue(tenant_key: str) -> list[dict]:
    with _lock:
        return _queue_attendants_unlocked(_tenant_unlocked(tenant_key))


def preview_next_attendant(tenant_key: str) -> dict | None:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        queued = _queue_attendants_unlocked(tenant)
        eligible = queued or _eligible_attendants_unlocked(tenant)
        if not eligible:
            return None
        pointer = int(tenant.get("pointer", 0) or 0) % len(eligible)
        return eligible[pointer]


def should_auto_assign(tenant_key: str) -> bool:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        return (
            bool(tenant.get("enabled", False))
            and tenant.get("mode") == "automatic"
            and bool(_queue_attendants_unlocked(tenant))
        )


def list_rules(tenant_key: str) -> list[dict]:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        return [_normalize_rule(rule) for rule in tenant.get("rules", [])]


def upsert_rule(tenant_key: str, rule: dict) -> dict:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        normalized = _normalize_rule(rule)
        now = _now()
        normalized["updated_at"] = now
        if not normalized.get("created_at"):
            normalized["created_at"] = now
        rules = [_normalize_rule(item) for item in tenant.get("rules", [])]
        for idx, existing in enumerate(rules):
            if existing.get("id") == normalized["id"]:
                normalized["created_at"] = existing.get("created_at") or now
                rules[idx] = normalized
                tenant["rules"] = rules
                _save()
                return normalized
        if not rule.get("id"):
            normalized["id"] = f"rule-{len(rules) + 1}-{int(datetime.now(timezone.utc).timestamp())}"
        rules.append(normalized)
        tenant["rules"] = rules
        _save()
        return normalized


def delete_rule(tenant_key: str, rule_id: str) -> bool:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        rules = [_normalize_rule(rule) for rule in tenant.get("rules", [])]
        kept = [rule for rule in rules if rule.get("id") != rule_id]
        if len(kept) == len(rules):
            return False
        tenant["rules"] = kept
        _save()
        return True


def matching_rule_for_lead(tenant_key: str, lead: dict) -> dict | None:
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        if (
            not bool(tenant.get("enabled", False))
            or tenant.get("mode") != "automatic"
            or not bool(_queue_attendants_unlocked(tenant))
            or lead.get("assigned_to")
            or lead.get("assignment_status") == "assigned"
        ):
            return None
        for rule in [_normalize_rule(item) for item in tenant.get("rules", [])]:
            if _rule_matches_lead(rule, lead):
                return rule
        return None


def assign_next(tenant_key: str, lead_id: str = "", reason: str = "manual_preview") -> dict:
    """Avanca a roleta do tenant e retorna o atendente escolhido.

    A chamada automatica deve passar antes por should_auto_assign().
    """
    with _lock:
        tenant = _tenant_unlocked(tenant_key)
        queue = tenant.get("queue", []) or []
        attendants_by_email = {
            a.get("email", ""): a
            for a in sorted(tenant.get("attendants", []), key=lambda item: item.get("order", 0))
            if a.get("active") and a.get("status", "available") == "available" and _normalize_attendant(a).get("instance_id")
        }
        eligible = [attendants_by_email[email] for email in queue if email in attendants_by_email]
        if not eligible:
            eligible = list(attendants_by_email.values())
        if not eligible:
            return {"assigned": None, "reason": "no_available_attendant"}

        pointer = int(tenant.get("pointer", 0) or 0) % len(eligible)
        chosen = eligible[pointer]
        chosen["last_assigned_at"] = _now()
        chosen["total_assigned"] = int(chosen.get("total_assigned", 0) or 0) + 1
        tenant["pointer"] = (pointer + 1) % len(eligible)
        event = {
            "lead_id": lead_id,
            "to_email": chosen.get("email", ""),
            "to_instance_id": chosen.get("instance_id", "") or chosen.get("email", ""),
            "reason": reason,
            "created_at": _now(),
        }
        tenant.setdefault("history", []).append(event)
        _save()
        return {"assigned": _normalize_attendant(chosen), "event": event}


# ── Helper compartilhado — usado por routers/assignments.py e routers/crm.py ──

def apply_assignment_rule_to_lead(lead: dict, reason: str) -> dict:
    """Aplica roleta automática após o lead já estar salvo no CRM.

    Resolve o tenant pelo dono da instancia do lead — nao recebe tenant_key
    porque e chamado de contextos sem request HTTP (ex: processamento de
    webhook em background).
    """
    import services.crm_service as crm_svc  # import local para evitar circular
    if not lead or lead.get("assigned_to") or lead.get("assignment_status") == "assigned":
        return lead
    try:
        instance_id = lead.get("instancia_id", "")
        owners = _owner_email_by_instance()
        tenant_key = owners.get(instance_id, "")
        if not tenant_key:
            return lead
        rule = matching_rule_for_lead(tenant_key, lead)
        if not rule:
            return lead
        result = assign_next(tenant_key, lead_id=lead.get("id", ""), reason=f"{reason}:{rule.get('id', '')}")
        attendant = result.get("assigned")
        if not attendant:
            return lead
        updated = crm_svc.assign_lead(
            lead_id=lead.get("id", ""),
            assigned_to=attendant.get("email", ""),
            assigned_to_name=attendant.get("nome", ""),
            assigned_at=attendant.get("last_assigned_at", ""),
            instancia_id=attendant.get("instance_id", ""),
        ) or lead
        try:
            campanha = updated.get("campanha") or "Campanha desconhecida"
            crm_svc.add_tag_if_missing(updated.get("id", ""), f"Campanha: {campanha}")
        except Exception as tag_exc:
            logger.info(f"[ASSIGNMENT] Falha ao gravar tag de campanha: {tag_exc}")
        return updated
    except Exception as exc:
        logger.info(f"[ASSIGNMENT] Falha ao aplicar regra automatica: {exc}")
        return lead
