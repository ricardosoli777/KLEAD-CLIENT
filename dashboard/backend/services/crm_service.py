"""
CRM Service — KLEAD
Gerencia leads, colunas do Kanban e persistência em crm.json
"""

import csv
import io
import json
import logging
import uuid
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from services import crm_pg_service

_lock = threading.Lock()

_DEFAULT_COLUMN_TEMPLATE = [
    {"suffix": "novo",           "name": "Novo Lead",         "order": 0, "color": "#25D366", "capi_event": "Lead"},
    {"suffix": "qualif",         "name": "Qualificando",      "order": 1, "color": "#F0B429", "capi_event": "Contact"},
    {"suffix": "quente",         "name": "Lead Quente",       "order": 2, "color": "#F97316", "capi_event": "SubmitApplication"},
    {"suffix": "reuniao",        "name": "Reunião Agendada",  "order": 3, "color": "#60A5FA", "capi_event": "Schedule"},
    {"suffix": "fechado",        "name": "Fechado",           "order": 4, "color": "#A78BFA", "capi_event": "Purchase"},
    {"suffix": "desqualificado", "name": "Desqualificado",    "order": 5, "color": "#9CA3AF", "capi_event": "none"},
]

_crm: dict = {"columns": [], "leads": []}
_last_persisted_crm: dict = {"columns": [], "leads": []}


class CrmPersistenceError(RuntimeError):
    """Indica que uma mutacao do CRM nao foi persistida no Postgres."""


def init_storage():
    global _crm, _last_persisted_crm
    try:
        ok, snapshot = crm_pg_service.load_snapshot()
        if ok and isinstance(snapshot, dict):
            _crm = snapshot
        else:
            logger.warning(f"[CRM] Falha ao carregar do Postgres: {snapshot}")
    except Exception as exc:
        logger.warning(f"[CRM] Erro ao conectar ao Postgres no startup: {exc}")
    if not _crm.get("columns"):
        _crm["columns"] = []
    _last_persisted_crm = json.loads(json.dumps(_crm))
    _migrate_legacy_global_columns()


def _migrate_legacy_global_columns():
    """
    Colunas antigas (criadas antes do isolamento por instância) não tinham
    `instancia_id`. Se sobrar alguma órfã e existir só uma instância com leads
    hoje, vincula essas colunas a ela — preserva o CRM do cliente já existente
    sem perder dados. Rodado uma vez no startup; não faz nada se já migrado.
    """
    cols = _crm.get("columns", [])
    orphan_ids = {c["id"] for c in cols if not (c.get("instancia_id") or "").strip()}
    if not orphan_ids:
        return
    leads = _crm.get("leads", [])
    instancia_ids = {l.get("instancia_id", "") for l in leads if l.get("instancia_id")}
    target = next(iter(instancia_ids)) if len(instancia_ids) == 1 else ""
    if not target:
        logger.warning(
            f"[CRM] {len(orphan_ids)} coluna(s) sem instancia_id e não foi possível "
            "inferir dono automaticamente (0 ou 2+ instâncias com leads) — "
            "vincule manualmente via banco se necessário."
        )
        return
    for col in cols:
        if col["id"] in orphan_ids:
            col["instancia_id"] = target
    logger.warning(f"[CRM] {len(orphan_ids)} coluna(s) legada(s) vinculada(s) à instância '{target}'.")
    _save()


def ensure_default_columns(instancia_id: str) -> None:
    """Cria as 6 colunas padrão para uma instância nova, se ela ainda não tiver nenhuma."""
    instancia_id = (instancia_id or "").strip()
    if not instancia_id:
        return
    with _lock:
        cols = _crm.setdefault("columns", [])
        if any((c.get("instancia_id") or "") == instancia_id for c in cols):
            return  # já tem colunas — não sobrescreve
        for tpl in _DEFAULT_COLUMN_TEMPLATE:
            cols.append({
                "id": f"{instancia_id}__{tpl['suffix']}",
                "name": tpl["name"],
                "order": tpl["order"],
                "color": tpl["color"],
                "capi_event": tpl["capi_event"],
                "instancia_id": instancia_id,
            })
        _save()
        logger.info(f"[CRM] 6 colunas padrão criadas para a instância '{instancia_id}'.")


def _save():
    global _last_persisted_crm
    ok, detail = crm_pg_service.sync_snapshot(_crm)
    if not ok:
        logger.warning(f"[CRM] Falha ao sincronizar com Postgres: {detail}")
        # Todas as mutações acontecem sob _lock. Em produção, restaurar in-place
        # mantém as referências ao dict e impede RAM e Postgres de divergirem.
        # Sem PG configurado preservamos o contrato histórico do modo dev/teste.
        if crm_pg_service.pg_configured():
            _crm.clear()
            _crm.update(json.loads(json.dumps(_last_persisted_crm)))
        raise CrmPersistenceError(f"Falha ao persistir CRM no Postgres: {detail}")
    _last_persisted_crm = json.loads(json.dumps(_crm))


def export_crm() -> dict:
    with _lock:
        return json.loads(json.dumps(_crm))


def validate_postgres_mirror() -> tuple[bool, dict]:
    with _lock:
        snapshot = json.loads(json.dumps(_crm))
    return crm_pg_service.validate_snapshot(snapshot)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timeline_event(action: str, detail: str = "") -> dict:
    return {"action": action, "detail": detail, "at": _now()}


def _col_name(coluna_id: str) -> str:
    for c in _crm.get("columns", []):
        if c["id"] == coluna_id:
            return c["name"]
    return coluna_id


def _normalize_status(value: str | None) -> str:
    text = (value or "").strip().lower()
    aliases = {
        "novo": "novo",
        "lead": "novo",
        "em_contato": "em_contato",
        "em contato": "em_contato",
        "contato": "em_contato",
        "qualificado": "qualificado",
        "qualificou": "qualificado",
        "agendado": "agendado",
        "agendamento": "agendado",
        "visita_agendada": "visita_agendada",
        "visita agendada": "visita_agendada",
        "reuniao_agendada": "visita_agendada",
        "reunião agendada": "visita_agendada",
        "vendido": "vendido",
        "venda": "vendido",
        "fechado": "vendido",
        "desqualificado": "desqualificado",
        "descartado": "desqualificado",
    }
    return aliases.get(text, text)


# ── Colunas ───────────────────────────────────────────────────────────────────

def get_columns(instancia_id: str | None = None) -> list:
    """
    Se `instancia_id` for informado, retorna só as colunas dessa instância.
    Sem ele, retorna todas — uso interno (ex: resolver nome de coluna por id
    já globalmente único), nunca deve ser exposto direto a um endpoint sem
    filtro de instância.
    """
    with _lock:
        cols = _crm.get("columns", [])
        if instancia_id:
            cols = [c for c in cols if (c.get("instancia_id") or "") == instancia_id]
        return sorted(cols, key=lambda c: c.get("order", 0))


def create_column(instancia_id: str, name: str, color: str = "#8696A0", capi_event: str | None = None) -> dict:
    instancia_id = (instancia_id or "").strip()
    with _lock:
        cols = _crm.setdefault("columns", [])
        same_instance = [c for c in cols if (c.get("instancia_id") or "") == instancia_id]
        max_order = max((c.get("order", 0) for c in same_instance), default=-1)
        col: dict = {
            "id": f"{instancia_id}__{str(uuid.uuid4())[:8]}" if instancia_id else str(uuid.uuid4())[:8],
            "name": name,
            "order": max_order + 1,
            "color": color,
            "instancia_id": instancia_id,
        }
        if capi_event is not None:
            col["capi_event"] = capi_event
        cols.append(col)
        _save()
        return col


def update_column(instancia_id: str, col_id: str, name: str | None = None, color: str | None = None, order: int | None = None, capi_event: str | None = None) -> dict | None:
    with _lock:
        for col in _crm.get("columns", []):
            if col["id"] == col_id:
                if (col.get("instancia_id") or "") != (instancia_id or ""):
                    return None  # coluna existe, mas não pertence a essa instância
                if name       is not None: col["name"]       = name
                if color      is not None: col["color"]      = color
                if order      is not None: col["order"]      = order
                if capi_event is not None: col["capi_event"] = capi_event
                _save()
                return col
        return None


def delete_column(instancia_id: str, col_id: str) -> bool:
    with _lock:
        cols = _crm.get("columns", [])
        target = next((c for c in cols if c["id"] == col_id), None)
        if target is None or (target.get("instancia_id") or "") != (instancia_id or ""):
            return False
        cols_new = [c for c in cols if c["id"] != col_id]
        _crm["columns"] = cols_new
        same_instance_remaining = [c for c in cols_new if (c.get("instancia_id") or "") == (instancia_id or "")]
        if same_instance_remaining:
            first = sorted(same_instance_remaining, key=lambda c: c.get("order", 0))[0]["id"]
            for lead in _crm.get("leads", []):
                if lead.get("instancia_id") == instancia_id and lead.get("coluna_id") == col_id:
                    lead["coluna_id"] = first
        _save()
        return True


# ── Leads ─────────────────────────────────────────────────────────────────────

def get_leads(coluna_id: str | None = None, origem: str | None = None, search: str | None = None) -> list:
    with _lock:
        leads = _crm.get("leads", [])
        if coluna_id:
            leads = [l for l in leads if l.get("coluna_id") == coluna_id]
        if origem:
            leads = [l for l in leads if l.get("origem") == origem]
        if search:
            q = search.lower()
            leads = [l for l in leads if
                     q in (l.get("nome") or "").lower()
                     or q in (l.get("telefone") or "")
                     or q in (l.get("bsuid") or "").lower()
                     or q in (l.get("whatsapp_username") or "").lower()
                     or q in (l.get("empresa") or "").lower()
                     or q in (l.get("email") or "").lower()]
        return sorted(leads, key=lambda l: l.get("created_at", ""), reverse=True)


def get_lead(lead_id: str) -> dict | None:
    with _lock:
        for l in _crm.get("leads", []):
            if l["id"] == lead_id:
                return l
        return None


def find_lead_by_phone(instancia_id: str, telefone: str) -> dict | None:
    """
    Busca um lead por telefone dentro de UMA instância — usado no fluxo
    quente do Inbox (takeover/release/send/delete). Filtra por instancia_id
    primeiro para não escanear leads de todos os outros tenants a cada
    chamada.
    """
    if not telefone:
        return None
    with _lock:
        for l in _crm.get("leads", []):
            if l.get("instancia_id") == instancia_id and l.get("telefone") == telefone:
                return l
        return None


def get_unassigned_leads() -> list[dict]:
    with _lock:
        leads = [
            l for l in _crm.get("leads", [])
            if not l.get("assigned_to") and l.get("assignment_status", "unassigned") != "assigned"
        ]
        return sorted(leads, key=lambda l: l.get("created_at", ""))


def _make_lead(telefone: str, nome: str, origem: str, instancia_id: str,
               score: int | None, score_reason: str | None,
               historico: list | None, coluna_id: str | None,
               email: str = "", empresa: str = "", campanha: str = "",
               cidade: str = "", estado: str = "", cep: str = "", pais: str = "",
               assigned_to: str = "", assigned_to_name: str = "",
               assigned_at: str = "", assignment_status: str = "unassigned",
               bsuid: str = "", parent_bsuid: str = "",
               whatsapp_username: str = "", phone_available: bool = True,
               source_meta: dict | None = None) -> dict:
    all_cols = _crm.get("columns", [])
    cols = [c for c in all_cols if (c.get("instancia_id") or "") == instancia_id]
    if not cols:
        logger.warning(f"[CRM] Instância '{instancia_id}' sem colunas próprias ao criar lead — usando fallback global (verifique ensure_default_columns).")
        cols = all_cols
    valid_ids = {c["id"] for c in cols}
    default_col = sorted(cols, key=lambda c: c.get("order", 0))[0]["id"]
    coluna_id = coluna_id if coluna_id in valid_ids else default_col
    now = _now()
    return {
        "id":              str(uuid.uuid4()),
        "nome":            nome or telefone,
        "telefone":        telefone,
        "bsuid":           bsuid,
        "parent_bsuid":    parent_bsuid,
        "whatsapp_username": whatsapp_username,
        "phone_available": bool(phone_available),
        "email":           email,
        "empresa":         empresa,
        "campanha":        campanha,
        "cidade":          cidade,
        "estado":          estado,
        "cep":             cep,
        "pais":            pais,
        "leadgen_id":      (source_meta or {}).get("leadgen_id", ""),
        "form_id":         (source_meta or {}).get("form_id", ""),
        "page_id":         (source_meta or {}).get("page_id", ""),
        "ctwa_clid":       (source_meta or {}).get("ctwa_clid", ""),
        "source_meta":     dict(source_meta or {}),
        "valor_estimado":  "",
        "origem":          origem,
        "instancia_id":    instancia_id,
        "coluna_id":       coluna_id,
        "score":           score,
        "score_reason":    score_reason or "",
        "lead_status":     "",
        "followup_date":   "",
        "historico":       historico or [],
        "notas":           [],
        "contatos":        [],
        "tags":            [],
        "assigned_to":     assigned_to,
        "assigned_to_name": assigned_to_name,
        "assigned_at":     assigned_at,
        "assignment_status": assignment_status,
        "segmento":        "",
        "poder_aquisitivo": "",
        "timeline":        [_timeline_event("criado", f"Lead criado via {origem}")],
        "created_at":      now,
        "updated_at":      now,
    }


def archive_conversation_history(instancia_id: str, conv_key: str, messages: list) -> bool:
    """
    Congela o histórico da conversa no card do lead ANTES de a conversa ser
    apagada do InboxSDR — apagar do inbox não pode apagar o histórico do CRM.
    Só age se o lead já existe no CRM (= foi qualificado e enviado); conversa
    sem card some junto com o histórico, por design.

    Mescla em vez de sobrescrever: se o lead já tinha histórico arquivado de
    um ciclo anterior (apagou → retomou pelo CRM → apagou de novo), o thread
    antigo é preservado e as mensagens novas entram no final.
    """
    if not messages:
        return False
    novos = [
        {"role": m.get("role", ""), "content": m.get("content", ""), "timestamp": m.get("timestamp", "")}
        for m in messages
        if m.get("content")
    ]
    if not novos:
        return False
    with _lock:
        for lead in _crm.get("leads", []):
            if lead.get("instancia_id") != instancia_id:
                continue
            same_phone = bool(conv_key) and lead.get("telefone") == conv_key
            same_bsuid = bool(conv_key) and bool(lead.get("bsuid")) and lead.get("bsuid") == conv_key
            if not (same_phone or same_bsuid):
                continue
            # Dedup por (role, content): remove do histórico antigo o que o
            # thread atual já contém (o snapshot feito no envio pro CRM não
            # tinha timestamp, então comparar por timestamp duplicaria tudo).
            novos_sigs = {(m["role"], m["content"]) for m in novos}
            antigos = [
                m for m in (lead.get("historico") or [])
                if (m.get("role", ""), m.get("content", "")) not in novos_sigs
            ]
            lead["historico"] = antigos + novos
            lead["historico_arquivado_em"] = _now()
            lead.setdefault("timeline", []).append(
                _timeline_event("historico_arquivado", f"Conversa do inbox arquivada no card ({len(novos)} mensagens)")
            )
            lead["updated_at"] = _now()
            _save()
            return True
    return False


def upsert_lead(telefone: str, nome: str, origem: str, instancia_id: str,
                score: int | None = None, score_reason: str | None = None,
                historico: list | None = None, coluna_id: str | None = None,
                email: str = "", empresa: str = "", campanha: str = "",
                cidade: str = "", estado: str = "", cep: str = "", pais: str = "",
                assigned_to: str = "", assigned_to_name: str = "",
                assigned_at: str = "", assignment_status: str = "",
                bsuid: str = "", parent_bsuid: str = "",
                whatsapp_username: str = "", phone_available: bool = True,
                source_meta: dict | None = None) -> dict:
    with _lock:
        for lead in _crm.get("leads", []):
            same_instance = lead.get("instancia_id") == instancia_id
            same_bsuid = bool(bsuid) and lead.get("bsuid") == bsuid
            same_phone = bool(telefone) and lead.get("telefone") == telefone
            if same_instance and (same_bsuid or same_phone):
                if nome:              lead["nome"]         = nome
                if telefone:          lead["telefone"]     = telefone
                if bsuid:             lead["bsuid"]        = bsuid
                if parent_bsuid:      lead["parent_bsuid"] = parent_bsuid
                if whatsapp_username: lead["whatsapp_username"] = whatsapp_username
                lead["phone_available"] = bool(phone_available)
                if source_meta:
                    lead["source_meta"] = dict(source_meta)
                    lead["leadgen_id"] = source_meta.get("leadgen_id", lead.get("leadgen_id", ""))
                    lead["form_id"] = source_meta.get("form_id", lead.get("form_id", ""))
                    lead["page_id"] = source_meta.get("page_id", lead.get("page_id", ""))
                    if source_meta.get("ctwa_clid"):
                        lead["ctwa_clid"] = source_meta["ctwa_clid"]
                if score is not None: lead["score"]        = score
                if score_reason:      lead["score_reason"] = score_reason
                if historico:         lead["historico"]    = historico
                if email:             lead["email"]        = email
                if empresa:           lead["empresa"]      = empresa
                if campanha:          lead["campanha"]     = campanha
                if cidade:            lead["cidade"]       = cidade
                if estado:            lead["estado"]       = estado
                if cep:               lead["cep"]          = cep
                if pais:              lead["pais"]         = pais
                if assigned_to:
                    lead["assigned_to"] = assigned_to
                    lead["assigned_to_name"] = assigned_to_name
                    lead["assigned_at"] = assigned_at
                    lead["assignment_status"] = assignment_status or "assigned"
                    lead.setdefault("timeline", []).append(
                        _timeline_event("atribuido", f"Lead atribuido para {assigned_to_name or assigned_to}")
                    )
                lead["updated_at"] = _now()
                _save()
                return lead
        lead = _make_lead(
            telefone, nome, origem, instancia_id, score, score_reason, historico,
            coluna_id, email, empresa, campanha, cidade, estado, cep, pais, assigned_to, assigned_to_name,
            assigned_at, assignment_status or ("assigned" if assigned_to else "unassigned"),
            bsuid, parent_bsuid, whatsapp_username, phone_available, source_meta,
        )
        _crm.setdefault("leads", []).append(lead)
        _save()
        return lead


def assign_lead(lead_id: str, assigned_to: str, assigned_to_name: str, assigned_at: str,
                instancia_id: str | None = None) -> dict | None:
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                previous_instance = lead.get("instancia_id", "")
                if instancia_id:
                    lead["instancia_id"] = instancia_id
                lead["assigned_to"] = assigned_to
                lead["assigned_to_name"] = assigned_to_name
                lead["assigned_at"] = assigned_at
                lead["assignment_status"] = "assigned"
                lead["updated_at"] = _now()
                detail = f"Lead atribuido para {assigned_to_name or assigned_to}"
                if instancia_id:
                    detail += f" na instancia {instancia_id}"
                    if previous_instance and previous_instance != instancia_id:
                        detail += f" (origem tecnica: {previous_instance})"
                lead.setdefault("timeline", []).append(
                    _timeline_event("atribuido", detail)
                )
                _save()
                return lead
        return None


def move_lead(lead_id: str, coluna_id: str | None) -> dict | None:
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                if coluna_id:
                    col = next((c for c in _crm.get("columns", []) if c["id"] == coluna_id), None)
                    if col is None or (col.get("instancia_id") or "") != lead.get("instancia_id", ""):
                        raise ValueError("coluna_invalida")
                old = _col_name(lead.get("coluna_id", ""))
                new = _col_name(coluna_id) if coluna_id else "lista de espera"
                lead["coluna_id"]  = coluna_id
                lead["updated_at"] = _now()
                lead.setdefault("timeline", []).append(
                    _timeline_event("movido", f"{old} → {new}")
                )
                _save()
                return lead
        return None


def add_note(lead_id: str, text: str) -> dict | None:
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                lead.setdefault("notas", []).append({"text": text, "at": _now()})
                lead.setdefault("timeline", []).append(_timeline_event("nota", text[:80]))
                lead["updated_at"] = _now()
                _save()
                return lead
        return None


def set_tags(lead_id: str, tags: list) -> dict | None:
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                lead["tags"]       = [t.strip() for t in tags if t.strip()]
                lead["updated_at"] = _now()
                _save()
                return lead
        return None


def add_tag_if_missing(lead_id: str, tag: str) -> dict | None:
    tag = (tag or "").strip()
    if not tag:
        return None
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                tags = lead.setdefault("tags", [])
                if tag not in tags:
                    tags.append(tag)
                    lead["updated_at"] = _now()
                    _save()
                return lead
        return None


def update_fields(lead_id: str, email: str | None = None, empresa: str | None = None,
                  valor_estimado: str | None = None, followup_date: str | None = None,
                  segmento: str | None = None, poder_aquisitivo: str | None = None,
                  cidade: str | None = None, estado: str | None = None,
                  cep: str | None = None, pais: str | None = None,
                  score: int | None = None, score_reason: str | None = None,
                  lead_status: str | None = None, instancia_id: str | None = None,
                  nome: str | None = None, telefone: str | None = None,
                  leadgen_id: str | None = None) -> dict | None:
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                if nome              is not None: lead["nome"]              = nome or lead.get("telefone", "")
                if telefone          is not None: lead["telefone"]          = telefone
                if email             is not None: lead["email"]             = email
                if empresa           is not None: lead["empresa"]           = empresa
                if valor_estimado    is not None: lead["valor_estimado"]    = valor_estimado
                if segmento          is not None: lead["segmento"]          = segmento
                if poder_aquisitivo  is not None: lead["poder_aquisitivo"]  = poder_aquisitivo
                if cidade            is not None: lead["cidade"]            = cidade
                if estado            is not None: lead["estado"]            = estado
                if cep               is not None: lead["cep"]               = cep
                if pais              is not None: lead["pais"]              = pais
                if score is not None:
                    score_value = max(0, min(100, int(score)))
                    lead["score"] = score_value
                    lead.setdefault("timeline", []).append(
                        _timeline_event("score", f"Nota ajustada manualmente para {score_value}")
                    )
                if score_reason is not None:
                    lead["score_reason"] = score_reason
                    if score_reason.strip():
                        lead.setdefault("timeline", []).append(
                            _timeline_event("score_reason", score_reason.strip()[:80])
                        )
                if lead_status is not None:
                    normalized_status = _normalize_status(lead_status)
                    lead["lead_status"] = normalized_status
                    if normalized_status:
                        lead.setdefault("timeline", []).append(
                            _timeline_event("status", f"Status comercial: {normalized_status.replace('_', ' ')}")
                        )
                if followup_date  is not None:
                    lead["followup_date"] = followup_date
                    detail = f"Follow-up agendado para {followup_date}" if followup_date else "Follow-up removido"
                    lead.setdefault("timeline", []).append(_timeline_event("followup", detail))
                if instancia_id is not None:
                    lead["instancia_id"] = instancia_id
                if leadgen_id is not None:
                    lead["leadgen_id"] = leadgen_id
                    lead.setdefault("source_meta", {})["leadgen_id"] = leadgen_id
                lead["updated_at"] = _now()
                _save()
                return lead
        return None


def add_contact_log(lead_id: str, tipo: str, nota: str, proximo_contato: str | None = None) -> dict | None:
    """Registra um contato manual no histórico de acompanhamento do lead."""
    with _lock:
        for lead in _crm.get("leads", []):
            if lead["id"] == lead_id:
                entry = {
                    "tipo":   tipo,
                    "nota":   nota,
                    "at":     _now(),
                }
                lead.setdefault("contatos", []).append(entry)
                if proximo_contato is not None:
                    lead["followup_date"] = proximo_contato
                    lead.setdefault("timeline", []).append(
                        _timeline_event("contato", f"{tipo}: {nota[:60]}")
                    )
                    if proximo_contato:
                        lead["timeline"].append(
                            _timeline_event("followup", f"Próximo contato agendado: {proximo_contato}")
                        )
                lead["updated_at"] = _now()
                _save()
                return lead
        return None


def delete_lead(lead_id: str) -> bool:
    with _lock:
        leads = _crm.get("leads", [])
        new_leads = [l for l in leads if l["id"] != lead_id]
        if len(new_leads) == len(leads):
            return False
        _crm["leads"] = new_leads
        for lista in _crm.get("listas", []) or []:
            lista["lead_ids"] = [lid for lid in lista.get("lead_ids", []) if lid != lead_id]
        _save()
        from services import confirmation_pg_service
        confirmation_pg_service.delete_for_lead(lead_id)
        return True


def purge_instance_leads(instance_id: str) -> int:
    """
    Remove leads, colunas do Kanban e referências em listas vinculados a uma
    instância — chamado quando a instância é apagada, pra não deixar lixo
    órfão no banco (colunas sem dono não some sozinho, não tem cascade).
    """
    with _lock:
        leads = _crm.get("leads", [])
        removed_ids = {lead["id"] for lead in leads if lead.get("instancia_id") == instance_id}
        leads_changed = bool(removed_ids)
        if leads_changed:
            _crm["leads"] = [lead for lead in leads if lead.get("instancia_id") != instance_id]
            for lista in _crm.get("listas", []) or []:
                lista["lead_ids"] = [lid for lid in lista.get("lead_ids", []) if lid not in removed_ids]

        cols = _crm.get("columns", [])
        cols_kept = [c for c in cols if (c.get("instancia_id") or "") != instance_id]
        cols_changed = len(cols_kept) != len(cols)
        if cols_changed:
            _crm["columns"] = cols_kept

        if leads_changed or cols_changed:
            _save()
        if removed_ids:
            from services import confirmation_pg_service
            for lead_id in removed_ids:
                confirmation_pg_service.delete_for_lead(lead_id)
        return len(removed_ids)


# ── Listas de Leads ───────────────────────────────────────────────────────────

def get_lists() -> list:
    with _lock:
        return _crm.get("listas", [])


def get_list(lista_id: str) -> dict | None:
    with _lock:
        return next((l for l in _crm.get("listas", []) if l["id"] == lista_id), None)


def create_list(name: str, filtros: dict, owner_key: str = "") -> dict:
    with _lock:
        lista = {
            "id":       str(uuid.uuid4())[:8],
            "name":     name,
            "filtros":  filtros,
            "lead_ids": [],
            "followup_date": "",
            "owner_key": owner_key,  # tenant dono da lista (email do admin); "" = legado, sem dono
            "created_at": _now(),
        }
        _crm.setdefault("listas", []).append(lista)
        _save()
        return lista


def update_list_followup(lista_id: str, followup_date: str) -> dict | None:
    with _lock:
        for lista in _crm.get("listas", []):
            if lista["id"] == lista_id:
                lista["followup_date"] = followup_date
                _save()
                return lista
        return None


def add_lead_to_list(lista_id: str, lead_id: str) -> dict | None:
    with _lock:
        for lista in _crm.get("listas", []):
            if lista["id"] == lista_id:
                if lead_id not in lista.setdefault("lead_ids", []):
                    lista["lead_ids"].append(lead_id)
                    _save()
                return lista
        return None


def remove_lead_from_list(lista_id: str, lead_id: str) -> dict | None:
    with _lock:
        for lista in _crm.get("listas", []):
            if lista["id"] == lista_id:
                lista.setdefault("lead_ids", [])
                lista["lead_ids"] = [l for l in lista["lead_ids"] if l != lead_id]
                _save()
                return lista
        return None


def delete_list(lista_id: str) -> bool:
    with _lock:
        listas = _crm.get("listas", [])
        new = [l for l in listas if l["id"] != lista_id]
        if len(new) == len(listas):
            return False
        _crm["listas"] = new
        _save()
        return True


def get_list_leads(lista_id: str) -> list | None:
    """Retorna os leads completos da lista, combinando membros manuais e filtros."""
    with _lock:
        lista = next((l for l in _crm.get("listas", []) if l["id"] == lista_id), None)
        if lista is None:
            return None

        all_leads = _crm.get("leads", [])
        seen = set()
        result = []

        manual_ids = set(lista.get("lead_ids", []))
        for lead in all_leads:
            if lead["id"] in manual_ids:
                seen.add(lead["id"])
                result.append(lead)

        filtros = lista.get("filtros", {})
        if any(filtros.values()):
            filtered = all_leads
            if filtros.get("coluna_id"):
                filtered = [l for l in filtered if l.get("coluna_id") == filtros["coluna_id"]]
            if filtros.get("origem"):
                filtered = [l for l in filtered if l.get("origem") == filtros["origem"]]
            if filtros.get("score_min") is not None:
                filtered = [l for l in filtered if (l.get("score") or 0) >= filtros["score_min"]]
            if filtros.get("tags"):
                tags = set(filtros["tags"])
                filtered = [l for l in filtered if tags & set(l.get("tags", []))]
            for lead in filtered:
                if lead["id"] not in seen:
                    seen.add(lead["id"])
                    result.append(lead)

        return result


def get_list_contacts(lista_id: str) -> list | None:
    """Retorna leads da lista no formato de contatos do disparador.
    Combina leads adicionados manualmente (lead_ids) com leads do filtro."""
    leads = get_list_leads(lista_id)
    if leads is None:
        return None
    return [
        {"phone": lead["telefone"], "name": lead.get("nome", lead["telefone"])}
        for lead in leads
        if lead.get("telefone")
    ]


def export_csv(coluna_id: str | None = None, origem: str | None = None, search: str | None = None, allowed_instances: list | None = None) -> str:
    leads = get_leads(coluna_id, origem, search)
    if allowed_instances is not None:
        leads = [l for l in leads if l.get("instancia_id") in allowed_instances]
    cols  = {c["id"]: c["name"] for c in get_columns()}
    buf   = io.StringIO()
    w     = csv.writer(buf)
    w.writerow(["Nome", "Telefone", "Email", "Empresa", "Cidade", "Estado", "CEP", "Pais", "Valor Estimado",
                "Origem", "Etapa", "Score", "Tags", "Follow-up", "Criado em", "Atualizado em"])
    for l in leads:
        w.writerow([
            l.get("nome", ""),
            l.get("telefone", ""),
            l.get("email", ""),
            l.get("empresa", ""),
            l.get("cidade", ""),
            l.get("estado", ""),
            l.get("cep", ""),
            l.get("pais", ""),
            l.get("valor_estimado", ""),
            l.get("origem", ""),
            cols.get(l.get("coluna_id", ""), l.get("coluna_id", "")),
            l.get("score", ""),
            ", ".join(l.get("tags", [])),
            l.get("followup_date", ""),
            l.get("created_at", "")[:10],
            l.get("updated_at", "")[:10],
        ])
    return buf.getvalue()
