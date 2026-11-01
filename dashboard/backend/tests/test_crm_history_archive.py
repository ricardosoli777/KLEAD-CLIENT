"""
Testes do arquivamento de histórico de conversa no card do CRM —
apagar a conversa do InboxSDR não pode apagar o histórico do lead qualificado.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import crm_service


def _setup_crm(leads):
    crm_service._crm = {
        "columns": [{"id": "novo", "name": "Novo", "order": 0, "color": "#000"}],
        "leads": leads,
    }


def _msg(role, content, ts="2026-07-11T10:00:00+00:00"):
    return {"role": role, "content": content, "timestamp": ts}


def test_arquiva_no_lead_existente():
    _setup_crm([{
        "id": "l1", "instancia_id": "inst1", "telefone": "5511999990001",
        "historico": [], "timeline": [],
    }])
    with patch("services.crm_service._save"):
        ok = crm_service.archive_conversation_history(
            "inst1", "5511999990001",
            [_msg("user", "oi"), _msg("assistant", "olá, tudo bem?")],
        )
    assert ok is True
    lead = crm_service._crm["leads"][0]
    assert len(lead["historico"]) == 2
    assert lead["historico"][0]["content"] == "oi"
    assert lead["historico"][1]["timestamp"]  # timestamp preservado
    assert lead["historico_arquivado_em"]
    assert any(t.get("action") == "historico_arquivado" for t in lead["timeline"])


def test_sem_lead_no_crm_nao_arquiva():
    """Conversa sem card no CRM (não qualificada) some junto — por design."""
    _setup_crm([])
    with patch("services.crm_service._save"):
        ok = crm_service.archive_conversation_history("inst1", "5511999990001", [_msg("user", "oi")])
    assert ok is False


def test_lead_de_outra_instancia_nao_recebe():
    _setup_crm([{
        "id": "l1", "instancia_id": "OUTRA", "telefone": "5511999990001",
        "historico": [], "timeline": [],
    }])
    with patch("services.crm_service._save"):
        ok = crm_service.archive_conversation_history("inst1", "5511999990001", [_msg("user", "oi")])
    assert ok is False


def test_merge_dedup_com_snapshot_antigo_sem_timestamp():
    """O snapshot do envio pro CRM não tinha timestamp — arquivar a conversa
    completa não pode duplicar essas mensagens."""
    _setup_crm([{
        "id": "l1", "instancia_id": "inst1", "telefone": "5511999990001",
        "historico": [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá, tudo bem?"}],
        "timeline": [],
    }])
    with patch("services.crm_service._save"):
        crm_service.archive_conversation_history(
            "inst1", "5511999990001",
            [_msg("user", "oi"), _msg("assistant", "olá, tudo bem?"), _msg("user", "quero saber o preço")],
        )
    lead = crm_service._crm["leads"][0]
    contents = [m["content"] for m in lead["historico"]]
    assert contents == ["oi", "olá, tudo bem?", "quero saber o preço"]


def test_ciclo_anterior_preservado():
    """Apagou → retomou pelo CRM → apagou de novo: o thread antigo fica."""
    _setup_crm([{
        "id": "l1", "instancia_id": "inst1", "telefone": "5511999990001",
        "historico": [_msg("user", "primeira conversa"), _msg("assistant", "resposta antiga")],
        "timeline": [],
    }])
    with patch("services.crm_service._save"):
        crm_service.archive_conversation_history(
            "inst1", "5511999990001",
            [_msg("human", "retomando contato"), _msg("user", "oi de novo")],
        )
    lead = crm_service._crm["leads"][0]
    contents = [m["content"] for m in lead["historico"]]
    assert contents == ["primeira conversa", "resposta antiga", "retomando contato", "oi de novo"]


def test_match_por_bsuid():
    _setup_crm([{
        "id": "l1", "instancia_id": "inst1", "telefone": "", "bsuid": "abc123DEF",
        "historico": [], "timeline": [],
    }])
    with patch("services.crm_service._save"):
        ok = crm_service.archive_conversation_history("inst1", "abc123DEF", [_msg("user", "oi")])
    assert ok is True


def test_mensagens_vazias_nao_arquivam():
    _setup_crm([{
        "id": "l1", "instancia_id": "inst1", "telefone": "5511999990001",
        "historico": [], "timeline": [],
    }])
    with patch("services.crm_service._save"):
        assert crm_service.archive_conversation_history("inst1", "5511999990001", []) is False
        assert crm_service.archive_conversation_history("inst1", "5511999990001", [{"role": "user", "content": ""}]) is False
